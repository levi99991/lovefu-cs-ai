"""
大島樂眠 AI 輔睡員 — Prompt 組裝器
lovefu-cs-brain/scripts/prompt_assembler.py

把 persona + knowledge + memory + 外部資料 組裝成完整的 LLM messages。
"""

import os
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger("lovefu.brain.prompt")

# ============================================================
# Persona（人設核心段落，常駐載入）
# ============================================================
PERSONA_CORE = """你是大島樂眠的「樂眠輔睡員」，島內人叫你「小島」。

你是輔助睡眠的人員——用傾聽、陪伴和專業知識，幫島民找到屬於自己的好眠方式。
你不是睡眠顧問（不做診斷或處方），你不是銷售（不推銷、不追單、不製造焦慮）。

語氣鐵律：
1. 先聽再說——回覆前先確認你理解了問題
2. 問一個就好——每次最多問一個問題
3. 像朋友聊天——口語化、短句、3~5 句以內
4. 誠實為上——不確定就說「我幫你確認一下」
5. 溫暖收尾——讓人覺得被照顧到了

稱呼：用「你」不用「您」，自稱「我」或「我們大島」
回覆格式：不用 markdown、不用條列、不用粗體，語氣詞（～喔呢啦）每則不超過 2 個
長度：一般 50~120 字，複雜問題最多 200 字，超過 80 字分段
絕對禁止：提競品、做醫療建議、編造資訊、催促購買、洩露個資"""


# ============================================================
# Knowledge 檔案路徑（按意圖對應）
# ============================================================
KNOWLEDGE_BASE = Path(os.getenv(
    "KNOWLEDGE_PATH",
    str(Path(__file__).parent.parent.parent / "lovefu-cs-knowledge" / "references")
))

INTENT_TO_KNOWLEDGE = {
    "PRODUCT": ["products-mattress.md"],
    "SLEEP": ["sleep-science.md"],
    "RETURN": ["service-policy.md"],
    "STORE": ["store-info.md"],
    "MEMBER": ["member-program.md"],
    "ORDER": ["service-policy.md"],  # 配送相關資訊
}

# 產品關鍵字 → 載入對應的 reference
PRODUCT_KEYWORD_MAP = {
    "products-mattress.md": ["床墊", "山丘", "冰島", "飄雲", "無光", "薄墊", "厚墊", "獨立筒"],
    "products-pillow.md": ["枕頭", "月眠枕", "月眠", "側睡枕", "雲朵枕", "量脖子"],
    "products-other.md": ["床架", "懸浮", "沙發", "窩沙發", "棉被", "床包", "寢飾", "竹眠", "保潔墊", "眼罩"],
}


def _select_knowledge_files(intent: str, message: str) -> list[str]:
    """根據意圖和訊息內容，決定載入哪些 knowledge reference 檔。"""
    files = []

    # PRODUCT 和 SLEEP 意圖要根據關鍵字細分
    if intent in ("PRODUCT", "SLEEP"):
        for filename, keywords in PRODUCT_KEYWORD_MAP.items():
            if any(kw in message for kw in keywords):
                files.append(filename)

        # SLEEP 額外載入 sleep-science
        if intent == "SLEEP":
            files.append("sleep-science.md")

        # 沒命中任何關鍵字 → 預設載入床墊（最高頻）
        if not files:
            files.append("products-mattress.md")
    else:
        files = INTENT_TO_KNOWLEDGE.get(intent, [])

    # 去重，最多 2 個
    seen = set()
    unique = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique[:2]


def _load_knowledge(filenames: list[str]) -> str:
    """從 reference 檔案載入知識文字。"""
    texts = []
    for fn in filenames:
        filepath = KNOWLEDGE_BASE / fn
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            texts.append(content)
        else:
            logger.warning(f"Knowledge file not found: {filepath}")
    return "\n\n---\n\n".join(texts)


# ============================================================
# 組裝完整 Prompt
# ============================================================
def assemble_prompt(
    message: str,
    intent: str,
    memory: dict,
    extra_context: str = "",
) -> list[dict]:
    """
    組裝完整的 LLM messages 陣列。

    回傳格式：
    [
        {"role": "system", "content": "完整 system prompt"},
        {"role": "user", "content": "..."},     ← 歷史對話
        {"role": "assistant", "content": "..."}, ← 歷史對話
        ...
        {"role": "user", "content": "當前訊息"}
    ]
    """
    # ── 組裝 System Prompt ──
    system_parts = [PERSONA_CORE]

    # 顧客 profile
    profile_text = memory.get("profile_text", "")
    if profile_text:
        system_parts.append(f"## 這位顧客\n{profile_text}")

    # 歷史摘要
    summary_text = memory.get("summary_text", "")
    if summary_text:
        system_parts.append(f"## 歷史摘要\n{summary_text}")

    # 知識庫
    knowledge_files = _select_knowledge_files(intent, message)
    if knowledge_files:
        knowledge_text = _load_knowledge(knowledge_files)
        if knowledge_text:
            system_parts.append(f"## 產品與服務知識\n{knowledge_text}")

    # 外部查詢資料
    if extra_context:
        system_parts.append(f"## 查詢到的資料\n{extra_context}")

    # 回覆提醒
    system_parts.append(
        "## 回覆提醒\n"
        "用口語化的繁體中文回覆，像在 LINE 上跟朋友聊天。"
        "50~120 字，超過 80 字分段。不用 markdown、不用條列。"
        "最多問一個問題。"
    )

    system_content = "\n\n".join(system_parts)

    # ── 組裝 Messages ──
    messages = [{"role": "system", "content": system_content}]

    # 歷史對話
    recent_turns = memory.get("recent_turns", [])
    messages.extend(recent_turns)

    # 當前訊息
    messages.append({"role": "user", "content": message})

    return messages
