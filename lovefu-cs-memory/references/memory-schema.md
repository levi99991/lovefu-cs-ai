# 記憶資料格式定義｜memory-schema.md

## 完整 Schema

```json
{
  "line_uid": "string — LINE User ID，此記憶的唯一識別",

  "profile": {
    "member_name": "string|null — 顧客姓名（Omnichat 帶過來或對話中自報）",
    "member_id": "string|null — Shopline Customer ID（Omnichat 綁定或手動比對）",
    "member_tier": "string|null — 海獺等級（初入島/睡薄墊/癱床）",
    "preferences": ["string — 偏好標籤陣列，如 '仰睡'、'腰部敏感'、'偏好硬床'"],
    "last_intent": "string|null — 上一次對話的意圖分類（ORDER/PRODUCT/SLEEP/...）",
    "satisfaction_count": "int — 連續滿意次數（用於統計）",
    "dissatisfaction_count": "int — 連續不滿意次數（≥2 觸發轉接人工）"
  },

  "turns": [
    {
      "role": "string — 'user' 或 'assistant'",
      "content": "string — 訊息內容",
      "ts": "string — ISO 8601 時間戳記"
    }
  ],

  "summary": "string — 舊對話的壓縮摘要（超過 10 輪後產生）",

  "last_active": "string — 最後活動時間（ISO 8601），用於過期判斷",
  "created_at": "string — 此記憶建立時間",
  "turn_count_total": "int — 累計對話總輪數（含已壓縮的）"
}
```

## 欄位說明

### profile.preferences

累積式更新，不會覆蓋：
- 第一次聊到仰睡 → ["仰睡"]
- 第二次聊到腰痠 → ["仰睡", "腰部敏感"]
- 第三次聊到怕熱 → ["仰睡", "腰部敏感", "怕熱"]

去重邏輯在 update_profile() 中處理。

### profile.dissatisfaction_count

- 偵測到不滿意 → +1
- 偵測到滿意或換話題 → 歸零
- ≥ 2 → cs-brain 觸發轉接人工
- 轉接後歸零

### turns

- 最多保留 20 條訊息（10 輪 user+assistant）
- 超過時觸發壓縮，最舊的移入 summary
- 每條 content 不截斷（但 LLM 的回覆通常 < 200 字）

### summary

- 初始為空字串
- 壓縮後長度控制在 500 字以內
- 格式為自然語言第三人稱描述
- 生產環境由 LLM 壓縮（memory_summarize.py）
- MVP 階段用簡易拼接（memory_store.py 內建）

## 儲存 Key 設計

| 後端 | Key 格式 | 範例 |
|------|---------|------|
| dict | Python dict key | `"U1234567890abcdef"` |
| Redis | `memory:{line_uid}` | `memory:U1234567890abcdef` |

## 大小估算

| 項目 | 估算大小 |
|------|---------|
| 一輪對話（user+assistant） | ~400 bytes |
| 10 輪對話 | ~4 KB |
| profile | ~500 bytes |
| summary（滿） | ~1.5 KB |
| 每位顧客記憶上限 | ~6 KB |

以 2,500 位活躍顧客計算：~15 MB，Redis 免費方案（25 MB）足夠。

## 範例：完整記憶

```json
{
  "line_uid": "U1234567890abcdef",
  "profile": {
    "member_name": "王小明",
    "member_id": "cust_abc123",
    "member_tier": "睡薄墊的海獺",
    "preferences": ["仰睡", "腰部敏感", "偏好硬床"],
    "last_intent": "ORDER",
    "satisfaction_count": 3,
    "dissatisfaction_count": 0
  },
  "turns": [
    {"role": "user", "content": "我上禮拜訂的那張床出貨了嗎", "ts": "2026-04-07T14:30:00+08:00"},
    {"role": "assistant", "content": "我幫你查一下！你的訂單 #LF2026001 山丘樂眠床 5 尺，目前還在製作中...", "ts": "2026-04-07T14:30:05+08:00"},
    {"role": "user", "content": "大概還要多久", "ts": "2026-04-07T14:30:20+08:00"},
    {"role": "assistant", "content": "預計大約再 10 個工作天左右，完成後會有專人跟你約配送時間喔！", "ts": "2026-04-07T14:30:25+08:00"}
  ],
  "summary": "顧客王小明 4/5 詢問山丘床墊和冰島床墊差異，表示腰部敏感偏好硬床，決定購買山丘 5 尺。4/6 確認付款完成。",
  "last_active": "2026-04-07T14:30:25+08:00",
  "created_at": "2026-04-05T10:15:00+08:00",
  "turn_count_total": 18
}
```
