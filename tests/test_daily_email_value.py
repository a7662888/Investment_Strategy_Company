# -*- coding: utf-8 -*-
import json
import os
import unittest
from unittest.mock import patch

import run_daily_email


class DailyEmailValueTests(unittest.TestCase):
    def test_email_uses_daily_value_and_portfolio_actions(self):
        def fake_api(path, payload=None):
            if path.startswith("/api/market-risk"):
                return {"risk_level": "GREEN", "regime": "區間整理"}
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
                    "price": 46.7, "unrealized_gain": -0.1,
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
        self.assertNotIn("舊短線動作", html)


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class DailyEmailPositionTests(unittest.TestCase):
    def test_sync_secret_reads_private_positions_api(self):
        payload = {
            "version": 3,
            "positions": [{"symbol": "0056.TW", "shares": 1000, "cost": 54}],
        }

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 45)
            self.assertTrue(request.full_url.endswith("/api/positions"))
            self.assertEqual(request.headers.get("Authorization"), "Bearer test-sync-key")
            return _JsonResponse(payload)

        with patch.dict(os.environ, {"STOCK_POSITIONS": "sync:test-sync-key"}, clear=False), \
             patch.object(run_daily_email.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(run_daily_email.resolve_positions(), payload["positions"])

    def test_sync_secret_rejects_uninitialized_private_positions(self):
        with patch.dict(os.environ, {"STOCK_POSITIONS": "sync:test-sync-key"}, clear=False), \
             patch.object(
                 run_daily_email.urllib.request,
                 "urlopen",
                 return_value=_JsonResponse({"version": 0, "positions": []}),
             ):
            with self.assertRaisesRegex(RuntimeError, "not been initialized"):
                run_daily_email.resolve_positions()

    def test_legacy_json_secret_remains_supported(self):
        positions = [{"symbol": "00787.TW", "shares": 1000, "cost": 34.18}]
        with patch.dict(os.environ, {"STOCK_POSITIONS": json.dumps(positions)}, clear=False), \
             patch.object(run_daily_email, "load_positions", return_value=(
                 {"version": 0, "positions": []}, {"source": "none"}
             )):
            self.assertEqual(run_daily_email.resolve_positions(), positions)


if __name__ == "__main__":
    unittest.main()
