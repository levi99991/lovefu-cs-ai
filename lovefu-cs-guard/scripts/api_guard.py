"""
大島樂眠 AI 輔睡員 — API 安全閘門
lovefu-cs-guard/scripts/api_guard.py

所有外部 API 呼叫必須經過此模組。
只允許 GET 請求 + 白名單內的 endpoint。
"""

import os
import re
import logging
from typing import Optional
import httpx

from .audit_logger import log_api_call

logger = logging.getLogger("lovefu.guard")

# ============================================================
# 設定
# ============================================================

SHOPLINE_STORE_HANDLE = os.getenv("SHOPLINE_STORE_HANDLE", "")
SHOPLINE_ACCESS_TOKEN = os.getenv("SHOPLINE_ACCESS_TOKEN", "")
SHOPLINE_API_VERSION = os.getenv("SHOPLINE_API_VERSION", "v20260301")
SHOPLINE_BASE = f"https://{SHOPLINE_STORE_HANDLE}.myshopline.com/admin/openapi/{SHOPLINE_API_VERSION}"

LOGISTICS_API_BASE = os.getenv("LOGISTICS_API_BASE", "")
LOGISTICS_API_KEY = os.getenv("LOGISTICS_API_KEY", "")

# WMS 暢流物流（lovefu.wms.changliu.com.tw）
WMS_BASE_URL = os.getenv("WMS_BASE_URL", "https://lovefu.wms.changliu.com.tw")
WMS_MODE = os.getenv("WMS_MODE", "mock").lower()

# SHOPLINE_MODE：mock | production
#   mock       → 不打真實 API，回傳 cs-shopline/scripts/mock_data.py 的假資料
#   production → 走完整的 httpx 請求（需 SHOPLINE_ACCESS_TOKEN）
# 預設 mock 是為了讓沒有 Token 的開發者也能跑通整套系統。
SHOPLINE_MODE = os.getenv("SHOPLINE_MODE", "mock").lower()

# ============================================================
# 白名單
# ============================================================

ALLOWED_SHOPLINE_PATHS = [
    # 訂單
    "/orders.json",
    "/orders/{id}.json",
    "/orders/{id}/transactions.json",
    # 出貨
    "/fulfillment_orders/fulfillment_orders_search.json",
    "/fulfillment_orders/{id}/fulfillment_orders.json",
    # 會員
    "/customers.json",
    "/customers/{id}.json",
    "/customers/search.json",
    # 商品
    "/products.json",
    "/products/{id}.json",
    "/products/count.json",
    # 配送
    "/pickup/list.json",
    # 庫存
    "/inventory_levels.json",
]

ALLOWED_LOGISTICS_PATHS = [
    "/tracking/{number}",
    "/shipments/{id}",
]

# WMS 暢流 — 嚴格白名單（只允許唯讀 GET 端點）
ALLOWED_WMS_PATHS = [
    "/api_v1/order/order_query.php",
    "/api_v1/order/order_logistics.php",
    "/api_v1/order/logistics_code.php",
    "/api_v1/order/count.php",
    "/api_v1/inventory/stock_query.php",
    "/api_v1/inventory/stockin_record.php",
    "/api_v1/pos/store.php",
    "/api_v1/product/pro_query.php",
    "/api_v1/product/pro_detail.php",
    "/api_v1/product/count.php",
    "/api_v1/product/category.php",
    "/api_v1/product/bom.php",
    "/api_v1/inbound/query.php",
]

# WMS 明確黑名單 — 即使有人改錯 method 也會被攔（全是 POST 寫入操作）
WMS_BLACKLIST_PATHS = [
    "/api_v1/order/cancel.php",
    "/api_v1/order/order_pick.php",
    "/api_v1/order/order_add.php",
    "/api_v1/logistics/confirm.php",
    "/api_v1/logistics/request.php",
    "/api_v1/logistics/label.php",
    "/api_v1/logistics/complete.php",
    "/api_v1/logistics/callback.php",
    "/api_v1/inventory/stock_update.php",
    "/api_v1/inbound/add.php",
    "/api_v1/inbound/cancel.php",
    "/api_v1/product/pro_add.php",
    "/api_v1/pos/promotion.php",
]

# 黑名單關鍵字（即使在白名單路徑下也攔截）
BLOCKED_KEYWORDS = [
    "cancel", "refund", "delete", "update", "create",
    "close", "archive", "activate", "reopen",
]


# ============================================================
# 路徑匹配
# ============================================================

def _path_matches_pattern(path: str, pattern: str) -> bool:
    """檢查實際路徑是否匹配白名單 pattern（支援 {id} 萬用字元）"""
    regex = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
    return bool(re.fullmatch(regex, path))


def _is_path_allowed(path: str, allowed_list: list[str]) -> bool:
    """檢查路徑是否在白名單中"""
    return any(_path_matches_pattern(path, p) for p in allowed_list)


def _contains_blocked_keyword(path: str) -> bool:
    """檢查路徑是否包含黑名單關鍵字"""
    path_lower = path.lower()
    return any(kw in path_lower for kw in BLOCKED_KEYWORDS)


