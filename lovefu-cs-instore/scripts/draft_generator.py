"""
門市追客草稿生成器
基於 cs-knowledge 與 cs-persona，依照 5 階段 + 顧客 lead 資料生成個人化草稿。
所有草稿都標明 [DRAFT - 需真人確認後發送]。
"""
import logging
from typing import Optional

logger = logging.getLogger("lovefu.cs_instore.draft")


# 各階段草稿模板（佔位符 {customer_name}、{tried_products}、{store_name}、{advisor_name}）
TEMPLATES = {
    "S0_intro": (
        "{customer_name}您好，我是大島樂眠的小島 ✦\n"
        "感謝今天到 {store_name} 試躺{tried_products}\n"
        "{advisor_name}說您今天感受得很細，這份對睡眠的在意，正是好夢的開始。\n"
        "回家路上慢慢走，有任何關於今天試躺的感受、或睡眠的疑問，隨時跟我說。\n"
        "晚安！歡迎回家。"
    ),
    "S1_feedback": (
        "{customer_name} 早安 ✦\n"
        "昨天試躺{tried_products}之後，今天起床的感覺如何呢？\n"
        "有時候一張床會不會適合，要再多睡一晚才會浮現答案。\n"
        "如果有任何想分享的、想問的，回覆我就好，我都在。"
    ),
    "S2_deep": (
        "{customer_name}，我是小島 ✦\n"
        "想跟您分享一個睡眠的小知識：{knowledge_snippet}\n"
        "這也是為什麼當初設計{tried_products}時，我們特別注重{product_feature}。\n"
        "如果有想再了解的細節，或想預約再次試躺，都可以告訴我。"
    ),
    "S3_offer": (
        "{customer_name} 您好 ✦\n"
        "想跟您分享一個消息：{current_promo}\n"
        "如果您正在考慮{tried_products}，這檔期可能是不錯的時機。\n"
        "另外，提醒您我們的「安心睡計畫」— {comfort_policy}，讓您下決定不用有壓力。\n"
        "需要再次試躺、或想聊聊細節，都歡迎找我。"
    ),
    "S4_care": (
        "{customer_name}，最近睡得好嗎？✦\n"
        "今天不是要推薦什麼，只是想問候您一聲。\n"
        "睡眠是一輩子的事，{store_name}和我都會在這裡，無論您什麼時候想再來坐坐。\n"
        "晚安！歡迎回家。"
    ),
}


def generate_draft(
    stage_id: str,
    lead_data: dict,
    knowledge_snippet: Optional[str] = None,
    current_promo: Optional[str] = None,
) -> str:
    """
    依階段 + lead + 動態知識/檔期，生成草稿。
    回傳純文字草稿，前後不加任何 marker（呼叫端自行加上 [DRAFT] tag）。
    """
    template = TEMPLATES.get(stage_id)
    if not template:
        return ""

    tried = lead_data.get("tried_products", [])
    if isinstance(tried, list):
        tried_str = "、".join(tried) if tried else "我們的產品"
    else:
        tried_str = tried or "我們的產品"

    fields = {
        "customer_name": lead_data.get("customer_name", "您"),
        "tried_products": tried_str,
        "store_name": lead_data.get("store_name", "我們的體驗店"),
        "advisor_name": lead_data.get("advisor_name", "輔睡員"),
        "knowledge_snippet": knowledge_snippet or "好的睡眠，從接住身體曲線的支撐開始。",
        "product_feature": lead_data.get("product_feature", "壓力分散與透氣"),
        "current_promo": current_promo or "本月會員回饋方案",
        "comfort_policy": "100 天睡眠保證，不適合可全額退費",
    }

    try:
        return template.format(**fields)
    except KeyError as e:
        logger.warning("draft template missing field %s", e)
        return template
