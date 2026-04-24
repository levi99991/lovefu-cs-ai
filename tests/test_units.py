"""
大島樂眠 — 單元測試（不依賴 HTTP server）
覆蓋 Round 3 新增的關鍵模組：wms_cache、pii_decrypt、api_guard、audit_logger
以及既有的 memory_store.customer_journey 合併邏輯。

使用：
    pytest tests/test_units.py -v
"""
import os
import sys
import time
from pathlib import Path

import pytest

# 讓 tests 目錄下的測試可以 import main.py 所註冊的 package 別名
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# 觸發 main.py 的 skill package 註冊
os.environ.setdefault("WMS_MODE", "mock")
os.environ.setdefault("SHOPLINE_MODE", "mock")
os.environ.setdefault("MEMORY_BACKEND", "dict")
os.environ.setdefault("AUDIT_SINK", "stdout")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("LLM_MODE", "mock")

import main  # noqa: E402  — 執行 package 別名註冊

from lovefu_cs_logistics.scripts import wms_cache, wms_client, pii_decrypt  # noqa: E402
from lovefu_cs_guard.scripts import api_guard, audit_logger  # noqa: E402
from lovefu_cs_memory.scripts import memory_store  # noqa: E402


# ====================================================================
# wms_cache — 終態 24hr / 中途 5min / partial miss
# ====================================================================
class TestWMSCache:
    def setup_method(self):
        wms_cache.clear_all()
        wms_cache._dict_cache.clear()

    def test_terminal_state_uses_long_ttl(self):
        """終態（F/送達）→ 24hr TTL"""
        calls = []
        def fetcher(nos):
            calls.append(list(nos))
            return [{"order_no": n, "timelines": [{"status": "送達"}]} for n in nos]

        r1 = wms_cache.cached_cargo_status(["L001"], fetcher)
        r2 = wms_cache.cached_cargo_status(["L001"], fetcher)

        assert len(r1) == 1 and len(r2) == 1
        assert len(calls) == 1, "終態應命中 cache，不應再次 fetch"

    def test_mid_state_uses_short_ttl(self):
        """中途（派送中）→ 5min TTL，但同一 5min 內仍命中"""
        calls = []
        def fetcher(nos):
            calls.append(list(nos))
            return [{"order_no": n, "timelines": [{"status": "派送中"}]} for n in nos]

        wms_cache.cached_cargo_status(["L002"], fetcher)
        wms_cache.cached_cargo_status(["L002"], fetcher)
        assert len(calls) == 1, "5min 內同貨態應命中 cache"

    def test_partial_miss_only_fetches_missing(self):
        """部分 hit 部分 miss → 只對 missing 發查詢"""
        calls = []
        def fetcher(nos):
            calls.append(list(nos))
            return [{"order_no": n, "timelines": [{"status": "送達"}]} for n in nos]

        wms_cache.cached_cargo_status(["L003"], fetcher)  # miss → fetch
        wms_cache.cached_cargo_status(["L003", "L004"], fetcher)  # L003 hit, L004 miss

        assert calls == [["L003"], ["L004"]], f"partial miss 錯誤：{calls}"

    def test_cache_expiry(self):
        """TTL 過期後應重新 fetch"""
        calls = []
        def fetcher(nos):
            calls.append(list(nos))
            return [{"order_no": n, "timelines": [{"status": "派送中"}]} for n in nos]

        wms_cache.cached_cargo_status(["L005"], fetcher)
        # 手動過期：把 expire_ts 設為 0（tuple 是 (expire_ts, value)）
        for key in list(wms_cache._dict_cache.keys()):
            _, v = wms_cache._dict_cache[key]
            wms_cache._dict_cache[key] = (0, v)
        wms_cache.cached_cargo_status(["L005"], fetcher)
        assert len(calls) == 2, "過期後應重新 fetch"


