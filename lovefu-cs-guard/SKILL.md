---
name: lovefu-cs-guard
description: >
  大島樂眠（LoveFu）AI 輔睡員系統的安全閘門。
  確保所有外部 API 呼叫嚴格唯讀（GET only），管理 Token 權限範圍，
  記錄審計日誌，過濾個資，防止任何意外的資料寫入或洩漏。
  當 cs-shopline 或 cs-logistics 發起任何 API 呼叫時，
  必須經過此 Skill 的安全檢查。
  當使用者提到「API 安全」「權限控管」「唯讀」「資料保護」
  「個資遮蔽」「審計日誌」或任何涉及系統安全的問題時，必定使用此 skill。
---

# AI 輔睡員安全閘門

## 核心原則：三道防線

```
第一道：Token 權限（只申請 read_* scope）
第二道：程式碼層（只封裝 GET method）
第三道：審計日誌（每次呼叫都記錄）
```

任何一道被突破，其他兩道仍然擋住。三道同時失敗才會出事。

---

## 第一道防線：Shopline Token 權限

### 只申請以下 read 權限

在 Shopline 後台建立私有應用程式時，**只勾選這 6 個 scope**：

| AccessScope | 用途 | 對應 Skill |
|------------|------|-----------|
| `read_orders` | 查詢訂單狀態、付款狀態、金額 | cs-shopline |
| `read_customers` | 查詢會員資料、等級、點數 | cs-shopline |
| `read_products` | 查詢商品名稱、規格、價格 | cs-shopline |
| `read_assigned_fulfillment_orders` | 查詢出貨狀態、物流追蹤 | cs-shopline |
| `read_shipping` | 查詢配送方式、取貨資訊 | cs-shopline |
| `read_inventory` | 查詢庫存狀態 | cs-shopline |

### 絕對不勾選的權限

以下 scope 全部跳過，一個都不要勾：

`write_orders`、`write_customers`、`write_products`、`write_shipping`、
`write_inventory`、`write_discounts`、`write_checkouts`、`write_draft_orders`、
`write_return`、`write_themes`、`write_page`、`write_content`、
`write_files`、`write_marketing_event`、`write_subscription_contracts`、
`write_assigned_fulfillment_orders`、`write_gift_card`、
`write_product_listings`、`write_fulfillment_service`、
`write_publications`、`write_store_information`、
`write_payment_gateways`、`write_script_tags`、
`write_bulkoperation`、`write_translation`、
`write_markets`、`write_price_rules`、
`write_product_sizechart`、`write_product_variant_images`、
`write_selling_plan_group`、`write_shop_policy`

完整 write 權限清單 → `references/shopline-scopes.md`

---

## 第二道防線：程式碼層

### API Guard 中介層

所有外部 API 呼叫必須經過 `scripts/api_guard.py`，此中介層：

1. **只允許 GET 請求** — POST / PUT / DELETE / PATCH 全部擋掉
2. **Endpoint 白名單** — 只有列在白名單內的 URL path 才能通過
3. **參數過濾** — 移除任何可能觸發寫入的參數
4. **回應過濾** — 遮蔽個資欄位後才回傳給 LLM

使用方式：
```python
from scripts.api_guard import safe_get

# ✅ 允許 — GET 請求 + 白名單內的 endpoint
result = await safe_get("/orders.json", params={"search": "0912345678"})

# ❌ 攔截 — 非白名單的 endpoint
result = await safe_get("/orders/123/cancel.json")  # 回傳 None + 記錄警告

# ❌ 不存在 — 沒有封裝 POST/PUT/DELETE method
# safe_post() 不存在，呼叫會直接報錯
```

白名單 → `references/allowed-endpoints.md`
腳本 → `scripts/api_guard.py`

---

## 第三道防線：審計日誌

### 每次 API 呼叫記錄

```json
{
  "timestamp": "2026-04-07T14:30:00+08:00",
  "method": "GET",
  "endpoint": "/orders.json",
  "params": {"search": "****5678"},
  "status_code": 200,
  "caller": "cs-shopline.query_orders",
  "line_uid": "U1234****",
  "response_size_bytes": 2048,
  "blocked": false
}
```

### 被攔截的呼叫也記錄

```json
{
  "timestamp": "2026-04-07T14:31:00+08:00",
  "method": "POST",
  "endpoint": "/orders/123/cancel.json",
  "blocked": true,
  "block_reason": "method_not_allowed",
  "caller": "unknown"
}
```

腳本 → `scripts/audit_logger.py`

---

## 個資遮蔽規則

在 API 回應傳給 LLM 之前，必須遮蔽以下欄位：

| 欄位類型 | 遮蔽規則 | 範例 |
|---------|---------|------|
| 手機號碼 | 只顯示後 4 碼 | 0912345678 → ****5678 |
| Email | 前 3 碼 + *** + @domain | che@gmail.com → che***@gmail.com |
| 地址 | 只顯示到區/路 | 板橋區雙十路二段110號 → 板橋區雙十路 |
| 信用卡 | 完全不顯示 | **** |
| 身分證 | 完全不顯示 | **** |

### LLM 回覆中的個資

AI 回覆顧客時：
- 可以顯示顧客自己的訂單編號（完整）
- 可以顯示商品名稱和金額
- 手機和地址依上表遮蔽
- 不主動提供其他顧客的任何資訊

完整規則 → `references/data-masking-rules.md`

---

## 物流倉儲 API 安全規則

同樣適用三道防線：
- Token 只申請讀取權限
- 程式碼只封裝 GET
- 每次呼叫記錄日誌

物流 API 的 endpoint 白名單在合作夥伴提供 API 文件後補入。

---

## 異常處理

### API 呼叫失敗時

| 狀況 | 處理方式 |
|------|---------|
| HTTP 401/403（Token 無效） | 記錄錯誤，回覆「系統暫時無法查詢」，轉人工 |
| HTTP 404（查無資料） | 回覆「查不到這筆資料，確認一下訂單編號？」 |
| HTTP 429（超過限流） | 等待後重試一次，仍失敗則回覆「系統忙碌」 |
| HTTP 500（伺服器錯誤） | 記錄錯誤，回覆「系統暫時有點忙」，轉人工 |
| Timeout（超過 15 秒） | 回覆「查詢需要一點時間，稍等我一下」，重試一次 |

### 所有異常都不暴露技術細節

顧客永遠不會看到「HTTP 500」「API Error」「Token expired」這類訊息。
一律用自然語言包裝。

---

## 檔案索引

```
lovefu-cs-guard/
├── SKILL.md                          # 本檔案：安全總綱
├── scripts/
│   ├── api_guard.py                  # API 安全中介層
│   └── audit_logger.py               # 審計日誌記錄器
└── references/
    ├── shopline-scopes.md            # Shopline 完整權限對照表
    ├── allowed-endpoints.md          # API endpoint 白名單
    └── data-masking-rules.md         # 個資遮蔽規則
```
