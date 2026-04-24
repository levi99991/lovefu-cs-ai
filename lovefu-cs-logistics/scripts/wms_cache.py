"""
WMS 查詢快取 — 避免頻繁打 WMS API
- 貨態（order_logistics）：中途狀態 5 分鐘、終態（F=出貨完成）24 小時
- 訂單（order_query）：5 分鐘
- 庫存（stock_query）：2 分鐘（庫存變動頻繁，不宜久）
- 門市（pos/store）：24 小時
- 物流商代碼：24 小時

Backend：
- 預設 in-memory dict（每個 worker 獨立；Railway 單 worker 足夠）
- 若 CACHE_BACKEND=redis 則走 Redis（多 worker 共用）
"""
import os
import time
import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("lovefu.cs_logistics.cache")

CACHE_BACKEND = os.getenv("CACHE_BACKEND", "dict").lower()

# TTL (秒)
TTL_CARGO_TRANSIT = int(os.getenv("WMS_CACHE_CARGO_TTL", "300"))       # 5 min
TTL_CARGO_DONE = int(os.getenv("WMS_CACHE_CARGO_DONE_TTL", "86400"))   # 24 hr
TTL_ORDER = int(os.getenv("WMS_CACHE_ORDER_TTL", "300"))
TTL_INVENTORY = int(os.getenv("WMS_CACHE_INV_TTL", "120"))
TTL_STORES = int(os.getenv("WMS_CACHE_STORES_TTL", "86400"))
TTL_CODES = int(os.getenv("WMS_CACHE_CODES_TTL", "86400"))

_dict_cache: dict = {}  # key → (expire_ts, value)
_redis = None


def _get_redis():
    global _redis
    if _redis is None and CACHE_BACKEND == "redis":
        import redis
        _redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
    return _redis


def _cache_get(key: str) -> Optional[Any]:
    if CACHE_BACKEND == "redis":
        r = _get_redis()
        raw = r.get(f"wmscache:{key}")
        return json.loads(raw) if raw else None

    entry = _dict_cache.get(key)
    if not entry:
        return None
    expire_ts, value = entry
    if time.time() > expire_ts:
        _dict_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int) -> None:
    if CACHE_BACKEND == "redis":
        r = _get_redis()
        r.setex(f"wmscache:{key}", ttl, json.dumps(value, ensure_ascii=False, default=str))
        return
    _dict_cache[key] = (time.time() + ttl, value)


def cached_cargo_status(order_nos: list[str], fetcher: Callable[[list[str]], list[dict]]) -> list[dict]:
    """
    對貨態做逐筆快取。終態（status_code F 或 timelines 含「送達/完成」）快取 24 小時，其餘 5 分鐘。
    - order_nos：要查的訂單號
    - fetcher：當快取 miss 時對 WMS 發實際查詢的函式
    """
    cached: dict[str, dict] = {}
    missing: list[str] = []
    for no in order_nos:
        v = _cache_get(f"cargo:{no}")
        if v is not None:
            cached[no] = v
        else:
            missing.append(no)

    if missing:
        fresh = fetcher(missing)
        for row in fresh:
            no = row.get("order_no")
            if not no:
                continue
            # 判斷是否終態
            is_done = False
            events = row.get("timelines") or []
            if events:
                last_status = str(events[-1].get("status", ""))
                if any(kw in last_status for kw in ["送達", "完成", "取件", "簽收"]):
                    is_done = True
            ttl = TTL_CARGO_DONE if is_done else TTL_CARGO_TRANSIT
            _cache_set(f"cargo:{no}", row, ttl)
            cached[no] = row

    # 保留原順序
    return [cached[no] for no in order_nos if no in cached]


def cached_orders(order_nos: list[str], fetcher: Callable[[list[str]], list[dict]]) -> list[dict]:
    cached: dict[str, dict] = {}
    missing: list[str] = []
    for no in order_nos:
        v = _cache_get(f"order:{no}")
        if v is not None:
            cached[no] = v
        else:
            missing.append(no)
    if missing:
        for row in fetcher(missing):
            no = row.get("order_no")
            if no:
                _cache_set(f"order:{no}", row, TTL_ORDER)
                cached[no] = row
    return [cached[no] for no in order_nos if no in cached]


def cached_inventory(skus: list[str], fetcher: Callable[[list[str]], list[dict]]) -> list[dict]:
    cached: dict[str, dict] = {}
    missing: list[str] = []
    for s in skus:
        v = _cache_get(f"inv:{s}")
        if v is not None:
            cached[s] = v
        else:
            missing.append(s)
    if missing:
        for row in fetcher(missing):
            sku = row.get("sku")
            if sku:
                _cache_set(f"inv:{sku}", row, TTL_INVENTORY)
                cached[sku] = row
    return [cached[s] for s in skus if s in cached]


def cached_stores(fetcher: Callable[[], list[dict]]) -> list[dict]:
    v = _cache_get("stores:all")
    if v is not None:
        return v
    fresh = fetcher()
    _cache_set("stores:all", fresh, TTL_STORES)
    return fresh


def cached_logistics_codes(fetcher: Callable[[], list[dict]]) -> list[dict]:
    v = _cache_get("codes:all")
    if v is not None:
        return v
    fresh = fetcher()
    _cache_set("codes:all", fresh, TTL_CODES)
    return fresh


def clear_all() -> None:
    """測試 / 強制更新用"""
    if CACHE_BACKEND == "redis":
        r = _get_redis()
        for k in r.scan_iter("wmscache:*"):
            r.delete(k)
    _dict_cache.clear()


def cache_stats() -> dict:
    return {
        "backend": CACHE_BACKEND,
        "dict_size": len(_dict_cache),
    }
