"""
cs-handoff — 主管理層（對外 API + 狀態機）

狀態機：
                      ┌──────────────────────────────────┐
                      │  signal_detector 偵測到斷點       │
                      └──────────────┬───────────────────┘
                                     ▼
                              ┌───────────┐
                              │  pending  │ ← 剛建立
                              └─────┬─────┘
                       acknowledge  │  timeout (60 min)
                                    ▼
                    ┌─────────────────────────────┐
        ┌───────────│     acknowledged            │
        │           └────────────┬────────────────┘
        │ resolved               │  stuck > 30 min
        ▼                        ▼
  ┌───────────┐            ┌───────────┐
  │  closed   │            │ escalated │ → HQ / 店長
  └───────────┘            └───────────┘

對外 API：
  - check_auto_handoff(...) : 由 cs-brain 在回覆前調用，返回是否要轉人工
  - trigger(...) : 顯式建立 handoff（可帶額外脈絡）
  - acknowledge(handoff_id, advisor_id) : 輔睡員按下「我接手」
  - resolve(handoff_id, outcome) : 對話結案
  - get(handoff_id) / list_pending() : 查詢

每個 handoff 包含：
  handoff_id, line_uid, signal_type, priority, reason,
  created_at, acknowledged_at, resolved_at, status,
  advisor, store_id, customer_display, summary, customer_journey
"""
import os
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from .signal_detector import detect_handoff_signal
from .advisor_roster import route_handoff, next_open_datetime, STORES
from .notification_dispatcher import dispatch
from .advisor_reminder import schedule_reminders, cancel_reminders, patient_message_for_elapsed
from .handoff_store import store as _store

logger = logging.getLogger("lovefu.handoff.manager")

TW_TZ = timezone(timedelta(hours=8))


