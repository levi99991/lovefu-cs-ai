"""
門市追客排程器 — 5 階段（T+0 / +24h / +3d / +7d / +14d）
"""
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("lovefu.cs_instore.scheduler")

INSTORE_MAX_MSG_PER_14D = int(os.getenv("INSTORE_MAX_MSG_PER_14D", "5"))
INSTORE_PAUSE_ON_BLOCK = os.getenv("INSTORE_PAUSE_ON_BLOCK", "true").lower() == "true"

# 5 階段時間偏移
STAGES = [
    ("S0_intro",    timedelta(minutes=30),  "當下確認"),
    ("S1_feedback", timedelta(hours=24),    "睡眠回饋"),
    ("S2_deep",     timedelta(days=3),      "深度諮詢"),
    ("S3_offer",    timedelta(days=7),      "優惠提醒"),
    ("S4_care",     timedelta(days=14),     "最後關懷"),
]

# Module-level store；生產環境改用 Redis / DB
_LEADS: dict = {}            # store_lead_id → {data...}
_LINE_BIND: dict = {}        # line_uid → store_lead_id
_DRAFTS: dict = {}           # draft_id → {...}
_PAUSED: set = set()         # line_uid 在追客暫停中


def register_lead(store_lead_data: dict) -> dict:
    """
    門市試躺結束 → 註冊 lead。
    輸入：{store_id, advisor_name, customer_name, phone_last4, tried_products, family_context, budget, intent}
    輸出：{store_lead_id, qr_url}
    """
    store_lead_id = "SL_" + secrets.token_urlsafe(8)
    _LEADS[store_lead_id] = {
        **store_lead_data,
        "store_lead_id": store_lead_id,
        "created_at": datetime.now().isoformat(),
        "stage": "試躺中",
        "consent_marketing": store_lead_data.get("consent_marketing", False),
    }

    # LINE OA 加好友 URL（門市 QR）
    line_oa_id = os.getenv("LINE_OA_ID", "@lovefu")
    qr_url = f"https://line.me/R/ti/p/{line_oa_id}?lead={store_lead_id}"

    logger.info("Lead registered: %s @ %s", store_lead_id, store_lead_data.get("store_id"))
    return {"store_lead_id": store_lead_id, "qr_url": qr_url}


def bind_line(store_lead_id: str, line_uid: str) -> bool:
    """顧客掃 QR 加好友後，cs-instore 將 store_lead_id 與 line_uid 綁定"""
    if store_lead_id not in _LEADS:
        logger.warning("bind_line: unknown store_lead_id %s", store_lead_id)
        return False
    _LINE_BIND[line_uid] = store_lead_id
    _LEADS[store_lead_id]["line_uid"] = line_uid
    _LEADS[store_lead_id]["stage"] = "待追蹤"
    _LEADS[store_lead_id]["bound_at"] = datetime.now().isoformat()
    schedule_follow_ups(line_uid)
    return True


def schedule_follow_ups(line_uid: str) -> int:
    """為新待追蹤顧客建立 5 階段排程（產生 5 個 pending draft）"""
    store_lead_id = _LINE_BIND.get(line_uid)
    if not store_lead_id:
        return 0

    base_time = datetime.now()
    count = 0
    for stage_id, offset, _name in STAGES:
        draft_id = "DR_" + secrets.token_urlsafe(8)
        _DRAFTS[draft_id] = {
            "draft_id": draft_id,
            "line_uid": line_uid,
            "store_lead_id": store_lead_id,
            "stage_id": stage_id,
            "scheduled_at": (base_time + offset).isoformat(),
            "status": "pending_generate",  # pending_generate → pending_review → sent / cancelled
            "draft_text": None,
            "sent_at": None,
        }
        count += 1
    logger.info("Scheduled %d follow-ups for %s", count, line_uid)
    return count


def list_pending_drafts(store_advisor: Optional[str] = None) -> list[dict]:
    """列出待產草稿或待真人審閱的 draft（給 OM 後台顯示）"""
    now = datetime.now()
    out = []
    for d in _DRAFTS.values():
        if d["status"] in ("sent", "cancelled"):
            continue
        if datetime.fromisoformat(d["scheduled_at"]) > now:
            continue
        # 篩選 advisor
        if store_advisor:
            lead = _LEADS.get(d["store_lead_id"], {})
            if lead.get("advisor_name") != store_advisor:
                continue
        out.append(d)
    return out


def mark_sent(draft_id: str, edited_text: str | None = None) -> bool:
    """真人按確認後標記已發送"""
    d = _DRAFTS.get(draft_id)
    if not d:
        return False
    if edited_text is not None:
        d["final_text"] = edited_text
    else:
        d["final_text"] = d.get("draft_text", "")
    d["status"] = "sent"
    d["sent_at"] = datetime.now().isoformat()
    return True


def can_send_more(line_uid: str) -> bool:
    """檢查 14 天內是否還沒到上限"""
    if line_uid in _PAUSED:
        return False
    fourteen_days_ago = datetime.now() - timedelta(days=14)
    sent_count = sum(
        1 for d in _DRAFTS.values()
        if d.get("line_uid") == line_uid
        and d.get("status") == "sent"
        and d.get("sent_at")
        and datetime.fromisoformat(d["sent_at"]) > fourteen_days_ago
    )
    return sent_count < INSTORE_MAX_MSG_PER_14D


def pause_follow_ups(line_uid: str, reason: str) -> None:
    """顧客封鎖 / 已下單 / 主動拒絕 → 暫停"""
    _PAUSED.add(line_uid)
    # 取消所有未發 draft
    for d in _DRAFTS.values():
        if d.get("line_uid") == line_uid and d["status"] not in ("sent", "cancelled"):
            d["status"] = "cancelled"
            d["cancel_reason"] = reason
    logger.info("Paused follow-ups for %s: %s", line_uid, reason)


def get_journey_stage(line_uid: str) -> str:
    """查詢顧客當前 customer_journey.stage"""
    sl = _LINE_BIND.get(line_uid)
    if not sl:
        return "未追蹤"
    return _LEADS.get(sl, {}).get("stage", "未追蹤")


def advance_stage(line_uid: str, new_stage: str) -> None:
    """由 webhook 或外部事件推進 stage（已下單 / 已交付 / 沉睡客）"""
    sl = _LINE_BIND.get(line_uid)
    if sl and sl in _LEADS:
        _LEADS[sl]["stage"] = new_stage
        if new_stage in ("已下單", "已交付", "沉睡客"):
            pause_follow_ups(line_uid, f"stage_{new_stage}")
