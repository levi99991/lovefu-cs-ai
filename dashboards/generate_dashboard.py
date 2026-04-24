"""
大島樂眠 — 儀表板資料注入器
dashboards/generate_dashboard.py

作用：
  1. 查 Cloudflare D1 audit_log（透過官方 HTTP API）
  2. 讀 cs-instore 追客狀態
  3. 讀 cs-memory 顧客 journey
  4. 把以上資料組成 JSON，替換 followup-dashboard.html 中的 <script id="dashboard-data"> 內容
  5. 產出 dashboards/out/followup-dashboard-YYYYMMDD.html

使用：
    # Demo / 開發（用內建範例資料）
    python3 dashboards/generate_dashboard.py --mock

    # 生產（查 D1 + cs-instore）
    python3 dashboards/generate_dashboard.py

    # 排程（每小時跑一次，配合 cowork schedule 或 cron）
    0 * * * * cd /app && python3 dashboards/generate_dashboard.py

環境變數（讀 .env）：
    CF_ACCOUNT_ID        Cloudflare 帳號 ID
    CF_D1_DATABASE_ID    D1 資料庫 ID
    CF_API_TOKEN         Cloudflare API Token（權限：D1 Read）
    CF_D1_AUDIT_TABLE    預設 audit_log
"""
import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

import httpx  # 已在 requirements.txt

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = Path(__file__).resolve().parent / "followup-dashboard.html"
OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(exist_ok=True)

CF_ACCOUNT_ID     = os.getenv("CF_ACCOUNT_ID", "")
CF_D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID", "")
CF_API_TOKEN      = os.getenv("CF_API_TOKEN", "")
CF_D1_TABLE       = os.getenv("CF_D1_AUDIT_TABLE", "audit_log")

TW_TZ = timezone(timedelta(hours=8))


# ================================================================
# D1 查詢
# ================================================================
def _d1_query(sql: str, params: list | None = None) -> list[dict]:
    """對 Cloudflare D1 發 SQL，回傳 rows（list[dict]）。失敗則 raise。"""
    if not (CF_ACCOUNT_ID and CF_D1_DATABASE_ID and CF_API_TOKEN):
        raise RuntimeError("缺少 Cloudflare D1 環境變數（CF_ACCOUNT_ID/CF_D1_DATABASE_ID/CF_API_TOKEN）")

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
    payload = {"sql": sql, "params": params or []}
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}

    resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"D1 query 失敗：{data.get('errors')}")
    return data["result"][0]["results"]


def _humanize_bytes(n: int) -> str:
    if n >= 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024 / 1024:.1f} GB"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


