# 訂單狀態對照表｜order-status-mapping.md

> 來源：https://developer.shopline.com/docs/admin-rest-api/order/order-management/get-orders/

## 訂單狀態 (status)

| API 值 | 中文 | 輔睡員怎麼說 |
|--------|------|-------------|
| open | 處理中 | 「你的訂單正在處理中」 |
| confirmed | 已確認 | 「訂單已經確認了」 |
| completed | 已完成 | 「這筆訂單已經完成囉」 |
| cancelled | 已取消 | 「這筆訂單已經被取消了」 |

## 付款狀態 (financial_status)

| API 值 | 中文 | 輔睡員怎麼說 |
|--------|------|-------------|
| unpaid | 未付款 | 「這筆訂單還沒付款喔」 |
| authorized | 已授權 | 「付款已經授權，正在處理中」 |
| pending | 付款處理中 | 「付款正在處理中，再等一下下」 |
| partially_paid | 部分付款 | 「這筆訂單有部分金額已付款」 |
| paid | 已付款 | 「已經付款完成了」 |
| partially_refunded | 部分退款 | 「有部分金額已經退回」 |
| refunded | 已退款 | 「款項已經全部退回了」 |

## 出貨狀態 (fulfillment_status)

| API 值 | 中文 | 輔睡員怎麼說 |
|--------|------|-------------|
| unshipped | 尚未出貨 | 「還在準備中，還沒出貨」 |
| partial | 部分出貨 | 「有一部分商品已經出貨了」 |
| shipped | 已出貨 | 「已經出貨了！」 |

## 出貨單狀態 (fulfillment_orders.status)

| API 值 | 中文 |
|--------|------|
| open | 待出貨 |
| in_progress | 處理中 |
| submitted | 已提交 |
| accepted | 已接受 |
| closed | 已完成 |
| cancelled | 已取消 |
| request_declined | 被退回 |

## AI 回覆組合範例

### 已付款但尚未出貨
「你的訂單 #LF2026001 已經付款完成了。目前還在製作中，預計 14~30 個工作天出貨。完成後會有專人跟你約配送時間喔！」

### 已出貨
「你的訂單 #LF2026001 已經出貨了！追蹤碼是 [追蹤碼]，你可以用這組號碼查詢物流進度。配送前一天司機會打電話跟你確認時間。」

### 未付款
「你的訂單 #LF2026001 目前還沒付款喔。需要我幫你確認付款方式嗎？」
