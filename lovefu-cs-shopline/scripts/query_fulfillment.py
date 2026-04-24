"""
大島樂眠 AI 輔睡員 — 出貨查詢
lovefu-cs-shopline/scripts/query_fulfillment.py
"""

from typing import Optional
from lovefu_cs_guard.scripts.api_guard import shopline_safe_get


async def query_fulfillment_by_order(
    order_id: str,
    line_uid: str = "",
) -> dict:
    """用訂單 ID 查詢出貨狀態。"""
    return await shopline_safe_get(
        path=f"/fulfillment_orders/{order_id}/fulfillment_orders.json",
        caller="cs-shopline.query_fulfillment_by_order",
        line_uid=line_uid,
    )


def format_fulfillment_for_llm(api_response: Optional[dict]) -> str:
    """將出貨 API 回應格式化為 LLM 可讀摘要。"""
    if not api_response:
        return "查無出貨紀錄。"

    fulfillments = api_response.get("fulfillment_orders", [])
    if not fulfillments:
        return "此訂單尚未安排出貨。"

    summaries = []
    for f in fulfillments:
        status = f.get("status", "未知")
        tracking = f.get("tracking_number", "")
        company = f.get("tracking_company", "")
        fulfill_at = f.get("fulfill_at", "")[:10] if f.get("fulfill_at") else ""

        summary = f"出貨狀態：{_translate_fulfillment_status(status)}"

        if fulfill_at:
            summary += f"\n　出貨日期：{fulfill_at}"
        if company:
            summary += f"\n　物流公司：{company}"
        if tracking:
            summary += f"\n　追蹤碼：{tracking}"
        else:
            summary += "\n　追蹤碼：尚未產生"

        summaries.append(summary)

    return "\n---\n".join(summaries)


def extract_tracking_numbers(api_response: Optional[dict]) -> list[str]:
    """
    從出貨回應中提取所有追蹤碼。
    供 cs-logistics 進一步查詢即時物流狀態。
    """
    if not api_response:
        return []

    numbers = []
    for f in api_response.get("fulfillment_orders", []):
        tn = f.get("tracking_number", "")
        if tn:
            numbers.append(tn)
    return numbers


FULFILLMENT_STATUS_MAP = {
    "open": "待出貨",
    "in_progress": "處理中",
    "closed": "已完成",
    "cancelled": "已取消",
    "submitted": "已提交",
    "accepted": "已接受",
    "request_declined": "被退回",
}


def _translate_fulfillment_status(val: str) -> str:
    return FULFILLMENT_STATUS_MAP.get(val, val or "未知")
