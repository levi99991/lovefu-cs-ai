# 大島樂眠 AI 輔睡員 — 系統架構

## 全景圖

```
┌─────────────────────────────────────────────────────────────────────┐
│                       顧客層 (Customer Layer)                        │
│                                                                     │
│         LINE 官方帳號（@lovefu）                                     │
└────────────────────┬────────────────────────────────────────────────┘
                     │ LINE Messaging API
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      整合層 (Integration Layer)                      │
│                                                                     │
│   ┌──────────────────┐         ┌────────────────────┐              │
│   │     Omnichat     │────────▶│      Make.com      │              │
│   │  (CRM / 真人客服) │         │  (Webhook 路由)     │              │
│   └──────┬───────────┘         └─────────┬──────────┘              │
│          │                               │                          │
│          │ 真人輔睡員可隨時接管              │ HTTP POST /chat        │
│          │                               │                          │
└──────────┼───────────────────────────────┼──────────────────────────┘
           │                               │
           │                               ▼
           │           ┌──────────────────────────────────────────┐
           │           │     AI 輔睡員後端 (本服務 / Railway)       │
           │           │                                          │
           │           │  main.py (FastAPI 入口)                  │
           │           │       │                                  │
           │           │       ▼                                  │
           │           │  ┌─────────────────────────────────┐     │
           │           │  │   lovefu-cs-brain (中控)        │     │
           │           │  │                                 │     │
           │           │  │  /chat 6 步流程：                │     │
           │           │  │   0. Omnichat mute 檢查          │     │
           │           │  │   1. 轉接關鍵字攔截               │     │
           │           │  │   2. 載入記憶                    │     │
           │           │  │   3. 意圖分類（GPT-4o-mini）      │     │
           │           │  │   4. 按意圖調取資料               │     │
           │           │  │   5. 組裝 Prompt + LLM 生成       │     │
           │           │  │   6. 儲存記憶 + 回傳              │     │
           │           │  └────┬────────────────────────────┘     │
           │           │       │                                  │
           │           │       ▼                                  │
           │           │  ┌──────────┐  ┌──────────┐  ┌────────┐  │
           │           │  │ persona  │  │knowledge │  │ memory │  │
           │           │  └──────────┘  └──────────┘  └────────┘  │
           │           │  ┌──────────┐  ┌──────────────────────┐  │
           │           │  │ shopline │──│  guard (安全閘門)     │  │
           │           │  └──────────┘  └──────────┬───────────┘  │
           │           └────────────────────────────┼─────────────┘
           │                                        │
           │                                        ▼
           │                  ┌─────────────────────────────┐
           │                  │   外部資料 (Mock 或真實)     │
           │                  │                             │
           │                  │  Shopline Admin API (唯讀)   │
           └─────────────────▶│  (or mock_data.py)          │
                              │                             │
                              │  OpenAI Chat Completions    │
                              └─────────────────────────────┘
```

## 訊息生命週期（Happy Path）

```
顧客傳訊 "我的訂單到哪了"
   │
   ▼
LINE Channel
   │
   ▼
Omnichat (記錄、判斷是否人工接手)
   │
   ▼
Make.com Webhook
   │ HTTP POST /chat
   │ {
   │   line_uid: "U123",
   │   message: "我的訂單到哪了",
   │   member_id: "cust_abc",
   │   omnichat_event: null
   │ }
   ▼
FastAPI /chat
   │
   ├─ Step 0: omnichat_coexist.check_should_mute() → False（無人接管）
   │
   ├─ Step 1: check_escalation_keywords() → False（沒「我要客訴」之類）
   │
   ├─ Step 2: get_memory_for_prompt(line_uid) → 載入過去 10 輪
   │
   ├─ Step 3: classify_intent("我的訂單到哪了") → "ORDER"
   │
   ├─ Step 4: _fetch_order_context(...)
   │           └─ shopline_safe_get("/orders.json", {buyer_id: "cust_abc"})
   │                  └─ guard 檢查白名單 → 通過
   │                       └─ Mock 模式：回傳 mock_data → mask_pii → 回應
   │           └─ format_orders_for_llm() → 自然語言摘要
   │
   ├─ Step 5: assemble_prompt() + call_llm("gpt-4o-mini")
   │           └─ 回覆字串
   │
   └─ Step 6: save_turn() × 2
              update_profile(intent="ORDER")
              return ChatResponse(reply="你的訂單...", intent="ORDER")
   │
   ▼
Make.com → LINE Reply API → 顧客
```

## 資料流向

| 資料類型 | 來源 | 經過 | 終點 |
|---|---|---|---|
| 顧客訊息 | LINE | Omnichat → Make → /chat | LLM context |
| 對話歷史 | cs-memory | prompt_assembler | LLM context |
| 產品知識 | cs-knowledge/references/*.md | prompt_assembler | LLM context |
| 訂單/會員 | Shopline API（or mock） | cs-guard mask_pii → format_*_for_llm | LLM context |
| AI 回覆 | LLM | brain | Make → LINE Reply |
| 審計日誌 | cs-guard | audit_logger | stdout / 集中式日誌 |

## 安全邊界

guard 模組是「唯一」對外發 HTTP 的地方（除了 `model_router.py` 對 OpenAI）。設計上：

1. 沒有 `safe_post` / `safe_put` / `safe_delete` 函式，從根上排除寫入。
2. 路徑必須過 `ALLOWED_SHOPLINE_PATHS` 白名單。
3. `cancel`、`refund`、`update`... 等關鍵字直接攔截。
4. 所有回應遞迴 `mask_pii()`：手機 `****1234`、Email `abc***@x.com`、地址只保留到路名。
5. 每次呼叫都記 `audit_logger.log_api_call`，含 caller、line_uid、blocked、status_code。

## 環境變數對照

| 模組 | 變數 | 預設 | 必填 |
|---|---|---|---|
| brain.model_router | `OPENAI_API_KEY` | — | ✅ |
| brain.model_router | `LLM_MODEL_SIMPLE` | gpt-4o-mini | ❌ |
| brain.model_router | `LLM_MODEL_COMPLEX` | gpt-4o | ❌ |
| guard.api_guard | `SHOPLINE_MODE` | mock | ❌ |
| guard.api_guard | `SHOPLINE_STORE_HANDLE` | — | production 模式才需 |
| guard.api_guard | `SHOPLINE_ACCESS_TOKEN` | — | production 模式才需 |
| memory.memory_store | `MEMORY_BACKEND` | dict | ❌ |
| memory.memory_store | `REDIS_HOST` | localhost | redis 模式才需 |
| brain.omnichat_coexist | `MUTE_AFTER_AGENT_REPLY_MIN` | 30 | ❌ |
| brain.omnichat_coexist | `MUTE_AFTER_TAKEOVER_HOURS` | 24 | ❌ |
