"""
cs-handoff — 輔睡員值班班表

功能：
  - 依時段判斷各門市是否營業 / 有人值班
  - 路由 handoff 到對應門市（依顧客綁定的門市 / 最近門市）
  - HRM API 整合，支援即時值班表查詢
  - 離線時段 fallback 到 HQ 客服值班 / 隔日排程

班表資料：
  - HRM API 優先（5 分鐘快取）
  - 靜態班表作為備用
  - 營業時段可由環境變數覆蓋，預設 10:00-21:00（週一至週日）
  - 每家門市至少 2 名輔睡員輪值；首席 + 副手
"""
import os
from datetime import datetime, time, timezone, timedelta
from typing import Optional
import logging

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger("lovefu.handoff.roster")

TW_TZ = timezone(timedelta(hours=8))

# ============================================================================
# HRM API Configuration
# ============================================================================

HRM_API_URL = os.getenv("HRM_API_URL", "")
HRM_API_KEY = os.getenv("HRM_API_KEY", "")
HRM_CACHE_TTL = int(os.getenv("HRM_CACHE_TTL", "300"))  # 5 minutes

_hrm_cache: dict[str, tuple[datetime, list]] = {}

# ============================================================
# 班表資料（靜態樣本；未來接 HRM / OM 班表 API）
# ============================================================
STORES = {
    "taichung_7": {
        "name": "台中七期旗艦店",
        "phone": "04-2255-xxxx",
        "open_hour": 10,
        "close_hour": 21,
        "advisors": [
            {"name": "小明", "line_id": "U_advisor_mm", "role": "首席", "active": True},
            {"name": "Amy", "line_id": "U_advisor_amy", "role": "副手", "active": True},
        ],
        "notify_group": os.getenv("LINE_NOTIFY_TAICHUNG", ""),
    },
    "taipei_zhongshan": {
        "name": "台北中山店",
        "phone": "02-2555-xxxx",
        "open_hour": 11,
        "close_hour": 21,
        "advisors": [
            {"name": "Kevin", "line_id": "U_advisor_kevin", "role": "首席", "active": True},
            {"name": "小美", "line_id": "U_advisor_xm", "role": "副手", "active": True},
        ],
        "notify_group": os.getenv("LINE_NOTIFY_TAIPEI", ""),
    },
    "kaohsiung": {
        "name": "高雄左營店",
        "phone": "07-555-xxxx",
        "open_hour": 11,
        "close_hour": 21,
        "advisors": [
            {"name": "Tom", "line_id": "U_advisor_tom", "role": "首席", "active": True},
        ],
        "notify_group": os.getenv("LINE_NOTIFY_KAOHSIUNG", ""),
    },
}

# HQ 遠端客服（門市下班時的總後援）
HQ_DESK = {
    "name": "總部客服中心",
    "advisors": [
        {"name": "JANE", "line_id": "U_hq_jane", "role": "HQ", "active": True},
    ],
    "open_hour": int(os.getenv("HQ_OPEN_HOUR", "9")),
    "close_hour": int(os.getenv("HQ_CLOSE_HOUR", "22")),
    "notify_group": os.getenv("LINE_NOTIFY_HQ", ""),
}


def now_tw() -> datetime:
    return datetime.now(TW_TZ)


def is_store_open(store_id: str, at: Optional[datetime] = None) -> bool:
    at = at or now_tw()
    store = STORES.get(store_id)
    if not store:
        return False
    h = at.hour
    return store["open_hour"] <= h < store["close_hour"]


def is_hq_open(at: Optional[datetime] = None) -> bool:
    at = at or now_tw()
    return HQ_DESK["open_hour"] <= at.hour < HQ_DESK["close_hour"]


# ============================================================================
# HRM API Integration
# ============================================================================

async def fetch_roster_from_hrm(store_id: str) -> list[dict]:
    """從 HRM 系統取得即時值班表。Cache 5 分鐘。"""
    if not HRM_API_URL or not httpx:
        return []

    cache_key = f"roster:{store_id}"
    cached = _hrm_cache.get(cache_key)
    if cached and (datetime.now(TW_TZ) - cached[0]).total_seconds() < HRM_CACHE_TTL:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(
                f"{HRM_API_URL}/api/v1/roster",
                params={
                    "store_id": store_id,
                    "date": datetime.now(TW_TZ).strftime("%Y-%m-%d"),
                },
                headers={"Authorization": f"Bearer {HRM_API_KEY}"},
            )
            if r.status_code == 200:
                advisors = r.json().get("advisors", [])
                _hrm_cache[cache_key] = (datetime.now(TW_TZ), advisors)
                return advisors
    except Exception as e:
        logger.warning(f"HRM roster fetch failed for {store_id}: {e}")

    return []


