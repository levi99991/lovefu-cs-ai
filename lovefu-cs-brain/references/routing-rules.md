# 路由規則｜routing-rules.md

## 完整路由表

| 意圖 | LLM 模型 | 調取資料 | 載入知識庫 | 轉人工？ |
|------|---------|---------|----------|--------|
| ORDER | gpt-4o-mini | cs-shopline 訂單 + cs-logistics 物流 | service-policy.md | 否 |
| PRODUCT | gpt-4o | 無 | products-*.md（按關鍵字） | 否 |
| SLEEP | gpt-4o | 無 | sleep-science.md + products-*.md | 否 |
| RETURN | gpt-4o | 無（AI 只收集資訊） | service-policy.md | 是（收集完後） |
| COMPLAINT | 不經 LLM | 無 | 無 | 是（立即） |
| STORE | gpt-4o-mini | 無 | store-info.md | 否 |
| MEMBER | gpt-4o-mini | cs-shopline 會員 | member-program.md | 否 |
| CHAT | gpt-4o-mini | 無 | 無 | 否 |

## ORDER 路由細節

```
1. 有 member_id？
   └→ 是 → query_orders_by_buyer(member_id) → 格式化
   └→ 否 → 繼續 ↓

2. 訊息中有訂單編號（#LF2026001 或 D2034）？
   └→ 是 → query_orders_by_name(order_name) → 格式化
   └→ 否 → 繼續 ↓

3. 訊息中有手機（09xxxxxxxx）或 Email？
   └→ 是 → query_orders_by_search(search) → 格式化
   └→ 否 → AI 詢問顧客提供訂單編號或手機

4. 查到訂單後，有追蹤碼？
   └→ 是 → cs-logistics 查即時物流（如果 cs-logistics 已上線）
   └→ 否 → 告知「尚未出貨」+ 預計時間
```

## PRODUCT 路由細節

```
1. 訊息包含「床墊」「山丘」「冰島」「飄雲」「無光」？
   └→ 載入 products-mattress.md

2. 訊息包含「枕頭」「月眠」「側睡枕」？
   └→ 載入 products-pillow.md

3. 訊息包含「床架」「沙發」「棉被」「床包」「竹眠」？
   └→ 載入 products-other.md

4. 都不包含？
   └→ 預設載入 products-mattress.md（最高頻）

5. 涉及多類產品（如「床墊配床架」）？
   └→ 載入兩個 reference（上限 2 個）
```

## RETURN 路由細節

```
1. 載入 service-policy.md
2. AI 進入「收集模式」：
   - 確認訂單編號
   - 確認退換貨原因
   - 確認包裝/配件是否完整
3. 資訊收集完畢
4. 回傳 need_human: true
5. Make.com 通知人工輔睡員接手
```

## 轉人工的三種路徑

```
路徑 A：關鍵字攔截（Step 1）
  「找主管」「消保官」「找真人」
  → 立即轉接，不經 LLM

路徑 B：意圖為 COMPLAINT（Step 3）
  情緒激動的訊息經 LLM 分類為 COMPLAINT
  → 回覆安撫話術 + 轉接

路徑 C：不滿意計數器（Step 2）
  連續 2 次不滿意
  → 回覆道歉 + 轉接
```

## 模型切換的環境變數

```
LLM_MODEL_SIMPLE=gpt-4o-mini    # 日常對話
LLM_MODEL_COMPLEX=gpt-4o        # 深度諮詢
```

未來如果要換成 Gemini：
```
LLM_MODEL_SIMPLE=gemini-2.5-flash
LLM_MODEL_COMPLEX=gemini-2.5-pro
```

只需要改環境變數 + model_router.py 中的 API endpoint。
其他所有 Skill 不受影響。
