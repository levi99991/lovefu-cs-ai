"""
大島樂眠 AI 輔睡員 — 對話記憶 LLM 壓縮
lovefu-cs-memory/scripts/memory_summarize.py

當對話歷史超過 10 輪時，用 GPT-4o mini 把舊對話壓縮成一段摘要。
這是 memory_store.py 中 _compress_turns() 的進階版。
生產環境使用此腳本替換簡易壓縮。
"""

import os
import httpx
import logging

logger = logging.getLogger("lovefu.memory.summarize")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

SUMMARIZE_PROMPT = """你是大島樂眠的 AI 輔睡員記憶助手。
請把以下對話歷史壓縮成一段精簡摘要（100字以內），保留：
1. 顧客的姓名（如果有提到）
2. 顧客的睡眠困擾和偏好
3. 討論過的產品
4. 訂單相關資訊（訂單編號、狀態）
5. 未解決的問題

不需要保留：
- 打招呼和客套話
- AI 的完整回覆內容
- 已經解決的問題

用第三人稱描述，例如：
「顧客王小明仰睡為主、腰部敏感，4/5 詢問山丘床墊 5 尺，已知售價 NT$XX,XXX，考慮中。」

對話歷史：
{conversation}
"""


async def summarize_turns_with_llm(turns: list[dict]) -> str:
    """
    用 GPT-4o mini 把對話歷史壓縮成摘要。
    成本極低（~50 字輸出 + ~200 字輸入 ≈ $0.0001 per call）。
    """
    # 組裝對話文字
    conversation_text = "\n".join([
        f"{'顧客' if t['role'] == 'user' else 'AI'}：{t['content']}"
        for t in turns
    ])

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": SUMMARIZE_PROMPT.format(
                                conversation=conversation_text
                            ),
                        }
                    ],
                    "max_tokens": 200,
                    "temperature": 0.3,
                },
                timeout=15.0,
            )
            data = resp.json()
            summary = data["choices"][0]["message"]["content"].strip()
            logger.info(f"LLM summarized {len(turns)} messages → {len(summary)} chars")
            return summary

    except Exception as e:
        logger.error(f"LLM summarize failed: {e}")
        # 失敗時用簡易壓縮兜底
        user_msgs = [t["content"][:50] for t in turns if t["role"] == "user"]
        return f"顧客先前聊過：{'；'.join(user_msgs)}"
