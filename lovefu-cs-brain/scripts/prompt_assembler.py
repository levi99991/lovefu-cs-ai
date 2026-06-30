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
    "products-pillow.md": ["枕頭", "月眠枕", "月眠", "側睡枕", "雲朵枕", "量脖子", "墊高片", "側高片", "加購"],
    "products-other.md": ["床架", "懸浮", "沙發", "窩沙發", "棉被", "涼被", "兩用被", "床包", "寢飾", "竹眠", "保潔墊", "眼罩", "床頭櫃", "洗衣精", "洗被袋"],
    "products-mattress-custom.md": ["客製", "訂製", "客訂", "特規", "IKEA", "ikea", "Ikea", "MUJI", "muji", "Muji", "尺寸不合", "特殊尺寸"],
}

# 優惠/活動關鍵字 → 額外載入 current-promotions.md（任何意圖都適用）
PROMO_KEYWORDS = ["優惠", "活動", "折扣", "促銷", "漲價", "滿額", "折抵", "購物金", "點數", "會員", "入厝", "說明會", "出清", "分期", "方案"]


# 單一知識檔最多載入的字數（避免一次塞太多，導致 LLM 請求過大而失敗）
PER_FILE_CHAR_CAP = 5500
# 一次最多載入幾個知識檔
MAX_KNOWLEDGE_FILES = 3


def _select_knowledge_files(intent: str, message: str) -> list[str]:
    """根據意圖和訊息內容，決定載入哪些 knowledge reference 檔。

    重點：只要訊息「點名了某個產品」，就一定載入該產品的價格檔——
    不管意圖被分到 PRODUCT、MEMBER、RETURN 還是其他。
    這樣「山丘加月眠枕一起買有折扣嗎」被分到 MEMBER 時，
    也會帶著山丘與月眠枕的價格，AI 才算得出滿額折。

    優先順序：被點名的產品價格檔 > 當期活動（含滿額折邏輯）> 意圖基礎檔。
    最多 3 個檔，避免請求過大讓 LLM 失敗。
    """
    # 1) 不分意圖：訊息提到任一產品 → 該產品價格檔（最優先）
    product_files = [
        fn for fn, kws in PRODUCT_KEYWORD_MAP.items()
        if any(kw in message for kw in kws)
    ]

    # 2) 含優惠/活動關鍵字 → 當期活動（含滿額折計算邏輯）
    promo_files = (
        ["current-promotions.md"]
        if any(kw in message for kw in PROMO_KEYWORDS) else []
    )

    # 3) 意圖基礎檔（PRODUCT 靠關鍵字載入，不另加）
    if intent == "SLEEP":
        base_files = ["sleep-science.md"]
    elif intent == "PRODUCT":
        base_files = []
    else:
        base_files = list(INTENT_TO_KNOWLEDGE.get(intent, []))

    # PRODUCT / SLEEP 沒命中任何產品 → 預設床墊（最高頻）
    if intent in ("PRODUCT", "SLEEP") and not product_files:
        product_files = ["products-mattress.md"]

    # 依優先序合併、去重、取前 N 個
    seen = set()
    unique = []
    for f in product_files + promo_files + base_files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique[:MAX_KNOWLEDGE_FILES]


def _load_knowledge(filenames: list[str]) -> str:
    """從 reference 檔案載入知識文字。

    每個檔最多取 PER_FILE_CHAR_CAP 字（價格表與規則都在檔案前段，
    截掉的是後段敘述），避免一次塞太多讓 LLM 請求過大而失敗。
    """
    texts = []
    for fn in filenames:
        filepath = KNOWLEDGE_BASE / fn
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            if len(content) > PER_FILE_CHAR_CAP:
                content = content[:PER_FILE_CHAR_CAP] + "\n…（其餘細節以官網或轉人工為準）"
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
