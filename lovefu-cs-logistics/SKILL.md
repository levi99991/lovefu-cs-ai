---
name: lovefu-cs-logistics
description: 大島樂眠（LoveFu）AI 輔睡員的 WMS 暢流物流系統整合。透過 WMS Admin REST API（lovefu.wms.changliu.com.tw）查詢訂單出貨狀態、貨態 timeline、即時庫存、門市清單。所有 API 呼叫經過 cs-guard.wms_safe_get() 嚴格唯讀，POST 端點 100% 黑名單。內建 AES-128-ECB 解密 + 立即遮罩機制處理 receiver_name/receiver_phone。當顧客問「我的東西寄了嗎」「貨到哪了」「哪間門市自取」「現在還有貨嗎」時必定使用此 skill。
---

# lovefu-cs-logistics — WMS 暢流物流整合

## 設計原則

1. **絕對唯讀**：本 skill 沒有 `wms_safe_post`，從根上排除寫入。
2. **白名單嚴格**：只允許 6 個 WMS GET 端點。任何不在白名單的路徑直接拒絕。
3. **PII 立即遮罩**：WMS 回傳的 AES-128-ECB 加密欄位必須先解密、立即遮罩，禁止解密明文進入 LLM。
4. **時間戳標註**：WMS 貨態非即時，所有貨態回應必須附「最後更新時間」供 LLM 加註提醒。
5. **Token 快取**：BasicAuth → JWT（1 小時），用 module-level dict 快取，提前 5 分鐘換新。

## 端點對照（白名單）

| 端點 | 用途 | 限制 |
|---|---|---|
| `GET /api_v1/token/authorize.php` | 換 JWT | 內部用 |
| `GET /api_v1/order/order_query.php` | 查訂單詳情 | 含 AES 加密欄位 |
| `GET /api_v1/order/order_logistics.php` | 查貨態 timeline | 一次 max 50 訂單，非即時 |
| `GET /api_v1/order/logistics_code` | 物流商代碼清單 | — |
| `GET /api_v1/inventory/stock_query.php` | 查庫存 | 一次 max 50 SKU |
| `GET /api_v1/pos/store.php` | 查門市清單 | — |
| `GET /api_v1/product/query` | 查產品（補強用） | 主以 Shopline 為準 |

## 端點黑名單（永不支援）

```
POST /api_v1/order/cancel        ← 取消訂單
POST /api_v1/order/pick          ← 揀貨
POST /api_v1/order/add           ← 新增訂單
POST /api_v1/logistics/apply     ← 申請物流單（花錢）
POST /api_v1/logistics/finish    ← 完成出貨
POST /api_v1/inventory/update    ← 修改庫存
POST /api_v1/inbound/add         ← 新增入庫單
POST /api_v1/pos/promotion       ← 修改促銷
```

## 環境變數

```
WMS_MODE=mock                    # mock | production
WMS_BASE_URL=https://lovefu.wms.changliu.com.tw
WMS_BASIC_AUTH_USER=             # production 必填
WMS_BASIC_AUTH_PASS=             # production 必填
WMS_PII_AES_KEY=                 # AES-128-ECB 解密 key（從 WMS 後台取得）
WMS_TOKEN_TTL_SEC=3300           # 提前 5 分鐘換新（55 分鐘）
```

## 對外函式

| 函式 | 用途 |
|---|---|
| `query_orders(order_nos: list[str])` | 查訂單詳情（已遮罩 PII） |
| `query_cargo_status(order_nos: list[str])` | 查貨態（含時間戳） |
| `query_inventory(skus: list[str])` | 查庫存（自動 chunked 50/批） |
| `query_stores()` | 查門市清單 |
| `query_logistics_codes()` | 查物流商清單 |
| `format_orders_for_llm(orders)` | 訂單轉自然語言 |
| `format_cargo_status_for_llm(timelines)` | 貨態轉自然語言（含「最後更新 X 分鐘前」） |

## 使用範例

```python
from lovefu_cs_logistics import query_cargo_status, format_cargo_status_for_llm

result = query_cargo_status(["L20260415001"])
prompt_text = format_cargo_status_for_llm(result)
# → "您的訂單 L20260415001 目前狀態：已交付物流商，預計 2026-04-17 送達。
#    最後更新：12 分鐘前（資料來源 WMS 同步快照）"
```

## 與 cs-shopline 的關係（SSOT）

| 資料 | 來源 |
|---|---|
| 付款 / 商品 / 退款 / 會員 | cs-shopline |
| 出貨 / 貨態 / 庫存 / 物流單 | **cs-logistics（本 skill）** |

cs-brain 同時呼叫兩者後，按 SSOT 規則合併資料再進 LLM。
