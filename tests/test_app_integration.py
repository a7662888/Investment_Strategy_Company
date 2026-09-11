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
        assert portfolio["actions"][0]["exit_engine"]["shadow"] is True
        print("✅ daily value current-state / portfolio endpoints 正常")
    finally:
        server.shutdown()


def test_private_positions_endpoints():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old_token = os.environ.get("POSITIONS_SYNC_TOKEN")
    old_enabled = os.environ.get("POSITIONS_SYNC_ENABLED")
    os.environ["POSITIONS_SYNC_TOKEN"] = "integration-sync-key"
    # 2026-09-01 起預設開啟（業主指示），故「關閉」需明確設定才測得到緊急煞車。
    os.environ["POSITIONS_SYNC_ENABLED"] = "0"
    # 固化密鑰優先於環境變數，且本機可能真的存在一份；停用它，本測試才測得到
    # 「以環境變數設定同步」這條路徑。
    persisted_patch = patch("company.model.positions.stored_sync_token", return_value=None)
    persisted_patch.start()
    document = {
        "schema_version": 1, "version": 2, "updated_at": "2026-08-04T00:00:00+00:00",
        "positions": [{"symbol": "0056.TW", "shares": 1000.0, "cost": 54.0}],
    }
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/positions", timeout=5)
            raise AssertionError("explicit POSITIONS_SYNC_ENABLED=0 must stop the sync")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503

        # 第二道緊急煞車：即使旗標未設，POSITIONS_SYNC_DISABLED=1 仍須擋下。
        os.environ.pop("POSITIONS_SYNC_ENABLED", None)
        os.environ["POSITIONS_SYNC_DISABLED"] = "1"
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/positions", timeout=5)
            raise AssertionError("POSITIONS_SYNC_DISABLED=1 must stop the sync")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
        os.environ.pop("POSITIONS_SYNC_DISABLED", None)

        # 真正讓端點 fail-closed 的是「沒有同步密鑰就沒有有效 bearer token」，
        # 而不是旗標；沒設密鑰時即使旗標開啟也必須 503。
        saved_token = os.environ.pop("POSITIONS_SYNC_TOKEN")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_DATA_TOKEN", None)
            os.environ.pop("GITHUB_PAT", None)
            # 固化密鑰存在時「已設定」就成立，故此處一併停用，才測得到未設定的情形。
            with patch("company.model.positions.stored_sync_token", return_value=None):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/positions", timeout=5)
                    raise AssertionError("no configured secret must stop the sync")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 503
        os.environ["POSITIONS_SYNC_TOKEN"] = saved_token
        os.environ["POSITIONS_SYNC_ENABLED"] = "1"

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
        persisted_patch.stop()
        if old_token is None:
            os.environ.pop("POSITIONS_SYNC_TOKEN", None)
        else:
            os.environ["POSITIONS_SYNC_TOKEN"] = old_token
        if old_enabled is None:
            os.environ.pop("POSITIONS_SYNC_ENABLED", None)
        else:
            os.environ["POSITIONS_SYNC_ENABLED"] = old_enabled


def test_quote_endpoint_filters_symbols_and_marks_market_session():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        quote = {
            "symbol": "2330.TW", "shortName": "台積電", "regularMarketPrice": 1000.0,
            "regularMarketChangePercent": 1.0, "regularMarketTime": 1,
            "source": "TWSE MIS", "realtimeStatus": "盤中撮合",
        }
        with patch("app.fetch_twse_mis_quotes", return_value=[quote]), patch("app.is_tw_market_session", return_value=True):
            url = f"http://127.0.0.1:{port}/api/quote?symbols=2330.TW,%3Cscript%3E,2330.TW"
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        assert payload["marketSession"] is True
        assert [row["symbol"] for row in payload["quoteResponse"]["result"]] == ["2330.TW"]
        assert payload["fetchedAt"]
    finally:
        server.shutdown()
        server.shutdown()


if __name__ == "__main__":
    test_health_endpoint()
    test_legacy_decision_endpoints_are_retired()
    test_value_current_and_portfolio_endpoints()
    test_private_positions_endpoints()
    print("✅ app 整合測試全數通過")