# ============================================================
# 個資遮蔽
# ============================================================

def mask_pii(data: dict) -> dict:
    """
    遞迴遍歷 dict，遮蔽個資欄位。
    在 API 回應傳給 LLM 之前呼叫。
    """
    if not isinstance(data, dict):
        return data

    masked = {}
    for key, value in data.items():
        if isinstance(value, dict):
            masked[key] = mask_pii(value)
        elif isinstance(value, list):
            masked[key] = [mask_pii(item) if isinstance(item, dict) else _mask_value(key, item) for item in value]
        else:
            masked[key] = _mask_value(key, value)
    return masked


def _mask_value(key: str, value) -> str:
    """根據欄位名稱判斷是否需要遮蔽"""
    if not isinstance(value, str):
        return value

    key_lower = key.lower()

    # 手機號碼
    if key_lower in ("phone", "mobile", "tel", "telephone", "contact_phone"):
        return _mask_phone(value)

    # Email
    if key_lower in ("email", "contact_email", "buyer_email"):
        return _mask_email(value)

    # 地址
    if key_lower in ("address", "address1", "address2", "shipping_address", "full_address"):
        return _mask_address(value)

    # 信用卡
    if key_lower in ("card_number", "credit_card", "pan"):
        return "************"

    # 身分證
    if key_lower in ("id_number", "national_id", "identity"):
        return "**********"

    return value


def _mask_phone(phone: str) -> str:
    """手機：只顯示後 4 碼"""
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) >= 8:
        return "****" + digits[-4:]
    return "****"


def _mask_email(email: str) -> str:
    """Email：前 3 碼 + *** + @domain"""
    if "@" in email:
        local, domain = email.split("@", 1)
        visible = local[:3] if len(local) >= 3 else local
        return f"{visible}***@{domain}"
    return "***"


def _mask_address(address: str) -> str:
    """地址：只保留到路/街名"""
    for delimiter in ["路", "街"]:
        idx = address.find(delimiter)
        if idx != -1:
            return address[: idx + 1]
    # 找不到路/街就保留前 6 個字
    return address[:6] + "..."


# ============================================================
# 安全 GET 請求
# ============================================================

async def shopline_safe_get(
    path: str,
    params: Optional[dict] = None,
    caller: str = "unknown",
    line_uid: str = "",
) -> Optional[dict]:
    """
    對 Shopline API 發送安全的 GET 請求。

    - 只允許 GET
    - 只允許白名單內的 path
    - 回應自動遮蔽個資
    - 每次呼叫記錄審計日誌
    """

    # 檢查黑名單關鍵字
    if _contains_blocked_keyword(path):
        log_api_call(
            method="GET", endpoint=path, status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="blocked_keyword",
        )
        logger.warning(f"BLOCKED: path contains blocked keyword: {path}")
        return None

    # 檢查白名單
    if not _is_path_allowed(path, ALLOWED_SHOPLINE_PATHS):
        log_api_call(
            method="GET", endpoint=path, status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="not_in_whitelist",
        )
        logger.warning(f"BLOCKED: path not in whitelist: {path}")
        return None

    # ── Mock 模式短路：不打真實 API，直接回傳假資料 ──
    if SHOPLINE_MODE == "mock":
        from lovefu_cs_shopline.scripts.mock_data import get_mock_response
        mock = get_mock_response(path, params)
        log_api_call(
            method="GET", endpoint=path, status_code=200 if mock else 404,
            caller=caller, line_uid=line_uid,
            blocked=False, response_size=len(str(mock or "")),
            block_reason="mock_mode",
        )
        logger.info(f"MOCK: {path} → {'hit' if mock else 'miss'}")
        return mask_pii(mock) if mock else None

    # ── Production 模式：發送真實 GET 請求 ──
    if not SHOPLINE_ACCESS_TOKEN:
        logger.error(
            "SHOPLINE_MODE=production 但 SHOPLINE_ACCESS_TOKEN 未設定。"
            "請設定環境變數，或切回 SHOPLINE_MODE=mock。"
        )
        return None

    url = f"{SHOPLINE_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {SHOPLINE_ACCESS_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params, timeout=15.0)

            log_api_call(
                method="GET", endpoint=path, status_code=resp.status_code,
                caller=caller, line_uid=line_uid,
                blocked=False, response_size=len(resp.content),
            )

            if resp.status_code == 200:
                data = resp.json()
                return mask_pii(data)
            else:
                logger.warning(f"Shopline API error: {resp.status_code} for {path}")
                return None

    except httpx.TimeoutException:
        log_api_call(
            method="GET", endpoint=path, status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=False, block_reason="timeout",
        )
        logger.error(f"Timeout: {path}")
        return None

    except Exception as e:
        log_api_call(
            method="GET", endpoint=path, status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=False, block_reason=f"exception: {str(e)}",
        )
        logger.error(f"Error: {path}: {e}")
        return None


