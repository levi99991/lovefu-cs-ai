"""
大島樂眠 AI 輔睡員 — Shopline Mock 資料源
lovefu-cs-shopline/scripts/mock_data.py

當環境變數 SHOPLINE_MODE=mock 時，所有 Shopline API 呼叫都會被
api_guard.shopline_safe_get 攔截，改回傳此模組產生的假資料。

用途：
  - 本機開發、CI 測試、Demo
  - 在還沒拿到真實 Shopline Token 之前先把整套系統跑通
  - 自動化測試（避免打到真實後台）

設計原則：
  - 回傳結構必須與真實 Shopline API 一致（這樣下游 format_*_for_llm 不需要改）
  - 涵蓋 happy path 與常見邊界（已出貨/未出貨/已付款/未付款）
  - 不含真實個資（手機/Email/地址都用測試值）
"""

from typing import Optional
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))


# ============================================================
# 訂單資料（搭配 products-mattress.md 的真實價格）
# ============================================================

_NOW = datetime.now(TW_TZ)


def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


_MOCK_ORDERS = [
    # 已出貨且已付款（最常見的查詢情境）
    {
        "id": "ord_mock_001",
        "name": "#LF20260301001",
        "status": "open",
        "financial_status": "paid",
        "fulfillment_status": "shipped",
        "total_price": "20900",
        "currency": "TWD",
        "created_at": _iso(7),
        "order_at": _iso(7),
        "tracking_number": "MOCK0001234567",
        "buyer_id": "cust_mock_001",
        "email": "test01@example.com",
        "phone": "0912345678",
        "line_items": [
            {"title": "山丘樂眠床 標準雙人 5×6.2 尺", "quantity": 1, "price": "20900"},
            {"title": "月眠枕 3.0 舒柔米×慵懶綠", "quantity": 2, "price": "5380"},
        ],
        "shipping_address": {
            "address1": "台北市信義區松仁路 100 號",
            "city": "台北市",
            "zip": "110",
        },
    },
    # 已付款但尚未出貨
    {
        "id": "ord_mock_002",
        "name": "#LF20260310002",
        "status": "open",
        "financial_status": "paid",
        "fulfillment_status": "unshipped",
        "total_price": "29600",
        "currency": "TWD",
        "created_at": _iso(3),
        "order_at": _iso(3),
        "tracking_number": "",
        "buyer_id": "cust_mock_001",
        "email": "test01@example.com",
        "phone": "0912345678",
        "line_items": [
            {"title": "冰島樂眠床 標準單人 3×6.2 尺", "quantity": 1, "price": "29600"},
        ],
        "shipping_address": {
            "address1": "台北市信義區松仁路 100 號",
            "city": "台北市",
            "zip": "110",
        },
    },
    # 未付款（待匯款）
    {
        "id": "ord_mock_003",
        "name": "#LF20260312003",
        "status": "open",
        "financial_status": "unpaid",
        "fulfillment_status": "unshipped",
        "total_price": "8900",
        "currency": "TWD",
        "created_at": _iso(1),
        "order_at": _iso(1),
        "tracking_number": "",
        "buyer_id": "cust_mock_002",
        "email": "test02@example.com",
        "phone": "0987654321",
        "line_items": [
            {"title": "無光薄墊 標準單人 3×6.2 尺", "quantity": 1, "price": "8900"},
        ],
    },
]


# ============================================================
# 會員資料
# ============================================================

_MOCK_CUSTOMERS = {
    "cust_mock_001": {
        "id": "cust_mock_001",
        "name": "王小明",
        "email": "test01@example.com",
        "phone": "0912345678",
        "tier": "睡厚墊的海獺",      # VIP 等級
        "points": 1280,
        "total_spent": "55880",
        "orders_count": 3,
        "created_at": _iso(180),
    },
    "cust_mock_002": {
        "id": "cust_mock_002",
        "name": "陳小美",
        "email": "test02@example.com",
        "phone": "0987654321",
        "tier": "新島民",
        "points": 89,
        "total_spent": "8900",
        "orders_count": 1,
        "created_at": _iso(15),
    },
}


# ============================================================
# Mock 路由器（依路徑回傳對應假資料）
# ============================================================


def get_mock_response(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    根據 path 與 params 回傳對應的 mock 資料。
    回傳結構與真實 Shopline API 一致。

    支援的 path：
      - /orders.json                    → 訂單列表（依 buyer_id / search_content / name 過濾）
      - /orders/{id}.json               → 單筆訂單
      - /customers.json                 → 會員列表
      - /customers/{id}.json            → 單一會員
      - /customers/search.json          → 搜尋會員（依 query）

    其他路徑 → 回傳 None（讓 api_guard 走 fallback）
    """
    params = params or {}

    # ── 訂單列表 ──
    if path == "/orders.json":
        orders = _MOCK_ORDERS

        # 模擬篩選邏輯
        if buyer_id := params.get("buyer_id"):
            orders = [o for o in orders if o["buyer_id"] == buyer_id]
        elif name := params.get("name"):
            orders = [o for o in orders if name.lstrip("#") in o["name"]]
        elif search := params.get("search_content"):
            search_lower = search.lower()
            orders = [
                o for o in orders
                if search_lower in o["name"].lower()
                or search in (o.get("phone") or "")
                or search.lower() in (o.get("email") or "").lower()
                or any(search in i["title"] for i in o.get("line_items", []))
            ]
        elif email := params.get("email"):
            orders = [o for o in orders if o.get("email") == email]

        try:
            limit = int(params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5

        return {"orders": orders[:limit]}

    # ── 單筆訂單 ──
    if path.startswith("/orders/") and path.endswith(".json") and "/transactions" not in path:
        order_id = path.replace("/orders/", "").replace(".json", "")
        order = next((o for o in _MOCK_ORDERS if o["id"] == order_id), None)
        return {"order": order} if order else {"order": None}

    # ── 會員列表 ──
    if path == "/customers.json":
        return {"customers": list(_MOCK_CUSTOMERS.values())}

    # ── 單一會員 ──
    if path.startswith("/customers/") and path.endswith(".json") and "/search" not in path:
        cust_id = path.replace("/customers/", "").replace(".json", "")
        cust = _MOCK_CUSTOMERS.get(cust_id)
        return {"customer": cust} if cust else {"customer": None}

    # ── 搜尋會員 ──
    if path == "/customers/search.json":
        query = (params.get("query") or "").lower()
        results = [
            c for c in _MOCK_CUSTOMERS.values()
            if query in c.get("phone", "")
            or query in c.get("email", "").lower()
            or query in c.get("name", "")
        ]
        return {"customers": results}

    # ── 出貨查詢（簡化版） ──
    if "fulfillment_orders" in path:
        return {
            "fulfillment_orders": [
                {
                    "id": "ff_mock_001",
                    "status": "in_progress",
                    "tracking_number": "MOCK0001234567",
                    "tracking_company": "黑貓宅急便",
                    "shipped_at": _iso(2),
                }
            ]
        }

    return None
