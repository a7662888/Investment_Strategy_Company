from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app
from company.model.intraday_flash import build_flash


TAIPEI = timezone(timedelta(hours=8))


def snapshot_at(moment: datetime, **overrides):
    row = {
        "symbol": "1513.TW",
        "trade_date": moment.astimezone(TAIPEI).date().isoformat(),
        "timestamp_status": "valid",
        "ts": int(moment.timestamp() * 1_000_000_000),
        "open": 166.0,
        "high": 168.5,
        "low": 165.5,
        "close": 167.0,
        "change_rate": -0.3,
        "vwap": 166.8,
        "close_vs_vwap_pct": 0.12,
        "last_volume": 4,
        "total_volume": 12345,
        "last_amount": 668000,
        "total_amount": 2059000000,
        "yesterday_volume": 10000,
        "volume_ratio": 1.23,
        "bid": 166.5,
        "bid_volume": 20,
        "ask": 167.0,
        "ask_volume": 15,
        "spread_pct": 0.299,
        "tick_pressure": "buy",
        "source": "Shioaji snapshots",
    }
    row.update(overrides)
    return row


def test_fresh_intraday_shioaji_snapshot_is_primary_and_exposes_more_fields():
    now = datetime.now(timezone.utc)
    snapshots = {"1513.TW": snapshot_at(now - timedelta(minutes=2))}
    with patch.object(app, "load_market_snapshots", return_value=snapshots), \
         patch.object(app, "is_tw_market_session", return_value=True), \
         patch.object(app, "fetch_twse_mis_quotes") as mis:
        payload = app.fetch_quote(["1513.TW"])

    quote = payload["quoteResponse"]["result"][0]
    assert quote["source"] == "永豐 Shioaji"
    assert quote["averagePrice"] == 166.8
    assert quote["volumeRatio"] == 1.23
    assert quote["yesterdayVolume"] == 10000
    assert quote["bidVolume"] == 20
    assert quote["askVolume"] == 15
    assert quote["marketContext"]["validatedForRanking"] is False
    assert payload["shioajiPrimaryCount"] == 1
    mis.assert_called_once_with([])


def test_stale_same_day_shioaji_is_context_not_primary_price():
    now = datetime.now(timezone.utc)
    snapshot = snapshot_at(now - timedelta(hours=1))
    official = {
        "symbol": "1513.TW", "shortName": "中興電", "regularMarketPrice": 168.0,
        "regularMarketChangePercent": 0.3, "regularMarketTime": int(now.timestamp()),
        "marketDate": snapshot["trade_date"], "source": "TWSE MIS",
        "realtimeStatus": "盤中撮合",
    }
    with patch.object(app, "load_market_snapshots", return_value={"1513.TW": snapshot}), \
         patch.object(app, "is_tw_market_session", return_value=True), \
         patch.object(app, "fetch_twse_mis_quotes", return_value=[official]):
        payload = app.fetch_quote(["1513.TW"])

    quote = payload["quoteResponse"]["result"][0]
    assert quote["source"] == "TWSE MIS"
    assert quote["regularMarketPrice"] == 168.0
    assert quote["marketContext"]["volumeRatio"] == 1.23
    assert payload["shioajiPrimaryCount"] == 0


def test_intraday_flash_whitelists_public_market_context_only():
    state = {
        "as_of": "2026-10-02",
        "evaluations": [{
            "symbol": "1513.TW", "name": "中興電", "as_of": "2026-10-02",
            "quality_pass": True, "action": "accumulate", "decision": "可分批研究",
            "entry_range": [154.0, 167.5], "rank_score": 80, "is_etf": False,
        }],
    }
    quotes = {"1513.TW": {
        "regularMarketPrice": 167.0, "source": "永豐 Shioaji", "regularMarketTime": 123,
        "marketContext": {"vwap": 166.8, "volumeRatio": 1.23, "account_id": "must-not-leak"},
    }}
    doc = build_flash(state, quotes, now=datetime(2026, 10, 5, 12, 30, tzinfo=TAIPEI))
    context = doc["items"][0]["market_context"]
    assert context == {"vwap": 166.8, "volumeRatio": 1.23}
    assert "account_id" not in str(doc)