async def logistics_safe_get(
    path: str,
    params: Optional[dict] = None,
    caller: str = "unknown",
    line_uid: str = "",
) -> Optional[dict]:
    """
    對物流倉儲 API 發送安全的 GET 請求。
    同樣的三道防線。
    """
    if not LOGISTICS_API_BASE:
        return None

    if _contains_blocked_keyword(path):
        log_api_call(
            method="GET", endpoint=f"logistics:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="blocked_keyword",
        )
        return None

    if not _is_path_allowed(path, ALLOWED_LOGISTICS_PATHS):
        log_api_call(
            method="GET", endpoint=f"logistics:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="not_in_whitelist",
        )
        return None

    url = f"{LOGISTICS_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {LOGISTICS_API_KEY}"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params, timeout=15.0)

            log_api_call(
                method="GET", endpoint=f"logistics:{path}", status_code=resp.status_code,
                caller=caller, line_uid=line_uid,
                blocked=False, response_size=len(resp.content),
            )

            if resp.status_code == 200:
                return mask_pii(resp.json())
            return None

    except Exception as e:
        logger.error(f"Logistics error: {path}: {e}")
        return None


# ============================================================
# WMS 暢流 — 嚴格唯讀 GET（同步函式，配合 cs-logistics）
# ============================================================

def _is_wms_blacklisted(path: str) -> bool:
    """命中 WMS 明確黑名單即拒絕"""
    p = path.lower()
    return any(b in p for b in WMS_BLACKLIST_PATHS)


def wms_safe_get(
    path: str,
    params: Optional[dict] = None,
    caller: str = "cs-logistics",
    line_uid: str = "",
) -> Optional[dict]:
    """
    WMS API 安全 GET。
    - 雙保險：黑名單命中即拒；不在白名單也拒
    - 自動 token 取得（mock 模式跳過）
    - 回應自動經過 PII 解密+遮罩（cs-logistics.pii_decrypt）
    - 沒有對應的 wms_safe_post，從根上排除寫入
    """
    # 1) 黑名單最高優先
    if _is_wms_blacklisted(path):
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="wms_blacklist",
        )
        logger.warning("WMS BLOCKED (blacklist): %s", path)
        return None

    # 2) 白名單檢查
    if not _is_path_allowed(path, ALLOWED_WMS_PATHS):
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="wms_not_in_whitelist",
        )
        logger.warning("WMS BLOCKED (not in whitelist): %s", path)
        return None

    # 3) 黑名單關鍵字檢查（cancel/refund/update...）
    if _contains_blocked_keyword(path):
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=True, block_reason="blocked_keyword",
        )
        return None

    # 4) Mock 模式 — 路由到 mock_wms_data
    if WMS_MODE == "mock":
        try:
            from lovefu_cs_logistics.scripts.wms_client import _mock_dispatch
            mock = _mock_dispatch(path, params or {})
        except ImportError:
            mock = {"rows": []}
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=200,
            caller=caller, line_uid=line_uid,
            blocked=False, block_reason="wms_mock_mode",
            response_size=len(str(mock or "")),
        )
        # mock 資料已是遮罩好的；仍跑一次 mask_pii 保險
        return mask_pii(mock) if isinstance(mock, dict) else mock

    # 5) Production 模式 — 取 token + httpx GET
    try:
        from lovefu_cs_logistics.scripts.wms_auth import get_token
        from lovefu_cs_logistics.scripts.pii_decrypt import decrypt_and_mask
    except ImportError as e:
        logger.error("WMS production 模式但 cs-logistics 未安裝: %s", e)
        return None

    try:
        token = get_token()
    except Exception as e:
        logger.error("WMS auth 失敗: %s", e)
        return None

    url = f"{WMS_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client() as client:
            resp = client.get(url, headers=headers, params=params, timeout=15.0)
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=resp.status_code,
            caller=caller, line_uid=line_uid,
            blocked=False, response_size=len(resp.content),
        )
        if resp.status_code != 200:
            logger.warning("WMS API error %s for %s", resp.status_code, path)
            return None

        body = resp.json()

        # WMS 回應格式：{ "result": {"ok": bool, "message": str}, "data": {...} }
        result_info = body.get("result", {})
        if not result_info.get("ok"):
            logger.warning("WMS API result.ok=false for %s: %s", path, result_info.get("message"))
            return None

        data = body.get("data")
        if data is None:
            logger.warning("WMS API data=null for %s", path)
            return None

        # 先解密 AES PII → 立即遮罩 → 再走通用 mask_pii
        data = decrypt_and_mask(data)
        return mask_pii(data) if isinstance(data, dict) else data

    except Exception as e:
        log_api_call(
            method="GET", endpoint=f"wms:{path}", status_code=0,
            caller=caller, line_uid=line_uid,
            blocked=False, block_reason=f"exception: {e}",
        )
        logger.error("WMS error: %s: %s", path, e)
        return None


# ============================================================
# 注意：此模組不存在 POST / PUT / DELETE / PATCH 方法
# 這是刻意的設計。如果有人嘗試 import safe_post，會直接報錯。
# 同樣不存在 wms_safe_post — WMS 寫入永久不支援。
# ============================================================
