# Make.com Scenario 設定指南｜make-setup-guide.md

## 架構：路徑 1（LINE → Omnichat → Make → 後端）

```
LINE 顧客
    │
    ▼
Omnichat（綁定會員身份）
    │
    ▼ Webhook
Make.com Scenario
    │
    ├→ HTTP POST /chat（打後端）
    │
    ├→ 收到回覆
    │
    └→ Router
         ├→ need_human = false → LINE Send Reply
         └→ need_human = true  → LINE Reply + 通知人工
```

---

## Scenario 1：主對話流程

### Module 1：Custom Webhook（觸發器）

設定 Omnichat Webhook 指向此 URL。
接收的資料格式：

```json
{
  "line_uid": "U1234567890abcdef",
  "message": "我的床墊什麼時候會到？",
  "member_name": "王小明",
  "member_id": "cust_abc123",
  "member_tier": "睡薄墊的海獺",
  "reply_token": "xxxxxxx",
  "timestamp": "2026-04-07T14:30:00+08:00"
}
```

### Module 2：HTTP — Make a Request

- URL：`https://你的後端.railway.app/chat`
- Method：POST
- Body type：JSON
- Headers：`Content-Type: application/json`
- Timeout：30 秒
- Body：

```json
{
  "line_uid": "{{1.line_uid}}",
  "message": "{{1.message}}",
  "member_name": "{{1.member_name}}",
  "member_id": "{{1.member_id}}",
  "member_tier": "{{1.member_tier}}",
  "timestamp": "{{1.timestamp}}"
}
```

### Module 3：Router（分支判斷）

條件 1：`{{2.body.need_human}}` = false
條件 2：`{{2.body.need_human}}` = true

### Module 4a：LINE — Send a Reply Message（正常回覆）

- Reply Token：`{{1.reply_token}}`
- Message type：Text
- Text：`{{2.body.reply}}`

### Module 4b：LINE — Send a Reply Message（轉接回覆）

- Reply Token：`{{1.reply_token}}`
- Message type：Text
- Text：`{{2.body.reply}}`

接著：

### Module 5b：LINE Notify / Slack（通知人工）

通知內容：
```
🔔 需要人工接手

顧客：{{1.member_name}}
LINE UID：{{1.line_uid}}
意圖：{{2.body.intent}}
原因：{{2.body.human_reason}}
最後訊息：{{1.message}}
```

通知管道選擇：
- LINE Notify → 通知到客服群組
- Slack Webhook → 通知到 Slack 頻道
- 或兩者都發

---

## Scenario 2：錯誤處理

在 Module 2（HTTP）後面加一個 Error Handler：

如果後端回傳非 200 或 timeout：
1. LINE Reply：「系統暫時有點忙，讓我幫你轉接輔睡員喔！」
2. 通知人工（同 Module 5b）

---

## Scenario 3：出貨通知推播（選配）

```
Shopline Webhook: 訂單出貨
    │
    ▼
Make Webhook（接收 Shopline 事件）
    │
    ▼
HTTP: 查詢訂單詳情（用後端 or 直接打 Shopline API）
    │
    ▼
LINE: Push Message
    「你的訂單 #XXX 已經出貨了！追蹤碼是 XXX。
     配送前一天司機會打電話確認時間喔～」
```

設定方式：
1. 在 Shopline 後台建立 Webhook 訂閱「Fulfillment created」事件
2. Webhook URL 指向 Make.com 的 Custom Webhook
3. Make 收到後用 LINE Push Message 通知顧客

---

## 方案注意事項

### Make 方案選擇

| 每日對話量 | 每月操作數 | 建議方案 | 月費 |
|-----------|----------|---------|------|
| < 50 則 | ~7,500 | Pro | US$59 |
| 50~100 則 | ~15,000 | Teams | US$89 |
| > 100 則 | > 15,000 | Teams 或 Enterprise | US$89+ |

計算方式：每則對話 ≈ 5 次操作（Webhook + HTTP + Router + Reply + 可能的通知）

### Timeout 設定

- HTTP Module timeout 設 30 秒
- 後端 LLM 呼叫在 30 秒內完成
- 如果 GPT-4o 偶爾超時 → Error Handler 兜底

### 測試流程

1. 先用 Make 的 Webhook 手動傳測試資料
2. 確認後端回傳格式正確
3. 接上 Omnichat Webhook
4. 用自己的 LINE 帳號測試完整流程
5. 邀請 2~3 位同事當白老鼠測試
6. 逐步開放給真實顧客
