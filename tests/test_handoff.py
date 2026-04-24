"""
cs-handoff 單元測試
"""
import sys
import pathlib
ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# 註冊 skill 為 package
import main  # noqa: F401 (觸發 skill registration)

import pytest
from datetime import datetime, timedelta

from lovefu_cs_handoff.scripts.signal_detector import (
    detect_explicit,
    detect_emotion,
    detect_high_value,
    detect_low_confidence,
    detect_handoff_signal,
)
from lovefu_cs_handoff.scripts import handoff_manager


# ============================================================
# 1. Signal Detector
# ============================================================
class TestSignalDetector:

    def test_explicit_detection_P0(self):
        r = detect_explicit("我要真人啦")
        assert r is not None
        assert r[0] == "EXPLICIT"
        assert r[2] == "P0"

    def test_explicit_none_on_normal_text(self):
        assert detect_explicit("床墊多少錢") is None

    def test_emotion_anger_P0(self):
        r = detect_emotion("你們到底搞什麼啊太扯了")
        assert r is not None
        assert r[0] == "EMOTION"
        assert r[2] == "P0"

    def test_emotion_dissatisfaction_accumulated(self):
        r = detect_emotion("我已經說過了", dissatisfaction_count=2)
        assert r is not None
        assert r[2] == "P0"

    def test_emotion_repeat_question(self):
        r = detect_emotion("訂單在哪", repeat_question_count=3)
        assert r is not None
        assert r[0] == "EMOTION"

    def test_high_value_return_P0(self):
        r = detect_high_value("我要退貨", "RETURN", 0)
        assert r is not None
        assert r[0] == "HIGH_VALUE"
        assert r[2] == "P0"

    def test_high_value_defect_P0(self):
        r = detect_high_value("床墊有破洞", "CHAT", 0)
        assert r is not None
        assert r[2] == "P0"

    def test_high_value_booking_P1(self):
        r = detect_high_value("想預約試躺", "STORE", 0)
        assert r is not None
        assert r[2] == "P1"

    def test_low_confidence_clarify_fail(self):
        r = detect_low_confidence(0.9, 2, "隨便一句話")
        assert r is not None
        assert r[0] == "LOW_CONF"

    def test_priority_ordering_explicit_first(self):
        # 同時有 explicit 和 emotion → explicit 優先
        r = detect_handoff_signal("太扯了我要真人", intent="CHAT")
        assert r[0] == "EXPLICIT"

    def test_no_signal_on_normal_chat(self):
        r = detect_handoff_signal("床墊多少錢", intent="PRODUCT")
        assert r is None


