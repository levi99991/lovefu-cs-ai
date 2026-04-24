# Shopline API 速查表｜shopline-api-reference.md

> 來源：https://developer.shopline.com/docs/admin-rest-api/

## Base URL

```
https://{handle}.myshopline.com/admin/openapi/{version}
```

- handle：商店識別碼（網域前綴）
- version：API 版本（建議用 v20260301）

## 認證

```
Headers:
  Authorization: Bearer {access_token}
  Content-Type: application/json; charset=utf-8
```

## 分頁機制

- 預設每頁 50 筆，最多 100 筆（用 `limit` 參數控制）
- 下一頁用 response header 中的 `link` 取得 `page_info` 值
- 不支援 offset 分頁，只支援 cursor-based

## 常用 Endpoint 速查

### GET /orders.json

| 參數 | 說明 |
|------|------|
| search_content | 模糊搜尋（訂單號/手機/快遞號/商品名/SKU/金額） |
| name | 訂單編號（精確） |
| email | 買家 Email |
| buyer_id | 買家 ID |
| status | open / cancelled / any |
| financial_status | paid / unpaid / refunded / partially_refunded |
| fulfillment_status | shipped / unshipped / partial |
| created_at_min / created_at_max | 建立時間範圍（ISO 8601） |
| updated_at_min / updated_at_max | 更新時間範圍（ISO 8601） |
| limit | 筆數上限（max 100） |
| sort_condition | 排序（如 order_at:desc） |
| fields | 指定回傳欄位（逗號分隔） |

### GET /customers/{id}.json

| 參數 | 說明 |
|------|------|
| fields | 指定回傳欄位 |

### GET /customers/search.json

| 參數 | 說明 |
|------|------|
| query | 搜尋關鍵字（手機/Email/姓名） |
| limit | 筆數上限 |

### GET /fulfillment_orders/{order_id}/fulfillment_orders.json

無額外查詢參數，直接用 order_id。

### GET /products.json

| 參數 | 說明 |
|------|------|
| ids | 商品 ID（逗號分隔，最多 100） |
| title | 商品名稱 |
| limit | 筆數上限 |

### GET /pickup/list.json

| 參數 | 說明 |
|------|------|
| language | 語言 |

### GET /inventory_levels.json

| 參數 | 說明 |
|------|------|
| inventory_item_ids | 庫存品項 ID |
| location_ids | 倉庫位置 ID |

## Rate Limit

Shopline API 有呼叫頻率限制（具體數值依方案不同）。
建議每次對話最多呼叫 3 次 API，避免觸發限流。