# ====================================================================
# pii_decrypt — AES 解密即遮罩（明文不外流）
# ====================================================================
class TestPIIDecrypt:
    def test_mask_after_decrypt_name(self):
        assert pii_decrypt._mask_after_decrypt("receiver_name", "王小明") == "王*明"
        assert pii_decrypt._mask_after_decrypt("receiver_name", "李") == "*"

    def test_mask_after_decrypt_phone(self):
        assert pii_decrypt._mask_after_decrypt("receiver_phone", "0912345678") == "****5678"
        assert pii_decrypt._mask_after_decrypt("buyer_phone", "0912") == "****0912"

    def test_mask_after_decrypt_address(self):
        m = pii_decrypt._mask_after_decrypt("receiver_address", "台北市大安區忠孝東路四段 100 號")
        assert "忠孝東" in m or "大安區" in m
        assert "100" not in m, "門牌號絕不得外洩"

    def test_decrypt_and_mask_passthrough_non_encrypted(self):
        """非加密欄位（order_no、items）原樣保留"""
        data = {
            "order_no": "L001",
            "items": [{"sku": "MAT-HILL-Q", "qty": 1}],
            "receiver_name": "plaintext_not_b64",  # 非 base64，不會觸發解密
        }
        result = pii_decrypt.decrypt_and_mask(data)
        assert result["order_no"] == "L001"
        assert result["items"][0]["sku"] == "MAT-HILL-Q"
        # 非 AES 格式 → 保留原值（不會誤判解密）
        assert result["receiver_name"] == "plaintext_not_b64"


# ====================================================================
# api_guard — WMS 白名單/黑名單/關鍵字過濾
# ====================================================================
class TestAPIGuard:
    def test_whitelist_path_allowed(self):
        """合法 GET 路徑通過白名單比對"""
        # 白名單中第一條應為訂單查詢，查不到具體也能通過 prefix 比對
        assert any(
            api_guard._is_path_allowed(p, api_guard.ALLOWED_WMS_PATHS)
            for p in [p.replace("{", "").replace("}", "") for p in api_guard.ALLOWED_WMS_PATHS]
        )

    def test_blacklist_has_dangerous_paths(self):
        """黑名單必須包含高風險寫入路徑"""
        blacklist = api_guard.WMS_BLACKLIST_PATHS
        assert any("cancel" in p for p in blacklist), "必須封鎖 cancel"
        assert any("request" in p or "confirm" in p for p in blacklist), \
            "必須封鎖 logistics/request 或 logistics/confirm（下單物流會花錢）"
        assert any("order_add" in p for p in blacklist), "必須封鎖 order_add"
        assert any("stock_update" in p for p in blacklist), "必須封鎖 stock_update"

    def test_no_wms_safe_post_exists(self):
        """架構強制：沒有 wms_safe_post 就沒辦法從 root 寫入"""
        assert not hasattr(api_guard, "wms_safe_post"), \
            "不得提供 wms_safe_post！WMS 必須 GET-only"

    def test_keyword_filter_blocks_write_verbs(self):
        """BLOCKED_KEYWORDS 覆蓋核心寫入動詞"""
        for bad_path in [
            "/admin/openapi/v1/orders/create",
            "/admin/order/cancel/123",
            "/admin/stock/update",
            "/admin/user/delete",
            "/admin/coupon/refund",
        ]:
            assert api_guard._contains_blocked_keyword(bad_path), \
                f"{bad_path} 應被關鍵字過濾攔下"

    def test_wms_safe_get_returns_none_on_blacklist(self):
        """wms_safe_get 對黑名單路徑回傳 None（不 raise，避免打斷對話流程）"""
        # 取一個黑名單路徑
        blacklisted = api_guard.WMS_BLACKLIST_PATHS[0]
        result = api_guard.wms_safe_get(blacklisted)
        assert result is None, "黑名單路徑應回傳 None"

    def test_wms_safe_get_returns_none_on_unknown_path(self):
        """wms_safe_get 對未白名單路徑也必須回傳 None"""
        result = api_guard.wms_safe_get("/api_v1/totally/unknown/endpoint")
        assert result is None, "未白名單路徑應回傳 None"


