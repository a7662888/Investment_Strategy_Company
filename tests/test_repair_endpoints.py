import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app


@contextmanager
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_broker_inventory_requires_auth_without_loading_private_data():
    with patch.object(app, "positions_sync_enabled", return_value=True), \
         patch("company.model.positions.expected_sync_token", return_value="synthetic-test-only"), \
         patch("company.model.positions.is_authorized", return_value=False), \
         patch("company.data.broker_positions.load_broker_positions") as load, server_url() as url:
        try:
            urllib.request.urlopen(url + "/api/broker-positions")
            raise AssertionError("authentication required")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        load.assert_not_called()


def test_daily_history_invalid_calendar_date_is_400():
    with server_url() as url:
        for day in ("2026-02-30", "2026-99-99", "../secret"):
            try:
                urllib.request.urlopen(url + "/api/daily-history?date=" + day)
                raise AssertionError("invalid date accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
                assert json.loads(exc.read())["error"] == "invalid date"


def _post(url, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_manual_refresh_requires_the_owner_key_and_runs_nothing_without_it():
    with patch("company.model.positions.expected_sync_token", return_value="synthetic-test-only"), \
         patch.object(app, "manual_refresh") as manual, server_url() as url:
        status, body = _post(url + "/api/daily-refresh/manual", {"job": "intraday"})
        assert status == 401
        status, _ = _post(url + "/api/daily-refresh/manual", {"job": "intraday"}, token="wrong")
        assert status == 401
        manual.assert_not_called()


def test_manual_refresh_refusal_and_broker_dispatch():
    from company.model import daily_jobs as jobs
    jobs._MANUAL.clear()
    with patch.object(jobs, "manual_check", return_value=(False, "非盤中時段")), \
         patch.object(jobs, "dispatch_workflow") as dispatch:
        payload, status = app.manual_refresh("intraday")
        assert status == 409 and payload["started"] is False
        dispatch.assert_not_called()
    with patch.object(jobs, "manual_check", return_value=(True, "ok")), \
         patch.object(jobs, "dispatch_workflow", return_value={"dispatched": True}) as dispatch:
        payload, status = app.manual_refresh("broker")
        assert status == 202 and payload["mode"] == "workflow"
        dispatch.assert_called_once_with(jobs.BROKER_WORKFLOW, {"mode": "refresh"})


def test_broker_ledger_requires_auth_and_sell_timing_uses_lots_only_when_authorized():
    with patch("company.model.positions.expected_sync_token", return_value="synthetic-test-only"), \
         patch.object(app, "load_private_ledger") as ledger, server_url() as url:
        try:
            urllib.request.urlopen(url + "/api/broker-ledger")
            raise AssertionError("authentication required")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        with patch.object(app, "build_sell_timing", return_value={"items": []}) as build:
            _post(url + "/api/sell-timing", {"positions": []})
            assert build.call_args.args[1] is None
        ledger.assert_not_called()


def test_timing_research_requires_the_owner_key():
    with patch("company.model.positions.expected_sync_token", return_value="synthetic-test-only"), server_url() as url:
        try:
            urllib.request.urlopen(url + "/api/timing-research")
            raise AssertionError("authentication required")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
