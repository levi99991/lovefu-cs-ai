# Omnichat 共存機制 — AI/真人雙軌客服

## 設計目標

- AI 預設先回，承擔 80% 的常規問答（產品、訂單、體驗店、會員）
- 真人輔睡員可在「任何時間點」插手接管，AI 立刻閉嘴
- 接管後一段時間內（預設 30 分鐘 / 24 小時），AI 完全靜默
- 真人按下「交回給 AI」後立即恢復

## 三種介入時機

### 1. 真人在 Omnichat 後台手動回覆訊息
→ Make.com 偵測到「後台 outbound 事件」→ 帶 `omnichat_event: "agent_replied"` 呼叫 `/chat` → AI 靜默 30 分鐘

### 2. 真人按下「接手」按鈕
→ Make.com 偵測到 takeover webhook → 帶 `omnichat_event: "agent_takeover"` 呼叫 `/chat` → AI 靜默 24 小時

### 3. 真人按下「交回給 AI」
→ Make.com 帶 `omnichat_event: "agent_release"` → AI 立即恢復

## 流程圖

```
顧客傳訊
   │
   ▼
Make.com 收到 webhook
   │
   ├─ 是 outbound（真人剛回了）？  ──Y──▶ omnichat_event=agent_replied
   ├─ 是 takeover 事件？           ──Y──▶ omnichat_event=agent_takeover
   ├─ 是 release 事件？            ──Y──▶ omnichat_event=agent_release
   └─ 都不是                       ────▶ omnichat_event=null
   │
   ▼
POST /chat
   │
   ▼
omnichat_coexist.check_should_mute(line_uid, event)
   │
   ├─ event=agent_replied   → 設 mute 30min  → return (True, "agent_just_replied")
   ├─ event=agent_takeover  → 設 mute 24h    → return (True, "agent_takeover")
   ├─ event=agent_release   → 清除 mute      → return (False, None) → 進入正常流程
   └─ event=null            → 檢查 mute 狀態
                                 ├─ 在 mute 中 → return (True, "still_in_mute_window")
                                 └─ 不在      → return (False, None)
   │
   ▼
brain 收到 should_mute=True
   │
   ▼
ChatResponse(reply="", silent=True, silent_reason="...")
   │
   ▼
Make.com 看到 silent=true → 跳過 LINE Reply 動作（只記日誌）
```

## Make.com Scenario 設定

### Module 1: Omnichat Webhook
監聽兩條 stream：
- `messages.created`（顧客傳訊）
- `conversations.updated`（真人接管 / 釋放 / 真人回覆）

### Module 2: Router 分流
判斷 webhook 類型：
- `event.type === "message.in"` → 顧客訊息，轉 Module 3
- `event.type === "message.out" && event.actor === "agent"` → 真人剛回了，轉 Module 4
- `event.action === "takeover"` → 轉 Module 5
- `event.action === "release"` → 轉 Module 6

### Module 3-6: HTTP POST 到 /chat
所有路徑都 POST 到同一個 `/chat`，差別只在 `omnichat_event`：

```json
// Module 3 (顧客訊息)
{
  "line_uid": "{{event.line_uid}}",
  "message": "{{event.message.text}}",
  "member_name": "{{event.contact.name}}",
  "member_id": "{{event.contact.shopline_id}}",
  "omnichat_event": null
}

// Module 4 (真人回覆)
{
  "line_uid": "{{event.line_uid}}",
  "message": "",
  "omnichat_event": "agent_replied"
}

// Module 5 (接管)
{
  "line_uid": "{{event.line_uid}}",
  "message": "",
  "omnichat_event": "agent_takeover"
}

// Module 6 (釋放)
{
  "line_uid": "{{event.line_uid}}",
  "message": "",
  "omnichat_event": "agent_release"
}
```

### Module 7: Router 處理 /chat 回應
- `silent === true` → 不做事
- `need_human === true` → 通知真人輔睡員（LINE Notify / Slack）
- 其他 → LINE Reply 把 `reply` 字串發回顧客

## 為什麼不用「LINE 已讀」或「打字中」訊號？

LINE Messaging API 沒有可靠的「真人正在打字」事件。必須仰賴 Omnichat 主動發出的 webhook 才能判斷。如果 Omnichat 設定不完整，可能出現「真人和 AI 同時回覆」的情況——但這比「AI 完全不回」風險低得多，所以採取「保守靜默」設計。

## 監控與調整

```bash
# 查詢某顧客的 mute 狀態
curl https://your-app.railway.app/mute/U1234567890abcdef

# 強制解除某顧客的 mute（ops 用）
curl -X DELETE https://your-app.railway.app/mute/U1234567890abcdef
```

預設值若不夠用，調整 `.env`：
```
MUTE_AFTER_AGENT_REPLY_MIN=15      # 真人回覆後改為 15 分鐘
MUTE_AFTER_TAKEOVER_HOURS=8        # 接管後改為 8 小時
```

## 邊界情境

| 情境 | 系統行為 |
|---|---|
| 真人回了 → 30 分鐘內顧客又傳訊 | 仍 mute，AI 不回（除非真人也回） |
| 真人回了 → 31 分鐘後顧客又傳訊 | mute 已過期，AI 自動恢復回應 |
| 接管中 → 顧客傳「我要客訴」 | 仍 mute（既然真人接管，由真人處理客訴） |
| 接管中 → 真人忘了 release | 24 小時自動 release |
| 重啟服務（dict 模式） | mute 狀態清空 = AI 全部恢復回應（這是 dict 模式的已知限制，生產環境請用 redis） |
