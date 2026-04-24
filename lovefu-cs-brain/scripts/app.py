"""
大島樂眠 AI 輔睡員 — 中控大腦
lovefu-cs-brain/scripts/app.py

這是整套系統的唯一入口。
Make.com POST /chat → 此程式 → 回傳 JSON → Make.com → LINE Reply
"""

import os
import logging
from typing import Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime

from .intent_classifier import classify_intent, check_escalation_keywords
from .prompt_assembler import assemble_prompt
from .model_router import select_model, call_llm
from .omnichat_coexist import check_should_mute, is_currently_muted

# 跨 Skill 引用
from lovefu_cs_memory.scripts.memory_store import (
    load_memory, save_turn, update_profile, get_memory_for_prompt
)
from lovefu_cs_shopline.scripts.query_orders import (
    query_orders_by_buyer, query_orders_by_name,
    query_orders_by_search, format_orders_for_llm
)
from lovefu_cs_shopline.scripts.query_customer import (
    query_customer_by_id, format_customer_for_llm
)
from lovefu_cs_logistics.scripts.wms_client import (
    query_orders as wms_query_orders,
    query_cargo_status as wms_query_cargo_status,
    query_inventory as wms_query_inventory,
    query_stores as wms_query_stores,
    format_cargo_status_for_llm as wms_format_cargo_status,
    format_inventory_for_llm as wms_format_inventory,
    format_stores_for_llm as wms_format_stores,
)
from lovefu_cs_shopline.scripts.query_fulfillment import (
    query_fulfillment_by_order, format_fulfillment_for_llm,
    extract_tracking_numbers
)

# ============================================================
# 初始化
# ============================================================
app = FastAPI(title="LoveFu AI 輔睡員", version="2.2.0")
logger = logging.getLogger("lovefu.brain")


# ============================================================
# 啟動守門 — 防止 LLM_MODE=mock 誤上 production
# ============================================================
def _startup_safety_check():
    env = os.getenv("ENV", "development").lower()
    llm_mode = os.getenv("LLM_MODE", "production").lower()
    if env == "production" and llm_mode == "mock":
        raise RuntimeError(
            "🚨 致命設定錯誤：ENV=production 但 LLM_MODE=mock。"
            "Mock 模式只能用於測試環境，上線前必須改回 production。"
        )
    logger.info(f"Startup check OK — ENV={env}, LLM_MODE={llm_mode}")


_startup_safety_check()


# ============================================================
# 訊息 debounce — 同一顧客短時間連發訊息併批處理
# 策略：第一則訊息等 window，順便把後續訊息撈進來一起處理；
#       window 內的後續訊息直接 silent（不回 LINE），由第一則代答。
# ============================================================
import asyncio
from collections import defaultdict

DEBOUNCE_WINDOW_SEC = float(os.getenv("MESSAGE_DEBOUNCE_SEC", "1.5"))
_debounce_buffer: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
_debounce_leader_until: dict[str, datetime] = {}  # uid → 第一則 handler 窗口結束時間


def _debounce_register(line_uid: str, message: str) -> tuple[bool, str]:
    """
    登記本則訊息。
    回傳 (is_leader, combined_message)
      is_leader=True  → 該 request 負責處理；combined_message 可能包含後續累積
      is_leader=False → 該 request 已被前一則吸收，應立即回 silent
    """
    now = datetime.utcnow()
    leader_until = _debounce_leader_until.get(line_uid)
    _debounce_buffer[line_uid].append((now, message))

    if leader_until and now < leader_until:
        # 前面已有 leader 正在等窗口 → 我是從犯
        return False, ""

    # 我是 leader → 設置窗口
    _debounce_leader_until[line_uid] = now + timedelta(seconds=DEBOUNCE_WINDOW_SEC)
    return True, message


async def _debounce_wait_and_collect(line_uid: str, initial_message: str) -> str:
    """Leader 專用：等 window，收集所有累積的訊息合併返回。"""
    await asyncio.sleep(DEBOUNCE_WINDOW_SEC)
    messages = [m for _, m in _debounce_buffer.get(line_uid, [])]
    # 清空 buffer + leader 標記
    _debounce_buffer.pop(line_uid, None)
    _debounce_leader_until.pop(line_uid, None)
    if len(messages) <= 1:
        return initial_message
    return "\n".join(messages)


