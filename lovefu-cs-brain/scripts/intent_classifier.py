"""
大島樂眠 AI 輔睡員 — 意圖分類
lovefu-cs-brain/scripts/intent_classifier.py
"""

import os
import httpx
import logging

logger = logging.getLogger("lovefu.brain.intent")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODE = os.getenv("LLM_MODE", "production").lower()  # production / mock

INTENT_PROMPT = """根據以下顧客訊息，判斷意圖類型。只回傳一個類型代碼，不要多說。

類型：
- ORDER：查詢訂單詳情、訂單明細（含付款、商品、數量、退款）
- CARGO：純查貨態、物流追蹤（「我的貨到哪了」「到了嗎」「今天會到嗎」）
- STOCK：查即時庫存、還有沒有貨、現貨嗎、補貨了嗎
- PRODUCT：詢問產品規格、價格、差異比較、哪張床好、多少錢
- SLEEP：睡眠困擾、選床建議、腰痠、悶熱、淺眠、枕頭怎麼選
- RETURN：退換貨、安心睡退貨、商品瑕疵、我想退、不滿意想退
- COMPLAINT：客訴、抱怨、要找主管、情緒激動、詐騙、太扯
- STORE：體驗店資訊、預約、地址、營業時間、門市在哪
- MEMBER：會員點數、等級、折扣、優惠券、海獺
- CHAT：閒聊、打招呼、感謝、你好、謝謝、晚安

顧客訊息：{message}"""

VALID_INTENTS = {"ORDER", "CARGO", "STOCK", "PRODUCT", "SLEEP", "RETURN", "COMPLAINT", "STORE", "MEMBER", "CHAT"}

ESCALATION_KEYWORDS = [
    "找主管", "找你們主管", "找負責人", "找店長",
    "真人客服", "人工客服", "找真人", "不要跟機器人講",
    "轉接", "轉真人",
    "消保官", "消基會", "投訴", "要投訴", "申訴",
    "律師", "告你們", "法院", "公平會",
    "詐騙", "騙子", "爛透了", "什麼態度",
    "太誇張", "太扯了", "受不了",
]


def check_escalation_keywords(message: str) -> bool:
    """檢查是否包含轉接關鍵字。不經 LLM，即時比對。"""
    return any(kw in message for kw in ESCALATION_KEYWORDS)


def _mock_classify(message: str) -> str:
    """離線意圖分類（關鍵字匹配）— 被 _fast_classify 包裝。"""
    intent, _ = _fast_classify(message)
    return intent


# ============================================================
# Fast-path Classifier（keyword + regex）
#   ─ 80% 訊息可直接判定，不需 call LLM
#   ─ 回傳 (intent, confidence 0.0-1.0)
#   ─ confidence < FAST_PATH_CONFIDENCE 會再 fallback LLM
# ============================================================

FAST_PATH_CONFIDENCE = float(os.getenv("INTENT_FAST_PATH_CONFIDENCE", "0.75"))
# 分類門檻：< LOW_CONFIDENCE_THRESHOLD 時啟動多分類並行（未來擴充）
LOW_CONFIDENCE_THRESHOLD = 0.5


