"""僅合成資料：新私有歷史 API 的實際 HTTP 授權與防洩漏驗證。"""
from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
import owner_access


ENDPOINTS = ("/api/portfolio-nav", "/api/trade-events")
SECRET = "synthetic-history-owner-test-secret"


@contextlib.contextmanager
def history_server():
    # 不讀固化憑證或呼叫任何遠端儲存；所有歷史資料均為合成。
    with patch("company.model.positions.expected_sync_token", return_value=SECRET):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def get(base, path, token=None, method="GET"):
    headers = {"Authorization": "Bearer " + token} if token else {}
    req = urllib.request.Request(base + path, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        return response.code, dict(response.headers), body


def device_token():
    pair = owner_access.create_pairing(SECRET)
    return owner_access.claim_pairing(pair["code"], SECRET, "synthetic-client")["token"]


def test_history_api_rejects_anonymous_and_wrong_token_before_private_read():
    with history_server() as base, patch("company.model.portfolio_nav.load_public") as load:
        for path in ENDPOINTS:
            for token in (None, "incorrect-synthetic-credential"):
                code, headers, body = get(base, path, token)
                assert code == 401
                assert json.loads(body) == {"error": "unauthorized"}
                assert headers["Cache-Control"] == "no-store"
        load.assert_not_called()


def test_history_api_accepts_owner_and_current_paired_device_without_cache():
    payload = {"coverage_start": "2026-11-02", "data_days": 1, "rows": []}
    with history_server() as base, patch("company.model.portfolio_nav.load_public", return_value=payload) as load:
        for token in (SECRET, device_token()):
            for path, kind in zip(ENDPOINTS, ("nav", "events")):
                code, headers, body = get(base, path, token)
                assert code == 200
                assert json.loads(body) == payload
                assert headers["Cache-Control"] == "no-store"
                assert headers["X-Content-Type-Options"] == "nosniff"
                load.assert_called_with(kind)


def test_history_api_rejects_expired_device_and_unconfigured_owner():
    token = device_token()
    with history_server() as base, patch("company.model.portfolio_nav.load_public") as load:
        with patch("owner_access.time.time", return_value=time.time() + owner_access.DEVICE_TTL + 1):
            for path in ENDPOINTS:
                assert get(base, path, token)[0] == 401
        with patch("company.model.positions.expected_sync_token", return_value=None):
            for path in ENDPOINTS:
                assert get(base, path, SECRET)[0] == 401
        load.assert_not_called()


def test_history_api_masks_private_storage_errors():
    with history_server() as base, patch("company.model.portfolio_nav.load_public", side_effect=ValueError("synthetic-sensitive-value")):
        for path in ENDPOINTS:
            code, _, body = get(base, path, SECRET)
            assert code == 503
            assert b"synthetic-sensitive-value" not in body
            assert json.loads(body) == {"error": "PRIVATE_HISTORY_UNAVAILABLE"}


def test_private_history_cannot_be_downloaded_as_static_file():
    with history_server() as base:
        for path in ("/private/trade_events.json", "/private/portfolio_nav.json",
                     "/data/daily_audit/broker_positions/trade_events.json",
                     "/data/daily_audit/broker_positions/portfolio_nav.json",
                     "/%2e%2e/data/daily_audit/broker_positions/portfolio_nav.json"):
            assert get(base, path)[0] == 404


def test_history_api_is_read_only():
    with history_server() as base, patch("company.model.portfolio_nav.load_public") as load:
        for path in ENDPOINTS:
            for method in ("POST", "PUT", "DELETE", "HEAD"):
                assert get(base, path, SECRET, method)[0] in (400, 404, 405, 501)
        load.assert_not_called()


def test_history_api_reads_real_private_local_store_for_paired_device(tmp_path):
    """不用 loader stub：HTTP → 真 serializer → 真本地私有 JSON。"""
    from company.data import broker_positions
    from company.model import portfolio_nav, private_history_store

    account = "synthetic-account-binding-not-for-api"
    common = {"schema_version": 1, "account_key": account,
              "coverage_start": "2026-11-11", "last_success_at": "2026-11-11T08:30:00+00:00",
              "data_days": 1}
    nav = {**common, "contains_provisional": False, "rows": [{
        "date": "2026-11-11", "market_value": 12340.0, "total_cost": 12000.0,
        "positions": [{"symbol": "9999.TW", "shares": 100, "price": 123.4}],
    }]}
    events = {**common, "contains_provisional_prices": True, "events": [{
        "event_key": "synthetic-event", "date": "2026-11-11", "symbol": "9999.TW",
        "side": "buy", "shares": 100, "price": 120.0,
    }]}
    private_dir = tmp_path / "synthetic-private"
    private_dir.mkdir()
    with patch.object(broker_positions, "LOCAL_PATH", private_dir / "snapshot.json"), \
            patch.object(private_history_store.store, "_config", return_value=None):
        # 經實際 store.update 寫入，驗證實際 serializer 與檔名對應。
        private_history_store.update(private_dir / "portfolio_nav.json", portfolio_nav.NAV_REMOTE,
                                     lambda old: nav)
        private_history_store.update(private_dir / "trade_events.json", portfolio_nav.EVENTS_REMOTE,
                                     lambda old: events)
        private_history_store.update(private_dir / "portfolio_history_status.json", portfolio_nav.STATUS_REMOTE,
                                     lambda old: {"at": common["last_success_at"], "ok": True,
                                                  "error_code": None, "account_key": account,
                                                  "unlisted_private_field": "synthetic-status-private"})
        token = device_token()
        with history_server() as base:
            for endpoint, records_key, expected in ((ENDPOINTS[0], "rows", nav["rows"]),
                                                    (ENDPOINTS[1], "events", events["events"])):
                code, headers, body = get(base, endpoint)
                assert code == 401 and json.loads(body) == {"error": "unauthorized"}
                assert b"9999.TW" not in body and b"12340" not in body
                for credential in (SECRET, token):
                    code, headers, body = get(base, endpoint, credential)
                    assert code == 200
                    result = json.loads(body)
                    assert result[records_key] == expected
                    assert result["coverage_start"] == "2026-11-11"
                    assert result["last_success_at"] == common["last_success_at"]
                    assert result["data_days"] == 1
                    assert result["scope"] == portfolio_nav.SCOPE
                    assert result["storage"] == {"source": "local", "durable": False}
                    assert result["contains_provisional"] is (records_key == "events")
                    assert result["last_attempt"] == {"at": common["last_success_at"], "ok": True, "error_code": None}
                    assert "account_key" not in result
                    assert account.encode() not in body
                    assert b"synthetic-status-private" not in body
                    assert headers["Cache-Control"] == "no-store"