# ============================================================
# 資料模型
# ============================================================
class ChatRequest(BaseModel):
    line_uid: str
    message: str
    member_name: Optional[str] = None
    member_id: Optional[str] = None
    member_tier: Optional[str] = None
    timestamp: Optional[str] = None
    # Omnichat 共存：由 Make.com 從 Omnichat Webhook 帶過來
    #   "agent_replied"   真人剛回了訊息，AI 應靜默 30 分鐘
    #   "agent_takeover"  真人標記接管，AI 應靜默 24 小時
    #   "agent_release"   真人交回給 AI
    #   None              一般顧客訊息
    omnichat_event: Optional[str] = None
    # 冪等鍵（Omnichat message_id / LINE webhook eventId），防止 Make.com 重試重複處理
    idempotency_key: Optional[str] = None


# ============================================================
# 冪等處理 — 5 分鐘窗口內相同 key 直接回上次結果
# ============================================================
from collections import OrderedDict
from datetime import datetime as _dt, timedelta as _td

_IDEMPOTENCY_CACHE: "OrderedDict[str, tuple[_dt, dict]]" = OrderedDict()
_IDEMPOTENCY_TTL = _td(minutes=5)
_IDEMPOTENCY_MAX = 2000


def _idempotency_get(key: str) -> Optional[dict]:
    if not key:
        return None
    now = _dt.utcnow()
    # 清過期
    while _IDEMPOTENCY_CACHE:
        k, (t, _) = next(iter(_IDEMPOTENCY_CACHE.items()))
        if now - t > _IDEMPOTENCY_TTL:
            _IDEMPOTENCY_CACHE.popitem(last=False)
        else:
            break
    entry = _IDEMPOTENCY_CACHE.get(key)
    return entry[1] if entry else None


def _idempotency_set(key: str, response: dict) -> None:
    if not key:
        return
    _IDEMPOTENCY_CACHE[key] = (_dt.utcnow(), response)
    if len(_IDEMPOTENCY_CACHE) > _IDEMPOTENCY_MAX:
        _IDEMPOTENCY_CACHE.popitem(last=False)


class ChatResponse(BaseModel):
    reply: str
    need_human: bool = False
    human_reason: Optional[str] = None
    intent: Optional[str] = None
    # silent=True 時，Make.com 應略過回覆動作（不傳 LINE）
    silent: bool = False
    silent_reason: Optional[str] = None
    ack_text: Optional[str] = None  # 即時確認訊息，Make.com 先發這則，再等完整回覆


