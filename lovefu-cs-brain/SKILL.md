---
name: lovefu-cs-brain
description: >
  大島樂眠（LoveFu）AI 輔睡員系統的中控大腦。
  接收顧客訊息，協調所有其他 Skill（persona、knowledge、guard、
  shopline、logistics、memory），組裝最終 Prompt，呼叫 LLM，產出回覆。
  這是整套客服系統的唯一入口和唯一出口。
  每一則顧客訊息都經過此 Skill 處理。
  當使用者提到「AI 輔睡員」「客服系統」「對話流程」「中控」
  「Brain」「系統架構」或任何涉及大島樂眠 AI 客服整體運作的問題時，
  必定使用此 skill。
---

# AI 輔睡員中控大腦

## 總覽：六步處理流程

```
顧客從 LINE 傳訊息
    │
    ▼
Step 1 ─ 檢查轉接關鍵字（不經 LLM，即時攔截）
    │
    ▼
Step 2 ─ 載入對話記憶（cs-memory）
    │
    ▼
Step 3 ─ 意圖分類（GPT-4o mini，快速便宜）
    │
    ▼
Step 4 ─ 按意圖調取資料（cs-shopline / cs-knowledge）
    │
    ▼
Step 5 ─ 組裝 Prompt + LLM 生成回覆
    │
    ▼
Step 6 ─ 儲存記憶 + 回傳 Make.com
```

任何 Step 出錯 → 兜底回覆 + 轉人工。
永遠不讓顧客卡住。

---

## Step 1：轉接關鍵字攔截

**不經 LLM**，直接比對關鍵字清單。

命中 → 立刻回傳轉接話術 + `need_human: true`，不走後續步驟。
未命中 → 繼續 Step 2。

關鍵字清單定義在 `cs-persona/references/escalation-rules.md`。

---

## Step 2：載入對話記憶

呼叫 `cs-memory.get_memory_for_prompt(line_uid)`，取得：

- `profile_text`：顧客姓名、海獺等級、偏好標籤
- `summary_text`：舊對話壓縮摘要
- `recent_turns`：最近 10 輪對話（message list 格式）
- `dissatisfaction_count`：不滿意計數器

如果計數器 ≥ 2 → 觸發轉接，不走後續步驟。

---

## Step 3：意圖分類

用 **GPT-4o mini**（最便宜最快）做一次輕量分類。

### Prompt

```
根據以下顧客訊息，判斷意圖類型。只回傳一個類型代碼。

類型：
- ORDER：查詢訂單、出貨進度、物流追蹤
- PRODUCT：詢問產品規格、價格、差異比較
- SLEEP：睡眠困擾、選床建議、睡眠諮詢
- RETURN：退換貨、安心睡退貨、商品瑕疵
- COMPLAINT：客訴、抱怨、情緒激動
- STORE：體驗店資訊、預約、地址營業時間
- MEMBER：會員點數、等級、優惠
- CHAT：閒聊、打招呼、感謝

顧客訊息：{message}
```

### 設定
- model: `gpt-4o-mini`
- max_tokens: 10
- temperature: 0
- timeout: 10 秒

成本：每次 ~$0.00005（幾乎免費）。

意圖定義 → `references/intent-types.md`

---

## Step 4：按意圖調取資料

### 路由表

| 意圖 | 調取什麼 | 從哪裡取 |
|------|---------|---------|
| ORDER | 訂單狀態 + 出貨狀態 | cs-shopline → cs-logistics |
| PRODUCT | 對應產品的 reference 檔 | cs-knowledge |
| SLEEP | sleep-science.md + 對應產品 | cs-knowledge |
| RETURN | service-policy.md | cs-knowledge |
| COMPLAINT | 不調取，直接轉人工 | — |
| STORE | store-info.md | cs-knowledge |
| MEMBER | 會員資料 + member-program.md | cs-shopline + cs-knowledge |
| CHAT | 不調取 | — |

### ORDER 意圖的細節

1. 有 member_id → `cs-shopline.query_orders_by_buyer(member_id)`
2. 顧客提供了訂單編號 → `cs-shopline.query_orders_by_name(order_name)`
3. 顧客提供了手機/Email → `cs-shopline.query_orders_by_search(search)`
4. 都沒有 → AI 詢問：「方便提供訂單編號或手機號碼嗎？」
5. 如果查到追蹤碼 → `cs-logistics.query_tracking(tracking_number)`

### PRODUCT 意圖的細節

根據顧客提到的關鍵字決定載入哪個 reference：
- 提到「床墊」「山丘」「冰島」「飄雲」「無光」→ `products-mattress.md`
- 提到「枕頭」「月眠枕」→ `products-pillow.md`
- 提到「床架」「沙發」「棉被」「床包」「寢飾」→ `products-other.md`
- 不確定 → 載入 `products-mattress.md`（最高頻）