# ====================================================================
# audit_logger — 多 sink（stdout + JSONL file）
# ====================================================================
class TestAuditLogger:
    def test_jsonl_sink(self, tmp_path):
        """JSONL sink：每一筆寫一行，line_uid 已遮罩"""
        log_file = tmp_path / "audit.jsonl"
        # 動態切 sink
        audit_logger.AUDIT_SINK = f"jsonl_file"
        audit_logger.AUDIT_JSONL_PATH = str(log_file)

        audit_logger.log_api_call(
            "GET", "/admin/openapi/v1/orders/123",
            200, caller="cs_shopline", line_uid="U12345abcdef",
            response_size=1024,
        )
        audit_logger.log_api_call(
            "POST", "/order/cancel",
            403, caller="cs_logistics",
            blocked=True, block_reason="blacklist_hit",
        )

        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        import json
        e1 = json.loads(lines[0])
        e2 = json.loads(lines[1])
        assert e1["blocked"] is False
        assert e1["line_uid"] == "U1234****"  # 遮罩
        assert e2["blocked"] is True
        assert e2["block_reason"] == "blacklist_hit"

        # 還原
        audit_logger.AUDIT_SINK = "stdout"


# ====================================================================
# memory_store — customer_journey 合併去重
# ====================================================================
class TestCustomerJourney:
    def setup_method(self):
        # 清理 dict backend
        memory_store._dict_store.clear() if hasattr(memory_store, "_dict_store") else None

    def test_tried_products_dedupe(self):
        memory_store.update_profile("U_test_j1", customer_journey={
            "stage": "試躺中",
            "tried_products": ["山丘床墊", "月眠枕"],
        })
        memory_store.update_profile("U_test_j1", customer_journey={
            "tried_products": ["月眠枕", "冰島床墊"],  # 重複應去重
        })
        cj = memory_store.get_memory_for_prompt("U_test_j1")["customer_journey"]
        assert set(cj["tried_products"]) == {"山丘床墊", "月眠枕", "冰島床墊"}
        assert cj["stage"] == "試躺中"  # 前次值保留（merge，非覆蓋）

    def test_journey_merge_keeps_unrelated_fields(self):
        memory_store.update_profile("U_test_j2", member_name="阿明", member_tier="海獺金卡")
        memory_store.update_profile("U_test_j2", customer_journey={"stage": "已下單"})
        mem = memory_store.get_memory_for_prompt("U_test_j2")
        # profile_text 應同時包含會員資訊 + 跨渠道狀態
        assert "阿明" in mem["profile_text"] or "海獺金卡" in mem["profile_text"]
        assert mem["customer_journey"]["stage"] == "已下單"


# ====================================================================
# wms_client — LLM 格式化（含「最後更新 X 分鐘前」人性化時戳）
# ====================================================================
class TestWMSClient:
    def test_format_orders_shows_product_and_tracking(self):
        wms_cache.clear_all()
        orders = wms_client.query_orders(["L20260415001"])
        text = wms_client.format_orders_for_llm(orders)
        assert "訂單" in text
        assert "山丘床墊" in text
        assert "8901234567890" in text  # 託運單號

    def test_format_cargo_includes_humanized_timestamp(self):
        wms_cache.clear_all()
        cargo = wms_client.query_cargo_status(["L20260415001"])
        text = wms_client.format_cargo_status_for_llm(cargo)
        assert "最新狀態" in text, "貨態必須顯示最新狀態"
        assert "派送中" in text
        assert "WMS 同步快照" in text, "貨態必須提醒資料非即時"

    def test_format_inventory(self):
        wms_cache.clear_all()
        stock = wms_client.query_inventory(["MAT-HILL-Q", "PIL-MOON-3"])
        text = wms_client.format_inventory_for_llm(stock)
        assert "山丘床墊" in text or "MAT-HILL-Q" in text
        assert "件" in text
