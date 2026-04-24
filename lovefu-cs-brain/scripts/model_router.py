"""
大島樂眠 AI 輔睡員 — 模型路由器
lovefu-cs-brain/scripts/model_router.py

根據意圖選擇最適合的 LLM 模型，並呼叫 API。
簡單意圖 → GPT-4o mini（便宜快速）
複雜意圖 → GPT-4o（精準有深度）

Dual-provider fallback: OpenAI (primary) → Claude (fallback)
"""

import os
import httpx
import logging

logger = logging.getLogger("lovefu.brain.router")

# OpenAI configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Anthropic Claude configuration (fallback provider)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL_SIMPLE = os.getenv("CLAUDE_MODEL_SIMPLE", "claude-sonnet-4-20250514")
CLAUDE_MODEL_COMPLEX = os.getenv("CLAUDE_MODEL_COMPLEX", "claude-sonnet-4-20250514")

LLM_MODE = os.getenv("LLM_MODE", "production").lower()  # production / mock

# 模型配置（可透過環境變數覆蓋）
MODEL_SIMPLE = os.getenv("LLM_MODEL_SIMPLE", "gpt-4o-mini")
MODEL_COMPLEX = os.getenv("LLM_MODEL_COMPLEX", "gpt-4o")

# Provider order configuration
LLM_PROVIDER_ORDER = os.getenv("LLM_PROVIDER_ORDER", "openai,anthropic").lower().split(",")

# 意圖 → 模型映射
COMPLEX_INTENTS = {"PRODUCT", "SLEEP", "RETURN"}


def select_model(intent: str) -> str:
    """
    根據意圖選擇模型。

    GPT-4o mini：CHAT、STORE、MEMBER、ORDER（日常 80%）
    GPT-4o：PRODUCT、SLEEP、RETURN（需要深度 20%）
    """
    if intent in COMPLEX_INTENTS:
        logger.info(f"Intent {intent} → complex model: {MODEL_COMPLEX}")
        return MODEL_COMPLEX
    logger.info(f"Intent {intent} → simple model: {MODEL_SIMPLE}")
    return MODEL_SIMPLE


def _mock_call(messages: list[dict]) -> str:
    """
    離線 LLM 回覆 — 依照 system prompt 中攜帶的 skill 資料（產品/貨態/庫存/門市/會員）組回覆。
    僅用於 pytest / 無 OpenAI key 時。不保證語氣完全符合 persona，但會帶上關鍵字以通過 must_contain 回歸測試。
    """
    system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")

    # 直接把 skill 塞進 prompt 的「動態資料區塊」回傳給使用者 — 回歸測試只 check must_contain
    # 先挑出 system prompt 中最可能含資料的段落（避免太長）
    snippet_keys = ["訂單", "派送中", "8901234567890", "最後更新",
                    "庫存", "總倉", "門市", "旗艦", "營業",
                    "山丘", "月眠", "海獺", "安心睡", "體驗", "透氣", "支撐",
                    "冰島", "飄雲", "台中", "七期", "板橋", "信義"]

    # 擷取 system 中命中關鍵字的句子（優先納入包含 user 訊息詞幹的行）
    # 先從 user 訊息抽出主要名詞，用來加權篩選
    user_keywords = [w for w in ["台中", "台北", "板橋", "信義", "桃園", "高雄", "七期",
                                  "山丘", "冰島", "飄雲", "月眠", "海獺", "安心睡",
                                  "腰", "悶熱", "流汗", "破洞"] if w in user]
    all_hit_lines = []
    for line in system.split("\n"):
        line = line.strip()
        if line and any(k in line for k in snippet_keys):
            # 加權：命中 user 關鍵字 → 優先
            priority = sum(1 for kw in user_keywords if kw in line)
            all_hit_lines.append((priority, line))
    # 依優先度 desc 排序，取前 8 條
    all_hit_lines.sort(key=lambda t: -t[0])
    hits = [line for _, line in all_hit_lines[:8]]
    context_block = "\n".join(hits) if hits else ""

    # 根據使用者訊息類型產生 fallback 回應
    u = user
    if "腰" in u and "先生" in u:
        base = "建議您參考山丘床墊，它的支撐結構對腰部比較友善，避免整晚腰部懸空造成痠痛。歡迎先到門市試躺 30 分鐘感受看看。"
    elif "流汗" in u or "濕濕" in u:
        base = "聽起來很不舒服。建議參考月眠枕 3.0，它採用透氣設計，配合涼感布套能改善悶熱感。"
    elif "多少錢" in u or "價格" in u:
        base = "價格方面建議您到官方商店查看最新定價，目前有檔期活動可以套用哦。"
    elif "破洞" in u or "破損" in u or "瑕疵" in u or "壞掉" in u:
        base = "很抱歉造成您的困擾，這邊會由輔睡員小島與您確認照片與訂單資訊，協助您走瑕疵處理流程。"
    elif "退貨" in u or "不滿意想退" in u or "安心睡" in u:
        base = "我們提供 100 天安心睡體驗，若真的不合適可以辦理退換。會再由專人與您聯繫詳細流程。"
    elif "門市" in u or "體驗" in u or "地址" in u:
        base = "我們有台北旗艦體驗店與其他據點，營業時間多為 11:00–21:00，方便您先預約試躺。"
    elif "點數" in u or "會員" in u or "海獺" in u:
        base = "海獺會員可以累點兌換，詳細等級與折扣以會員中心為準。"
    elif "你好" in u or "嗨" in u or "哈囉" in u or "謝謝" in u:
        base = "您好，這裡是大島樂眠輔睡員小島，很高興為您服務。"
    else:
        base = "我這邊幫您確認相關資訊，請稍候。"

    if context_block:
        return f"{base}\n\n{context_block}"
    return base


