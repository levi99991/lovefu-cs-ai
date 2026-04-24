# Shopline 權限對照表｜shopline-scopes.md

> 來源：https://developer.shopline.com/docs/apps/api-instructions-for-use/access-scope/

## ✅ AI 輔睡員需要的權限（全部是 read）

| Scope | 說明 | 可查詢的 API |
|-------|------|-------------|
| `read_orders` | 訂單查詢 | GET /orders.json、GET /orders/{id}.json |
| `read_customers` | 會員查詢 | GET /customers.json、GET /customers/{id}.json |
| `read_products` | 商品查詢 | GET /products.json、GET /products/{id}.json |
| `read_assigned_fulfillment_orders` | 出貨查詢 | GET /fulfillment_orders |
| `read_shipping` | 配送查詢 | GET /pickup/list.json |
| `read_inventory` | 庫存查詢 | GET /inventory_levels.json |

## ❌ 絕對不申請的權限（全部是 write 或不相關的 read）

### write 權限（共 28 個，全部跳過）

- write_orders — 修改訂單、退款、取消、風險管理
- write_assigned_fulfillment_orders — 出貨操作、取消出貨
- write_gift_card — 禮品卡操作
- write_inventory — 庫存修改
- write_product_sizechart — 尺寸表修改
- write_discounts — 折扣碼管理
- write_shipping — 運費、物流商設定
- write_markets — 市場設定
- write_product_variant_images — 商品圖片修改
- write_return — 退貨操作
- write_checkouts — 結帳操作
- write_translation — 翻譯修改
- write_page — 頁面修改
- write_draft_orders — 草稿訂單
- write_themes — 佈景主題修改
- write_products — 商品修改
- write_customers — 會員資料修改
- write_files — 檔案上傳
- write_product_listings — 商品上架
- write_fulfillment_service — 物流服務商設定
- write_publications — 發布管理
- write_store_information — 商店設定修改
- write_payment_gateways — 金流設定
- write_script_tags — Script 注入
- write_bulkoperation — 批次操作
- write_content — 內容修改
- write_selling_plan_group — 訂閱方案
- write_price_rules — 價格規則
- write_shop_policy — 商店政策
- write_marketing_event — 行銷事件
- write_subscription_contracts — 訂閱合約

### 不相關的 read 權限（不需要申請）

- read_page — 頁面讀取（AI 不需要）
- read_themes — 佈景主題（AI 不需要）
- read_script_tags — Script 標籤（AI 不需要）
- read_translation — 翻譯（AI 不需要）
- read_content — 內容（AI 不需要）
- read_draft_orders — 草稿訂單（AI 不需要）
- read_publications — 發布（AI 不需要）
- read_store_information — 商店設定（AI 不需要）
- read_store_staff — 員工（AI 不需要）
- read_store_log — 商店日誌（AI 不需要）
- read_store_metrics — 商店數據（AI 不需要）
- read_marketing_event — 行銷事件（AI 不需要）
- read_data_report — 報表（AI 不需要）
- read_bulkoperation — 批次操作（AI 不需要）
- read_price_rules — 價格規則（AI 不需要）
- read_selling_plan_group — 訂閱方案（AI 不需要）
- read_subscription_contracts — 訂閱合約（AI 不需要）
- read_payment — 金流（AI 不需要）
- read_gift_card — 禮品卡（AI 不需要）
- read_product_listings — 商品上架（AI 不需要）
- read_product_sizechart — 尺寸表（AI 不需要）
- read_product_variant_images — 變體圖片（AI 不需要）
- read_markets — 市場（AI 不需要）
- read_shop_policy — 商店政策（AI 不需要）
- read_returns — 退貨紀錄（AI 不需要，退貨由人工處理）
- read_location — 地點（AI 不需要）
- read_discounts — 折扣碼（AI 不需要，活動資訊從知識庫取得）
- read_fulfillment_service — 物流服務商（AI 不需要）

## 最小權限原則

只申請 AI 實際會用到的 6 個 read scope。
即使未來可能用到，也不要預先申請——需要時再加。
