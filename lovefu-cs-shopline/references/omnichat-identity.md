# Omnichat 會員身份對應｜omnichat-identity.md

## 核心問題

LINE User ID 和 Shopline Customer ID 是兩套系統，沒有天然對應關係。
Omnichat 作為中介，串接了兩者。

## Omnichat 如何綁定

1. Omnichat 安裝在 Shopline 後台（全通路會員模組）
2. Omnichat 與 LINE 官方帳號連結
3. 當顧客用 LINE 快速登入 Shopline，或在 Omnichat 中完成手機綁定
4. Omnichat 將 LINE UID、手機號碼、Shopline 會員 ID 三者關聯起來
5. 之後顧客從 LINE 發訊息，Omnichat 可以帶出 Shopline 會員資料

## 關鍵技術要求

LINE Messaging API Channel 和 LINE Login Channel 必須在同一個 Provider 下。
如果不同 Provider，LINE UID 會不一致，Omnichat 無法正確對應。

## AI 輔睡員收到的資料

Make.com 從 Omnichat Webhook 帶過來的資料：

```json
{
  "line_uid": "U1234567890abcdef",
  "message": "顧客的訊息",
  "member_name": "王小明",          // Omnichat 綁定後才有
  "member_id": "cust_abc123",      // Shopline Customer ID，綁定後才有
  "member_tier": "睡薄墊的海獺",    // 如果 Omnichat 有同步等級
  "reply_token": "xxxxxxx",
  "timestamp": "2026-04-07T14:30:00+08:00"
}
```

## 三種身份狀態

### A：已綁定（最理想）

member_name 和 member_id 都有值。
AI 可以直接用 member_id 查詢 Shopline 訂單和會員資料。

### B：未綁定（需引導）

member_name 和 member_id 為空。
AI 引導顧客提供手機或 Email：

1. AI 收到手機/Email
2. 用 cs-shopline 的 search_customers() 或 query_orders_by_search() 比對
3. 如果找到 → 記錄映射關係（LINE UID → Shopline ID）
4. 如果找不到 → 可能不是會員，正常服務

注意：這個「記錄映射」步驟涉及寫入操作。
在 AI 嚴格唯讀的原則下，有兩個選項：
- 選項 A：只在當次對話中暫存（存在 cs-memory），不寫入 Omnichat
- 選項 B：通知人工輔睡員在 Omnichat 後台手動綁定

建議先用選項 A，MVP 跑起來後再評估是否需要選項 B。

### C：非會員

顧客不是大島的會員，也沒有訂單。
AI 正常提供睡眠諮詢和產品資訊，不需要身份對應。

## Omnichat API 呼叫上限提醒

Shopline API 有呼叫上限。避免從 Shopline 後台手動一次大量操作，
以免資料無法即時同步到 Omnichat。
同理，AI 的查詢頻率也要控制在合理範圍內。
