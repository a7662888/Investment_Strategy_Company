from datetime import datetime, timezone
from unittest.mock import patch

import app
from company.model.market_research import build_market_research
from run_market_scanners import collect_scanners


def test_rankings_exclude_stale_quotes_and_missing_turnover():
    state = {"as_of": "2026-10-02", "evaluations": [{"symbol": "2330.TW", "name": "TSMC"}]}
    snapshots = {
        "2330.TW": {"trade_date": "2026-10-02", "total_amount": 200, "total_volume": 100, "change_rate": 2},
        "2317.TW": {"trade_date": "2026-10-01", "total_amount": 9999},
        "1101.TW": {"trade_date": "2026-10-02", "amount": 99999},
    }
    result = build_market_research(state, snapshots)
    assert result["coverage"] == 2
    assert [row["symbol"] for row in result["rankings"]["amount"]] == ["2330.TW"]
    assert result["capabilities"]["institutional_streaks"] is False
    assert result["capabilities"]["prediction_validated"] is False


def test_freshness_uses_last_completed_trading_day_on_weekend_and_preopen():
    state = {"as_of": "2026-10-02", "coverage": {"price_stale": 0}}
    with patch("company.data.market_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
        assert app.current_state_freshness(state, datetime(2026, 10, 3, 2, tzinfo=timezone.utc))["status"] == "current"
        assert app.current_state_freshness(state, datetime(2026, 10, 5, 0, tzinfo=timezone.utc))["status"] == "current"
        assert app.current_state_freshness(state, datetime(2026, 10, 5, 7, tzinfo=timezone.utc))["status"] == "stale"


def test_entry_evidence_rejects_different_trade_date():
    state = {"as_of": "2026-10-02", "top_picks": [{"symbol": "2330.TW", "as_of": "2026-10-02"}]}
    with patch.object(app, "load_market_snapshots", return_value={"2330.TW": {"trade_date": "2026-10-01"}}):
        result = app.annotate_entry_evidence(state)
    assert result["top_picks"][0]["entry_evidence"] is None


def test_scanner_date_guard_isolation_and_provider_sort_contract():
    class Types:
        AmountRank = "amount"
        VolumeRank = "volume"
        ChangePercentRank = "change"

    class API:
        def __init__(self):
            self.calls = []

        def scanners(self, **kw):
            self.calls.append(kw)
            if kw["scanner_type"] == "volume":
                raise TimeoutError("private provider message must not enter output")
            return [type("Row", (), {"date": "2026-10-01", "code": "2330"})()]

    api = API()
    doc = collect_scanners(api, Types, "2026-10-02")
    assert doc["errors"] == {"volume": "TimeoutError"}
    assert not any(doc["rankings"].values())
    assert api.calls[-1]["ascending"] is False
    assert api.calls[0]["ascending"] is True


def test_scanner_rejects_invalid_timestamp_and_preserves_valid_timestamp():
    class Types:
        AmountRank = VolumeRank = ChangePercentRank = "ranking"
    stamp = int(datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    class API:
        def scanners(self, **kw):
            return [type("Row", (), {"date": "2026-10-02", "code": code, "ts": ts})()
                    for code, ts in (("2330", 0), ("2317", stamp))]
    doc = collect_scanners(API(), Types, "2026-10-02")
    assert [row["code"] for row in doc["rankings"]["amount"]] == ["2317"]
    assert doc["rankings"]["amount"][0]["ts"] == stamp
