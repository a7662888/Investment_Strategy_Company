# -*- coding: utf-8 -*-
"""
單一價值決策鏈整合測試：health、母池、每日價值狀態、持股判斷與舊端點退役。

跑法:python tests/test_app_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod

def test_health_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/api/health/live", "/api/health/ready", "/api/data-status", "/api/decision-ledger?limit=1", "/api/mother-pool"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                payload = json.loads(r.read().decode("utf-8"))
            assert isinstance(payload, dict)
        print(f"✅ Phase 0 health/data/ledger endpoints 正常(port {port})")
    finally:
        server.shutdown()


def test_legacy_decision_endpoints_are_retired():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/api/agent-signals", "/api/recommend", "/api/train", "/api/next-day-plan", "/api/codex-long-term"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError(f"{path} should be retired")
            except urllib.error.HTTPError as exc:
                assert exc.code == 410
                payload = json.loads(exc.read().decode("utf-8"))
                assert payload["replacement"] == "/api/value-current"
        print("✅ legacy multi-agent and short-term endpoints return 410")
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


if __name__ == "__main__":
    test_health_endpoint()
    test_legacy_decision_endpoints_are_retired()
    test_value_current_and_portfolio_endpoints()
    test_private_positions_endpoints()
    print("✅ app 整合測試全數通過")
