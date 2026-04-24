---
name: lovefu-cs-memory
description: >
  大島樂眠（LoveFu）AI 輔睡員的對話記憶管理系統。
  管理每位顧客的對話歷史，實現多輪連續對話。
  包含短期記憶（最近 10 輪完整對話）、長期摘要（壓縮歷史）、
  顧客 profile（身份映射、偏好標籤）、過期清理機制。
  被 cs-brain 在每次對話開始時自動讀取、結束時自動寫入。
  當使用者提到「對話記憶」「上下文」「記住我說的」
  「你剛剛不是說」「我之前問過」或任何涉及對話連續性的問題時，
  此 skill 在背景運作確保 AI 記得前文。
---

# AI 輔睡員對話記憶系統

## 為什麼需要記憶

沒有記憶，每次對話都像第一次見面：

```
顧客：我腰痠想換床墊
AI：你平常仰睡還是側睡呢？
顧客：仰睡
AI：（下一次對話）你好～有什麼可以幫你的？  ← 完全忘了
```

有了記憶：

```
顧客：我腰痠想換床墊
AI：你平常仰睡還是側睡呢？
顧客：仰睡
（隔天）
顧客：昨天聊到的那張床多少錢？
AI：你昨天提到腰痠、仰睡為主，我推薦的山丘樂眠床，5尺的話是 NT$XX,XXX。
```

---

## 記憶架構：三層設計

```
┌──────────────────────────────────┐
│  短期記憶（turns）                 │
│  最近 10 輪完整 user/assistant    │
│  每次對話即時讀寫                   │
├──────────────────────────────────┤
│  長期摘要（summary）               │
│  超過 10 輪後由 LLM 壓縮          │
│  一段話概括這位顧客的歷史           │
├──────────────────────────────────┤
│  顧客 profile（meta）             │
│  身份映射、偏好標籤、上次意圖       │
│  結構化資料，不經 LLM 處理         │
└──────────────────────────────────┘
```

每位顧客一組記憶，以 LINE UID 為 key。

---

## 資料格式

完整 schema → `references/memory-schema.md`

快速預覽：

```json
{
  "line_uid": "U1234567890abcdef",
  "profile": {
    "member_name": "王小明",
    "member_id": "cust_abc123",
    "member_tier": "睡薄墊的海獺",
    "preferences": ["仰睡", "腰部敏感", "偏好硬床"],
    "last_intent": "PRODUCT",
    "satisfaction_count": 0,
    "dissatisfaction_count": 0
  },
  "turns": [
    {"role": "user", "content": "我腰痠想換床墊", "ts": "2026-04-07T14:30:00"},
    {"role": "assistant", "content": "腰痠真的很影響睡眠呢...", "ts": "2026-04-07T14:30:05"},
    {"role": "user", "content": "仰睡為主", "ts": "2026-04-07T14:30:20"},
    {"role": "assistant", "content": "了解，山丘樂眠床可能很適合你...", "ts": "2026-04-07T14:30:25"}
  ],
  "summary": "",
  "last_active": "2026-04-07T14:30:25",
  "created_at": "2026-04-07T14:30:00",
  "turn_count_total": 4
}
```

---

## 讀寫流程

### 對話開始（cs-brain 呼叫）

```python
memory = await load_memory(line_uid)
# 回傳 turns + summary + profile
# 如果沒有記錄 → 回傳空的初始結構
```

### 對話結束（cs-brain 呼叫）

```python
await save_turn(line_uid, role="user", content="顧客的訊息")
await save_turn(line_uid, role="assistant", content="AI 的回覆")
await update_profile(line_uid, intent="ORDER", preferences=["仰睡"])
```

### 自動壓縮（save_turn 時觸發）

```python
# 如果 turns 超過 10 輪（20 條訊息）
# 把最舊的 5 輪壓縮成 summary
# turns 只保留最近 5 輪
```

---

## 記憶在 Prompt 中怎麼用

cs-brain 組裝 System Prompt 時：

