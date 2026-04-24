# Railway 部署逐步指南

## 前置

- 一個 GitHub 帳號 + 此專案的 Repo
- 一個 Railway 帳號（free tier 起跑足夠）
- OpenAI API Key
- （生產環境）Shopline Admin Access Token

## Step 1：把專案推上 GitHub

```bash
cd /path/to/大島樂眠新策略
git init
git add .
git commit -m "Initial: lovefu cs ai system"
git branch -M main
git remote add origin https://github.com/<你>/<repo>.git
git push -u origin main
```

`.gitignore` 已內建 `.env`，不會把 Token 推上去。

## Step 2：Railway 建專案

1. Railway → **New Project** → **Deploy from GitHub repo**
2. 選擇剛剛推的 repo
3. Railway 會自動偵測 `railway.json` 與 `Procfile`，開始 build

## Step 3：設定環境變數

Project 頁面 → **Variables** → 依 `.env.example` 逐一填入。

**最少需要的：**
```
OPENAI_API_KEY=sk-proj-XXXXXXXXXX
SHOPLINE_MODE=mock           # 第一次部署先用 mock 把流程跑通
MEMORY_BACKEND=dict          # 第一次部署先用 dict
LOG_LEVEL=INFO
```

確認可以跑通後再切 production：
```
# Shopline（訂單/會員查詢）
SHOPLINE_MODE=production
SHOPLINE_STORE_HANDLE=lovefu321930
SHOPLINE_ACCESS_TOKEN=你的-shopline-token

# WMS 暢流（庫存/貨態/門市查詢）
WMS_MODE=production
WMS_API_ID=你的-wms-api-id
WMS_API_KEY=你的-wms-api-key
WMS_PII_AES_KEY=你的-aes-解密金鑰（WMS 後台「API 設定」頁取得）
```

## Step 4：（建議）加掛 Redis

只有 dict 模式時，每次部署 / Railway 自動重啟，所有對話記憶 + mute 狀態都會清空。生產環境應改用 Redis：

1. Project → **+ New** → **Database** → **Redis**
2. Redis 服務的 **Variables** 頁會看到 `REDIS_URL`、`REDISHOST`、`REDISPORT`、`REDISPASSWORD`
3. 回到主 Service → **Variables** 加入：
   ```
   MEMORY_BACKEND=redis
   REDIS_HOST=${{Redis.REDISHOST}}
   REDIS_PORT=${{Redis.REDISPORT}}
   REDIS_PASSWORD=${{Redis.REDISPASSWORD}}
   ```
   （`${{...}}` 是 Railway 的引用語法，會自動帶入內網 host）

## Step 5：取得對外網址

Project → **Settings** → **Networking** → **Generate Domain**
會拿到 `xxx.up.railway.app` 形式的網址。

## Step 6：驗證部署

```bash
# 健康檢查
curl https://xxx.up.railway.app/health
# 預期：{"status":"ok","version":"2.0.0",...}

# 試打 /chat（mock 模式）
curl -X POST https://xxx.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -d '{
    "line_uid": "Utest",
    "message": "山丘床墊多少錢",
    "member_id": "cust_mock_001"
  }'
# 預期：拿到一段「小島」風格的回覆，intent="PRODUCT"
```

## Step 7：串到 Make.com

1. Make.com → 建立新 Scenario（或編輯既有的）
2. Trigger：Omnichat Webhook（依 `docs/omnichat-coexistence.md` 設定）
3. Action：HTTP > **Make a request**
   - URL：`https://xxx.up.railway.app/chat`
   - Method：POST
   - Body Type：Raw → JSON
   - Body：見 `docs/omnichat-coexistence.md` Module 3-6 範例
4. 後續 Router：
   - `silent === true` → Stop
   - `need_human === true` → 發 LINE Notify 給輔睡員群組
   - 其他 → LINE Send Reply Message

## Step 8：監控

Railway → Service → **Deployments** → 點任一 deployment → **View Logs**

關鍵日誌會在這裡看到：
```
INFO:lovefu.brain:Intent ORDER → simple model: gpt-4o-mini
INFO:lovefu.guard:MOCK: /orders.json → hit
INFO:lovefu.brain.omnichat:Mute active for U123, remaining 12.3 min
```

## 常見問題

### Q：build 失敗，找不到模組
A：檢查 `requirements.txt` 有沒有最新版本。Railway 預設用 Nixpacks，會讀 `requirements.txt` + `runtime.txt`。

### Q：呼叫 /chat 回 500
A：看 Railway logs。最常見是 `OPENAI_API_KEY` 沒設，或 mock 模式下傳了不存在的 `member_id`。

### Q：切到 production 後抓不到訂單
A：
1. 確認 Token 有勾這 6 個 read scope（見 `lovefu-cs-guard/references/shopline-scopes.md`）
2. 確認 `SHOPLINE_STORE_HANDLE` 是你的店家 handle（不含 `.myshopline.com`）
3. 看 audit log，是不是被 `not_in_whitelist` 攔了

### Q：WMS 查不到庫存 / 貨態
A：
1. 確認 `WMS_MODE=production`
2. 確認 `WMS_API_ID` 和 `WMS_API_KEY` 已填入（從 WMS 暢流後台取得）
3. 先跑 `python scripts/test_api_connection.py` 確認 Token 能取得
4. 看 audit log 是否被 `wms_not_in_whitelist` 攔了
5. 若收件人姓名/電話顯示為密文，檢查 `WMS_PII_AES_KEY` 是否正確（WMS 後台「API 設定」頁）

### Q：AI 跟真人同時回覆顧客
A：Omnichat 沒有發 `agent_replied` webhook 給 Make.com。檢查 Omnichat → Settings → Webhooks 有沒有勾 outbound message 事件。

### Q：想把整個服務搬到自己的伺服器
A：照著 `Procfile` 跑就好：`uvicorn main:app --host 0.0.0.0 --port 8000`。Railway 沒有 lock-in。
