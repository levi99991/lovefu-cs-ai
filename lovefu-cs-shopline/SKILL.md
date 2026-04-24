---
name: lovefu-cs-shopline
description: >
  透過 Shopline Admin REST API 查詢訂單、會員、商品資料，
  並將原始 API 回應轉換為 AI 輔睡員可讀的自然語言摘要。
  所有 API 呼叫經過 cs-guard 安全閘門，嚴格唯讀。
  當顧客查詢訂單（「我的東西寄了嗎」「訂單到哪了」「出貨進度」），
  查詢會員等級/點數，提供訂單編號或手機號碼要查資料，
  退換貨時需要確認訂單資訊，或任何涉及 Shopline 後台資料的查詢時，
  必定使用此 skill。
---

# Shopline 訂單與會員查詢

## 總覽

此 Skill 提供四個查詢能力，全部是 GET only：

| 能力 | 函式 | 說明 |
|------|------|------|
| 查訂單 | `query_orders()` | 用訂單編號/手機/Email/會員ID 搜尋訂單 |
| 查會員 | `query_customer()` | 用會員 ID 查詢等級、點數、基本資料 |
| 查出貨 | `query_fulfillment()` | 用訂單 ID 查詢出貨狀態和追蹤碼 |
| 查商品 | `query_product()` | 用商品 ID 查詢名稱和規格（輔助用） |

所有函式內部呼叫 `cs-guard` 的 `shopline_safe_get()`，不直接打 API。

---

## 身份識別流程

### 顧客從 LINE 發訊息進來時

```
LINE UID 進來
    │
    ├→ Omnichat 已綁定？ → 直接取得 Shopline 會員 ID → 查詢
    │
    └→ 未綁定？ → AI 詢問手機或 Email
                      │
                      ├→ 顧客提供 → 用 search_content 查 orders
                      │                或用 email 查 customers
                      │
                      └→ 顧客不提供 → 只能做一般諮詢，無法查訂單
```

### 引導話術

未綁定時：
「為了幫你查訂單，方便告訴我你下單時用的手機號碼或 Email 嗎？」

顧客不願提供時：
「沒問題！如果之後需要查詢，隨時跟我說就好。還有什麼其他可以幫你的嗎？」

---

## 訂單查詢 — query_orders

### API 呼叫

```
GET /orders.json
```

### 常用查詢參數

| 參數 | 用途 | 範例 |
|------|------|------|
| search_content | 模糊搜尋（訂單號/手機/快遞單號/商品名/SKU/金額） | D2034 |
| name | 精確查訂單編號 | D2034 |
| email | 用 Email 查 | che@gmail.com |
| buyer_id | 用會員 ID 查 | 4201057495 |
| status | 訂單狀態篩選 | open / cancelled / any |
| financial_status | 付款狀態篩選 | paid / unpaid / refunded |
| fulfillment_status | 出貨狀態篩選 | shipped / unshipped / partial |
| limit | 回傳筆數（最多 100） | 5 |

### 查詢策略

1. 如果有訂單編號 → 用 `name` 精確查
2. 如果有會員 ID（Omnichat 帶過來）→ 用 `buyer_id` 查
3. 如果只有手機/Email → 用 `search_content` 模糊查
4. 預設 limit=5，只抓最近 5 筆，避免資訊過多

### 回應格式化

把 API 回傳的 JSON 轉成輔睡員口吻的自然語言：

```
API 回傳：
{
  "name": "#LF2026001",
  "status": "open",
  "financial_status": "paid",
  "fulfillment_status": "unshipped",
  "total_price": "14900.00",
  "created_at": "2026-03-15T10:30:00+08:00",
  "line_items": [{"title": "山丘樂眠床 5尺", "quantity": 1}]
}

格式化為：
「訂單 #LF2026001 — 山丘樂眠床 5尺
　下單日期：3/15
　付款狀態：已付款
　出貨狀態：尚未出貨
　金額：NT$14,900」
```

腳本 → `scripts/query_orders.py`
狀態碼對照 → `references/order-status-mapping.md`

---

## 會員查詢 — query_customer

### API 呼叫

```
GET /customers/{id}.json
```

### 有用的回傳欄位

| 欄位 | 說明 |
|------|------|
| first_name / last_name | 姓名 |
| email | Email（需遮蔽） |
| phone | 手機（需遮蔽） |
| orders_count | 歷史訂單數 |
| total_spent | 累積消費金額 |
| tags | 標籤（可能包含會員等級） |
| created_at | 會員註冊日期 |

### 海獺等級判斷

Shopline 不一定有「會員等級」欄位，需要從 `total_spent` 推算：

| 累積消費 | 等級 |
|---------|------|
| < NT$80,001 | 初入島的海獺 |
| NT$80,001 ~ NT$120,000 | 睡薄墊的海獺（終身 96 折） |
| > NT$120,000 | 癱 LOVEFU 床的海獺（終身 94 折） |

或者 Omnichat 已經同步了會員等級標籤，直接使用即可。

---

## 出貨查詢 — query_fulfillment

### API 呼叫

```
GET /fulfillment_orders/{order_id}/fulfillment_orders.json
```

### 有用的回傳欄位

| 欄位 | 說明 |
|------|------|
| status | 出貨狀態 |
| tracking_number | 物流追蹤碼 |
| tracking_company | 物流公司 |
| fulfill_at | 出貨時間 |
| display_status | 顯示狀態（取貨場景用） |

### 與 cs-logistics 的銜接

如果這裡拿到了 tracking_number → 傳給 `cs-logistics` 查更即時的物流狀態。
如果還沒有 tracking_number → 代表尚未出貨，不需要查物流。

---

## 訂單狀態翻譯

API 回傳的是英文，AI 回覆顧客要用中文：

完整對照表 → `references/order-status-mapping.md`

快速參考：

| 英文 | 中文 |
|------|------|
| open | 處理中 |
| cancelled | 已取消 |
| paid | 已付款 |
| unpaid | 未付款 |
| partially_paid | 部分付款 |
| refunded | 已退款 |
| partially_refunded | 部分退款 |
| shipped | 已出貨 |
| unshipped | 尚未出貨 |
| partial | 部分出貨 |

---

## 異常處理

| 狀況 | AI 回覆 |
|------|--------|
| 查無訂單 | 「查不到這筆訂單耶，你確認一下訂單編號？或者用手機號碼試試看。」 |
| 查無會員 | 「找不到這個帳號的紀錄。你是用哪個手機號碼或 Email 註冊的呢？」 |
| API 錯誤 | 「系統暫時有點忙，我幫你轉接輔睡員查詢喔！」（轉人工） |
| 多筆訂單 | 只顯示最近 3 筆，問：「你要查的是哪一筆呢？」 |

---

## 檔案索引

```
lovefu-cs-shopline/
├── SKILL.md                          # 本檔案
├── scripts/
│   ├── query_orders.py               # 訂單查詢 + 格式化
│   ├── query_customer.py             # 會員查詢 + 格式化
│   ├── query_fulfillment.py          # 出貨查詢 + 格式化
│   └── format_response.py            # 共用格式化工具
└── references/
    ├── shopline-api-reference.md      # 常用 API endpoint 參數速查
    ├── order-status-mapping.md        # 訂單狀態英中對照表
    └── omnichat-identity.md           # Omnichat 會員身份對應邏輯
```
