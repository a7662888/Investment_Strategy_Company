# -*- coding: utf-8 -*-
"""
app.py 整合最小測試(Codex 要求:啟動 / /api/health / next-day-plan schema + 模擬持股)。
不依賴外部網路:用合成 rows 直接測 plan schema 與校準模型 additive 欄位;
另在執行緒啟動伺服器測 /api/health。

跑法:python tests/test_app_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod

REQUIRED_PLAN_KEYS = {
    "symbol", "as_of", "last_close", "held", "cost", "unrealized_gain",
    "score", "action", "reasons", "rule", "future_knowledge_used",
}


def _fake_rows(n: int = 200) -> list[dict]:
    rows = []
    px = 100.0
    d = date(2024, 1, 1)
    for i in range(n):
        px *= 1.0 + (0.004 if i % 5 else -0.003)  # 緩升帶回檔
        d += timedelta(days=1)
        rows.append({
            "date": d.isoformat(), "symbol": "9999.TW",
            "open": f"{px*0.997:.4f}", "high": f"{px*1.01:.4f}",
            "low": f"{px*0.99:.4f}", "close": f"{px:.4f}", "volume": "1000000",
        })
    return rows


def test_plan_schema_and_calibrated():
    rows = _fake_rows()
    plan = appmod.plan_next_session("9999.TW", rows, None)
    missing = REQUIRED_PLAN_KEYS - set(plan)
    assert not missing, f"next-day-plan 缺欄位:{missing}"
    # additive 校準模型欄位應存在(artifact 在 → enrich;不在 → 略過但 schema 仍完整)
    model = plan["model"]
    assert model["name"] == "interpretable_technical_ensemble_v1"
    if "calibrated_probability_up" in model:
        assert isinstance(model["calibrated_probability_up"], (int, float))
        assert model["calibrated_evidence"]  # 樣本外指標
    print("✅ next-day-plan schema 完整;校準欄位 additive 正常")


def test_held_position():
    rows = _fake_rows()
    plan = appmod.plan_next_session("9999.TW", rows, {"shares": 1000, "cost": 90.0})
    assert plan["held"] is True
    assert plan["unrealized_gain"] is not None
    print("✅ 模擬持股:held/unrealized_gain 正確")


def test_health_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/api/health/live", "/api/health/ready", "/api/data-status", "/api/decision-ledger?limit=1"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                payload = json.loads(r.read().decode("utf-8"))
            assert isinstance(payload, dict)
        print(f"✅ Phase 0 health/data/ledger endpoints 正常(port {port})")
    finally:
        server.shutdown()


def test_agent_signals_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    candidate = {"symbol": "2330.TW", "last_date": date.today().isoformat(), "last_close": 1000, "score": 8, "grade": "A", "reasons": ["test"]}
    body = json.dumps({"end": date.today().isoformat(), "limit": 5}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/agent-signals",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with patch.object(appmod, "discover_candidates", return_value={"candidates": [candidate]}), \
             patch.object(appmod, "discover_antigravity_candidates", return_value=[candidate]), \
             patch.object(appmod, "discover_claude_candidates", return_value=[candidate]), \
             patch.object(appmod, "freeze_candidate_groups", return_value={"status": "degraded", "added": 3}):
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        assert len(payload["codex"]["candidates"]) == 1
        assert len(payload["antigravity"]) == 1
        assert len(payload["claude"]) == 1
        assert payload["ledger"]["added"] == 3
        print("✅ /api/agent-signals 合併三家並接入ledger")
    finally:
        server.shutdown()


def test_value_current_and_portfolio_endpoints():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = {
        "as_of": "2026-07-29", "coverage": {"mother_pool": 100, "quality_covered": 1},
        "top_picks": [], "waiting_list": [],
        "evaluations": [{
            "symbol": "2330.TW", "name": "台積電", "price": 1000.0, "action": "accumulate",
            "quality_pass": True, "risk_tier": "一般", "valuation_pct": 20.0,
            "trend": "上升趨勢", "is_etf": False,
        }],
    }
    try:
        with patch("company.model.current_state.load_current_state", return_value=(state, {"durable": True})):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/value-current", timeout=5) as response:
                current = json.loads(response.read().decode("utf-8"))
            body = json.dumps({"positions": [{"symbol": "2330.TW", "shares": 10, "cost": 900}]}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/value-portfolio", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                portfolio = json.loads(response.read().decode("utf-8"))
        assert current["coverage"]["mother_pool"] == 100
        assert portfolio["personal_data_saved"] is False
        assert portfolio["actions"][0]["symbol"] == "2330.TW"
        print("✅ daily value current-state / portfolio endpoints 正常")
    finally:
        server.shutdown()


def test_private_positions_endpoints():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old_token = os.environ.get("POSITIONS_SYNC_TOKEN")
    os.environ["POSITIONS_SYNC_TOKEN"] = "integration-sync-key"
    document = {
        "schema_version": 1, "version": 2, "updated_at": "2026-08-04T00:00:00+00:00",
        "positions": [{"symbol": "0056.TW", "shares": 1000.0, "cost": 54.0}],
    }
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/positions", timeout=5)
            raise AssertionError("unauthenticated positions read should fail")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        auth = {"Authorization": "Bearer integration-sync-key"}
        with patch("company.model.positions.load_positions", return_value=(document, {"source": "github", "durable": True})):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/positions", headers=auth)
            with urllib.request.urlopen(request, timeout=5) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        assert loaded["version"] == 2 and loaded["storage"]["durable"] is True

        body = json.dumps({"version": 2, "positions": document["positions"]}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/positions", data=body,
            headers={**auth, "Content-Type": "application/json"}, method="POST",
        )
        saved = {**document, "version": 3}
        with patch("company.model.positions.save_positions", return_value=(saved, {"durable": True, "remote_saved": True})):
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        assert result["version"] == 3 and result["storage"]["durable"] is True
        print("✅ private positions endpoints require auth and preserve versions")
    finally:
        if old_token is None:
            os.environ.pop("POSITIONS_SYNC_TOKEN", None)
        else:
            os.environ["POSITIONS_SYNC_TOKEN"] = old_token
        server.shutdown()


def test_codex_v2_blocks_new_positions_on_red_market():
    rows = _fake_rows()
    analysis = appmod.analyze_candidate("9999.TW", rows, risk_level="RED")
    overlaid = appmod.apply_codex_v2_overlay("9999.TW", rows, analysis, {"risk_level": "RED"})
    assert overlaid["grade"] == "C"
    assert overlaid["action"] == "Codex v2: 禁買"
    assert overlaid["codex_decision_model"]["new_position_permission"] == "blocked"
    assert overlaid["codex_decision_model"]["vetoes"]
    print("✅ Codex v2 RED market blocks new positions")


if __name__ == "__main__":
    test_plan_schema_and_calibrated()
    test_held_position()
    test_codex_v2_blocks_new_positions_on_red_market()
    test_health_endpoint()
    test_agent_signals_endpoint()
    test_value_current_and_portfolio_endpoints()
    test_private_positions_endpoints()
    print("✅ app 整合測試全數通過")