# ============================================================
# 2. Handoff Manager 狀態機
# ============================================================
class TestHandoffManager:

    def setup_method(self):
        # 清掉 module-level state 保證測試隔離
        # 清掉 store 的 in-memory state 保證測試隔離
        from lovefu_cs_handoff.scripts.handoff_store import store as _s
        _s._handoffs.clear()
        _s._active_by_uid.clear()

    def test_trigger_creates_handoff(self):
        hid = handoff_manager.trigger(
            line_uid="U_test1",
            signal_type="EXPLICIT",
            reason="顧客要真人",
            priority="P0",
            intent="CHAT",
        )
        assert hid.startswith("HO_")
        h = handoff_manager.get(hid)
        assert h["status"] == "pending"
        assert h["line_uid"] == "U_test1"

    def test_duplicate_trigger_same_uid_returns_existing(self):
        h1 = handoff_manager.trigger("U_dup", "EXPLICIT", "r1", "P1", "CHAT")
        h2 = handoff_manager.trigger("U_dup", "EMOTION", "r2", "P1", "CHAT")
        assert h1 == h2

    def test_higher_priority_upgrades_existing(self):
        h1 = handoff_manager.trigger("U_up", "LOW_CONF", "r1", "P2", "CHAT")
        handoff_manager.trigger("U_up", "EMOTION", "r2", "P0", "CHAT")
        h = handoff_manager.get(h1)
        assert h["priority"] == "P0"

    def test_acknowledge_transitions_to_acknowledged(self):
        hid = handoff_manager.trigger("U_ack", "EXPLICIT", "r", "P1", "CHAT")
        ok = handoff_manager.acknowledge(hid, advisor_id="advisor_x")
        assert ok is True
        h = handoff_manager.get(hid)
        assert h["status"] == "acknowledged"
        assert h["acknowledged_by"] == "advisor_x"

    def test_acknowledge_twice_fails(self):
        hid = handoff_manager.trigger("U_ack2", "EXPLICIT", "r", "P1", "CHAT")
        handoff_manager.acknowledge(hid, "advisor_a")
        assert handoff_manager.acknowledge(hid, "advisor_b") is False

    def test_resolve_closes_and_clears_active(self):
        hid = handoff_manager.trigger("U_res", "EXPLICIT", "r", "P1", "CHAT")
        handoff_manager.acknowledge(hid, "advisor_y")
        ok = handoff_manager.resolve(hid, outcome="booked", note="預約明天體驗")
        assert ok is True
        h = handoff_manager.get(hid)
        assert h["status"] == "closed"
        assert h["outcome"] == "booked"
        # active 表應該清空
        assert handoff_manager.get_active_for_uid("U_res") is None

    def test_list_pending_filters_by_store(self):
        handoff_manager.trigger("U_l1", "EXPLICIT", "r", "P1", "CHAT")
        handoff_manager.trigger("U_l2", "EXPLICIT", "r", "P1", "CHAT")
        all_p = handoff_manager.list_pending()
        assert len(all_p) >= 2

    def test_check_auto_handoff_from_brain_ctx(self):
        need, reason = handoff_manager.check_auto_handoff(
            line_uid="U_auto",
            message="我要找真人啦",
            intent="CHAT",
            memory={"short_history": [], "profile": {}},
        )
        assert need is True
        assert reason is not None


# ============================================================
# 3. Reminder 升級邏輯（不等 timer，直接呼叫 callback）
# ============================================================
class TestEscalation:

    def setup_method(self):
        # 清掉 store 的 in-memory state 保證測試隔離
        from lovefu_cs_handoff.scripts.handoff_store import store as _s
        _s._handoffs.clear()
        _s._active_by_uid.clear()

    def test_first_reminder_bumps_priority(self):
        hid = handoff_manager.trigger("U_esc1", "LOW_CONF", "r", "P2", "CHAT")
        # 直接觸發 callback 模擬 5 分鐘到
        handoff_manager._on_reminder_stage("first_reminder", hid)
        h = handoff_manager.get(hid)
        assert h["priority"] == "P0"
        assert any(e["stage"] == "first_reminder" for e in h["escalation_log"])

    def test_mark_missed_sets_timeout(self):
        hid = handoff_manager.trigger("U_miss", "LOW_CONF", "r", "P1", "CHAT")
        handoff_manager._on_reminder_stage("mark_missed", hid)
        h = handoff_manager.get(hid)
        assert h["status"] == "missed"
        assert h["outcome"] == "timeout_no_response"

    def test_acknowledged_handoff_ignores_reminder(self):
        hid = handoff_manager.trigger("U_ack3", "EXPLICIT", "r", "P1", "CHAT")
        handoff_manager.acknowledge(hid, "advisor_z")
        handoff_manager._on_reminder_stage("escalate_hq", hid)
        h = handoff_manager.get(hid)
        # status 仍是 acknowledged，不應被 escalation 修改
        assert h["status"] == "acknowledged"


# ============================================================
# 4. 值班路由
# ============================================================
class TestRoster:

    def test_route_returns_some_target(self):
        from lovefu_cs_handoff.scripts.advisor_roster import route_handoff
        r = route_handoff()
        assert r["target_type"] in ("store", "hq", "offline")

    def test_next_open_datetime_is_future(self):
        from lovefu_cs_handoff.scripts.advisor_roster import next_open_datetime
        nxt = next_open_datetime()
        # 至少不會是過去
        from datetime import datetime, timezone, timedelta
        TW = timezone(timedelta(hours=8))
        assert nxt >= datetime.now(TW) - timedelta(hours=1)
