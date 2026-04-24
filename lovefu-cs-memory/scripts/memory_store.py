"""
大島樂眠 AI 輔睡員 — 對話記憶存取
lovefu-cs-memory/scripts/memory_store.py

支援兩種模式：
  - dict 模式（MVP，程式重啟消失）
  - Redis 模式（生產環境，持久化）

透過環境變數 MEMORY_BACKEND 切換：
  MEMORY_BACKEND=dict（預設）
  MEMORY_BACKEND=redis
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from copy import deepcopy

logger = logging.getLogger("lovefu.memory")

TW_TZ = timezone(timedelta(hours=8))
MAX_TURNS = 10           # 保留最近 10 輪（20 條訊息）
COMPRESS_THRESHOLD = 10  # 超過 10 輪觸發壓縮
MEMORY_TTL_DAYS = 7      # 7 天無互動自動過期

MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "dict")

# ============================================================
# Dict 模式（MVP）
# ============================================================
_dict_store: dict[str, dict] = {}

# ============================================================
# Redis 模式（生產環境）
# ============================================================
_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD", None),
            decode_responses=True,
        )
    return _redis_client


# ============================================================
# 空記憶模板
# ============================================================
def _create_empty(line_uid: str) -> dict:
    now = datetime.now(TW_TZ).isoformat()
    return {
        "line_uid": line_uid,
        "profile": {
            "member_name": None,
            "member_id": None,
            "member_tier": None,
            "preferences": [],
            "last_intent": None,
            "satisfaction_count": 0,
            "dissatisfaction_count": 0,
            # 跨渠道狀態（門市試躺 ↔ LINE 對話 ↔ Shopline 訂單 ↔ WMS 出貨）
            "customer_journey": {
                "stage": None,              # 試躺中 / 待追蹤 / 已下單 / 已交付 / 沉睡客
                "store_lead_id": None,      # 門市生成 lead id（綁定線下↔線上）
                "store_advisor": None,      # 負責輔睡員姓名
                "store_name": None,         # 試躺門市
                "tried_products": [],       # 試躺商品 list
                "family_context": None,     # 家庭結構（單身/夫妻/育兒...）
                "budget_range": None,       # 預算區間
                "decision_timeline": None,  # 預計購買時程
                "follow_up_at": None,       # 下次追客時間
                "follow_up_count": 0,       # 已發送追客次數（限 14 天 5 則）
                "do_not_contact": False,    # 顧客明示拒絕主動訊息
                "consent_marketing": False, # 行銷訊息同意
                "last_order_no": None,      # 關聯 Shopline / WMS 訂單號
            },
        },
        "turns": [],
        "summary": "",
        "last_active": now,
        "created_at": now,
        "turn_count_total": 0,
    }


# ============================================================
# 讀取記憶
# ============================================================
def load_memory(line_uid: str) -> dict:
    """
    讀取顧客的完整記憶。
    如果沒有紀錄，回傳空的初始結構。
    """
    if MEMORY_BACKEND == "redis":
        return _load_redis(line_uid)
    return _load_dict(line_uid)


def _load_dict(line_uid: str) -> dict:
    if line_uid in _dict_store:
        return deepcopy(_dict_store[line_uid])
    return _create_empty(line_uid)


def _load_redis(line_uid: str) -> dict:
    r = _get_redis()
    key = f"memory:{line_uid}"
    data = r.get(key)
    if data:
        return json.loads(data)
    return _create_empty(line_uid)


# ============================================================
# 儲存一輪對話
# ============================================================
def save_turn(line_uid: str, role: str, content: str) -> dict:
    """
    新增一條對話記錄（user 或 assistant）。
    回傳更新後的記憶。
    """
    memory = load_memory(line_uid)
    now = datetime.now(TW_TZ).isoformat()

    memory["turns"].append({
        "role": role,
        "content": content,
        "ts": now,
    })
    memory["last_active"] = now
    memory["turn_count_total"] += 1

    # 檢查是否需要壓縮
    turn_pairs = len(memory["turns"]) // 2
    if turn_pairs > COMPRESS_THRESHOLD:
        memory = _compress_turns(memory)

    _save(line_uid, memory)
    return memory


# ============================================================
# 更新 profile
# ============================================================
def update_profile(
    line_uid: str,
    member_name: Optional[str] = None,
    member_id: Optional[str] = None,
    member_tier: Optional[str] = None,
    intent: Optional[str] = None,
    preferences: Optional[list[str]] = None,
    dissatisfied: Optional[bool] = None,
    customer_journey: Optional[dict] = None,
) -> dict:
    """
    更新顧客的 profile 資訊。
    只更新有提供值的欄位。
    customer_journey: 部分更新（merge），例如 {"stage": "已下單", "last_order_no": "L20260415001"}
    """
    memory = load_memory(line_uid)
    p = memory["profile"]

    if member_name is not None:
        p["member_name"] = member_name
    if member_id is not None:
        p["member_id"] = member_id
    if member_tier is not None:
        p["member_tier"] = member_tier
    if intent is not None:
        p["last_intent"] = intent
    if preferences is not None:
        # 合併新偏好，去重
        existing = set(p.get("preferences", []))
        existing.update(preferences)
        p["preferences"] = list(existing)

    # 不滿意計數器
    if dissatisfied is True:
        p["dissatisfaction_count"] = p.get("dissatisfaction_count", 0) + 1
    elif dissatisfied is False:
        p["dissatisfaction_count"] = 0

    # customer_journey 部分更新（merge 而非覆蓋）
    if customer_journey is not None:
        cj = p.setdefault("customer_journey", {})
        for k, v in customer_journey.items():
            if k == "tried_products" and isinstance(v, list):
                existing_tp = set(cj.get("tried_products", []))
                existing_tp.update(v)
                cj["tried_products"] = list(existing_tp)
            else:
                cj[k] = v

    memory["last_active"] = datetime.now(TW_TZ).isoformat()
    _save(line_uid, memory)
    return memory


# ============================================================
# 取得 Prompt 用的記憶片段
# ============================================================
def get_memory_for_prompt(line_uid: str) -> dict:
    """
    回傳組裝 Prompt 需要的三個部分：
    - profile_text: 顧客資訊（自然語言）
    - summary_text: 歷史摘要
    - recent_turns: 最近對話（message list 格式）
    """
    memory = load_memory(line_uid)
    p = memory["profile"]

    # Profile 文字
    profile_parts = []
    if p.get("member_name"):
        profile_parts.append(f"姓名：{p['member_name']}")
    if p.get("member_tier"):
        profile_parts.append(f"海獺等級：{p['member_tier']}")
    if p.get("preferences"):
        profile_parts.append(f"偏好：{'、'.join(p['preferences'])}")

    # 跨渠道狀態 — 讓 LLM 知道顧客是否來自門市試躺、目前 stage、追客次數
    cj = p.get("customer_journey") or {}
    if cj.get("stage"):
        profile_parts.append(f"跨渠道狀態：{cj['stage']}")
    if cj.get("store_name"):
        profile_parts.append(f"試躺門市：{cj['store_name']}（負責輔睡員：{cj.get('store_advisor', '—')}）")
    if cj.get("tried_products"):
        profile_parts.append(f"曾試躺：{'、'.join(cj['tried_products'])}")
    if cj.get("family_context"):
        profile_parts.append(f"家庭：{cj['family_context']}")
    if cj.get("budget_range"):
        profile_parts.append(f"預算：{cj['budget_range']}")
    if cj.get("do_not_contact"):
        profile_parts.append("⚠️ 顧客已表明不接受主動推播，僅被動回覆")

    profile_text = "\n".join(profile_parts) if profile_parts else ""

    # Summary
    summary_text = memory.get("summary", "")

    # 最近對話（轉成 LLM message 格式）
    recent_turns = [
        {"role": t["role"], "content": t["content"]}
        for t in memory["turns"]
    ]

    return {
        "profile_text": profile_text,
        "summary_text": summary_text,
        "recent_turns": recent_turns,
        "dissatisfaction_count": p.get("dissatisfaction_count", 0),
        "customer_journey": cj,
    }


# ============================================================
# 刪除記憶（隱私保護）
# ============================================================
def delete_memory(line_uid: str):
    """顧客要求刪除資料時呼叫。"""
    if MEMORY_BACKEND == "redis":
        r = _get_redis()
        r.delete(f"memory:{line_uid}")
    else:
        _dict_store.pop(line_uid, None)
    logger.info(f"Memory deleted for {line_uid[:5]}****")


# ============================================================
# 內部工具
# ============================================================
def _save(line_uid: str, memory: dict):
    """寫入記憶到後端。"""
    if MEMORY_BACKEND == "redis":
        r = _get_redis()
        key = f"memory:{line_uid}"
        r.set(key, json.dumps(memory, ensure_ascii=False))
        r.expire(key, MEMORY_TTL_DAYS * 24 * 60 * 60)
    else:
        _dict_store[line_uid] = deepcopy(memory)


def _compress_turns(memory: dict) -> dict:
    """
    把最舊的對話壓縮成 summary，只保留最近的 turns。

    壓縮策略（不呼叫 LLM 的簡易版）：
    - 取最舊的 N 輪
    - 拼接成一段文字摘要
    - 保留最近 5 輪

    生產環境建議用 memory_summarize.py 的 LLM 壓縮。
    """
    turns = memory["turns"]
    keep_count = MAX_TURNS  # 保留最近 10 條訊息（5 輪）

    if len(turns) <= keep_count:
        return memory

    # 要壓縮的部分
    to_compress = turns[:-keep_count]
    # 保留的部分
    memory["turns"] = turns[-keep_count:]

    # 簡易摘要：取 user 的訊息拼接
    user_msgs = [t["content"][:50] for t in to_compress if t["role"] == "user"]
    new_summary_part = "；".join(user_msgs)

    # 合併到現有 summary
    existing = memory.get("summary", "")
    if existing:
        memory["summary"] = f"{existing}。之後又聊到：{new_summary_part}"
    else:
        memory["summary"] = f"顧客先前聊過：{new_summary_part}"

    # 限制 summary 長度（避免無限增長）
    if len(memory["summary"]) > 500:
        memory["summary"] = memory["summary"][-500:]

    logger.info(f"Compressed {len(to_compress)} messages into summary")
    return memory
