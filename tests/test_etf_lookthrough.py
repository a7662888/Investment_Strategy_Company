import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app as appmod
import etf_research


def snapshot(code):
    return {"code": code, "as_of": "2026-10-30", "source": "test", "scope": "test",
            "holdings": [{"code": "2330", "name": "台積電", "weight_pct": 57.5, "asset_type": "stock",
                          "shares": 1.0, "quantity_unit": "股數"}]}


def test_lookthrough_dispatches_by_issuer_and_rejects_unknown():
    etf_research._CACHE.clear()
    with patch.object(etf_research, "fubon_pair", return_value=(snapshot("006208"), None)) as fubon, \
            patch.object(etf_research, "kgi_pair", return_value=(snapshot("009816"), None)) as kgi:
        assert etf_research.lookthrough("006208")["weight_total_pct"] == 57.5
        assert etf_research.lookthrough("009816")["status"] == "official_snapshot"
        fubon.assert_called_once_with("006208")
        kgi.assert_called_once_with("009816")
    try:
        etf_research.lookthrough("2330")
        raise AssertionError("non-ETF code must be rejected")
    except ValueError:
        pass


def test_failed_read_is_reported_and_not_cached():
    etf_research._CACHE.clear()
    with patch.object(etf_research, "yuanta_pair", side_effect=TimeoutError("slow")) as yuanta:
        first = etf_research.lookthrough("0050")
        etf_research.lookthrough("0050")
    assert first["status"] == "unavailable" and first["holdings"] == [] and first["error"] == "TimeoutError"
    assert yuanta.call_count == 2


def test_kgi_passive_fund_id_does_not_enter_active_universe():
    assert "009816" not in etf_research.KGI
    assert etf_research.PASSIVE_KGI["009816"] == "J023"
    assert etf_research.yuanta_url("0050").count("0050") == 2


def test_endpoint_filters_codes():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch.object(etf_research, "lookthrough", side_effect=lambda c: {"code": c, "status": "official_snapshot"}):
            body = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/etf-lookthrough?codes=0050,2330,0050,<x>,006208", timeout=5).read())
        assert [i["code"] for i in body["items"]] == ["0050", "006208"]
        assert "009816" in body["supported"]
    finally:
        server.shutdown()


def test_blocked_issuer_falls_back_to_stored_daily_snapshot():
    etf_research._CACHE.clear()
    stored = {"saved_at": "2026-11-01T14:00:00+08:00", "current": snapshot("0050")}
    with patch.object(etf_research, "yuanta_pair", side_effect=OSError("blocked")), \
            patch.object(etf_research, "stored_pair", return_value=stored):
        doc = etf_research.lookthrough("0050")
    assert doc["status"] == "stored_official" and doc["stored_at"] == stored["saved_at"]
    assert doc["holdings"][0]["code"] == "2330" and doc["error"] == "OSError"


def test_daily_batch_stores_lookthrough_codes():
    import run_etf_holdings
    assert run_etf_holdings.issuer_of("009816") == "凱基"
    assert run_etf_holdings.issuer_of("0050") == "元大"
