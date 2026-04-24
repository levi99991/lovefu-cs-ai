"""
回歸測試 runner — 讀取 scenarios.yaml，對 /chat 端點逐項打測試

使用：
  # 本地 mock 模式，不需 OpenAI key
  pytest tests/test_scenarios.py -v

  # 對正式 deploy 打
  TARGET_URL=https://lovefu-ai.up.railway.app pytest tests/test_scenarios.py -v

注意：使用 GitHub Actions / Railway pre-deploy hook 每次更動 prompt 自動跑。
"""
import os
import time
from pathlib import Path

import httpx
import pytest
import yaml  # pip install pyyaml

TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8000")
SCENARIOS_FILE = Path(__file__).parent / "scenarios.yaml"


def load_scenarios():
    with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def scenarios():
    return load_scenarios()


@pytest.mark.parametrize("sc", load_scenarios(), ids=lambda s: s["id"])
def test_scenario(sc):
    """逐項打 /chat 驗證回覆"""
    payload = {
        "line_uid": f"U_test_{sc['id']}_{int(time.time())}",
        "message": sc["message"],
        "idempotency_key": f"key_{sc['id']}_{int(time.time())}",
    }
    if sc.get("omnichat_event"):
        payload["omnichat_event"] = sc["omnichat_event"]

    r = httpx.post(f"{TARGET_URL}/chat", json=payload, timeout=30.0)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    data = r.json()

    # 靜默檢查（Omnichat 共存）
    if sc.get("expected_silent"):
        assert data.get("silent") is True, f"{sc['id']} 應靜默但 silent={data.get('silent')}"
        return  # 靜默時不檢查內容

    # 轉人工檢查
    if sc.get("expected_need_human"):
        assert data.get("need_human") is True, f"{sc['id']} 應轉人工"

    # intent 檢查（軟性，允許 classifier 近似）
    if sc.get("expected_intent") and not sc.get("expected_silent"):
        got = data.get("intent")
        # 允許 ORDER / CARGO 互相替代（相近意圖）
        acceptable = {sc["expected_intent"]}
        if sc["expected_intent"] == "CARGO":
            acceptable.add("ORDER")
        if sc["expected_intent"] == "ORDER":
            acceptable.add("CARGO")
        assert got in acceptable, f"{sc['id']} intent={got} 不在 {acceptable}"

    # 必含關鍵字（任一）
    reply = data.get("reply", "")
    if sc.get("must_contain"):
        hit = any(kw in reply for kw in sc["must_contain"])
        assert hit, f"{sc['id']} reply={reply!r} 未包含任一 {sc['must_contain']}"

    # 絕不可出現
    if sc.get("must_not_contain"):
        for bad in sc["must_not_contain"]:
            assert bad not in reply, f"{sc['id']} reply 出現禁忌詞 {bad!r}"
