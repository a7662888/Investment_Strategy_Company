# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app


class TestOfficialQuotes(unittest.TestCase):
    def setUp(self) -> None:
        app._OFFICIAL_QUOTE_CACHE.update(at=0.0, items=[])

    def test_roc_date(self) -> None:
        self.assertEqual(app.roc_date_to_iso("115/06/22"), "2026-06-22")
        self.assertIsNone(app.roc_date_to_iso("bad"))

    def test_fetch_twse_and_tpex_daily_quotes(self) -> None:
        def fake_http(url: str, **_: object):
            if "twse.com.tw" in url:
                return [{
                    "Date": "1150622", "Code": "2330", "Name": "台積電",
                    "ClosingPrice": "1000", "Change": "+10", "OpeningPrice": "990",
                    "HighestPrice": "1010", "LowestPrice": "985", "TradeVolume": "1234",
                }]
            return [{
                "Date": "1150622", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
                "Close": "500", "Change": "-5", "Open": "510", "High": "512",
                "Low": "498", "TradingShares": "5678",
            }]

        with patch.object(app, "http_json", side_effect=fake_http):
            rows = app.fetch_official_daily_quotes(["2330.TW", "6488.TW"])
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(set(by_symbol), {"2330.TW", "6488.TW"})
        self.assertEqual(by_symbol["2330.TW"]["marketDate"], "2026-06-22")
        self.assertIn("official close", by_symbol["6488.TW"]["source"])

    def test_market_session_boundaries(self) -> None:
        tz = timezone(timedelta(hours=8))
        self.assertTrue(app.is_tw_market_session(datetime(2026, 6, 23, 9, 0, tzinfo=tz)))
        self.assertFalse(app.is_tw_market_session(datetime(2026, 6, 23, 14, 0, tzinfo=tz)))
        self.assertFalse(app.is_tw_market_session(datetime(2026, 6, 21, 10, 0, tzinfo=tz)))


if __name__ == "__main__":
    unittest.main()
