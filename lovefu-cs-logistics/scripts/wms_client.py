"""
WMS 暢流 高階查詢介面
所有 GET 呼叫都經過 cs-guard.wms_safe_get()，POST 從根上不存在。
"""
import os
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("lovefu.cs_logistics.client")

WMS_MODE = os.getenv("WMS_MODE", "mock")

# 嘗試 import guard；測試環境若無 guard 就用本地 stub
try:
    from lovefu_cs_guard.scripts.api_guard import wms_safe_get  # type: ignore
except ImportError:
    def wms_safe_get(path: str, params: dict | None = None) -> dict:
        # Fallback：直接 mock，方便獨立測試
        from . import mock_wms_data
        return _mock_dispatch(path, params or {})

from . import mock_wms_data
from .pii_decrypt import decrypt_and_mask
from .wms_cache import (
    cached_cargo_status, cached_orders, cached_inventory,
    cached_stores, cached_logistics_codes,
)


def _mock_dispatch(path: str, params: dict) -> dict:
    """Mock 模式路由"""
    if path.endswith("/order/order_query.php"):
        order_nos = (params.get("order_no") or "").split(",")
        rows = [mock_wms_data.MOCK_ORDERS[o] for o in order_nos if o in mock_wms_data.MOCK_ORDERS]
        return {"rows": rows}

    if path.endswith("/order/order_logistics.php"):
        order_nos = (params.get("order_no") or "").split(",")
        rows = [mock_wms_data.MOCK_CARGO_TIMELINES[o] for o in order_nos if o in mock_wms_data.MOCK_CARGO_TIMELINES]
        return {"rows": rows}

    if path.endswith("/inventory/stock_query.php"):
        skus = (params.get("sku") or "").split(",")
        rows = [mock_wms_data.MOCK_INVENTORY[s] for s in skus if s in mock_wms_data.MOCK_INVENTORY]
        return {"rows": rows}

    if path.endswith("/pos/store.php"):
        return {"rows": mock_wms_data.MOCK_STORES}

    if path.endswith("/order/logistics_code.php"):
        return {"rows": mock_wms_data.MOCK_LOGISTICS_CODES}

    return {"rows": []}


def _chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ======================================================================
# 對外 API
# ======================================================================

def _fetch_orders_uncached(order_nos: list[str]) -> list[dict]:
    all_rows: list[dict] = []
    for batch in _chunked(order_nos, 50):
        resp = wms_safe_get("/api_v1/order/order_query.php", {"order_no": ",".join(batch)})
        rows = resp.get("rows", [])
        rows = decrypt_and_mask(rows)
        all_rows.extend(rows)
    return all_rows


def _fetch_cargo_uncached(order_nos: list[str]) -> list[dict]:
    all_rows: list[dict] = []
    for batch in _chunked(order_nos, 50):
        resp = wms_safe_get("/api_v1/order/order_logistics.php", {"order_no": ",".join(batch)})
        all_rows.extend(resp.get("rows", []))
    return all_rows


def _fetch_inventory_uncached(skus: list[str]) -> list[dict]:
    all_rows: list[dict] = []
    for batch in _chunked(skus, 50):
        resp = wms_safe_get("/api_v1/inventory/stock_query.php", {"sku": ",".join(batch)})
        all_rows.extend(resp.get("rows", []))
    return all_rows


def query_orders(order_nos: list[str]) -> list[dict]:
    """查訂單詳情。AES 加密欄位已遮罩；5 分鐘逐筆快取。"""
    if not order_nos:
        return []
    return cached_orders(order_nos, _fetch_orders_uncached)


def query_cargo_status(order_nos: list[str]) -> list[dict]:
    """
    查貨態 timeline。中途狀態 5 分鐘快取、終態 24 小時快取。
    WMS 文件明示貨態非即時，format 時會加註時間戳提醒。
    """
    if not order_nos:
        return []
    return cached_cargo_status(order_nos, _fetch_cargo_uncached)


def query_inventory(skus: list[str]) -> list[dict]:
    """查庫存（自動分批 50/次 + 2 分鐘逐 SKU 快取）"""
    if not skus:
        return []
    return cached_inventory(skus, _fetch_inventory_uncached)


def query_stores() -> list[dict]:
    return cached_stores(lambda: wms_safe_get("/api_v1/pos/store.php").get("rows", []))


def query_logistics_codes() -> list[dict]:
    return cached_logistics_codes(lambda: wms_safe_get("/api_v1/order/logistics_code.php").get("rows", []))


