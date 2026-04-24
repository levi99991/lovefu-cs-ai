"""
大島樂眠 AI 輔睡員 — 訂單查詢
lovefu-cs-shopline/scripts/query_orders.py

所有 API 呼叫經過 cs-guard 安全閘門。
"""

from typing import Optional
from lovefu_cs_guard.scripts.api_guard import shopline_safe_get


async def query_orders_by_search(
    search: str,
    line_uid: str = "",
    limit: int = 5,
) -> dict:
    """
    用模糊搜尋查訂單。
    search 可以是訂單編號、手機、快遞單號、商品名、SKU、金額。
    """
    return await shopline_safe_get(
        path="/orders.json",
        params={
            "search_content": search,
            "limit": str(limit),
            "sort_condition": "order_at:desc",
        },
        caller="cs-shopline.query_orders_by_search",
        line_uid=line_uid,
    )


async def query_orders_by_name(
    order_name: str,
    line_uid: str = "",
) -> dict:
    """用訂單編號精確查詢。"""
    return await shopline_safe_get(
        path="/orders.json",
        params={"name": order_name, "limit": "1"},
        caller="cs-shopline.query_orders_by_name",
        line_uid=line_uid,
    )


async def query_orders_by_buyer(
    buyer_id: str,
    line_uid: str = "",
    limit: int = 5,
) -> dict:
    """用 Shopline 會員 ID 查詢最近的訂單。"""
    return await shopline_safe_get(
        path="/orders.json",
        params={
            "buyer_id": buyer_id,
            "limit": str(limit),
            "sort_condition": "order_at:desc",
        },
        caller="cs-shopline.query_orders_by_buyer",
        line_uid=line_uid,
    )


async def query_orders_by_email(
    email: str,
    line_uid: str = "",
    limit: int = 5,
) -> dict:
    """用 Email 查詢訂單。"""
    return await shopline_safe_get(
        path="/orders.json",
        params={
            "email": email,
            "limit": str(limit),
            "sort_condition": "order_at:desc",
        },
        caller="cs-shopline.query_orders_by_email",
        line_uid=line_uid,
    )


def format_orders_for_llm(api_response: Optional[dict]) -> str:
    """
    將 Shopline 訂單 API 回應格式化為 LLM 可讀的摘要。
    此摘要會被塞進 System Prompt 的「查詢到的資料」區塊。
    """
    if not api_response:
        return "查無訂單紀錄。"

    orders = api_response.get("orders", [])
    if not orders:
        return "查無訂單紀錄。"

    summaries = []
    for o in orders[:3]:  # 最多 3 筆
        # 基本資訊
        name = o.get("name", "N/A")
        status = _translate_status(o.get("status", ""))
        financial = _translate_financial(o.get("financial_status", ""))
        fulfillment = _translate_fulfillment(o.get("fulfillment_status", ""))
        total = o.get("total_price", "N/A")
        created = o.get("created_at", "")[:10]  # 只取日期

        # 商品明細
        items = o.get("line_items", [])
        item_names = "、".join([
            f"{i.get('title', '商品')}"
            + (f" x{i.get('quantity')}" if i.get("quantity", 1) > 1 else "")
            for i in items[:3]
        ])
        if len(items) > 3:
            item_names += f"...等共 {len(items)} 件"

        # 追蹤碼
        tracking = o.get("tracking_number", "")
        tracking_str = f"追蹤碼：{tracking}" if tracking else ""

        summary = (
            f"訂單 {name}\n"
            f"　商品：{item_names}\n"
            f"　下單日期：{created}\n"
            f"　訂單狀態：{status}\n"
            f"　付款狀態：{financial}\n"
            f"　出貨狀態：{fulfillment}\n"
            f"　金額：NT${total}"
        )
        if tracking_str:
            summary += f"\n　{tracking_str}"

        summaries.append(summary)

    return "\n---\n".join(summaries)


# ============================================================
# 狀態翻譯（英→中）
# ============================================================

STATUS_MAP = {
    "open": "處理中",
    "cancelled": "已取消",
    "closed": "已完成",
    "confirmed": "已確認",
    "completed": "已完成",
}

FINANCIAL_MAP = {
    "unpaid": "未付款",
    "authorized": "已授權",
    "pending": "付款處理中",
    "partially_paid": "部分付款",
    "paid": "已付款",
    "partially_refunded": "部分退款",
    "refunded": "已退款",
}

FULFILLMENT_MAP = {
    "unshipped": "尚未出貨",
    "partial": "部分出貨",
    "shipped": "已出貨",
}


def _translate_status(val: str) -> str:
    return STATUS_MAP.get(val, val or "未知")

def _translate_financial(val: str) -> str:
    return FINANCIAL_MAP.get(val, val or "未知")

def _translate_fulfillment(val: str) -> str:
    return FULFILLMENT_MAP.get(val, val or "未知")
