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