# ============================================================
# 1. 對 cs-brain 的入口：check_auto_handoff
# ============================================================
def check_auto_handoff(
    line_uid: str,
    message: str,
    intent: str,
    memory: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """
    讓 cs-brain 在回覆前呼叫。
    回傳 (need_handoff, reason_for_response)
      need_handoff=True → cs-brain 把 need_human 設 True，reason 帶入回應
      need_handoff=False → 正常回覆
    """
    memory = memory or {}

    # 從 memory 摸出輔助訊號
    dissat = memory.get("dissatisfaction_count", 0)
    turns = len(memory.get("short_history", []) or []) // 2
    # 重複問同一問題 = 最近 3 輪 user message 相似度高（簡化：文字重疊 >70%）
    repeat = _estimate_repeat_count(memory)

    signal = detect_handoff_signal(
        message=message,
        intent=intent,
        intent_confidence=1.0,       # cs-brain 目前尚未回傳 confidence，預留
        clarify_count=0,
        repeat_question_count=repeat,
        dissatisfaction_count=dissat,
        conversation_turns=turns,
    )
    if not signal:
        return False, None

    signal_type, reason, priority = signal
    # 建立 handoff record
    handoff_id = trigger(
        line_uid=line_uid,
        signal_type=signal_type,
        reason=reason,
        priority=priority,
        intent=intent,
        memory=memory,
    )
    logger.info(f"Auto handoff created: {handoff_id} ({signal_type}/{priority})")
    return True, reason


def _estimate_repeat_count(memory: dict) -> int:
    """極簡化：掃 short_history 看 user 訊息是否重複相似。"""
    history = memory.get("short_history") or []
    user_msgs = [h.get("content", "") for h in history if h.get("role") == "user"]
    if len(user_msgs) < 2:
        return 0
    last = user_msgs[-1]
    repeat = 0
    for prev in user_msgs[-4:-1]:
        if not prev or not last:
            continue
        # 文字重疊簡化指標
        overlap = len(set(last) & set(prev)) / max(len(set(last)), 1)
        if overlap > 0.6:
            repeat += 1
    return repeat


# ============================================================
# 2. 建立 handoff（顯式觸發）
# ============================================================
def trigger(
    line_uid: str,
    signal_type: str,
    reason: str,
    priority: str = "P1",
    intent: str = "CHAT",
    memory: Optional[dict] = None,
    preferred_store_id: Optional[str] = None,
) -> str:
    """建立 handoff，路由值班，派發通知，排提醒。"""
    memory = memory or {}

    # 若該 uid 已有 active handoff → 不重複建立，僅升級優先級
    existing = _store.get_active_by_uid(line_uid)
    if existing and existing["status"] in ("pending", "acknowledged"):
        existing_id = existing["handoff_id"]
        if _priority_higher(priority, existing["priority"]):
            _store.update(existing_id, {
                "priority": priority,
                "reason": f"{existing['reason']}；升級：{reason}",
            })
            logger.info(f"Upgraded existing handoff {existing_id} → {priority}")
        return existing_id

    # 路由
    preferred = preferred_store_id or memory.get("profile", {}).get("preferred_store_id")
    route = route_handoff(preferred_store_id=preferred)

    handoff_id = "HO_" + secrets.token_urlsafe(8)
    profile = memory.get("profile", {}) or {}
    customer_display = profile.get("member_name") or profile.get("line_nickname") or line_uid[:10] + "…"

    handoff = {
        "handoff_id": handoff_id,
        "line_uid": line_uid,
        "signal_type": signal_type,
        "priority": priority,
        "reason": reason,
        "intent": intent,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "outcome": None,
        "target_type": route["target_type"],
        "store_id": route["store_id"],
        "store_name": route["store_name"],
        "advisor": route["advisor"],
        "notify_group": route["notify_group"],
        "customer_display": customer_display,
        "customer_journey": memory.get("customer_journey"),
        "summary": _summarize(memory),
        "suggested_reply": _suggest_reply(signal_type, intent, reason),
        "chatroom_url": f"https://omnichat.tw/conversations?uid={line_uid}",
        "escalation_log": [],
    }

    _store.save(handoff)

    # 派發通知
    if route["target_type"] == "offline":
        # 全離線 → 只旗標 Omnichat + 登記隔日處理
        handoff["offline_until"] = next_open_datetime().isoformat()
        dispatch(handoff)
        logger.info(f"Handoff {handoff_id} offline, will resume at {handoff['offline_until']}")
    else:
        dispatch(handoff)
        # 排提醒
        schedule_reminders(handoff_id, on_stage=_on_reminder_stage)

    return handoff_id


def _priority_higher(new: str, old: str) -> bool:
    order = {"P0": 0, "P1": 1, "P2": 2}
    return order.get(new, 3) < order.get(old, 3)


# ============================================================
# 3. 輔睡員接手
# ============================================================
def acknowledge(handoff_id: str, advisor_id: str) -> bool:
    h = _store.get(handoff_id)
    if not h or h["status"] != "pending":
        return False
    _store.update(handoff_id, {
        "status": "acknowledged",
        "acknowledged_at": datetime.utcnow().isoformat(),
        "acknowledged_by": advisor_id,
    })
    cancel_reminders(handoff_id)
    logger.info(f"Handoff {handoff_id} acknowledged by {advisor_id}")
    return True


def resolve(handoff_id: str, outcome: str = "resolved", note: str = "") -> bool:
    """outcome: resolved / booked / purchased / no_response / cancelled"""
    h = _store.get(handoff_id)
    if not h:
        return False
    _store.update(handoff_id, {
        "status": "closed",
        "resolved_at": datetime.utcnow().isoformat(),
        "outcome": outcome,
        "note": note,
    })
    cancel_reminders(handoff_id)
    _store.remove_active(h["line_uid"], handoff_id)
    logger.info(f"Handoff {handoff_id} resolved: {outcome}")
    return True


# ============================================================
# 4. 升級 callback（由 advisor_reminder 觸發）
# ============================================================
def _on_reminder_stage(stage_name: str, handoff_id: str) -> None:
    h = _store.get(handoff_id)
    if not h or h["status"] != "pending":
        return

    esc_log = h.get("escalation_log") or []
    esc_log.append({"stage": stage_name, "at": datetime.utcnow().isoformat()})

    if stage_name == "first_reminder":
        _store.update(handoff_id, {
            "priority": "P0",
            "reason": f"{h['reason']}（5 分鐘未接手）",
            "escalation_log": esc_log,
        })
        h = _store.get(handoff_id)
        dispatch(h)
    elif stage_name == "escalate_store":
        _store.update(handoff_id, {
            "reason": f"{h['reason']}（15 分鐘未接手，全店廣播）",
            "escalation_log": esc_log,
        })
        h = _store.get(handoff_id)
        dispatch(h)
    elif stage_name == "escalate_hq":
        from .advisor_roster import HQ_DESK
        _store.update(handoff_id, {
            "target_type": "hq",
            "advisor": HQ_DESK["advisors"][0] if HQ_DESK["advisors"] else None,
            "notify_group": HQ_DESK.get("notify_group", ""),
            "reason": f"{h['reason']}（30 分鐘未接手，HQ 升級）",
            "escalation_log": esc_log,
        })
        h = _store.get(handoff_id)
        dispatch(h)
    elif stage_name == "mark_missed":
        _store.update(handoff_id, {
            "status": "missed",
            "resolved_at": datetime.utcnow().isoformat(),
            "outcome": "timeout_no_response",
            "escalation_log": esc_log,
        })
        _store.remove_active(h["line_uid"], handoff_id)
        logger.warning(f"Handoff {handoff_id} marked MISSED (60 min timeout)")


# ============================================================
# 5. 查詢 API
# ============================================================
def get(handoff_id: str) -> Optional[dict]:
    return _store.get(handoff_id)


def list_pending(store_id: Optional[str] = None) -> list[dict]:
    return _store.list_pending(store_id=store_id)


def list_missed(hours: int = 24) -> list[dict]:
    return _store.list_missed(hours=hours)


def get_active_for_uid(line_uid: str) -> Optional[dict]:
    return _store.get_active_by_uid(line_uid)


# ============================================================
# 6. 顧客端耐心話術（由 cs-brain 在 pending 期間用）
# ============================================================
def get_patient_message(line_uid: str) -> Optional[str]:
    h = get_active_for_uid(line_uid)
    if not h or h["status"] != "pending":
        return None
    created = datetime.fromisoformat(h["created_at"])
    elapsed = (datetime.utcnow() - created).total_seconds()
    phone = ""
    if h["store_id"] and h["store_id"] in STORES:
        phone = STORES[h["store_id"]].get("phone", "")
    return patient_message_for_elapsed(elapsed, store_phone=phone)


# ============================================================
# 7. 摘要 / 建議話術（輕量；高精度版可接 LLM）
# ============================================================
def _summarize(memory: dict) -> str:
    history = memory.get("short_history") or []
    if not history:
        return "（無對話紀錄）"
    last_user = next((h["content"] for h in reversed(history) if h.get("role") == "user"), "")
    return f"最近一則：{last_user[:80]}"


def _suggest_reply(signal_type: str, intent: str, reason: str) -> str:
    suggestions = {
        "EXPLICIT": "顧客主動要真人，可直接說「嗨～我是小島，看到你剛剛問的 XX，我來幫你處理」",
        "EMOTION": "先同理情緒：「真的很抱歉讓你有這個困擾」，再具體處理問題",
        "LOW_CONF": "AI 對意圖不確定，麻煩確認顧客真正的需求",
        "HIGH_VALUE": "高價值事件需當場定調；退換貨走安心睡流程，成交訊號快速拉到預約",
    }
    return suggestions.get(signal_type, "請根據對話脈絡協助顧客")