# ======================================================================
# 自然語言格式化（給 LLM）
# ======================================================================

_STATUS_NAME = {
    "F": "出貨完成",
    "W": "待出貨",
    "P": "處理中",
    "C": "已取消",
}


def format_orders_for_llm(orders: list[dict]) -> str:
    """
    格式化訂單給 LLM。
    WMS API 回傳 products[] 而非 items[]。
    """
    if not orders:
        return "查無此訂單。"
    lines = []
    for o in orders:
        status = o.get("status_name") or _STATUS_NAME.get(o.get("status_code", ""), o.get("status_code", "未知"))
        # WMS API 用 products[]，每個 product 有 name, qty, spec
        products = o.get("products", []) or o.get("items", [])  # 相容舊格式
        items_str = "、".join(
            f"{p.get('name')}{(' ' + p.get('spec')) if p.get('spec') else ''} ×{p.get('qty')}"
            for p in products
        )
        send_num = o.get("send_num") or "尚未產生託運單"
        freight = o.get("freight_name") or o.get("logistics_name", "—")
        lines.append(
            f"訂單 {o.get('order_no')}：{status}，"
            f"商品：{items_str}，物流：{freight}，託運單號：{send_num}"
        )
    return "\n".join(lines)


def _humanize_minutes(time_str: str) -> str:
    """
    將時間字串轉為「X 分鐘前」等人類易讀格式。
    支援 WMS 格式 yyyy/MM/dd HH:mm:ss 和 ISO 8601。
    """
    if not time_str:
        return "（無更新時間）"
    try:
        # 嘗試 WMS 格式：2026/04/24 14:30:00
        try:
            t = datetime.strptime(time_str, "%Y/%m/%d %H:%M:%S")
        except ValueError:
            # fallback ISO 8601
            t = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if t.tzinfo:
                t = t.replace(tzinfo=None)  # 轉 naive 比較
        delta = datetime.now() - t
        mins = int(delta.total_seconds() // 60)
        if mins < 0:
            return "剛才"
        if mins < 1:
            return "剛才"
        if mins < 60:
            return f"{mins} 分鐘前"
        hours = mins // 60
        if hours < 24:
            return f"{hours} 小時前"
        return f"{hours // 24} 天前"
    except Exception:
        return "（時間解析失敗）"


def format_cargo_status_for_llm(timelines: list[dict]) -> str:
    """
    格式化貨態 timeline 給 LLM。
    WMS API 回傳：rows[].{order_no, send_num, timelines[].{time, text}}
    注意：WMS 文件明示「並非所有訂單都有貨態資料，且資料並非即時資料」。
    """
    if not timelines:
        return "目前查無貨態資料。"
    out = []
    for row in timelines:
        order_no = row.get("order_no")
        send_num = row.get("send_num") or "尚未產生託運單"
        events = row.get("timelines", [])
        if not events:
            out.append(f"訂單 {order_no}：{send_num}，目前尚無貨態事件。")
            continue
        latest = events[-1]
        last_time = latest.get("time", "")
        last_updated = _humanize_minutes(last_time)
        out.append(
            f"訂單 {order_no}（{send_num}）最新狀態：{latest.get('text', '未知')}\n"
            f"時間：{last_time}（{last_updated}）\n"
            f"（資料來源 WMS 同步快照，可能與最新實況有 30 分鐘以內差距）"
        )
    return "\n\n".join(out)


def format_inventory_for_llm(items: list[dict]) -> str:
    """
    格式化庫存給 LLM。
    WMS API 回傳：rows[].{sku, item_no, name, spec, stock, safe_stock, occupied_stock, spaces[]}
    """
    if not items:
        return "查無庫存資訊。"
    lines = []
    for i in items:
        spec = f"（{i['spec']}）" if i.get("spec") else ""
        occupied = i.get("occupied_stock", 0)
        available = (i.get("stock", 0) or 0) - (occupied or 0)
        line = (
            f"{i.get('name')}{spec}（{i.get('sku')}）："
            f"總庫存 {i.get('stock', 0)} 件"
        )
        if occupied:
            line += f"（已佔用 {occupied}，可用 {available}）"
        lines.append(line)
    return "\n".join(lines)


def format_stores_for_llm(stores: list[dict]) -> str:
    if not stores:
        return "查無門市資訊。"
    return "\n".join(
        f"【{s.get('name')}】{s.get('address')}　電話：{s.get('phone')}　營業：{s.get('open')}"
        for s in stores
    )