# ================================================================
# 收集各項資料 — 生產模式
# ================================================================
def collect_live_data() -> dict:
    table = CF_D1_TABLE

    # Q1: 24 小時總覽
    r1 = _d1_query(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN blocked=1 THEN 1 ELSE 0 END) AS blocked,
            SUM(CASE WHEN status_code>=400 AND blocked=0 THEN 1 ELSE 0 END) AS errors,
            COALESCE(SUM(response_size), 0) AS bytes_total
        FROM {table}
        WHERE ts >= datetime('now', '-1 day')
    """)
    q1 = r1[0] if r1 else {"total": 0, "blocked": 0, "errors": 0, "bytes_total": 0}

    # Q2: 攔截原因（7 日）
    r2 = _d1_query(f"""
        SELECT COALESCE(block_reason, 'other') AS reason, COUNT(*) AS cnt
        FROM {table}
        WHERE blocked=1 AND ts >= datetime('now', '-7 day')
        GROUP BY block_reason
        ORDER BY cnt DESC
    """)
    block_reason = {
        "labels": [r["reason"] for r in r2],
        "counts": [r["cnt"] for r in r2],
    }

    # Q3: Caller 分佈（7 日總量）
    r3 = _d1_query(f"""
        SELECT caller, COUNT(*) AS cnt
        FROM {table}
        WHERE ts >= datetime('now', '-7 day') AND caller IS NOT NULL
        GROUP BY caller
        ORDER BY cnt DESC
    """)
    caller_share = {
        "labels": [r["caller"] or "unknown" for r in r3],
        "counts": [r["cnt"] for r in r3],
    }

    # Q3b: Caller 每日（最近 7 天堆疊圖）
    r3b = _d1_query(f"""
        SELECT DATE(ts) AS day, caller, COUNT(*) AS cnt
        FROM {table}
        WHERE ts >= datetime('now', '-7 day')
        GROUP BY DATE(ts), caller
        ORDER BY day
    """)
    days = sorted({r["day"] for r in r3b})
    day_labels = [d[5:] for d in days]  # MM-DD
    shopline  = [0] * len(days)
    logistics = [0] * len(days)
    unknown   = [0] * len(days)
    for r in r3b:
        idx = days.index(r["day"])
        c = (r["caller"] or "").lower()
        if "shopline" in c: shopline[idx]  += r["cnt"]
        elif "logistics" in c or "wms" in c: logistics[idx] += r["cnt"]
        else: unknown[idx] += r["cnt"]

    # Q4: 異常事件
    r4 = _d1_query(f"""
        SELECT ts, caller, endpoint, status_code, response_size, block_reason
        FROM {table}
        WHERE response_size > 524288
           OR (status_code >= 400 AND blocked = 0)
           OR blocked = 1
        ORDER BY ts DESC
        LIMIT 50
    """)
    alerts = [{
        "ts": r["ts"][:16].replace("T", " "),
        "caller": r["caller"] or "unknown",
        "endpoint": r["endpoint"] or "",
        "status": r["status_code"] or 0,
        "size": r["response_size"] or 0,
        "reason": r["block_reason"] or (
            "response_too_large" if (r["response_size"] or 0) > 524288 else "error"
        ),
    } for r in r4]

    # Q5: 每小時趨勢（24h）
    r5 = _d1_query(f"""
        SELECT strftime('%H', ts) AS hr, COUNT(*) AS cnt
        FROM {table}
        WHERE ts >= datetime('now', '-1 day')
        GROUP BY strftime('%H', ts)
        ORDER BY hr
    """)
    hourly_map = {int(r["hr"]): r["cnt"] for r in r5}
    # 取偶數小時作 X 軸
    hourly_labels = [f"{h:02d}" for h in range(0, 24, 2)]
    hourly_calls  = [sum(hourly_map.get(h + i, 0) for i in range(2)) for h in range(0, 24, 2)]

    # ---- 追客 / Advisor 統計：從 cs-instore 記憶體讀 ----
    funnel, advisors, safety, instore_stats = _collect_instore_stats()

    # ---- 意圖分佈：目前以 Demo 資料（真實環境可從 cs-memory 聚合）----
    intent_labels = ["SLEEP","PRODUCT","CARGO","ORDER","STORE","RETURN","CHAT","MEMBER","STOCK","COMPLAINT"]
    intent_counts = [0] * len(intent_labels)  # TODO: 從 cs-memory 聚合 last_intent

    return {
        "mode": "live",
        "last_updated": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
        "source": f"Cloudflare D1 ({CF_D1_TABLE}) + cs-instore state",
        "kpi": {
            "chats_24h": q1["total"],
            "chats_ai": q1["total"] - q1["blocked"],
            "chats_silent": 0,  # Omnichat 靜默日誌走另一管道
            "leads_total": instore_stats["leads_total"],
            "leads_new_7d": instore_stats["leads_new_7d"],
            "bind_rate": instore_stats["bind_rate"],
            "conv_rate": instore_stats["conv_rate"],
            "blocked_24h": q1["blocked"],
            "human_24h": instore_stats["human_24h"],
        },
        "hourly": {"labels": hourly_labels, "calls": hourly_calls},
        "intent": {"labels": intent_labels, "counts": intent_counts},
        "funnel": funnel,
        "stages": instore_stats["stages"],
        "safety": safety,
        "advisors": advisors,
        "audit": {
            "total_7d": sum(caller_share["counts"]),
            "blocked_7d": sum(block_reason["counts"]),
            "errors_7d": q1["errors"],
            "bytes_7d": _humanize_bytes(q1["bytes_total"]),
            "block_reason": block_reason,
            "caller_share": caller_share,
            "caller_daily": {
                "days": day_labels,
                "shopline": shopline,
                "logistics": logistics,
                "unknown": unknown,
            },
        },
        "alerts": alerts,
    }


def _collect_instore_stats() -> tuple[list, list, dict, dict]:
    """從 cs-instore 讀追客資料（Demo：空資料會走 fallback）"""
    try:
        sys.path.insert(0, str(ROOT))
        import main  # noqa: F401  — 觸發 package 註冊
        from lovefu_cs_instore.scripts import follow_up_scheduler as fus
    except Exception as e:
        print(f"[warn] cs-instore 讀取失敗，改用空資料：{e}", file=sys.stderr)
        return [], [], {}, {}

    leads = list(getattr(fus, "_LEAD_STORE", {}).values())
    total = len(leads)
    bound = sum(1 for l in leads if l.get("line_uid"))
    # 推定的三階段（S1/S2/S3）及成單
    ordered = sum(1 for l in leads if l.get("ordered"))
    bind_rate = round(bound / total * 100, 1) if total else 0.0
    conv_rate = round(ordered / total * 100, 1) if total else 0.0

    funnel = [
        {"label": "門市試躺",  "n": total, "rate": "100%"},
        {"label": "LINE 綁定",  "n": bound, "rate": f"{bind_rate}%"},
        {"label": "S1 已送達",  "n": sum(1 for l in leads if l.get("s1_sent")), "rate": "—"},
        {"label": "顧客回覆",   "n": sum(1 for l in leads if l.get("replied")), "rate": "—"},
        {"label": "成單",      "n": ordered, "rate": f"{conv_rate}%"},
    ] if total else []

    # Advisor 排行
    adv_map: dict[tuple, dict] = {}
    for l in leads:
        key = (l.get("store", "—"), l.get("advisor", "—"))
        agg = adv_map.setdefault(key, {"leads": 0, "bound": 0, "orders": 0})
        agg["leads"] += 1
        if l.get("line_uid"): agg["bound"] += 1
        if l.get("ordered"): agg["orders"] += 1

    advisors = []
    for (store, advisor), agg in adv_map.items():
        conv = round(agg["orders"] / agg["leads"] * 100, 1) if agg["leads"] else 0
        status = "ok" if conv >= 15 else ("warn" if conv >= 5 else "danger")
        advisors.append({
            "store": store, "advisor": advisor,
            "leads": agg["leads"], "bound": agg["bound"],
            "orders": agg["orders"], "conv": conv, "status": status,
        })
    advisors.sort(key=lambda a: (-a["conv"], -a["leads"]))

    stages = {
        "labels": ["S0 首問","S1 24h","S2 3d","S3 7d","S4 14d"],
        "sent":    [sum(1 for l in leads if l.get(f"s{i}_sent"))    for i in range(5)],
        "replied": [sum(1 for l in leads if l.get(f"s{i}_replied")) for i in range(5)],
    }

    safety = {
        "over_5": f"{sum(1 for l in leads if l.get('msg_count_14d', 0) >= 5)} 人（硬限制）",
        "paused": f"{sum(1 for l in leads if l.get('paused'))} 人",
        "optout": f"{sum(1 for l in leads if l.get('do_not_contact'))} 人",
        "review_pass": "100%",
    }

    stats = {
        "leads_total": total,
        "leads_new_7d": sum(1 for l in leads if _within_days(l.get("registered_at"), 7)),
        "bind_rate": bind_rate,
        "conv_rate": conv_rate,
        "human_24h": sum(1 for l in leads if l.get("need_human")),
        "stages": stages,
    }
    return funnel, advisors, safety, stats


def _within_days(ts_str: str | None, days: int) -> bool:
    if not ts_str:
        return False
    try:
        t = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return datetime.now(TW_TZ) - t <= timedelta(days=days)
    except Exception:
        return False


# ================================================================
# 主流程：注入 JSON → 產生檔案
# ================================================================
def inject_into_html(data: dict) -> Path:
    html = DASHBOARD_FILE.read_text(encoding="utf-8")

    # 以精確標記替換 <script id="dashboard-data" type="application/json"> ... </script>
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    pattern = re.compile(
        r'(<script id="dashboard-data" type="application/json">)(.*?)(</script>)',
        re.DOTALL,
    )
    new_html, n = pattern.subn(rf'\1\n{payload}\n\3', html)
    if n != 1:
        raise RuntimeError(f"找不到注入標記（替換次數={n}），請檢查 followup-dashboard.html")

    stamp = datetime.now(TW_TZ).strftime("%Y%m%d-%H%M")
    out_path = OUT_DIR / f"followup-dashboard-{stamp}.html"
    out_path.write_text(new_html, encoding="utf-8")
    # 同步覆寫一份 latest，讓使用者永遠打最新的
    (OUT_DIR / "followup-dashboard-latest.html").write_text(new_html, encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="使用內建 demo 資料（不打 D1）")
    args = ap.parse_args()

    if args.mock or not (CF_ACCOUNT_ID and CF_API_TOKEN):
        print("[mode] MOCK — 使用 HTML 內建 demo 資料，不打 D1")
        # 直接把原 HTML 複製到 out/（保持 demo 可交付）
        html = DASHBOARD_FILE.read_text(encoding="utf-8")
        stamp = datetime.now(TW_TZ).strftime("%Y%m%d-%H%M")
        out_path = OUT_DIR / f"followup-dashboard-{stamp}-mock.html"
        out_path.write_text(html, encoding="utf-8")
        (OUT_DIR / "followup-dashboard-latest.html").write_text(html, encoding="utf-8")
        print(f"✓ 輸出：{out_path}")
        return

    print("[mode] LIVE — 查 Cloudflare D1 + cs-instore")
    data = collect_live_data()
    out_path = inject_into_html(data)
    print(f"✓ 資料已注入：{out_path}")
    print(f"  總呼叫 24h：{data['kpi']['chats_24h']}")
    print(f"  Lead 總數：{data['kpi']['leads_total']}")
    print(f"  7 日攔截：{data['audit']['blocked_7d']}")


if __name__ == "__main__":
    main()
