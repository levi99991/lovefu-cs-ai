"""
WMS 暢流 API 認證 — BasicAuth → JWT，含 token 快取與自動續期
"""
import os
import time
import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger("lovefu.cs_logistics.auth")

WMS_BASE_URL = os.getenv("WMS_BASE_URL", "https://lovefu.wms.changliu.com.tw")
# WMS 暢流使用 API_ID + API_KEY 做 HTTP Basic Auth 換 JWT
WMS_API_ID = os.getenv("WMS_API_ID", "")
WMS_API_KEY = os.getenv("WMS_API_KEY", "")
WMS_TOKEN_TTL_SEC = int(os.getenv("WMS_TOKEN_TTL_SEC", "3300"))  # 55 min
WMS_MODE = os.getenv("WMS_MODE", "mock")

# Module-level cache: {token, expire_at}
_token_cache: dict = {"token": None, "expire_at": 0}


def get_token() -> str:
    """
    取得 WMS JWT。若快取仍有效則直接回傳，否則重新打 BasicAuth 換新。
    Mock 模式回傳固定字串，不打外部。
    """
    if WMS_MODE == "mock":
        return "MOCK_JWT_TOKEN"

    now = time.time()
    if _token_cache["token"] and _token_cache["expire_at"] > now:
        return _token_cache["token"]

    if not WMS_API_ID or not WMS_API_KEY:
        raise RuntimeError("WMS_API_ID / WMS_API_KEY 未設定")

    # WMS 文件：Authorization: Basic {base64_encode(API_ID:API_KEY)}
    cred = base64.b64encode(
        f"{WMS_API_ID}:{WMS_API_KEY}".encode("utf-8")
    ).decode("ascii")

    url = f"{WMS_BASE_URL}/api_v1/token/authorize.php"
    try:
        r = httpx.get(
            url,
            headers={"Authorization": f"Basic {cred}"},
            timeout=10.0,
        )
        r.raise_for_status()
        body = r.json()

        # WMS 回應格式：{ "result": {"ok": true}, "data": {"access_token": "..."} }
        result = body.get("result", {})
        if not result.get("ok"):
            raise RuntimeError(f"WMS auth failed: {result.get('message', 'unknown')}")

        data = body.get("data", {})
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"WMS auth response missing access_token: {body}")

        _token_cache["token"] = token
        _token_cache["expire_at"] = now + WMS_TOKEN_TTL_SEC
        logger.info("WMS token refreshed, ttl=%ss", WMS_TOKEN_TTL_SEC)
        return token

    except httpx.HTTPError as e:
        logger.error("WMS auth failed: %s", e)
        raise


def clear_token_cache() -> None:
    """強制清除 token 快取（測試 / 換 key 時用）"""
    _token_cache["token"] = None
    _token_cache["expire_at"] = 0