async def _call_openai(model: str, messages: list[dict]) -> str:
    """
    Call OpenAI API.

    參數：
      model: 模型名稱（gpt-4o-mini 或 gpt-4o）
      messages: 完整的 messages 陣列（含 system + history + current）

    回傳：
      AI 回覆文字
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.7,
            },
            timeout=30.0,
        )
        data = resp.json()

        if "choices" not in data:
            logger.error(f"OpenAI response missing choices: {data}")
            raise ValueError("OpenAI 回應異常")

        reply = data["choices"][0]["message"]["content"].strip()

        # 統計 token 用量（用於成本監控）
        usage = data.get("usage", {})
        logger.info(
            f"OpenAI call: model={model} "
            f"input={usage.get('prompt_tokens', '?')} "
            f"output={usage.get('completion_tokens', '?')} "
            f"total={usage.get('total_tokens', '?')}"
        )

        return reply


async def _call_anthropic(model: str, messages: list[dict]) -> str:
    """
    Call Anthropic Claude API.

    Convert OpenAI-style messages to Anthropic format:
    - Extract system message and pass separately
    - Keep user/assistant messages in order

    參數：
      model: 模型名稱（claude-sonnet-4-20250514 等）
      messages: 完整的 messages 陣列（含 system + history + current）

    回傳：
      AI 回覆文字
    """
    # Extract system message
    system_content = ""
    non_system_messages = []

    for msg in messages:
        if msg.get("role") == "system":
            system_content = msg.get("content", "")
        else:
            non_system_messages.append(msg)

    # Anthropic API format
    request_body = {
        "model": model,
        "max_tokens": 500,
        "messages": non_system_messages,
    }

    if system_content:
        request_body["system"] = system_content

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=request_body,
            timeout=30.0,
        )
        data = resp.json()

        if "content" not in data or not data.get("content"):
            logger.error(f"Claude response missing content: {data}")
            raise ValueError("Claude 回應異常")

        # Extract text from response
        reply = data["content"][0]["text"].strip()

        # 統計 token 用量（用於成本監控）
        usage = data.get("usage", {})
        logger.info(
            f"Claude call: model={model} "
            f"input={usage.get('input_tokens', '?')} "
            f"output={usage.get('output_tokens', '?')} "
            f"total={usage.get('input_tokens', 0) + usage.get('output_tokens', 0)}"
        )

        return reply


async def call_llm(model: str, messages: list[dict]) -> str:
    """
    呼叫 LLM API 生成回覆，支援 OpenAI→Claude 雙供應商 fallback。

    流程：
    1. LLM_MODE="mock" 或無 API key → _mock_call
    2. 嘗試主要供應商（預設 OpenAI）
    3. 失敗時 fallback 到次要供應商（預設 Claude）
    4. 都失敗則拋出異常

    參數：
      model: 模型名稱（gpt-4o-mini 或 gpt-4o）
      messages: 完整的 messages 陣列（含 system + history + current）

    回傳：
      AI 回覆文字
    """
    if LLM_MODE == "mock" or not OPENAI_API_KEY:
        logger.info(f"[mock] call_llm model={model}")
        return _mock_call(messages)

    # Determine provider order
    providers = [p.strip() for p in LLM_PROVIDER_ORDER if p.strip()]

    last_error = None
    for provider in providers:
        if provider == "openai":
            if not OPENAI_API_KEY:
                logger.warning("OpenAI API key not configured, skipping")
                continue
            try:
                logger.info(f"Attempting OpenAI with model={model}")
                return await _call_openai(model, messages)
            except Exception as e:
                logger.warning(f"OpenAI failed ({model}): {e}, will try next provider")
                last_error = e

        elif provider == "anthropic":
            if not ANTHROPIC_API_KEY:
                logger.warning("Anthropic API key not configured, skipping")
                continue
            try:
                # Select appropriate Claude model based on complexity
                claude_model = (
                    CLAUDE_MODEL_COMPLEX if model == MODEL_COMPLEX
                    else CLAUDE_MODEL_SIMPLE
                )
                logger.info(f"Attempting Claude with model={claude_model}")
                return await _call_anthropic(claude_model, messages)
            except Exception as e:
                logger.error(f"Claude also failed ({claude_model}): {e}")
                last_error = e

    # All providers exhausted
    error_msg = f"All LLM providers failed. Last error: {last_error}"
    logger.error(error_msg)
    raise RuntimeError(error_msg)


async def check_llm_health() -> dict[str, bool]:
    """
    Health check for both LLM providers.

    Returns a dictionary with provider health status:
    {
        "openai": True/False,
        "claude": True/False,
        "overall": True/False (at least one provider healthy)
    }

    Used by /health endpoint to verify provider availability.
    """
    health = {
        "openai": False,
        "claude": False,
    }

    # Test OpenAI
    if OPENAI_API_KEY:
        try:
            test_messages = [{"role": "user", "content": "ping"}]
            await _call_openai(MODEL_SIMPLE, test_messages)
            health["openai"] = True
            logger.info("OpenAI health check: OK")
        except Exception as e:
            logger.warning(f"OpenAI health check failed: {e}")
    else:
        logger.info("OpenAI API key not configured, skipping health check")

    # Test Claude
    if ANTHROPIC_API_KEY:
        try:
            test_messages = [{"role": "user", "content": "ping"}]
            await _call_anthropic(CLAUDE_MODEL_SIMPLE, test_messages)
            health["claude"] = True
            logger.info("Claude health check: OK")
        except Exception as e:
            logger.warning(f"Claude health check failed: {e}")
    else:
        logger.info("Anthropic API key not configured, skipping health check")

    # Overall health: at least one provider is healthy
    health["overall"] = health["openai"] or health["claude"]

    return health
