"""
大島樂眠 AI 輔睡員 — 會員查詢
lovefu-cs-shopline/scripts/query_customer.py
"""

from typing import Optional
from lovefu_cs_guard.scripts.api_guard import shopline_safe_get


async def query_customer_by_id(
    customer_id: str,
    line_uid: str = "",
) -> dict:
    """用 Shopline 會員 ID 查詢會員資料。"""
    return await shopline_safe_get(
        path=f"/customers/{customer_id}.json",
        caller="cs-shopline.query_customer_by_id",
        line_uid=line_uid,
    )


async def search_customers(
    query: str,
    line_uid: str = "",
    limit: int = 5,
) -> dict:
    """用手機或 Email 搜尋會員。"""
    return await shopline_safe_get(
        path="/customers/search.json",
        params={"query": query, "limit": str(limit)},
        caller="cs-shopline.search_customers",
        line_uid=line_uid,
    )


def format_customer_for_llm(api_response: Optional[dict]) -> str:
    """
    將會員 API 回應格式化為 LLM 可讀的摘要。
    個資已被 cs-guard 的 mask_pii() 遮蔽過。
    """
    if not api_response:
        return "查無會員紀錄。"

    customer = api_response.get("customer", {})
    if not customer:
        return "查無會員紀錄。"

    name = f"{customer.get('last_name', '')}{customer.get('first_name', '')}".strip()
    if not name:
        name = "未提供姓名"

    total_spent = float(customer.get("total_spent", 0))
    orders_count = customer.get("orders_count", 0)
    tier = _determine_tier(total_spent)

    summary = (
        f"會員姓名：{name}\n"
        f"海獺等級：{tier}\n"
        f"累積消費：NT${total_spent:,.0f}\n"
        f"歷史訂單：{orders_count} 筆"
    )

    # 標籤（如果有的話）
    tags = customer.get("tags", "")
    if tags:
        summary += f"\n標籤：{tags}"

    return summary


def _determine_tier(total_spent: float) -> str:
    """根據累積消費判斷海獺等級。"""
    if total_spent > 120000:
        return "癱 LOVEFU 床的海獺（終身 94 折 + 生日禮金 NT$1,000）"
    elif total_spent > 80000:
        return "睡薄墊的海獺（終身 96 折 + 生日禮金 NT$900）"
    else:
        return "初入島的海獺"
