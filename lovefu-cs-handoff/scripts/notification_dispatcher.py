"""
cs-handoff — 多通道通知派發（v2：含 retry + Email + 結構化 Slack）

通道：
  - LINE Notify（門市值班群組）→ 主要
  - Slack Webhook（總部管理層）→ 監控（Block Kit 結構化卡片）
  - Omnichat 旗標（客服台）→ 顧客端看得到「待人工」
  - Email（備援 / 夜間）→ 隔日處理（SMTP / SES）

路由規則：
  P0：全通道發 + Omnichat 旗標 + Email
  P1：LINE Notify + Omnichat
  P2：僅 Omnichat 旗標（非即時）

為避免重複通知，實作 dedupe（同一 handoff_id 5 分鐘內不重發）。
失敗的通道自動重試（最多 2 次，exponential backoff）。
"""
import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger("lovefu.handoff.notify")

LINE_NOTIFY_DEFAULT = os.getenv("LINE_NOTIFY_TOKEN", "")
SLACK_WEBHOOK = os.getenv("SLACK_HANDOFF_WEBHOOK", "")
OMNICHAT_API_KEY = os.getenv("OMNICHAT_API_KEY", "")
OMNICHAT_BASE = os.getenv("OMNICHAT_BASE_URL", "https://api.omnichat.com.tw")

# Email 設定
EMAIL_ENABLED = os.getenv("HANDOFF_EMAIL_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("HANDOFF_EMAIL_FROM", "ai-advisor@lovefu.tw")
EMAIL_TO_HQ = os.getenv("HANDOFF_EMAIL_TO_HQ", "")  # 多人逗號分隔

# 重試設定
MAX_RETRIES = int(os.getenv("NOTIFY_MAX_RETRIES", "2"))
RETRY_BACKOFF_BASE = 0.5  # 0.5s → 1s → 2s

# Dedupe 窗口
_sent_log: dict[str, datetime] = {}
_DEDUPE_WINDOW = timedelta(minutes=5)


def _dedupe_check(handoff_id: str, channel: str) -> bool:
    """True 表示可以發；False 表示已於窗口內發過。"""
    key = f"{handoff_id}:{channel}"
    now = datetime.utcnow()
    last = _sent_log.get(key)
    if last and (now - last) < _DEDUPE_WINDOW:
        return False
    _sent_log[key] = now
    # 清理過期 log（每次檢查時順便清理，防止記憶體膨脹）
    expired = [k for k, v in _sent_log.items() if (now - v) > _DEDUPE_WINDOW * 2]
    for k in expired:
        _sent_log.pop(k, None)
    return True


def _retry(fn, *args, max_retries=MAX_RETRIES, **kwargs) -> bool:
    """通用重試包裝器：失敗後 exponential backoff 重試。"""
    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **kwargs)
            if result:
                return True
        except Exception as e:
            logger.warning(f"Retry {attempt+1}/{max_retries+1} failed: {e}")
        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
    return False


def _build_advisor_message(handoff: dict) -> str:
    """組對輔睡員的通知文字（含摘要 + 建議話術）。"""
    lines = [
        f"🌙 需要接手｜{handoff.get('priority', 'P1')}｜{handoff.get('signal_type', '')}",
        f"原因：{handoff.get('reason', '—')}",
        "",
        f"顧客：{handoff.get('customer_display', '未綁定')}",
        f"意圖：{handoff.get('intent', 'CHAT')}",
    ]
    journey = handoff.get("customer_journey") or {}
    if journey:
        tried = journey.get("tried_products", [])
        if tried:
            lines.append(f"試過：{', '.join(tried[:5])}")
        budget = journey.get("budget")
        if budget:
            lines.append(f"預算：{budget}")
        family = journey.get("family_context")
        if family:
            lines.append(f"家庭：{family}")

    lines.extend([
        "",
        f"摘要：{handoff.get('summary', '（未產生）')}",
    ])
    if handoff.get("suggested_reply"):
        lines.extend(["", f"💡 建議話術：{handoff['suggested_reply']}"])
    if handoff.get("chatroom_url"):
        lines.extend(["", f"🔗 進入：{handoff['chatroom_url']}"])
    return "\n".join(lines)


# ============================================================
# LINE Notify（含 retry）
# ============================================================
def _send_line_notify_once(token: str, message: str) -> bool:
    if not token or not httpx:
        logger.debug("LINE Notify skipped (no token or no httpx)")
        return False
    with httpx.Client(timeout=5.0) as c:
        r = c.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message[:999]},
        )
        if r.status_code == 200:
            return True
        logger.warning(f"LINE Notify HTTP {r.status_code}: {r.text[:200]}")
        return False


def send_line_notify(token: str, message: str) -> bool:
    return _retry(_send_line_notify_once, token, message)