### RETURN 意圖的細節

1. 載入 `service-policy.md`
2. 額外指示 LLM：「顧客可能要退換貨。先確認訂單編號、退貨原因、包裝是否完整。AI 只受理不執行。收集完畢後提交人工。」
3. 收集完資訊後 → `need_human: true`

完整路由規則 → `references/routing-rules.md`

---

## Step 5：組裝 Prompt + LLM 生成

### Prompt 組裝順序

```
[1] cs-persona 人設（SKILL.md 的核心段落，約 800 tokens）

[2] 顧客 profile（從記憶取得）
    姓名：王小明
    海獺等級：睡薄墊的海獺
    偏好：仰睡、腰部敏感

[3] 歷史摘要（從記憶取得，如果有）
    顧客 4/5 曾詢問山丘床墊...

[4] cs-knowledge 知識片段（按意圖載入，1~2 個 reference）

[5] 查詢到的外部資料（Shopline 訂單、物流狀態）

[6] 回覆格式提醒
    口語化、50~120字、不用 markdown、最多問一個問題

[7] 最近對話記錄（recent_turns，最多 10 輪）

[8] 當前顧客訊息
```

### 模型選擇

| 意圖 | 模型 | 原因 |
|------|------|------|
| CHAT | gpt-4o-mini | 簡單，省錢 |
| STORE | gpt-4o-mini | 查表回答 |
| MEMBER | gpt-4o-mini | 格式化資料 |
| ORDER（純查詢） | gpt-4o-mini | 報狀態即可 |
| PRODUCT | gpt-4o | 需要精準配對推薦 |
| SLEEP | gpt-4o | 需要同理心和引導提問 |
| RETURN | gpt-4o | 複雜政策判斷 |
| COMPLAINT | 不經 LLM | 直接觸發轉接 |

### LLM 呼叫參數

```python
{
    "model": selected_model,
    "messages": assembled_messages,
    "max_tokens": 500,
    "temperature": 0.7,    # 一點變化讓回覆自然
    "timeout": 30,
}
```

腳本 → `scripts/prompt_assembler.py`、`scripts/model_router.py`

---

## Step 6：儲存記憶 + 回傳

1. `cs-memory.save_turn(line_uid, "user", 顧客訊息)`
2. `cs-memory.save_turn(line_uid, "assistant", AI 回覆)`
3. `cs-memory.update_profile(line_uid, intent=意圖, preferences=提取的偏好)`
4. 回傳 JSON 給 Make.com：

```json
{
  "reply": "AI 回覆文字",
  "need_human": false,
  "human_reason": null,
  "intent": "PRODUCT"
}
```

Make.com 收到後：
- `need_human: false` → LINE Send Reply
- `need_human: true` → LINE Reply + 通知人工

---

## 兜底機制

```python
try:
    # Step 1~6 正常流程
except Exception as e:
    # 任何錯誤都不讓顧客卡住
    return {
        "reply": "不好意思，系統暫時有點忙，讓我幫你轉接輔睡員來處理喔！",
        "need_human": True,
        "human_reason": f"系統錯誤: {str(e)}",
        "intent": "ERROR"
    }
```

---

## Make.com 串接

### 接收格式（Make POST 過來的）

```json
{
  "line_uid": "U1234567890abcdef",
  "message": "顧客的訊息",
  "member_name": "王小明",
  "member_id": "cust_abc123",
  "member_tier": "睡薄墊的海獺",
  "reply_token": "xxxxxxx",
  "timestamp": "2026-04-07T14:30:00+08:00"
}
```

### 回傳格式（回給 Make 的）

```json
{
  "reply": "string — AI 回覆文字，Make 用這個回傳 LINE",
  "need_human": "bool — 是否需要轉接人工",
  "human_reason": "string|null — 轉接原因",
  "intent": "string — 偵測到的意圖"
}
```

Make Scenario 設定指南 → `references/make-setup-guide.md`

---

## API Endpoint

```
POST /chat
```

部署在 Railway / Render，Make.com 的 HTTP 模組打這支。

健康檢查：
```
GET /health → {"status": "ok", "timestamp": "..."}
```

---

## 檔案索引

```
lovefu-cs-brain/
├── SKILL.md                          # 本檔案
├── scripts/
│   ├── app.py                        # FastAPI 主程式（唯一入口）
│   ├── intent_classifier.py          # 意圖分類模組
│   ├── prompt_assembler.py           # System Prompt 組裝器
│   └── model_router.py              # 分層模型選擇
└── references/
    ├── intent-types.md               # 8 種意圖定義 + 判斷範例
    ├── routing-rules.md              # 每種意圖的完整路由邏輯
    └── make-setup-guide.md           # Make.com Scenario 設定指南
```
