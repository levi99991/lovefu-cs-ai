#!/usr/bin/env python3
"""
大島樂眠 — API 連線測試腳本
部署到 Railway 後跑一次，確認 Shopline / WMS 都通。

用法：
  python scripts/test_api_connection.py

必須先設好環境變數（.env 或 Railway Variables）。
"""
import os
import sys
import json
import httpx
from datetime import datetime

# 顏色輸出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def section(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


# ============================================================
# 1. Shopline
# ============================================================
def test_shopline():
    section("Shopline API 連線測試")

    store = os.getenv("SHOPLINE_STORE_HANDLE", "")
    token = os.getenv("SHOPLINE_ACCESS_TOKEN", "")
    version = os.getenv("SHOPLINE_API_VERSION", "v20260301")
    mode = os.getenv("SHOPLINE_MODE", "mock")

    print(f"  商店代號：{store or '(未設定)'}")
    print(f"  模式：{mode}")
    print(f"  API 版本：{version}")
    print(f"  Token：{'***' + token[-8:] if len(token) > 8 else '(未設定)'}")
    print()

    if mode == "mock":
        warn("SHOPLINE_MODE=mock，跳過真實 API 測試（改為 production 再跑）")
        return True

    if not store or not token:
        fail("SHOPLINE_STORE_HANDLE 或 SHOPLINE_ACCESS_TOKEN 未設定")
        return False

    base = f"https://{store}.myshopline.com/admin/openapi/{version}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    passed = True

    # Test 1: Orders
    print(f"  測試 /orders.json ...")
    try:
        r = httpx.get(
            f"{base}/orders.json",
            headers=headers,
            params={"limit": "2", "sort_condition": "order_at:desc"},
            timeout=15.0,
        )
        if r.status_code == 200:
            data = r.json()
            orders = data.get("orders", [])
            ok(f"訂單查詢成功，回傳 {len(orders)} 筆")
            for o in orders[:2]:
                name = o.get("name", "?")
                status = o.get("status", "?")
                total = o.get("total_price", "?")
                items = len(o.get("line_items", []))
                print(f"    📦 {name} | {status} | NT${total} | {items} 件商品")
            # 驗證 key 結構
            if orders:
                expected_keys = {"name", "status", "financial_status", "line_items", "total_price"}
                actual_keys = set(orders[0].keys())
                missing = expected_keys - actual_keys
                if missing:
                    warn(f"訂單回應缺少預期欄位：{missing}")
                else:
                    ok("訂單回應結構與 mock 一致")
        elif r.status_code == 401:
            fail(f"Token 無效或已過期 (HTTP 401)")
            passed = False
        elif r.status_code == 403:
            fail(f"Token 權限不足 (HTTP 403)，需要 read_orders scope")
            passed = False
        else:
            fail(f"HTTP {r.status_code}: {r.text[:200]}")
            passed = False
    except Exception as e:
        fail(f"連線失敗：{e}")
        passed = False

    # Test 2: Customers
    print(f"\n  測試 /customers.json ...")
    try:
        r = httpx.get(
            f"{base}/customers.json",
            headers=headers,
            params={"limit": "1"},
            timeout=15.0,
        )
        if r.status_code == 200:
            data = r.json()
            custs = data.get("customers", [])
            ok(f"會員查詢成功，回傳 {len(custs)} 筆")
            if custs:
                c = custs[0]
                nm = f"{c.get('last_name', '')}{c.get('first_name', '')}"
                ok(f"會員結構正常：{nm[:1]}** | 訂單 {c.get('orders_count', '?')} 筆")
        elif r.status_code == 401:
            fail("Token 無效 (401)")
            passed = False
        else:
            warn(f"HTTP {r.status_code}（可能是 scope 問題，非阻塞）")
    except Exception as e:
        fail(f"連線失敗：{e}")
        passed = False

    # Test 3: Fulfillment
    print(f"\n  測試 /fulfillment_orders/fulfillment_orders_search.json ...")
    try:
        r = httpx.get(
            f"{base}/fulfillment_orders/fulfillment_orders_search.json",
            headers=headers,
            params={"limit": "1"},
            timeout=15.0,
        )
        if r.status_code == 200:
            ok("出貨查詢端點可用")
        else:
            warn(f"HTTP {r.status_code}（端點可能需要特定參數）")
    except Exception as e:
        warn(f"出貨查詢失敗（非阻塞）：{e}")

    return passed