# ============================================================
# 主流程
# ============================================================
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        # ── Step -1：冪等檢查（防止 Make.com 重試重複處理）──
        cached = _idempotency_get(req.idempotency_key)
        if cached is not None:
            logger.info(f"Idempotency hit: {req.idempotency_key}")
            return ChatResponse(**cached)

        # ── Step -0.5：訊息 debounce（同一顧客連發訊息合併）──
        # Omnichat 事件、空訊息跳過 debounce
        is_leader = True
        effective_message = req.message
        if req.message and not req.omnichat_event and DEBOUNCE_WINDOW_SEC > 0:
            is_leader, _ = _debounce_register(req.line_uid, req.message)
            if not is_leader:
                logger.info(f"Debounced follower for {req.line_uid} — silenced")
                return ChatResponse(
                    reply="",
                    silent=True,
                    silent_reason="debounced_follower",
                )
            effective_message = await _debounce_wait_and_collect(req.line_uid, req.message)
            if effective_message != req.message:
                logger.info(f"Debounce merged {len(effective_message.split(chr(10)))} msgs for {req.line_uid}")

        # ── Step 0：Omnichat 共存檢查（最優先）──
        # 真人接管中 → AI 完全靜默，不呼叫 LLM、不寫記憶（避免污染對話）
        should_mute, mute_reason = check_should_mute(req.line_uid, req.omnichat_event)
        if should_mute:
            logger.info(f"Silent mode for {req.line_uid}: {mute_reason}")
            return ChatResponse(
                reply="",
                need_human=False,
                silent=True,
                silent_reason=mute_reason,
            )

        # ── Step 1：轉接關鍵字攔截（不經 LLM）──
        if check_escalation_keywords(effective_message):
            save_turn(req.line_uid, "user", effective_message)
            escalation_reply = (
                "了解你的狀況了，這個部分我想請更專業的輔睡員來幫你處理，"
                "會比較完整。我幫你轉接，請稍等一下喔！"
            )
            save_turn(req.line_uid, "assistant", escalation_reply)
            return ChatResponse(
                reply=escalation_reply,
                need_human=True,
                human_reason="偵測到轉接關鍵字",
                intent="COMPLAINT",
            )

        # ── Step 2：載入對話記憶 ──
        memory = get_memory_for_prompt(req.line_uid)

        # 更新 profile（如果 Omnichat 帶了會員資料）
        if req.member_name or req.member_id:
            update_profile(
                req.line_uid,
                member_name=req.member_name,
                member_id=req.member_id,
                member_tier=req.member_tier,
            )
            memory = get_memory_for_prompt(req.line_uid)

        # 不滿意計數器檢查
        if memory["dissatisfaction_count"] >= 2:
            escalation_reply = (
                "我理解你的感受，真的很抱歉讓你不滿意。"
                "我馬上請輔睡員來幫你處理，請稍等一下喔！"
            )
            save_turn(req.line_uid, "user", effective_message)
            save_turn(req.line_uid, "assistant", escalation_reply)
            update_profile(req.line_uid, dissatisfied=False)  # 歸零
            return ChatResponse(
                reply=escalation_reply,
                need_human=True,
                human_reason="連續不滿意觸發轉接",
                intent="COMPLAINT",
            )

        # ── Step 3：意圖分類 ──
        intent = await classify_intent(effective_message)

        # ── Step 3.5：產生即時確認訊息 ──
        ack = _generate_ack(intent, effective_message)

        # COMPLAINT 直接轉人工
        if intent == "COMPLAINT":
            escalation_reply = (
                "我理解你的感受，真的很抱歉讓你有這樣的體驗。"
                "我馬上請輔睡員來幫你處理，他們會盡快跟你聯繫。"
            )
            save_turn(req.line_uid, "user", effective_message)
            save_turn(req.line_uid, "assistant", escalation_reply)
            return ChatResponse(
                reply=escalation_reply,
                need_human=True,
                human_reason="意圖分類為 COMPLAINT",
                intent="COMPLAINT",
                ack_text=ack,
            )

        # ── Step 4：按意圖調取資料（Shopline / WMS / Knowledge 並行）──
        extra_context = await _fetch_context(
            intent=intent,
            message=effective_message,
            member_id=req.member_id,
            line_uid=req.line_uid,
        )

        # ── Step 5：組裝 Prompt + LLM 生成 ──
        model = select_model(intent)
        messages = assemble_prompt(
            message=effective_message,
            intent=intent,
            memory=memory,
            extra_context=extra_context,
        )
        reply = await call_llm(model=model, messages=messages)

        # ── Step 6：儲存記憶 + 回傳 ──
        save_turn(req.line_uid, "user", effective_message)
        save_turn(req.line_uid, "assistant", reply)
        update_profile(req.line_uid, intent=intent)

        # ── Step 7：Handoff 檢查（AI 自我評估是否該轉人工）──
        try:
            from lovefu_cs_handoff.scripts.handoff_manager import check_auto_handoff
            handoff_needed, handoff_reason = check_auto_handoff(
                line_uid=req.line_uid,
                message=effective_message,
                intent=intent,
                memory=memory,
            )
            if handoff_needed:
                logger.info(f"Auto-handoff triggered for {req.line_uid}: {handoff_reason}")
        except Exception as e:
            logger.warning(f"Handoff check failed (non-fatal): {e}")
            handoff_needed = False
            handoff_reason = None

        # RETURN 收集完資訊後轉人工
        need_human = (intent == "RETURN") or handoff_needed
        if intent == "RETURN":
            human_reason = "退換貨申請需人工處理"
        elif handoff_needed:
            human_reason = handoff_reason
        else:
            human_reason = None

        response = ChatResponse(
            reply=reply,
            need_human=need_human,
            human_reason=human_reason,
            intent=intent,
            ack_text=ack,
        )
        _idempotency_set(req.idempotency_key, response.dict())
        return response

    except Exception as e:
        logger.error(f"Brain error: {e}", exc_info=True)
        return ChatResponse(
            reply="不好意思，系統暫時有點忙，讓我幫你轉接輔睡員來處理喔！",
            need_human=True,
            human_reason=f"系統錯誤: {str(e)}",
            intent="ERROR",
            ack_text=None,
        )