# ============================================================
# Slack（Block Kit 結構化卡片 + retry）
# ============================================================
def _build_slack_blocks(handoff: dict) -> dict:
    """組 Slack Block Kit 卡片（比純文字更好操作）。"""
    priority = handoff.get("priority", "P1")
    emoji = {"P0": "🚨", "P1": "🌙", "P2": "📋"}.get(priority, "📋")
    color = {"P0": "#E74C3C", "P1": "#F39C12", "P2": "#3498DB"}.get(priority, "#95A5A6")

    return {
        "text": f"{emoji} Handoff｜{priority}｜{handoff.get('signal_type', '')}",
        "attachments": [{
            "color": color,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{emoji} {priority} Handoff — {handoff.get('signal_type', '')}"}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*顧客*\n{handoff.get('customer_display', '未綁定')}"},
                        {"type": "mrkdwn", "text": f"*意圖*\n{handoff.get('intent', 'CHAT')}"},
                        {"type": "mrkdwn", "text": f"*門市*\n{handoff.get('store_name', '—')}"},
                        {"type": "mrkdwn", "text": f"*輔睡員*\n{(handoff.get('advisor') or {}).get('name', '—')}"},
                    ]
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*原因*\n{handoff.get('reason', '—')}"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*摘要*\n{handoff.get('summary', '—')[:500]}"}
                },
            ]
        }],
    }
    # 建議話術 + 連結
    if handoff.get("suggested_reply"):
        payload["attachments"][0]["blocks"].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"💡 *建議話術*\n{handoff['suggested_reply']}"}
        })
    if handoff.get("chatroom_url"):
        payload["attachments"][0]["blocks"].append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "🔗 進入對話"},
                "url": handoff["chatroom_url"],
            }]
        })
    return payload


def _send_slack_once(webhook: str, handoff: dict) -> bool:
    if not webhook or not httpx:
        return False
    payload = _build_slack_blocks(handoff)
    with httpx.Client(timeout=5.0) as c:
        r = c.post(webhook, json=payload)
        if r.status_code == 200:
            return True
        logger.warning(f"Slack HTTP {r.status_code}: {r.text[:200]}")
        return False


def send_slack(webhook: str, handoff: dict) -> bool:
    return _retry(_send_slack_once, webhook, handoff)


# ============================================================
# Omnichat 旗標（把聊天室標為 待人工 + retry）
# ============================================================
def _flag_omnichat_once(line_uid: str, reason: str, priority: str) -> bool:
    if not OMNICHAT_API_KEY or not httpx:
        logger.debug(f"Omnichat flag simulated: {line_uid} ({reason})")
        return True  # 非致命，允許繼續
    with httpx.Client(timeout=5.0) as c:
        r = c.post(
            f"{OMNICHAT_BASE}/v1/conversations/flag",
            headers={"Authorization": f"Bearer {OMNICHAT_API_KEY}"},
            json={
                "line_uid": line_uid,
                "status": "pending_human",
                "priority": priority,
                "note": f"AI 偵測：{reason}",
            },
        )
        return r.status_code in (200, 201, 204)


def flag_omnichat(line_uid: str, reason: str, priority: str) -> bool:
    return _retry(_flag_omnichat_once, line_uid, reason, priority)


# ============================================================
# Email（SMTP，夜間 / P0 備援）
# ============================================================
def send_email(handoff: dict) -> bool:
    """透過 SMTP 發送 handoff 通知 Email。"""
    if not EMAIL_ENABLED or not SMTP_HOST or not EMAIL_TO_HQ:
        logger.debug("Email skipped (not configured)")
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        priority = handoff.get("priority", "P1")
        subject = f"[{priority}] 大島樂眠 Handoff — {handoff.get('signal_type', '')}｜{handoff.get('customer_display', '')}"

        body = _build_advisor_message(handoff)
        body += f"\n\n---\nHandoff ID: {handoff.get('handoff_id', '')}"
        body += f"\n建立時間：{handoff.get('created_at', '')}"

        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO_HQ
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        recipients = [e.strip() for e in EMAIL_TO_HQ.split(",") if e.strip()]
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        logger.info(f"Email sent for handoff {handoff.get('handoff_id')} to {EMAIL_TO_HQ}")
        return True
    except Exception as e:
        logger.warning(f"Email failed: {e}")
        return False


# ============================================================
# 主派發介面
# ============================================================
def dispatch(handoff: dict) -> dict:
    """
    按優先級派發通知。
    回傳每個通道的結果 {channel: bool}。
    """
    priority = handoff.get("priority", "P1")
    handoff_id = handoff.get("handoff_id", "unknown")
    results = {"line_notify": False, "slack": False, "omnichat": False, "email": False}

    # Omnichat 一律發（顧客端需要看到「待人工」狀態）
    if _dedupe_check(handoff_id, "omnichat"):
        results["omnichat"] = flag_omnichat(
            line_uid=handoff.get("line_uid", ""),
            reason=handoff.get("reason", ""),
            priority=priority,
        )

    # LINE Notify（P0 + P1）
    if priority in ("P0", "P1") and _dedupe_check(handoff_id, "line_notify"):
        token = handoff.get("notify_group") or LINE_NOTIFY_DEFAULT
        if token:
            msg = _build_advisor_message(handoff)
            results["line_notify"] = send_line_notify(token, msg)

    # Slack（僅 P0 — 給管理層監控）
    if priority == "P0" and SLACK_WEBHOOK and _dedupe_check(handoff_id, "slack"):
        results["slack"] = send_slack(SLACK_WEBHOOK, handoff)

    # Email（P0 全發 + 離線時段 P1 也發）
    if priority == "P0" and _dedupe_check(handoff_id, "email"):
        results["email"] = send_email(handoff)
    elif priority == "P1" and handoff.get("target_type") == "offline":
        if _dedupe_check(handoff_id, "email"):
            results["email"] = send_email(handoff)

    logger.info(f"Dispatch {handoff_id} ({priority}): {results}")
    return results