```
[cs-persona 人設]

## 這位顧客的資訊
姓名：王小明
海獺等級：睡薄墊的海獺
偏好：仰睡、腰部敏感、偏好硬床

## 歷史摘要
顧客王小明 4/5 曾詢問山丘床墊和冰島床墊的差異，表示腰部敏感偏好硬床。
4/6 詢問了配送時間和安心睡計畫規則。

## 最近對話
user: 昨天聊到的那張床多少錢？
assistant: ...

[cs-knowledge 知識片段]
[查詢到的外部資料]

## 當前訊息
user: 我想下單了，5尺的
```

這樣 LLM 就能「記得」顧客之前說過的所有內容。

---

## 偏好標籤自動提取

AI 在對話過程中識別到的顧客偏好，自動存入 profile.preferences：

| 顧客說的話 | 提取的標籤 |
|-----------|----------|
| 「我都仰睡」 | 仰睡 |
| 「側睡比較多」 | 側睡 |
| 「腰很痠」「腰部不舒服」 | 腰部敏感 |
| 「很怕熱」「容易流汗」 | 怕熱 |
| 「喜歡硬一點」 | 偏好硬床 |
| 「喜歡軟軟的」 | 偏好軟床 |
| 「肩膀很緊」 | 肩頸壓力 |
| 「跟另一半一起睡」 | 雙人使用 |
| 「給小孩用」 | 兒童使用 |
| 「租屋」「宿舍」 | 空間有限 |

這些標籤累積下來，讓 AI 對每位顧客越來越了解，推薦越來越精準。

意圖分類後由 cs-brain 呼叫 `update_profile()` 更新。

---

## 不滿意計數器

cs-persona 的轉接規則中，「連續 2 次不滿意觸發轉接」需要計數器：

```python
profile.dissatisfaction_count += 1  # 偵測到不滿意
profile.dissatisfaction_count = 0   # 顧客滿意或換話題時歸零
```

此計數器存在 profile 中，跨輪次持續追蹤。

---

## 儲存方案

### MVP 階段：Python dict + JSON 檔案

```python
# 記憶存在 Python dict 中（程式重啟就消失）
conversation_store: dict[str, dict] = {}

# 定期備份到 JSON 檔案（可選）
# 適合每日對話量 < 500 則的階段
```

### 生產階段：Redis

```python
import redis
r = redis.Redis(host="...", port=6379, db=0)

# 每位顧客一個 key，value 是 JSON 字串
r.set(f"memory:{line_uid}", json.dumps(memory_data))
r.expire(f"memory:{line_uid}", 60 * 60 * 24 * 7)  # 7 天過期

# 讀取
data = r.get(f"memory:{line_uid}")
memory = json.loads(data) if data else create_empty_memory(line_uid)
```

Redis 的優勢：自動過期、程式重啟不遺失、毫秒級讀寫。
Railway 和 Render 都有免費或低價的 Redis 附加服務。

---

## 過期與清理

| 規則 | 說明 |
|------|------|
| 7 天無互動 | 自動清除整組記憶（Redis TTL 或定期掃描） |
| 每次互動 | 重設 7 天倒數 |
| 手動清除 | 顧客要求「忘記我的資料」→ 刪除該 LINE UID 的所有記憶 |

### 為什麼是 7 天

- 太短（1 天）：隔天繼續問就忘了，體驗差
- 太長（30 天）：累積太多過期資訊，浪費 token 也可能誤導 AI
- 7 天是客服場景的甜蜜點：一筆退換貨通常 1~2 天內來回完成

---

## 隱私保護

- 記憶中不存儲完整手機號碼（只存遮蔽後的 ****5678）
- 記憶中不存儲信用卡號碼
- 記憶中不存儲身分證號碼
- 顧客要求刪除 → 立即清除該 LINE UID 的所有記憶
- 記憶資料不跨顧客共享

---

## 檔案索引

```
lovefu-cs-memory/
├── SKILL.md                          # 本檔案
├── scripts/
│   ├── memory_store.py               # 讀寫記憶（dict / Redis 雙模式）
│   ├── memory_summarize.py           # 長對話 LLM 壓縮
│   └── memory_cleanup.py             # 過期清理
└── references/
    └── memory-schema.md              # 完整記憶資料格式定義
```
