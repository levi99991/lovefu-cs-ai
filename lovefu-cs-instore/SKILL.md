---
name: lovefu-cs-instore
description: 大島樂眠（LoveFu）門市試躺顧客追蹤系統。將線下試躺與線上 LINE 對話打通，提供 5 階段（T+0 / 24h / 3d / 7d / 14d）追客節奏。AI 提供草稿，門市輔睡員透過 Omnichat 後台 1 鍵發送或編輯，永遠保留人類最終決定權。內建頻率上限（14 天 5 則）防止顧客封鎖。當門市完成試躺需要追蹤、需要將 store_lead_id 與 line_uid 綁定、需要產生追客訊息草稿、或需要查看顧客 customer_journey 狀態時必定使用此 skill。
---

# lovefu-cs-instore — 門市追客流程

## 設計原則

1. **半自動，永遠真人決定**：AI 只產草稿，門市輔睡員必須按確認才送出。
2. **跨渠道閉環**：門市試躺 → QR 加 LINE → AI 追客草稿 → 真人發送 → 顧客回覆 → 線上對話 → 下單 → 出貨追蹤。
3. **頻率自律**：14 天內每位顧客最多 5 則主動訊息；顧客一旦封鎖立即停發。
4. **狀態機驅動**：customer_journey.stage 決定當下能採取的行動。
5. **絕不催促**：所有草稿須符合 cs-persona「不催促、留白、呼吸感」原則。

## 5 階段追客節奏（Cadence）

| 階段 | 時機 | 目的 | 草稿要點 |
|---|---|---|---|
| 1 | T+0（試躺結束 30 分內） | 加深印象 | 個人化感謝＋試躺商品摘要＋睡眠小知識 |
| 2 | +24h | 引發互動 | 詢問試躺後感想，引導留言而非催單 |
| 3 | +3 天 | 強化信任 | 同類客戶見證 / 睡眠科學知識 |
| 4 | +7 天 | 決策推力 | 當期優惠 / 限量 / 安心睡計畫說明 |
| 5 | +14 天 | 最後關懷 | 「最近睡得好嗎？」非催單關懷 |

## customer_journey 狀態機

```
試躺中 ──加 LINE──▶ 待追蹤 ──下單──▶ 已下單 ──出貨完成 7d──▶ 已交付
                       │
                       └──14天未下單──▶ 沉睡客（不主動）
```

## 對外函式

| 函式 | 用途 |
|---|---|
| `register_lead(store_lead_data)` | 門市表單 → 產生 store_lead_id + LINE QR URL |
| `bind_line(store_lead_id, line_uid)` | 顧客掃 QR 加好友後綁定 |
| `schedule_follow_ups(line_uid)` | 為新待追蹤顧客排 5 階段排程 |
| `generate_draft(line_uid, stage)` | 用 cs-knowledge + cs-persona 產生草稿 |
| `list_pending_drafts(store_advisor)` | 列出某輔睡員待發草稿 |
| `mark_sent(draft_id, edited_text)` | 真人按確認後紀錄 |
| `pause_follow_ups(line_uid, reason)` | 顧客封鎖 / 已下單時暫停 |

## 環境變數

```
INSTORE_MAX_MSG_PER_14D=5            # 頻率上限
INSTORE_PAUSE_ON_BLOCK=true          # 偵測封鎖即暫停
INSTORE_DRAFT_REVIEW_REQUIRED=true   # 必須真人確認才送（不可關閉）
```

## 與其他 Skill 的關係

- 讀 cs-knowledge 取產品/睡眠知識做草稿
- 讀 cs-persona 確保語氣統一
- 寫 cs-memory.profile.customer_journey
- 監聽 cs-shopline 的訂單建立 → 推進 stage 到「已下單」
- 監聽 cs-logistics 的 status_code=F 滿 7 天 → 推進到「已交付」

## 法規與合規

- 須在門市試躺加 LINE 時取得「行銷訊息同意」勾選紀錄。
- 顧客若回覆「停止」或「不要再傳」→ 立即進入 pause 並標記 do_not_contact=true。
- 14 天內 5 則為硬性上限，可由 INSTORE_MAX_MSG_PER_14D 調低，不可調高。
