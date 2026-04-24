# API Endpoint 白名單｜allowed-endpoints.md

## 規則

只有列在此清單中的 endpoint 才能通過安全閘門。
所有 endpoint 一律只允許 GET method。

---

## Shopline Admin REST API

Base URL: `https://{handle}.myshopline.com/admin/openapi/v20260301`

### 訂單相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /orders.json | 批量查詢訂單 | read_orders |
| GET /orders/{id}.json | 查詢單筆訂單 | read_orders |
| GET /orders/{id}/transactions.json | 查詢訂單交易紀錄 | read_orders |

### 出貨相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /fulfillment_orders/fulfillment_orders_search.json | 出貨單列表 | read_assigned_fulfillment_orders |
| GET /fulfillment_orders/{order_id}/fulfillment_orders.json | 指定訂單的出貨單 | read_assigned_fulfillment_orders |

### 會員相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /customers.json | 批量查詢會員 | read_customers |
| GET /customers/{id}.json | 查詢單一會員 | read_customers |
| GET /customers/search.json | 搜尋會員 | read_customers |

### 商品相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /products.json | 批量查詢商品 | read_products |
| GET /products/{id}.json | 查詢單一商品 | read_products |
| GET /products/count.json | 商品數量 | read_products |

### 配送相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /pickup/list.json | 取貨資訊列表 | read_shipping |

### 庫存相關

| Endpoint | 用途 | Scope |
|----------|------|-------|
| GET /inventory_levels.json | 庫存查詢 | read_inventory |

---

## 物流倉儲 API（待合作夥伴提供後補入）

Base URL: `{LOGISTICS_API_BASE}`

| Endpoint | 用途 | 說明 |
|----------|------|------|
| GET /tracking/{number} | 追蹤物流狀態 | 待確認 |
| GET /shipments/{id} | 查詢出貨詳情 | 待確認 |

---

## 黑名單（明確禁止的 pattern）

以下 URL pattern 即使出現在白名單的 base path 下，也一律攔截：

- 任何包含 `cancel` 的 path
- 任何包含 `refund` 的 path
- 任何包含 `delete` 的 path
- 任何包含 `update` 的 path
- 任何包含 `create` 的 path
- 任何包含 `close` 的 path
- 任何包含 `open` 的 path（指操作類的 open，如 reopen order）
- 任何包含 `archive` 的 path
- 任何包含 `activate` 的 path

---

## 新增 endpoint 的流程

1. 確認此 endpoint 只支援 GET method
2. 確認對應的 scope 已在 Token 中申請
3. 加入此白名單文件
4. 更新 api_guard.py 的 ALLOWED_PATHS
5. 測試通過後才部署
