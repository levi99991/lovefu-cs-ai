"""
cs-handoff — 四大轉人工訊號偵測器

觸發類別：
  ① EXPLICIT  顧客明說「我要真人」「找店員」
  ② LOW_CONF  AI 信心不足（意圖模糊、連續澄清失敗、知識庫無匹配）
  ③ EMOTION   情緒紅線（生氣、重複問 3+ 次、客訴關鍵字）
  ④ HIGH_VALUE 高價值事件（預約到店、退換貨、安心睡、即將成交）

回傳：(signal_type, reason, priority)
  priority: P0 立即 / P1 5 分鐘內 / P2 30 分鐘內
"""
import re
from typing import Optional

# ============================================================
# ① EXPLICIT — 顧客明說（P0）
# ============================================================
EXPLICIT_KEYWORDS = [
    # 直接要求
    "要真人", "找真人", "真人客服", "人工客服",
    "找店員", "找輔睡員", "找店長", "找經理", "找主管",
    "轉人工", "轉接", "轉真人", "換真人",
    "不要 AI", "不要機器人", "不要聊天機器人",
    "我要跟人講", "我要跟人聊",
    # 反問
    "你是 AI 嗎", "你是機器人嗎", "你是真人嗎",
]


def detect_explicit(message: str) -> Optional[tuple[str, str, str]]:
    for kw in EXPLICIT_KEYWORDS:
        if kw in message:
            return ("EXPLICIT", f"顧客明說：『{kw}』", "P0")
    return None


# ============================================================
# ② LOW_CONF — AI 信心不足（P1）
# ============================================================
CLARIFY_HINTS = ["你沒聽懂", "我不是這個意思", "我不是說", "再聽一次", "聽不懂我"]


def detect_low_confidence(
    intent_confidence: float,
    clarify_count: int,
    message: str,
) -> Optional[tuple[str, str, str]]:
    # 顧客明確表達「你沒聽懂」
    if any(h in message for h in CLARIFY_HINTS):
        return ("LOW_CONF", "顧客表示 AI 誤解語意", "P1")
    # 連續 2 次澄清失敗
    if clarify_count >= 2:
        return ("LOW_CONF", f"連續 {clarify_count} 次澄清失敗", "P1")
    # 意圖信心極低
    if intent_confidence < 0.35:
        return ("LOW_CONF", f"意圖信心過低（{intent_confidence:.2f}）", "P2")
    return None


# ============================================================
# ③ EMOTION — 情緒紅線（P0 / P1）
# ============================================================
ANGER_KEYWORDS = [
    # 直接怒氣
    "你們到底", "搞什麼", "太扯", "太誇張", "爛透", "扯爆",
    "受不了", "很煩", "煩死", "超煩", "有病",
    # 詛咒
    "幹你", "去死", "王八", "該死",
    # 投訴威脅
    "投訴", "申訴", "消保官", "消基會", "告你", "律師",
    "公平會", "評審會",
]

COMPLAINT_PATTERNS = [
    # 重複不耐
    r"(我已經.{0,10}說過|講過)",
    r"(第\s*[二三四五2345]\s*次|又問|一直問)",
    # 時間壓力
    r"(到底什麼時候|等多久|等了.{1,5}天)",
]


def detect_emotion(
    message: str,
    repeat_question_count: int = 0,
    dissatisfaction_count: int = 0,
) -> Optional[tuple[str, str, str]]:
    # 強烈負面情緒
    for kw in ANGER_KEYWORDS:
        if kw in message:
            return ("EMOTION", f"情緒紅線關鍵字：『{kw}』", "P0")
    # cs-memory 的不滿意累積（P0，優先於訊息內容判斷）
    if dissatisfaction_count >= 2:
        return ("EMOTION", f"連續 {dissatisfaction_count} 次不滿意", "P0")
    # 重複問同一問題 3+ 次
    if repeat_question_count >= 3:
        return ("EMOTION", f"重複詢問同一問題 {repeat_question_count} 次", "P1")
    # 客訴模式
    for pat in COMPLAINT_PATTERNS:
        if re.search(pat, message):
            return ("EMOTION", "訊息出現客訴模式", "P1")
    return None


# ============================================================
# ④ HIGH_VALUE — 高價值事件（P0 / P1）
# ============================================================
HIGH_VALUE_KEYWORDS_P0 = [
    # 退換貨 / 安心睡
    "安心睡", "我要退", "要退貨", "要退款", "退錢",
    "瑕疵", "破洞", "破損", "發霉", "壞掉",
    # 明確要預約到店（近期）
    "今天去", "今天到", "明天去", "等等過去", "馬上到",
    # 價格談判
    "可以便宜", "有折扣嗎", "議價", "VIP 價", "可以算",
]

HIGH_VALUE_KEYWORDS_P1 = [
    # 預約（一般）
    "預約", "想去看", "想試躺", "想體驗", "到店",
    # 成交訊號
    "怎麼下單", "要買", "付款", "刷卡", "分期",
    # 大額商品詢問
    "買床", "買組", "全部", "套組",
]


def detect_high_value(
    message: str,
    intent: str,
    in_conversation_turns: int = 0,
) -> Optional[tuple[str, str, str]]:
    # P0：退換貨 / 瑕疵 / 議價
    for kw in HIGH_VALUE_KEYWORDS_P0:
        if kw in message:
            return ("HIGH_VALUE", f"高價值事件：『{kw}』", "P0")
    # 意圖 = RETURN 一律 P0
    if intent == "RETURN":
        return ("HIGH_VALUE", "退換貨申請", "P0")
    # P1：預約 / 成交訊號
    for kw in HIGH_VALUE_KEYWORDS_P1:
        if kw in message:
            return ("HIGH_VALUE", f"高價值訊號：『{kw}』", "P1")
    # 對話超過 6 輪仍未解決 → 升級
    if in_conversation_turns >= 6:
        return ("HIGH_VALUE", f"對話 {in_conversation_turns} 輪未收斂", "P2")
    return None


# ============================================================
# 主偵測介面
# ============================================================
def detect_handoff_signal(
    message: str,
    intent: str = "CHAT",
    intent_confidence: float = 1.0,
    clarify_count: int = 0,
    repeat_question_count: int = 0,
    dissatisfaction_count: int = 0,
    conversation_turns: int = 0,
) -> Optional[tuple[str, str, str]]:
    """
    統一入口。按優先順序檢查四大訊號，回傳第一個命中的。
    回傳格式：(signal_type, reason, priority) 或 None
    """
    # ① 顧客明說（優先，P0）
    result = detect_explicit(message)
    if result:
        return result
    # ② 情緒紅線（P0 > P1）
    result = detect_emotion(message, repeat_question_count, dissatisfaction_count)
    if result:
        return result
    # ③ 高價值事件（P0 > P1 > P2）
    result = detect_high_value(message, intent, conversation_turns)
    if result:
        return result
    # ④ AI 信心不足（最低優先）
    result = detect_low_confidence(intent_confidence, clarify_count, message)
    if result:
        return result
    return None
