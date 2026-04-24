# 大島樂眠 AI 輔睡員系統｜LoveFu Sleep Advisor

> 顧客 LINE → Omnichat → Make.com → 此服務（FastAPI on Railway）→ LLM → 回覆
>
> 與 Omnichat 真人客服「共存」：AI 預設先回，真人按下接管即靜默；24 小時後自動交回。

---

## 一、系統架構速覽

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌────────────────┐
│  顧客LINE  │ ──▶ │ Omnichat │ ──▶ │ Make.com │ ──▶ │ FastAPI (本服務) │
└──────────┘     └──────────┘     └──────────┘     └────────────────┘
                       │                                    │
                       │ ◀─── 真人輔睡員可隨時接管 ───────│
                       │                                    │
                       ▼                                    ▼
                  Shopline 後台              ┌──────────────────────┐
                  （訂單/會員/商品）          │ 6 個 Skill 模組      │
                                            │  ├─ cs-brain (中控)  │
                                            │  ├─ cs-persona (人設) │
                                            │  ├─ cs-knowledge (KB) │
                                            │  ├─ cs-memory (記憶)  │
                                            │  ├─ cs-shopline (查單)│
                                            │  └─ cs-guard (安全閘) │
                                            └──────────────────────┘
```

完整圖表見 [`docs/architecture.md`](docs/architecture.md)

---

## 二、6 個 Skill 一覽

| Skill | 角色 | 主要檔案 |
|---|---|---|
| `lovefu-cs-brain` | 中控大腦，所有訊息的唯一入口 | `scripts/app.py`, `intent_classifier.py`, `prompt_assembler.py`, `model_router.py`, `omnichat_coexist.py` |
| `lovefu-cs-persona` | 「小島」人設、語氣、禁忌、轉接規則 | `references/tone-guide.md` |
| `lovefu-cs-knowledge` | 全產品 / 睡眠科學 / 服務政策知識庫 | `references/products-*.md` |
| `lovefu-cs-memory` | 對話記憶（短期 10 輪 + 長期摘要 + Profile） | `scripts/memory_store.py` |
| `lovefu-cs-shopline` | Shopline 訂單 / 會員 / 出貨查詢，含 mock 模式 | `scripts/query_*.py`, `mock_data.py` |
| `lovefu-cs-guard` | API 安全閘門：唯讀、白名單、個資遮蔽、審計日誌 | `scripts/api_guard.py` |

---

## 三、Omnichat 共存機制

AI 預設先回每一則訊息。當以下任一情況發生，AI 立刻靜默：

| Make.com 帶入的 `omnichat_event` | AI 行為 | 持續時間 |
|---|---|---|
| `agent_replied` | 真人剛在 Omnichat 後台回了訊息 → AI 暫停 | 30 分鐘（可設） |
| `agent_takeover` | 真人按下「接手」按鈕 | 24 小時（可設） |
| `agent_release` | 真人按下「交回給 AI」 | 立即解除 |
| `null` / 一般訊息 | AI 正常處理 | — |

實作見 `lovefu-cs-brain/scripts/omnichat_coexist.py`，詳細流程見 [`docs/omnichat-coexistence.md`](docs/omnichat-coexistence.md)。

人工介入可呼叫 `DELETE /mute/{line_uid}` 強制解除。

---

## 四、Shopline 雙模式

| 模式 | 何時用 | 環境變數 |
|---|---|---|
| **mock** | 本機開發、Demo、CI、還沒拿到 Token 時 | `SHOPLINE_MODE=mock`（預設） |
| **production** | 上線後接真實 Shopline 後台 | `SHOPLINE_MODE=production` + `SHOPLINE_ACCESS_TOKEN` |

切換不需改任何程式碼。Mock 資料定義在 `lovefu-cs-shopline/scripts/mock_data.py`，回應結構與真實 API 完全一致。

API 全部唯讀（GET only），白名單與個資遮蔽都在 `lovefu-cs-guard/scripts/api_guard.py`。

---

## 五、本機跑起來

```bash
# 1. 安裝依賴
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，至少填入 OPENAI_API_KEY

# 3. 啟動
uvicorn main:app --reload --port 8000

# 4. 試打
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "line_uid": "Utest123",
    "message": "我想看山丘床墊的價格",
    "member_id": "cust_mock_001"
  }'

# 5. 健康檢查
curl http://localhost:8000/health
```

---

## 六、部署到 Railway

詳細步驟見 [`docs/railway-deployment.md`](docs/railway-deployment.md)。

簡述：
1. Railway 建新 Project → Connect GitHub Repo
2. Variables 頁面，依 `.env.example` 填入所有變數
3. （生產環境建議）加掛 Railway Redis 並把 `MEMORY_BACKEND=redis`
4. Deploy → 取得公開網址 → 填回 Make.com 的 HTTP 模組

---

## 七、API 介面

| Method | 路徑 | 說明 |
|---|---|---|
| `POST` | `/chat` | 主入口。Make.com 每收到 LINE 訊息呼叫一次 |
| `GET` | `/health` | Railway healthcheck |
| `GET` | `/mute/{line_uid}` | 查詢某顧客的 AI 靜默狀態 |
| `DELETE` | `/mute/{line_uid}` | 強制解除靜默（給 ops 用） |

`POST /chat` Request Body：
```json
{
  "line_uid": "U1234...",
  "message": "我的訂單到哪了",
  "member_name": "王小明",       // 選填，由 Omnichat 帶入
  "member_id": "cust_abc",       // 選填
  "member_tier": "睡厚墊的海獺",  // 選填
  "omnichat_event": null         // 或 agent_replied / agent_takeover / agent_release
}
```

Response：
```json
{
  "reply": "你的訂單 #LF20260301001 已經出貨囉...",
  "need_human": false,
  "intent": "ORDER",
  "silent": false,
  "silent_reason": null
}
```

`silent: true` 時 Make.com 應跳過回覆步驟（真人接管中）。

---

## 八、檔案地圖

```
.
├── main.py                  ← Railway 入口，hyphen→underscore 模組註冊
├── requirements.txt
├── Procfile
├── railway.json
├── runtime.txt
├── .env.example
├── README.md                ← 本檔
├── COWORK_HANDOFF.md        ← 完整交接細節（前情提要）
│
├── docs/
│   ├── architecture.md           ← 系統架構圖
│   ├── omnichat-coexistence.md   ← AI/真人共存流程
│   └── railway-deployment.md     ← 部署逐步指南
│
├── lovefu-cs-brain/         ← 9 檔
├── lovefu-cs-guard/         ← 7 檔
├── lovefu-cs-knowledge/     ← 10 檔（含已從官網抓取的真實價格）
├── lovefu-cs-memory/        ← 6 檔
├── lovefu-cs-persona/       ← 4 檔
└── lovefu-cs-shopline/      ← 9 檔（含 mock_data.py）
```

---

## 九、目前狀態與下一步

- ✅ 6 Skill 核心程式完成，~3000 行
- ✅ 知識庫真實價格已從官網抓取入庫
- ✅ Shopline mock 模式可獨立跑通
- ✅ Omnichat 共存（mute mode）已實作
- ✅ Railway 一鍵部署檔備齊

待補：
- [ ] 冰島 / 飄雲床墊厚度（嵌在產品頁圖片中，需手動填入 `products-mattress.md`）
- [ ] 三張獨立筒床墊重量（kg）
- [ ] 取得真實 Shopline Access Token（拿到後 `SHOPLINE_MODE=production` 即切換）
- [ ] Make.com Scenario 串接（HTTP POST → /chat，Router 依 `silent` / `need_human` 分流）
