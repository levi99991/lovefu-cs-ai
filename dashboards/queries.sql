-- ================================================================
-- 大島樂眠 AI 輔睡員 — 儀表板查詢範本
-- 對應 dashboards/generate_dashboard.py + followup-dashboard.html
--
-- 資料來源：
--   1. Cloudflare D1（audit_log）  — 透過 HTTP API 查詢
--   2. cs-instore 記憶體狀態        — Python 程式直接讀取
-- ================================================================

-- ================================================================
-- Section 1：安全審計指標（D1）
-- ================================================================

-- Q1. 今日 API 呼叫總覽（過 24 小時）
-- 供「總覽」頁使用
SELECT
    COUNT(*)                                        AS total_calls,
    SUM(CASE WHEN blocked = 1 THEN 1 ELSE 0 END)    AS blocked_calls,
    SUM(CASE WHEN status_code >= 400 AND blocked = 0 THEN 1 ELSE 0 END) AS error_calls,
    SUM(response_size)                              AS total_bytes
FROM audit_log
WHERE ts >= datetime('now', '-1 day');


-- Q2. 攔截事件分類（黑名單 vs 白名單 miss vs 關鍵字）
-- 供「安全審計」頁的攔截原因圓餅圖
SELECT
    COALESCE(block_reason, 'other') AS reason,
    COUNT(*)                        AS count
FROM audit_log
WHERE blocked = 1
  AND ts >= datetime('now', '-7 day')
GROUP BY block_reason
ORDER BY count DESC;


-- Q3. Caller 呼叫量（哪個 skill 最忙）
-- 供「安全審計」頁的 caller 堆疊圖
SELECT
    caller,
    DATE(ts)                                     AS day,
    COUNT(*)                                     AS calls,
    SUM(CASE WHEN blocked = 1 THEN 1 ELSE 0 END) AS blocked
FROM audit_log
WHERE ts >= datetime('now', '-7 day')
GROUP BY caller, DATE(ts)
ORDER BY day DESC, calls DESC;


-- Q4. 異常回應（可能資料外洩偵測）
-- 供「安全審計」頁的告警列表
SELECT ts, caller, endpoint, status_code, response_size
FROM audit_log
WHERE response_size > 524288   -- 512 KB 以上，正常 GET 不應這麼大
   OR (status_code >= 400 AND blocked = 0)
ORDER BY ts DESC
LIMIT 50;


-- Q5. 每小時呼叫趨勢（看流量尖峰）
-- 供「總覽」頁的主線圖
SELECT
    strftime('%Y-%m-%d %H:00', ts) AS hour,
    COUNT(*)                       AS calls
FROM audit_log
WHERE ts >= datetime('now', '-1 day')
GROUP BY strftime('%Y-%m-%d %H:00', ts)
ORDER BY hour;


-- ================================================================
-- Section 2：追客效成（未來遷入 D1 後使用，目前走 cs-instore 記憶體）
-- ================================================================
-- 註：下列 query 假設有 follow_up_log 表，schema 見 followup-schema.sql
-- （目前 cs-instore 狀態仍在 memory，生產環境請另建此表）

-- Q6. 漏斗：門市試躺 → LINE 綁定 → 首則追客送達 → 回覆 → 成單
-- SELECT
--     SUM(CASE WHEN stage = 'registered' THEN 1 ELSE 0 END)   AS leads_registered,
--     SUM(CASE WHEN stage = 'line_bound' THEN 1 ELSE 0 END)   AS leads_bound,
--     SUM(CASE WHEN stage = 's1_sent'    THEN 1 ELSE 0 END)   AS s1_sent,
--     SUM(CASE WHEN stage = 'replied'    THEN 1 ELSE 0 END)   AS replied,
--     SUM(CASE WHEN stage = 'ordered'    THEN 1 ELSE 0 END)   AS ordered
-- FROM follow_up_log
-- WHERE registered_at >= datetime('now', '-30 day');

-- Q7. 每位門市輔睡員的成效排行
-- SELECT
--     store_name, advisor_name,
--     COUNT(*)                                                AS leads,
--     SUM(CASE WHEN ordered = 1 THEN 1 ELSE 0 END)            AS orders,
--     ROUND(100.0 * SUM(ordered) / COUNT(*), 1)               AS conversion_rate
-- FROM follow_up_log
-- WHERE registered_at >= datetime('now', '-30 day')
-- GROUP BY store_name, advisor_name
-- ORDER BY conversion_rate DESC, leads DESC;
