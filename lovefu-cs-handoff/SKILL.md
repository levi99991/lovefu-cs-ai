---
name: lovefu-cs-handoff
description: 大島樂眠 AI 輔睡員「即時轉人工斷點」系統。偵測四大轉人工訊號（顧客明說 / AI 信心低 / 情緒紅線 / 高價值事件），觸發 handoff 狀態機，通知對應門市輔睡員，並以多段提醒機制確保接手。包含門市班表整合、值班輪替、未處理升級機制。當使用者提到「轉人工」「handoff」「輔睡員通知」「接手」「銜接斷點」「提醒機制」「門市通知」「客服值班」或任何涉及 AI 與真人輔睡員銜接的議題時，必定使用此 skill。
---

# lovefu-cs-handoff — 即時轉人工斷點系統

## 職責
1. **斷點偵測**：四大觸發訊號（顯性 / 信心 / 情緒 / 高價值）
2. **狀態機**：pending → acknowledged → resolved / escalated / timeout
3. **通知扇出**：LINE Notify / Slack / Omnichat 旗標 / Email
4. **提醒機制**：多段升級（5 分 / 30 分 / 1 小時 / 隔日）
5. **值班班表**：依門市營業時段、輔睡員狀態路由

## 邊界
- 僅做「斷點與通知」；實際接手對話由人工在 Omnichat 後台進行
- AI 觸發 handoff 後進入靜默（由 omnichat_coexist 的 mute 機制接手）
- 不直接寫入 Shopline / WMS，不觸碰 PII 明文

## 檔案
- `signal_detector.py` — 四大觸發訊號偵測
- `handoff_manager.py` — 狀態機 + 對外 API
- `advisor_reminder.py` — 提醒排程 + 升級
- `advisor_roster.py` — 值班班表 + 路由
- `notification_dispatcher.py` — 多通道通知