# ============================================================
# 二階段回覆：產生即時確認訊息
# ============================================================
def _generate_ack(intent: str, message: str) -> Optional[str]:
    """
    根據意圖生成即時確認訊息。
    Make.com 可以立即發送此訊息，同時生成完整回覆。
    """
    ack_messages = {
        "ORDER": "收到！我正在幫你查訂單，請稍等一下 🔍",
        "CARGO": "收到，我幫你查一下物流狀態 📦",
        "PRODUCT": "好的，讓我幫你找找適合的方案 🌙",
        "SLEEP": "好的，讓我幫你找找適合的方案 🌙",
        "RETURN": "了解，我先幫你確認退換貨資訊 📋",
        "STORE": "好的，我來幫你查門市資訊 🏪",
        "MEMBER": "收到，我幫你查一下會員資料 ✨",
        "STOCK": "收到，我幫你看一下庫存 📦",
    }
    return ack_messages.get(intent)


# ============================================================
# Step 4 細節：按意圖調取外部資料
# ============================================================
async def _fetch_context(
    intent: str,
    message: str,
    member_id: Optional[str],
    line_uid: str,
) -> str:
    """
    根據意圖，從 Shopline / 物流 / 知識庫取得額外上下文。
    回傳字串，直接塞進 Prompt。
    """

    if intent == "ORDER":
        return await _fetch_order_context(message, member_id, line_uid)

    elif intent == "MEMBER":
        return await _fetch_member_context(member_id, line_uid)

    elif intent == "RETURN":
        return (
            "顧客可能要退換貨。請先確認：\n"
            "1. 訂單編號\n"
            "2. 退換貨原因\n"
            "3. 包裝和配件是否完整\n"
            "AI 只負責受理和收集資訊，不能執行退貨操作。\n"
            "收集完畢後告訴顧客：已提交申請，輔睡員會在 1~2 工作天內聯繫。"
        )

    elif intent == "STORE":
        # 直接從 WMS pos/store.php 取最新門市清單（非同步包裝避免阻塞）
        try:
            stores = await asyncio.get_event_loop().run_in_executor(None, wms_query_stores)
            if stores:
                return "門市清單（WMS 即時）：\n" + wms_format_stores(stores)
        except Exception as e:
            logger.warning("WMS stores fetch failed: %s", e)
        return "顧客詢問體驗店資訊。請提供最近門市的地址、營業時間、電話，並引導預約體驗。"

    elif intent == "STOCK":
        # 顧客問「還有貨嗎」— 從訊息抽 SKU 或商品名
        import re as _re
        sku_match = _re.findall(r"[A-Z]{2,}-[A-Z0-9-]+", message)
        if sku_match:
            try:
                inv = await asyncio.get_event_loop().run_in_executor(
                    None, wms_query_inventory, sku_match[:10]
                )
                if inv:
                    return "庫存查詢（WMS 即時）：\n" + wms_format_inventory(inv)
            except Exception as e:
                logger.warning("WMS inventory fetch failed: %s", e)
        return "顧客詢問商品庫存。請 AI 確認是哪個型號/尺寸，再查 WMS 即時庫存。"

    elif intent == "CARGO":
        # 純查貨態（已有訂單號，只要 timeline）
        import re as _re
        order_matches = _re.findall(r"#?([A-Z]{0,3}\d{6,})", message)
        if order_matches:
            try:
                cargo = await asyncio.get_event_loop().run_in_executor(
                    None, wms_query_cargo_status, order_matches[:5]
                )
                if cargo:
                    return "貨態查詢（WMS）：\n" + wms_format_cargo_status(cargo)
            except Exception as e:
                logger.warning("WMS cargo fetch failed: %s", e)
        return "顧客詢問貨態但未提供訂單編號。請 AI 禮貌詢問訂單編號。"

    elif intent in ("PRODUCT", "SLEEP"):
        # 知識庫的內容由 prompt_assembler 從 reference 檔案載入
        # 這裡只提供額外指示
        if intent == "SLEEP":
            return "顧客有睡眠困擾。先傾聽、同理，再用引導式提問了解狀況，最後才建議適合的產品方向。"
        return ""

    return ""


