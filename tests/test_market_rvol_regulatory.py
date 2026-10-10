# -*- coding: utf-8 -*-
"""相對成交量、全市場排行補品質判定、處置／注意股標記。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model.market_research import build_market_research, regulatory_flags
from run_regulatory import NOTICE_FIELDS, PUNISH_FIELDS, rows_of
from run_shioaji_snapshot import apply_relative_volume

DAY = "2026-10-08"


class RelativeVolume(unittest.TestCase):
    def doc(self, volume=300):
        return {"trade_date": DAY, "snapshots": {"2330.TW": {"trade_date": DAY, "total_volume": volume}}}

    def test_ratio_against_prior_average(self):
        history = {"codes": {"2330.TW": [[f"2026-09-{d:02d}", 100] for d in range(22, 30)]}}
        doc = self.doc()
        history = apply_relative_volume(doc, history)
        row = doc["snapshots"]["2330.TW"]
        self.assertEqual(row["rvol20"], 3.0)
        self.assertEqual(row["rvol_days"], 8)
        self.assertEqual(history["codes"]["2330.TW"][-1], [DAY, 300])

    def test_needs_five_prior_days_and_rerun_is_idempotent(self):
        history = {"codes": {"2330.TW": [["2026-10-06", 100], ["2026-10-07", 100]]}}
        doc = self.doc()
        history = apply_relative_volume(doc, history)
        self.assertNotIn("rvol20", doc["snapshots"]["2330.TW"])
        history = apply_relative_volume(self.doc(), history)
        self.assertEqual(len(history["codes"]["2330.TW"]), 3)

    def test_same_day_value_is_not_its_own_baseline(self):
        history = {"codes": {"2330.TW": [[f"2026-09-{d:02d}", 100] for d in range(22, 29)] + [[DAY, 999]]}}
        doc = self.doc(200)
        apply_relative_volume(doc, history)
        self.assertEqual(doc["snapshots"]["2330.TW"]["rvol20"], 2.0)


class Rankings(unittest.TestCase):
    def setUp(self):
        self.state = {"as_of": DAY, "evaluations": [
            {"symbol": "2330.TW", "name": "台積電", "decision": "高檔，等待拉回", "eligible_pool": True},
            {"symbol": "1513.TW", "name": "中興電", "decision": "可分批研究", "eligible_pool": True}]}
        self.snaps = {
            "2330.TW": {"trade_date": DAY, "rvol20": 1.2, "volume_ratio": 1.4, "total_amount": 5e10},
            "1513.TW": {"trade_date": DAY, "rvol20": 2.5, "volume_ratio": 0.9, "total_amount": 1e9},
        }
        self.scan = {"trade_date": DAY, "rankings": {"amount": [{"code": "2330"}, {"code": "2330"}, {"code": "3231"}]}}

    def test_rvol_ranking_and_quality_on_market_rows(self):
        reg = {"punish": [{"code": "1513", "start_date": "2026-10-07", "end_date": "2026-10-20"}],
               "notice": [{"code": "3231"}]}
        doc = build_market_research(self.state, self.snaps, self.scan, reg)
        self.assertEqual([r["symbol"] for r in doc["rankings"]["rvol20"]], ["1513.TW", "2330.TW"])
        self.assertEqual(doc["rankings"]["rvol20"][0]["regulatory"], ["處置股 2026-10-07～2026-10-20"])
        market = doc["market_scanners"]["rankings"]["amount"]
        self.assertEqual(market[0]["decision"], "高檔，等待拉回")
        self.assertTrue(market[0]["in_pool"])
        self.assertEqual(market[1]["decision"], "未在母池（無品質判定）")
        self.assertEqual(market[1]["regulatory"], ["注意股"])
        self.assertEqual(doc["rvol_days"], 0)

    def test_without_regulatory_data_nothing_is_flagged(self):
        doc = build_market_research(self.state, self.snaps, None)
        self.assertTrue(all(r["regulatory"] == [] for r in doc["rankings"]["rvol20"]))
        self.assertEqual(regulatory_flags(None), {})


class RegulatoryParsing(unittest.TestCase):
    def test_columnar_and_row_shapes(self):
        class Columnar:
            def dict(self):
                return {"code": ["1513", "3231"], "start_date": ["2026-10-07", "2026-10-08"],
                        "end_date": ["2026-10-20", None]}
        rows = rows_of(Columnar(), PUNISH_FIELDS)
        self.assertEqual([r["code"] for r in rows], ["1513", "3231"])
        self.assertIsNone(rows[1]["end_date"])
        listed = rows_of([{"code": "2330", "reason": "漲幅異常"}], NOTICE_FIELDS)
        self.assertEqual(listed[0]["reason"], "漲幅異常")
        self.assertEqual(rows_of(None, NOTICE_FIELDS), [])


if __name__ == "__main__":
    unittest.main()


class LegacySnapshotRows(unittest.TestCase):
    def test_legacy_rows_use_the_daily_file_date_but_new_rows_must_validate(self):
        legacy = {"trade_date": "2026-09-22", "snapshots": {"2330.TW": {"total_volume": 100}}}
        history = apply_relative_volume(legacy, None)
        self.assertEqual(history["codes"]["2330.TW"], [["2026-09-22", 100]])
        stale = {"trade_date": DAY, "snapshots": {"2330.TW": {"trade_date": "2026-10-07",
                                                              "timestamp_status": "stale", "total_volume": 5}}}
        history = apply_relative_volume(stale, history)
        self.assertEqual(len(history["codes"]["2330.TW"]), 1)
