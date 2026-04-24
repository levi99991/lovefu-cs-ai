"""
大島樂眠 AI 輔睡員 — Omnichat 共存模式
lovefu-cs-brain/scripts/omnichat_coexist.py

設計目標：AI 預設先回，真人可隨時插手接管。

運作邏輯：
  - 每位顧客有一個 "mute_until" 時間戳。
  - Make.com 在每次呼叫 /chat 時，會帶上 omnichat_event 欄位：
      "agent_replied"  → 真人輔睡員剛剛回了訊息（在 Omnichat 後台手動回覆）
      "agent_takeover" → 真人主動標記接管（按下 Omnichat 的 "接手" 按鈕）
      "agent_release"  → 真人標記 "交回給 AI"
      None / "user_message" → 顧客一般訊息
  - 收到 "agent_replied" / "agent_takeover" → 將該 line_uid 設為 mute 30 分鐘（agent_replied）
    或 24 小時（agent_takeover）
  - 收到任何訊息時，先檢查是否在 mute 期間：
      是 → 直接回 silent=True，brain 不呼叫 LLM、不回應
      否 → 正常進入 6 步流程
  - 收到 "agent_release" → 立即解除 mute

對外介面：
  - check_should_mute(line_uid, omnichat_event) → (should_mute, mute_reason)
  - is_currently_muted(line_uid) → bool
  - clear_mute(line_uid) → None

儲存後端與 cs-memory 共用（MEMORY_BACKEND=dict 或 redis）。
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("lovefu.brain.omnichat")

TW_TZ = timezone(timedelta(hours=8))

# Mute 持續時間（可由環境變數覆蓋）
MUTE_AFTER_AGENT_REPLY = timedelta(minutes=int(os.getenv("MUTE_AFTER_AGENT_REPLY_MIN", "30")))
MUTE_AFTER_TAKEOVER = timedelta(hours=int(os.getenv("MUTE_AFTER_TAKEOVER_HOURS", "24")))

MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "dict")

# ============================================================
# 儲存層（與 memory_store 共用後端，但 key namespace 獨立）
# ============================================================
_dict_mute_store: dict[str, str] = {}  # line_uid → ISO timestamp


def _redis():
    """Lazy import 避免不必要的依賴載入。"""
    from lovefu_cs_memory.scripts.memory_store import _get_redis
    return _get_redis()


def _mute_key(line_uid: str) -> str:
    return f"lovefu:mute:{line_uid}"


def _set_mute_until(line_uid: str, until: datetime) -> None:
    iso = until.isoformat()
    if MEMORY_BACKEND == "redis":
        ttl_sec = max(int((until - datetime.now(TW_TZ)).total_seconds()), 1)
        _redis().setex(_mute_key(line_uid), ttl_sec, iso)
    else:
        _dict_mute_store[line_uid] = iso


def _get_mute_until(line_uid: str) -> Optional[datetime]:
    if MEMORY_BACKEND == "redis":
        val = _redis().get(_mute_key(line_uid))
    else:
        val = _dict_mute_store.get(line_uid)

    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _del_mute(line_uid: str) -> None:
    if MEMORY_BACKEND == "redis":
        _redis().delete(_mute_key(line_uid))
    else:
        _dict_mute_store.pop(line_uid, None)


# ============================================================
# 對外介面
# ============================================================

def is_currently_muted(line_uid: str) -> bool:
    """檢查此 line_uid 是否處於 mute 狀態（真人接管中）。"""
    until = _get_mute_until(line_uid)
    if not until:
        return False
    if datetime.now(TW_TZ) >= until:
        # 已過期，順手清掉
        _del_mute(line_uid)
        return False
    return True


def get_mute_remaining(line_uid: str) -> Optional[timedelta]:
    """取得剩餘的 mute 時間（給日誌/監控用）。"""
    until = _get_mute_until(line_uid)
    if not until:
        return None
    remaining = until - datetime.now(TW_TZ)
    return remaining if remaining.total_seconds() > 0 else None


def clear_mute(line_uid: str) -> None:
    """強制解除 mute（agent 按下 "交回給 AI"）。"""
    _del_mute(line_uid)
    logger.info(f"Mute cleared for {line_uid}")


def mark_agent_takeover(line_uid: str) -> None:
    """輔睡員接手 handoff → 立即 mute 24 小時。供 cs-handoff 調用。"""
    until = datetime.now(TW_TZ) + MUTE_AFTER_TAKEOVER
    _set_mute_until(line_uid, until)
    logger.info(f"Agent takeover for {line_uid} until {until.isoformat()}")


def check_should_mute(
    line_uid: str,
    omnichat_event: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    在每次 /chat 呼叫的入口處呼叫一次。

    回傳：
      (should_mute, reason)
        should_mute=True  → brain 應該短路，不呼叫 LLM、回覆 silent
        should_mute=False → 正常處理流程

    同時根據 event 更新 mute 狀態：
      "agent_replied"  → 設定 30 分鐘 mute
      "agent_takeover" → 設定 24 小時 mute
      "agent_release"  → 立即解除 mute
    """
    now = datetime.now(TW_TZ)

    # ── 1. 處理 event 副作用 ──
    if omnichat_event == "agent_replied":
        until = now + MUTE_AFTER_AGENT_REPLY
        _set_mute_until(line_uid, until)
        logger.info(f"agent_replied → mute {line_uid} until {until.isoformat()}")
        return True, "agent_just_replied"

    if omnichat_event == "agent_takeover":
        until = now + MUTE_AFTER_TAKEOVER
        _set_mute_until(line_uid, until)
        logger.info(f"agent_takeover → mute {line_uid} until {until.isoformat()}")
        return True, "agent_takeover"

    if omnichat_event == "agent_release":
        clear_mute(line_uid)
        return False, None

    # ── 2. 一般訊息 → 檢查當前是否在 mute 期間 ──
    if is_currently_muted(line_uid):
        remaining = get_mute_remaining(line_uid)
        logger.info(
            f"Mute active for {line_uid}, "
            f"remaining {remaining.total_seconds() / 60:.1f} min"
        )
        return True, "still_in_mute_window"

    return False, None