async def _safe_wms_cargo(order_nos: list) -> str:
    """
    非同步包裝：WMS 出貨狀態取得。
    用來與履約資訊並行查詢。
    """
    try:
        cargo = await asyncio.get_event_loop().run_in_executor(
            None, wms_query_cargo_status, order_nos[:5]
        )
        if cargo:
            return "\n\n【WMS 出貨狀態】\n" + wms_format_cargo_status(cargo)
    except Exception as e:
        logger.warning("WMS cargo fetch failed: %s", e)
    return ""


async def _safe_fulfillment(result: dict) -> str:
    """
    非同步包裝：履約資訊取得。
    用來與 WMS 貨態並行查詢。
    注意：query_fulfillment_by_order 本身就是 async，直接 await 即可。
    """
    try:
        for o in result.get("orders", [])[:3]:
            oid = o.get("id")
            if oid:
                ful = await query_fulfillment_by_order(oid)
                if ful:
                    return "\n\n【出貨明細】\n" + format_fulfillment_for_llm(ful)
    except Exception as e:
        logger.warning("Fulfillment fetch failed: %s", e)
    return ""


async def _fetch_order_context(
    message: str,
    member_id: Optional[str],
    line_uid: str,
) -> str:
    """訂單查詢的資料調取邏輯。"""
    result = None

    # 策略 1：有 member_id → 用 buyer_id 查
    if member_id:
        result = await query_orders_by_buyer(member_id, line_uid)

    # 策略 2：訊息中有訂單編號 pattern → 精確查
    if not result or not result.get("orders"):
        import re
        order_match = re.search(r"#?([A-Z]{0,3}\d{4,})", message)
        if order_match:
            result = await query_orders_by_name(order_match.group(0), line_uid)

    # 策略 3：訊息中有手機或 Email → 模糊查
    if not result or not result.get("orders"):
        phone_match = __import__("re").search(r"09\d{8}", message)
        email_match = __import__("re").search(r"\S+@\S+\.\S+", message)
        search_term = phone_match.group(0) if phone_match else (
            email_match.group(0) if email_match else None
        )
        if search_term:
            result = await query_orders_by_search(search_term, line_uid)

    if result and result.get("orders"):
        # ── SSOT 合併 Shopline（付款/商品/退款）+ WMS（出貨/貨態） ──
        shopline_text = format_orders_for_llm(result)

        # 從 Shopline 訂單抽出 order_no，並行查 WMS 貨態 + 履約資訊
        order_nos = []
        for o in result.get("orders", []):
            no = o.get("order_name") or o.get("order_no") or o.get("name")
            if no:
                order_nos.append(no.lstrip("#"))

        wms_section = ""
        fulfillment_section = ""

        if order_nos:
            # 並行取 WMS 貨態 + 履約資訊
            wms_task = asyncio.create_task(_safe_wms_cargo(order_nos))
            fulfillment_task = asyncio.create_task(_safe_fulfillment(result))

            wms_section = await wms_task
            fulfillment_section = await fulfillment_task

        return (
            "訂單查詢結果（付款/商品以 Shopline 為準，出貨/物流以 WMS 為準）：\n"
            f"{shopline_text}{wms_section}{fulfillment_section}"
        )

    return "查無訂單。請 AI 詢問顧客提供訂單編號或手機號碼。"