# ============================================================
# 2. WMS 暢流
# ============================================================
def test_wms():
    section("WMS 暢流物流 API 連線測試")

    base_url = os.getenv("WMS_BASE_URL", "https://lovefu.wms.changliu.com.tw")
    api_id = os.getenv("WMS_API_ID", "")
    api_key = os.getenv("WMS_API_KEY", "")
    aes_key = os.getenv("WMS_PII_AES_KEY", "")
    mode = os.getenv("WMS_MODE", "mock")

    print(f"  Base URL：{base_url}")
    print(f"  模式：{mode}")
    print(f"  API_ID：{api_id[:10] + '...' if len(api_id) > 10 else api_id or '(未設定)'}")
    print(f"  API_KEY：{'***' + api_key[-8:] if len(api_key) > 8 else '(未設定)'}")
    print(f"  AES Key：{'***' + aes_key[-4:] if len(aes_key) > 4 else '(未設定)'}")
    print()

    if mode == "mock":
        warn("WMS_MODE=mock，跳過真實 API 測試（改為 production 再跑）")
        return True

    if not api_id or not api_key:
        fail("WMS_API_ID 或 WMS_API_KEY 未設定")
        return False

    passed = True

    # Test 1: Token（API_ID + API_KEY → BasicAuth → JWT）
    print(f"  測試 Token 取得 ...")
    import base64
    cred = base64.b64encode(f"{api_id}:{api_key}".encode()).decode()
    token = None
    try:
        r = httpx.get(
            f"{base_url}/api_v1/token/authorize.php",
            headers={"Authorization": f"Basic {cred}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            body = r.json()
            result = body.get("result", {})
            if not result.get("ok"):
                fail(f"result.ok=false: {result.get('message', '?')}")
                passed = False
            else:
                data = body.get("data", {})
                token = data.get("access_token")
                if token:
                    ok(f"Token 取得成功（前 8 碼：{token[:8]}...）")
                else:
                    fail(f"回應缺少 data.access_token：{list(body.keys())}")
                    passed = False
        else:
            fail(f"Token 取得失敗 HTTP {r.status_code}: {r.text[:200]}")
            passed = False
    except Exception as e:
        fail(f"連線失敗：{e}")
        return False

    if not token:
        fail("無法繼續測試（無 token）")
        return False

    headers = {"Authorization": f"Bearer {token}"}

    # Test 2: 門市查詢
    print(f"\n  測試 /api_v1/pos/store.php ...")
    try:
        r = httpx.get(f"{base_url}/api_v1/pos/store.php", headers=headers, timeout=10.0)
        if r.status_code == 200:
            body = r.json()
            if body.get("result", {}).get("ok"):
                rows = body.get("data", {}).get("rows", [])
                ok(f"門市查詢成功，{len(rows)} 家門市")
                for s in rows[:3]:
                    print(f"    🏪 {s.get('name', '?')} | {s.get('address', '?')}")
            else:
                warn(f"result.ok=false: {body.get('result', {}).get('message')}")
        else:
            warn(f"HTTP {r.status_code}")
    except Exception as e:
        warn(f"失敗：{e}")

    # Test 3: 庫存
    print(f"\n  測試 /api_v1/inventory/stock_query.php ...")
    try:
        r = httpx.get(
            f"{base_url}/api_v1/inventory/stock_query.php",
            headers=headers,
            params={"sku": "MAT-HILL-Q"},  # 測試用 SKU
            timeout=10.0,
        )
        if r.status_code == 200:
            body = r.json()
            if body.get("result", {}).get("ok"):
                rows = body.get("data", {}).get("rows", [])
                ok(f"庫存查詢成功，{len(rows)} 筆結果")
                for inv in rows[:3]:
                    print(f"    📦 {inv.get('name', '?')}（{inv.get('sku')}）: 庫存 {inv.get('stock', '?')}")
            else:
                warn(f"result.ok=false: {body.get('result', {}).get('message')}")
        else:
            warn(f"HTTP {r.status_code}（SKU 可能不存在，結構測試即可）")
    except Exception as e:
        warn(f"失敗：{e}")

    # Test 4: AES 解密驗證
    print(f"\n  測試 AES 解密 ...")
    if not aes_key:
        warn("WMS_PII_AES_KEY 未設定，加密欄位將無法解密")
    else:
        ok(f"AES Key 已設定（{len(aes_key)} 字元）")
        # 解密驗證需要從實際 API 回應拿加密值才能測，這裡只檢查 key 長度
        if len(aes_key) < 16:
            warn("AES Key 長度 < 16，可能不正確（AES-128 需要 16 bytes）")
        else:
            ok("AES Key 長度正確")

    return passed


# ============================================================
# 3. LLM
# ============================================================
def test_llm():
    section("LLM 連線測試")

    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    llm_mode = os.getenv("LLM_MODE", "production")

    print(f"  模式：{llm_mode}")
    print(f"  OpenAI Key：{'***' + openai_key[-4:] if len(openai_key) > 4 else '(未設定)'}")
    print(f"  Anthropic Key：{'***' + anthropic_key[-4:] if len(anthropic_key) > 4 else '(未設定)'}")
    print()

    if llm_mode == "mock":
        warn("LLM_MODE=mock，使用模擬回覆（不需 API key）")
        return True

    if not openai_key:
        fail("OPENAI_API_KEY 未設定，LLM_MODE=production 需要此 key")
        return False

    # Quick test
    print(f"  測試 OpenAI API ...")
    try:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "說 OK"}],
                "max_tokens": 5,
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            ok("OpenAI API 連線正常")
        elif r.status_code == 401:
            fail("OpenAI API Key 無效 (401)")
            return False
        else:
            warn(f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        fail(f"連線失敗：{e}")
        return False

    if anthropic_key:
        print(f"\n  測試 Anthropic API ...")
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "說 OK"}],
                    "max_tokens": 5,
                },
                timeout=10.0,
            )
            if r.status_code == 200:
                ok("Anthropic API 連線正常（fallback 可用）")
            else:
                warn(f"Anthropic HTTP {r.status_code}（fallback 不可用，非阻塞）")
        except Exception as e:
            warn(f"Anthropic 連線失敗（非阻塞）：{e}")

    return True


# ============================================================
# Main
# ============================================================
def main():
    print(f"\n{BOLD}🌙 大島樂眠 AI 輔睡員 — API 連線測試{RESET}")
    print(f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"環境：{os.getenv('ENV', 'development')}")

    results = {}
    results["shopline"] = test_shopline()
    results["wms"] = test_wms()
    results["llm"] = test_llm()

    section("總結")
    all_pass = True
    for name, passed in results.items():
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {name:12s} {status}")
        if not passed:
            all_pass = False

    if all_pass:
        print(f"\n  {GREEN}{BOLD}✓ 所有 API 連線正常，可以上線！{RESET}\n")
    else:
        print(f"\n  {RED}{BOLD}✗ 有 API 連線失敗，請修正後再部署。{RESET}\n")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
