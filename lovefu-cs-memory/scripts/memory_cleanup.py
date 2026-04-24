"""
大島樂眠 AI 輔睡員 — 記憶過期清理
lovefu-cs-memory/scripts/memory_cleanup.py

用途：
  - Dict 模式：定期掃描並刪除過期記憶
  - Redis 模式：Redis TTL 自動處理，此腳本僅用於統計

可透過 Make.com 排程每日執行一次，
或在後端啟動時跑一次清理。
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("lovefu.memory.cleanup")

TW_TZ = timezone(timedelta(hours=8))
MEMORY_TTL_DAYS = int(os.getenv("MEMORY_TTL_DAYS", "7"))
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "dict")


def cleanup_dict_store(store: dict) -> dict:
    """
    掃描 dict 記憶，刪除超過 TTL 天數未活動的紀錄。
    回傳清理統計。
    """
    now = datetime.now(TW_TZ)
    cutoff = now - timedelta(days=MEMORY_TTL_DAYS)
    expired_keys = []

    for uid, memory in store.items():
        last_active_str = memory.get("last_active", "")
        if not last_active_str:
            expired_keys.append(uid)
            continue

        try:
            last_active = datetime.fromisoformat(last_active_str)
            if last_active < cutoff:
                expired_keys.append(uid)
        except (ValueError, TypeError):
            expired_keys.append(uid)

    for uid in expired_keys:
        del store[uid]

    stats = {
        "scanned": len(store) + len(expired_keys),
        "expired": len(expired_keys),
        "remaining": len(store),
        "cutoff_date": cutoff.isoformat(),
    }

    logger.info(f"Memory cleanup: {json.dumps(stats)}")
    return stats


def get_redis_stats() -> dict:
    """
    Redis 模式下，統計目前記憶數量。
    過期由 Redis TTL 自動處理，此函式僅用於監控。
    """
    import redis

    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        password=os.getenv("REDIS_PASSWORD", None),
        decode_responses=True,
    )

    keys = r.keys("memory:*")
    stats = {
        "total_memories": len(keys),
        "backend": "redis",
        "ttl_days": MEMORY_TTL_DAYS,
    }

    # 抽樣檢查 TTL
    if keys:
        sample_key = keys[0]
        ttl = r.ttl(sample_key)
        stats["sample_ttl_seconds"] = ttl

    logger.info(f"Redis memory stats: {json.dumps(stats)}")
    return stats