async def _fetch_member_context(
    member_id: Optional[str],
    line_uid: str,
) -> str:
    """會員查詢的資料調取邏輯。"""
    if not member_id:
        return "尚未識別會員身份。請 AI 詢問顧客手機或 Email。"

    result = await query_customer_by_id(member_id, line_uid)
    if result:
        return f"會員查詢結果：\n{format_customer_for_llm(result)}"

    return "查無會員紀錄。"


# ============================================================
# 健康檢查
# ============================================================
@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.2.0", "timestamp": datetime.utcnow().isoformat()}


# ============================================================
# Omnichat 共存：mute 狀態查詢 / 強制解除
# ============================================================
@app.get("/mute/{line_uid}")
async def mute_status(line_uid: str):
    """查詢某位顧客的 AI 靜默狀態。"""
    from .omnichat_coexist import get_mute_remaining
    if not is_currently_muted(line_uid):
        return {"line_uid": line_uid, "muted": False}
    remaining = get_mute_remaining(line_uid)
    return {
        "line_uid": line_uid,
        "muted": True,
        "remaining_seconds": int(remaining.total_seconds()) if remaining else 0,
    }


@app.delete("/mute/{line_uid}")
async def mute_clear(line_uid: str):
    """強制解除某位顧客的 AI 靜默。"""
    from .omnichat_coexist import clear_mute
    clear_mute(line_uid)
    return {"line_uid": line_uid, "muted": False, "action": "cleared"}


# ============================================================
# Advisor Handoff API — 供 Omnichat 後台 / LINE OA 輔睡員使用
# ============================================================
class AcknowledgeRequest(BaseModel):
    advisor_id: str


class ResolveRequest(BaseModel):
    outcome: str = "resolved"  # resolved / booked / purchased / no_response / cancelled
    note: Optional[str] = ""


@app.get("/handoffs/pending")
async def list_pending_handoffs(store_id: Optional[str] = None):
    """列出待接手的 handoff（給 Omnichat 後台 / LINE OA 顯示）。"""
    from lovefu_cs_handoff.scripts.handoff_manager import list_pending
    return {"handoffs": list_pending(store_id=store_id)}


@app.get("/handoffs/{handoff_id}")
async def get_handoff(handoff_id: str):
    from lovefu_cs_handoff.scripts.handoff_manager import get as get_handoff_rec
    h = get_handoff_rec(handoff_id)
    if not h:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return h


@app.post("/handoffs/{handoff_id}/acknowledge")
async def acknowledge_handoff(handoff_id: str, req: AcknowledgeRequest):
    """輔睡員按『我接手』→ 設定 Omnichat mute + 取消提醒。"""
    from lovefu_cs_handoff.scripts.handoff_manager import acknowledge, get as get_handoff_rec
    from .omnichat_coexist import mark_agent_takeover
    ok = acknowledge(handoff_id, req.advisor_id)
    if not ok:
        return JSONResponse(status_code=400, content={"error": "cannot_acknowledge"})
    h = get_handoff_rec(handoff_id)
    if h and h.get("line_uid"):
        mark_agent_takeover(h["line_uid"])  # AI 靜默 24 小時
    return {"ok": True, "handoff_id": handoff_id, "status": "acknowledged"}


@app.post("/handoffs/{handoff_id}/resolve")
async def resolve_handoff(handoff_id: str, req: ResolveRequest):
    """輔睡員結案（成交 / 預約 / 無回應 / 取消）。"""
    from lovefu_cs_handoff.scripts.handoff_manager import resolve
    ok = resolve(handoff_id, outcome=req.outcome, note=req.note or "")
    if not ok:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return {"ok": True, "handoff_id": handoff_id, "outcome": req.outcome}


@app.get("/handoffs/missed/recent")
async def list_missed_handoffs(hours: int = 24):
    """列出最近 N 小時內超時未接的 handoff（給晨會檢討用）。"""
    from lovefu_cs_handoff.scripts.handoff_manager import list_missed
    return {"hours": hours, "missed": list_missed(hours=hours)}


# ============================================================
# 啟動
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