def _fast_classify(message: str) -> tuple[str, float]:
    """
    關鍵字快速分類 + 信心分數
    高信心規則：明確的 SKU / 訂單號 / 強關鍵字
    中信心規則：一般關鍵字命中
    低信心：模糊或無匹配 → 留給 LLM
    """
    import re
    msg = message.strip()

    # ── 極高信心（0.95）：明確 pattern ──
    # SKU：MAT-XXX / PIL-XXX
    if re.search(r"\b[A-Z]{3}-[A-Z]{2,}-?[A-Z0-9]*\b", msg):
        return "STOCK", 0.95
    # 訂單號（含查詢關鍵字）
    if re.search(r"[A-Z]{1,3}\d{6,}", msg):
        if any(k in msg for k in ["寄", "到", "貨", "送", "物流", "配送", "追蹤"]):
            return "CARGO", 0.95
        return "ORDER", 0.9

    # ── 高信心（0.85）：明確客訴 / 轉接 ──
    if any(k in msg for k in ["主管", "投訴", "申訴", "詐騙", "爛透", "太扯", "受不了", "告你"]):
        return "COMPLAINT", 0.9
    # 明確退貨瑕疵
    if any(k in msg for k in ["破洞", "破損", "瑕疵", "壞掉", "缺角", "發霉", "異味"]):
        return "RETURN", 0.85
    if any(k in msg for k in ["退貨", "退換", "不滿意想退", "安心睡退", "我要退"]):
        return "RETURN", 0.85

    # ── 中信心（0.7-0.8）：一般關鍵字 ──
    rules = [
        (["現貨", "庫存", "有沒有貨", "還有貨", "補貨", "現在有嗎"], "STOCK", 0.8),
        (["寄了嗎", "到了沒", "到哪了", "送到", "貨到", "物流"], "CARGO", 0.8),
        (["訂單", "退款", "付款明細"], "ORDER", 0.75),
        (["腰痠", "悶熱", "流汗", "睡不好", "淺眠", "失眠"], "SLEEP", 0.8),
        (["選床", "選枕", "怎麼選", "哪張床"], "SLEEP", 0.75),
        # 贈品 / 不實宣傳類查詢（I01 安全情境）
        (["送 iPhone", "送手機", "送禮", "送 iPad", "買床送", "贈品", "免費送"], "PRODUCT", 0.8),
        (["多少錢", "價格", "規格", "尺寸", "差異", "比較"], "PRODUCT", 0.75),
        (["有店嗎", "有門市", "有體驗店", "地址", "營業時間", "預約"], "STORE", 0.8),
        (["門市", "體驗店", "旗艦"], "STORE", 0.7),
        (["會員", "點數", "優惠券", "折扣", "海獺"], "MEMBER", 0.8),
        (["你好", "哈囉", "嗨", "謝謝", "感謝", "晚安", "早安"], "CHAT", 0.75),
    ]
    for keywords, intent, conf in rules:
        if any(k in msg for k in keywords):
            return intent, conf

    # 無匹配 → 低信心，留給 LLM
    return "CHAT", 0.3


def _message_complexity(message: str) -> str:
    """快速判斷訊息複雜度：simple / medium / complex — 供後續優化決策。"""
    length = len(message)
    if length <= 10:
        return "simple"
    if length <= 40:
        return "medium"
    return "complex"


async def classify_intent(message: str) -> str:
    """
    意圖分類 — 兩段式：
    1. Fast-path（關鍵字 / regex）→ 信心 ≥ FAST_PATH_CONFIDENCE 直接返回
    2. LLM fallback（GPT-4o mini）→ 低信心 / 邊界情境
    成本：Fast-path $0（~80% 流量）；LLM fallback 每次 ~$0.00005
    延遲：Fast-path <5ms；LLM fallback ~500-1500ms
    """
    # 離線 / 無 API key → fast-path only
    if LLM_MODE == "mock" or not OPENAI_API_KEY:
        return _mock_classify(message)

    # Step 1：Fast-path 先試
    fast_intent, confidence = _fast_classify(message)
    if confidence >= FAST_PATH_CONFIDENCE:
        logger.debug(f"Fast-path intent: {fast_intent} (conf={confidence})")
        return fast_intent

    # Step 2：低信心 → LLM fallback
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": INTENT_PROMPT.format(message=message)}
                    ],
                    "max_tokens": 10,
                    "temperature": 0,
                },
                timeout=10.0,
            )
            data = resp.json()
            intent = data["choices"][0]["message"]["content"].strip().upper()
            if intent in VALID_INTENTS:
                return intent
            logger.warning(f"Invalid intent '{intent}' for message: {message[:50]}")
            # LLM 回傳不合法 → 退回 fast-path 結果（比純 CHAT 更安全）
            return fast_intent if fast_intent != "CHAT" else "CHAT"

    except Exception as e:
        logger.error(f"Intent classification error: {e}")
        # 異常時退回 fast-path 結果
        return fast_intent
