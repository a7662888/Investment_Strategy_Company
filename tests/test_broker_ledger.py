# -*- coding: utf-8 -*-
"""永豐逐筆買進紀錄、已實現損益與「買進後最高價」停損錨點。"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data.broker_ledger import fetch_realized, reconcile_lots
from company.model.sell_timing import build_levels, entry_anchor_high


class Reconcile(unittest.TestCase):
    def test_share_quantities_with_per_share_price(self):
        r = reconcile_lots(3000, 52.0, [{"date": "2026-09-01", "quantity": 1000, "price": 50.0},
                                        {"date": "2026-09-20", "quantity": 2000, "price": 53.0}])
        self.assertTrue(r["ok"])
        self.assertEqual([l["shares"] for l in r["lots"]], [1000, 2000])
        self.assertEqual(r["lots"][0]["price_basis"], "per_share")

    def test_lot_quantities_with_total_cost(self):
        r = reconcile_lots(2000, 30.0, [{"date": "20260901", "quantity": 1, "price": 29500},
                                        {"date": "20260902", "quantity": 1, "price": 30500}])
        self.assertTrue(r["ok"])
        self.assertEqual(r["units"]["quantity_factor"], 1000)
        self.assertEqual(r["lots"][0]["date"], "2026-09-01")
        self.assertAlmostEqual(r["lots"][0]["cost_per_share"], 29.5)
        self.assertEqual(r["lots"][0]["price_basis"], "total")

    def test_mismatches_are_rejected_not_guessed(self):
        self.assertEqual(reconcile_lots(3000, 52.0, [{"date": "2026-09-01", "quantity": 1500, "price": 52}])["reason"],
                         "quantity_mismatch")
        self.assertEqual(reconcile_lots(1000, 52.0, [{"date": "2026-09-01", "quantity": 1000, "price": 70}])["reason"],
                         "cost_mismatch")
        self.assertEqual(reconcile_lots(1000, 52.0, [])["reason"], "no_detail")


class Realized(unittest.TestCase):
    def test_trades_and_summary(self):
        class Api:
            def list_profit_loss(self, **kw):
                self.kw = kw
                return [{"code": "1216", "date": "20261001", "quantity": 1000, "price": 80.0, "pnl": 5600, "pr_ratio": 7.5, "cond": "Cash"}]

            def list_profit_loss_summary(self, **kw):
                return {"profitloss_summary": [{"code": "1216", "quantity": 1, "entry_price": 74.21, "cover_price": 80, "pnl": 5600}],
                        "total": {"pnl": 5600, "pr_ratio": 7.5}}
        api = Api()
        out = fetch_realized(api, "acct", "Share", lambda c: c + ".TW", today=date(2026, 10, 10))
        self.assertEqual(api.kw["begin_date"], "2025-10-10")
        self.assertEqual(out["trades"][0]["symbol"], "1216.TW")
        self.assertEqual(out["trades"][0]["date"], "2026-10-01")
        self.assertEqual(out["total"]["pnl"], 5600)


class EntryAnchor(unittest.TestCase):
    rows = [{"date": f"2026-08-{d:02d}", "high": 100 + d, "low": 90 + d, "close": 95 + d} for d in range(1, 29)]

    def test_anchor_since_entry_and_fallback(self):
        self.assertEqual(entry_anchor_high(self.rows, "2026-08-20"), 128.0)
        self.assertIsNone(entry_anchor_high(self.rows, "2026-07-01"))      # 早於資料範圍
        levels = build_levels({"ma20": None}, {"score": 40}, self.rows, 100, 0.2,
                              {"first_buy_date": "2026-08-10"})
        trailing = [l for l in levels if l["key"] == "atr_trailing"][0]
        self.assertEqual(trailing["anchor"], "since_entry")
        self.assertIn("2026-08-10 買進後最高", trailing["basis"])
        fallback = [l for l in build_levels({}, {"score": 40}, self.rows, 100, 0.2) if l["key"] == "atr_trailing"][0]
        self.assertEqual(fallback["anchor"], "lookback_60")


class SaveReportIsCountsOnly(unittest.TestCase):
    def test_public_log_never_contains_symbols(self):
        import run_broker_positions as runner
        runner._LEDGER.clear()
        runner._LEDGER.update({"account_key": "k",
                               "lots": {"lots": {"0056.TW": [{"date": "2026-09-01", "shares": 1000}]},
                                        "status": {"0056.TW": "ok", "2317.TW": "cost_mismatch"}},
                               "realized": {"range": ["a", "b"], "trades": [{"symbol": "1216.TW"}], "summary": [], "total": None}})
        with patch("company.model.durable_document.save_document", return_value={"durable": True}):
            report = runner._save_ledger()
        text = json.dumps(report)
        self.assertNotIn("0056", text)
        self.assertNotIn("1216", text)
        self.assertEqual(report["lots"]["unreconciled"], 1)
        runner._LEDGER.clear()


if __name__ == "__main__":
    unittest.main()
