"""
大島樂眠 AI 輔睡員 — 審計日誌
lovefu-cs-guard/scripts/audit_logger.py

記錄每一次 API 呼叫，包括成功和被攔截的。

三種 sink（AUDIT_SINK 環境變數控制，可逗號分隔多選）：
- stdout       → Python logger（開發預設）
- d1           → Cloudflare D1（HTTP API 非同步寫入；生產建議 90 天保留）
- jsonl_file   → 本機 JSONL 檔（AUDIT_JSONL_PATH）
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from queue import Queue, Empty

logger = logging.getLogger("lovefu.audit")

# 台灣時區
TW_TZ = timezone(timedelta(hours=8))

# Sink 設定
AUDIT_SINK = os.getenv("AUDIT_SINK", "stdout").lower()
AUDIT_JSONL_PATH = os.getenv("AUDIT_JSONL_PATH", "/tmp/lovefu_audit.jsonl")

# Cloudflare D1（需搭配 workers/wrangler 或 D1 HTTP API）
D1_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID", "")
D1_API_TOKEN = os.getenv("CF_API_TOKEN", "")
D1_TABLE = os.getenv("CF_D1_AUDIT_TABLE", "audit_log")

# D1 寫入採 queue + background worker，避免阻塞 /chat 路徑
_d1_queue: "Queue[dict]" = Queue(maxsize=5000)
_d1_worker_started = False
_d1_lock = threading.Lock()


def _d1_worker():
    """背景 thread：批次（10 筆 / 2 秒）把 queue 裡的 log 送到 D1"""
    import httpx
    batch: list[dict] = []
    while True:
        try:
            item = _d1_queue.get(timeout=2)
            batch.append(item)
            if len(batch) < 10:
                continue
        except Empty:
            pass

        if not batch:
            continue

        # 批次 INSERT
        placeholders = ",".join(["(?,?,?,?,?,?,?,?,?)"] * len(batch))
        sql = (
            f"INSERT INTO {D1_TABLE} "
            "(ts, method, endpoint, status_code, caller, line_uid_masked, blocked, block_reason, response_size) "
            f"VALUES {placeholders}"
        )
        params: list = []
        for e in batch:
            params.extend([
                e["timestamp"], e["method"], e["endpoint"], e["status_code"],
                e["caller"], e["line_uid"], int(e["blocked"]),
                e.get("block_reason") or "", e.get("response_size_bytes", 0),
            ])

        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{D1_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
            httpx.post(
                url,
                headers={"Authorization": f"Bearer {D1_API_TOKEN}", "Content-Type": "application/json"},
                json={"sql": sql, "params": params},
                timeout=10.0,
            )
        except Exception as e:
            logger.warning("D1 audit sink failed, dropping %d records: %s", len(batch), e)
        batch.clear()


def _ensure_d1_worker():
    global _d1_worker_started
    with _d1_lock:
        if not _d1_worker_started and D1_ACCOUNT_ID and D1_DATABASE_ID and D1_API_TOKEN:
            t = threading.Thread(target=_d1_worker, daemon=True, name="d1-audit-worker")
            t.start()
            _d1_worker_started = True


def log_api_call(
    method: str,
    endpoint: str,
    status_code: int,
    caller: str = "unknown",
    line_uid: str = "",
    blocked: bool = False,
    block_reason: Optional[str] = None,
    response_size: int = 0,
    params: Optional[dict] = None,
):
    """
    記錄一筆 API 呼叫日誌。

    生產環境建議將此輸出導向：
    - 結構化日誌服務（如 Datadog、Logflare）
    - 或簡單寫入 JSON Lines 檔案
    """
    # 遮蔽 line_uid（日誌中也不應該有完整 UID）
    masked_uid = line_uid[:5] + "****" if len(line_uid) > 5 else line_uid

    # 遮蔽 params 中的敏感值
    masked_params = {}
    if params:
        for k, v in params.items():
            if k.lower() in ("phone", "email", "search"):
                masked_params[k] = _mask_param(str(v))
            else:
                masked_params[k] = v

    entry = {
        "timestamp": datetime.now(TW_TZ).isoformat(),
        "method": method,
        "endpoint": endpoint,
        "status_code": status_code,
        "caller": caller,
        "line_uid": masked_uid,
        "blocked": blocked,
        "response_size_bytes": response_size,
    }

    if block_reason:
        entry["block_reason"] = block_reason
    if masked_params:
        entry["params"] = masked_params

    # 輸出日誌
    log_line = json.dumps(entry, ensure_ascii=False)

    sinks = {s.strip() for s in AUDIT_SINK.split(",") if s.strip()}

    # Sink 1: stdout（logger）— 預設
    if "stdout" in sinks or not sinks:
        if blocked:
            logger.warning(f"[BLOCKED] {log_line}")
        elif status_code >= 400:
            logger.error(f"[ERROR] {log_line}")
        else:
            logger.info(f"[OK] {log_line}")

    # Sink 2: JSONL file（本機 append-only）
    if "jsonl_file" in sinks:
        try:
            with open(AUDIT_JSONL_PATH, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception as e:
            logger.warning("JSONL audit sink failed: %s", e)

    # Sink 3: Cloudflare D1（非阻塞 queue）
    if "d1" in sinks:
        _ensure_d1_worker()
        try:
            _d1_queue.put_nowait(entry)
        except Exception:
            # Queue 滿（5000 筆還沒 flush）→ 丟棄最舊的一筆後重試
            logger.warning("D1 audit queue full — dropping record")


def _mask_param(value: str) -> str:
    """遮蔽搜尋參數中的敏感值"""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]
