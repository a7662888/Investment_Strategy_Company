# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model.entry_timing import grade_day_balance, grade_entry, grade_liquidity


class TestLiquidity(unittest.TestCase):
    """系統原本完全沒有可成交性檢查：買進區再漂亮，價差過大也吃掉報酬。"""

    def test_wide_spread_is_flagged(self) -> None:
        result = grade_liquidity({"spread_pct": 1.2, "total_volume": 5000})
        self.assertFalse(result["tradable"])
        self.assertIn("買賣價差", result["warnings"][0])

    def test_thin_volume_is_flagged(self) -> None:
        result = grade_liquidity({"spread_pct": 0.1, "total_volume": 120})
        self.assertFalse(result["tradable"])
        self.assertIn("成交量", result["warnings"][0])

    def test_liquid_name_passes(self) -> None:
        result = grade_liquidity({"spread_pct": 0.2, "total_volume": 18900})
        self.assertTrue(result["tradable"])
        self.assertEqual(result["warnings"], [])

    def test_missing_fields_do_not_raise_a_false_alarm(self) -> None:
        self.assertTrue(grade_liquidity({})["tradable"])


class TestDayBalance(unittest.TestCase):
    def test_close_above_vwap_with_volume_is_stabilizing(self) -> None:
        self.assertEqual(
            grade_day_balance({"close_vs_vwap_pct": 0.4, "volume_ratio": 1.4})["state"],
            "stabilizing")

    def test_heavy_selling_below_vwap_is_capitulation(self) -> None:
        balance = grade_day_balance({"close_vs_vwap_pct": -2.1, "volume_ratio": 2.6})
        self.assertEqual(balance["state"], "capitulation")
        self.assertIn("不可接刀", balance["note"])

    def test_no_volume_is_drifting(self) -> None:
        self.assertEqual(
            grade_day_balance({"close_vs_vwap_pct": -0.3, "volume_ratio": 0.6})["state"],
            "drifting")

    def test_absent_microstructure_returns_none(self) -> None:
        self.assertIsNone(grade_day_balance({"vwap": 100.0}))


class TestEntryHints(unittest.TestCase):
    """「等待止跌」原本沒有任何「等到什麼時候」的依據，這是本模組的著力點。"""

    def _entry(self, decision, snapshot):
        return grade_entry({"decision": decision}, snapshot)

    def test_waiting_names_get_a_stabilisation_hint(self) -> None:
        entry = self._entry("等待止跌", {"close_vs_vwap_pct": 0.5, "volume_ratio": 1.2,
                                      "spread_pct": 0.2, "total_volume": 9000})
        self.assertIn("優先觀察", entry["hint"])
        self.assertIn("不代表可立即買進", entry["hint"])

    def test_capitulation_warns_against_catching_the_knife(self) -> None:
        entry = self._entry("等待止跌", {"close_vs_vwap_pct": -3.0, "volume_ratio": 3.0,
                                      "spread_pct": 0.2, "total_volume": 9000})
        self.assertIn("切勿接刀", entry["hint"])

    def test_quiet_drift_keeps_waiting(self) -> None:
        entry = self._entry("等待止跌", {"close_vs_vwap_pct": -0.2, "volume_ratio": 0.5,
                                      "spread_pct": 0.2, "total_volume": 9000})
        self.assertIn("維持等待", entry["hint"])

    def test_buy_zone_with_no_volume_is_not_urged(self) -> None:
        entry = self._entry("可分批研究", {"close_vs_vwap_pct": -0.1, "volume_ratio": 0.5,
                                       "spread_pct": 0.2, "total_volume": 9000})
        self.assertIn("不急於一次買足", entry["hint"])

    def test_evidence_is_marked_unvalidated(self) -> None:
        entry = self._entry("可分批研究", {"close_vs_vwap_pct": 0.3, "volume_ratio": 1.1,
                                       "spread_pct": 0.2, "total_volume": 9000})
        self.assertFalse(entry["validated"])
        self.assertIn("不進入選股排序", entry["disclaimer"])

    def test_no_snapshot_means_no_annotation(self) -> None:
        self.assertIsNone(grade_entry({"decision": "可分批研究"}, None))


if __name__ == "__main__":
    unittest.main()
