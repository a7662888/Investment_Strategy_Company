# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import run_daily_email


class DailyEmailValueTests(unittest.TestCase):
    def test_email_uses_daily_value_and_portfolio_actions(self):
        def fake_api(path, payload=None):
            if path == "/api/next-day-plan":
                return {"market_index": {"risk_level": "GREEN"}, "plans": [{
                    "symbol": "0056.TW", "action": "舊短線動作", "last_close": 46.7,
                    "unrealized_gain": -0.1,
                }]}
            if path == "/api/value-current":
                return {"top_picks": [{
                    "symbol": "3045.TW", "name": "台灣大", "price": 113.5,
                    "valuation_zone": "深度低估（≤P20）", "trend": "短線轉穩",
                    "decision": "可分批研究", "as_of": "2026-07-29",
                }], "waiting_list": [{
                    "symbol": "1101.TW", "name": "台泥", "decision": "高風險反轉觀察",
                    "valuation_zone": "深度低估（≤P20）", "roe_ttm": -4.1,
                }]}
            if path == "/api/value-portfolio":
                return {"actions": [{
                    "symbol": "0056.TW", "action": "續抱領息，停止追加",
                    "reasons": ["ETF 不因均線訊號單獨賣出"],
                }]}
            if path.startswith("/api/decision-ledger"):
                return {"signals": []}
            raise AssertionError(path)

        with patch.object(run_daily_email, "api", side_effect=fake_api):
            html = run_daily_email.build_html([{"symbol": "0056.TW", "shares": 1000, "cost": 52.0}])
        self.assertIn("台灣大", html)
        self.assertIn("高風險反轉觀察", html)
        self.assertIn("續抱領息，停止追加", html)
        self.assertNotIn("舊短線動作</td>", html)


if __name__ == "__main__":
    unittest.main()