def get_on_duty_advisors(store_id: str) -> list[dict]:
    """取某門市當前值班的輔睡員（active=True）。優先從 HRM，備用靜態。"""
    store = STORES.get(store_id)
    if not store or not is_store_open(store_id):
        return []

    # Try HRM first (sync wrapper for backward compat)
    if HRM_API_URL:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside async context — can't await, use cached
                cached = _hrm_cache.get(f"roster:{store_id}")
                if cached and (datetime.now(TW_TZ) - cached[0]).total_seconds() < HRM_CACHE_TTL:
                    return cached[1]
            else:
                hrm_advisors = loop.run_until_complete(fetch_roster_from_hrm(store_id))
                if hrm_advisors:
                    return hrm_advisors
        except Exception:
            pass

    # Fallback: static
    return [a for a in store["advisors"] if a.get("active")]


async def get_on_duty_advisors_async(store_id: str) -> list[dict]:
    """取某門市當前值班的輔睡員（async 版本）。優先從 HRM，備用靜態。"""
    store = STORES.get(store_id)
    if not store or not is_store_open(store_id):
        return []

    if HRM_API_URL:
        hrm_advisors = await fetch_roster_from_hrm(store_id)
        if hrm_advisors:
            return hrm_advisors

    return [a for a in store["advisors"] if a.get("active")]


def pick_primary_advisor(store_id: str) -> Optional[dict]:
    """選首席；首席不在選副手。"""
    duty = get_on_duty_advisors(store_id)
    if not duty:
        return None
    chief = next((a for a in duty if a["role"] == "首席"), None)
    return chief or duty[0]


def route_handoff(
    preferred_store_id: Optional[str] = None,
    customer_location: Optional[str] = None,
) -> dict:
    """
    路由 handoff 到合適的值班點。
    回傳 {target_type: "store"|"hq"|"offline", store_id, advisor, notify_group, reason}
    """
    now = now_tw()
    # 1. 有綁定門市 → 優先該店
    if preferred_store_id and is_store_open(preferred_store_id, now):
        advisor = pick_primary_advisor(preferred_store_id)
        if advisor:
            store = STORES[preferred_store_id]
            return {
                "target_type": "store",
                "store_id": preferred_store_id,
                "store_name": store["name"],
                "advisor": advisor,
                "notify_group": store["notify_group"],
                "reason": "preferred_store_open",
            }
    # 2. 其他門市有開 → 找最近的（簡化：第一家開的）
    for sid in STORES:
        if is_store_open(sid, now):
            advisor = pick_primary_advisor(sid)
            if advisor:
                return {
                    "target_type": "store",
                    "store_id": sid,
                    "store_name": STORES[sid]["name"],
                    "advisor": advisor,
                    "notify_group": STORES[sid]["notify_group"],
                    "reason": "nearest_open_store",
                }
    # 3. 門市全關 → HQ 總部值班
    if is_hq_open(now):
        advisor = next((a for a in HQ_DESK["advisors"] if a["active"]), None)
        if advisor:
            return {
                "target_type": "hq",
                "store_id": "hq",
                "store_name": HQ_DESK["name"],
                "advisor": advisor,
                "notify_group": HQ_DESK["notify_group"],
                "reason": "stores_closed_hq_open",
            }
    # 4. 全離線 → 排隔日
    return {
        "target_type": "offline",
        "store_id": None,
        "store_name": None,
        "advisor": None,
        "notify_group": None,
        "reason": "all_closed_schedule_tomorrow",
    }


def next_open_datetime(store_id: Optional[str] = None) -> datetime:
    """回傳下一個營業開始時間（用於離線時告訴顧客何時能接手）。"""
    at = now_tw()
    store = STORES.get(store_id) if store_id else None
    open_h = store["open_hour"] if store else HQ_DESK["open_hour"]
    tomorrow_open = at.replace(hour=open_h, minute=0, second=0, microsecond=0)
    if at.hour >= open_h:
        tomorrow_open += timedelta(days=1)
    return tomorrow_open
