-- ================================================================
-- 大島樂眠 AI 輔睡員 — Cloudflare D1 審計日誌 Schema
-- 對應 lovefu-cs-guard/scripts/audit_logger.py 的 d1 sink
--
-- 建置步驟（Wrangler CLI）：
--   1. wrangler d1 create lovefu-audit
--   2. 將回傳的 database_id 填入 .env 的 CF_D1_DATABASE_ID
--   3. wrangler d1 execute lovefu-audit --file=./docs/d1-audit-schema.sql
--
-- 保留策略：建議 90 天（由排程任務定期刪除 ts < now()-90d 的資料）
-- ================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,                 -- ISO8601 台灣時間（datetime.now(TW_TZ).isoformat()）
    method          TEXT,                             -- GET / POST / …（目前僅 GET，POST 會被 guard 攔截）
    endpoint        TEXT,                             -- 如 /admin/openapi/v1/orders/{order_id}
    status_code     INTEGER,                          -- HTTP 回應碼；被攔截時記 403
    caller          TEXT,                             -- 呼叫者（如 cs_shopline、cs_logistics、unknown）
    line_uid_masked TEXT,                             -- 已遮罩的 LINE UID（U1234****）
    blocked         INTEGER NOT NULL DEFAULT 0,       -- 1 = 被 guard 攔下；0 = 正常呼叫
    block_reason    TEXT,                             -- 攔截原因（whitelist_miss / blacklist_hit / keyword_filter）
    response_size   INTEGER DEFAULT 0                 -- 回應 bytes；偵測異常大量下載
);

-- 查詢索引
CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_blocked ON audit_log(blocked);
CREATE INDEX IF NOT EXISTS idx_audit_caller  ON audit_log(caller);
CREATE INDEX IF NOT EXISTS idx_audit_status  ON audit_log(status_code);


-- ================================================================
-- 常用查詢範例（供 Follow-up 儀表板與安全分析使用）
-- ================================================================

-- 1. 過去 24 小時的攔截事件
-- SELECT ts, endpoint, caller, block_reason
-- FROM audit_log
-- WHERE blocked = 1
--   AND ts >= datetime('now', '-1 day')
-- ORDER BY ts DESC;

-- 2. 每個 caller 的今日呼叫量
-- SELECT caller, COUNT(*) AS calls, SUM(blocked) AS blocked_count
-- FROM audit_log
-- WHERE ts >= date('now')
-- GROUP BY caller
-- ORDER BY calls DESC;

-- 3. 異常回應大小（可能是資料外洩）
-- SELECT ts, caller, endpoint, response_size
-- FROM audit_log
-- WHERE response_size > 1000000   -- 1 MB 以上
-- ORDER BY response_size DESC
-- LIMIT 50;

-- 4. 90 天保留清理（建議每日排程執行一次）
-- DELETE FROM audit_log WHERE ts < datetime('now', '-90 day');
