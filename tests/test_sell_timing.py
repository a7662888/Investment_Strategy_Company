# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model.sell_timing import (
    atr_multiple, build_levels, build_plan, compute_sell_timing, wilder_atr,
)


def make_rows(count: int = 30, high: float = 102.0, low: float = 100.0,
              close: float = 101.0) -> list[dict]:
    return [{"date": f"2026-08-{idx + 1:02d}", "open": close, "high": high,
             "low": low, "close": close} for idx in range(count)]


class TestATR(unittest.TestCase):
    def test_constant_range_gives_that_range(self) -> None:
        # 每日 H-L=2、收盤不動 → TR 恆為 2 → ATR 應為 2
        self.assertAlmostEqual(wilder_atr(make_rows()), 2.0, places=4)

    def test_insufficient_history_returns_none(self) -> None:
        self.assertIsNone(wilder_atr(make_rows(10)))

    def test_rows_missing_high_low_are_ignored(self) -> None:
        rows = [{"date": "2026-08-01", "high": None, "low": None, "close": 100.0}]
        self.assertIsNone(wilder_atr(rows))

    def test_multiple_tightens_as_exit_score_rises(self) -> None:
        self.assertEqual(atr_multiple(80), 2.0)
        self.assertEqual(atr_multiple(50), 2.5)
        self.assertEqual(atr_multiple(35), 3.0)
        self.assertEqual(atr_multiple(10), 3.5)
        self.assertEqual(atr_multiple(None), 3.0)


class TestLevels(unittest.TestCase):
    def test_trailing_stop_uses_anchor_high_minus_multiple_atr(self) -> None:
        item = {"ma20": 99.0, "ma60": 95.0, "high_252": 120.0}
        levels = build_levels(item, {"score": 10.0}, make_rows(), cost=None, gain=None)
        stop = next(lv for lv in levels if lv["key"] == "atr_trailing")
        # 錨點 102，ATR 2，score 10 → 3.5 倍 → 102 - 7 = 95
        self.assertAlmostEqual(stop["price"], 95.0, places=2)

    def test_breakeven_only_when_currently_profitable(self) -> None:
        item = {"ma20": 99.0}
        with_gain = build_levels(item, {}, make_rows(), cost=90.0, gain=0.12)
        without_gain = build_levels(item, {}, make_rows(), cost=90.0, gain=-0.05)
        self.assertIn("breakeven", [lv["key"] for lv in with_gain])
        self.assertNotIn("breakeven", [lv["key"] for lv in without_gain])

    def test_etf_gets_no_trend_or_trailing_stops(self) -> None:
        item = {"is_etf": True, "ma20": 99.0, "ma60": 95.0, "high_252": 120.0}
        keys = [lv["key"] for lv in build_levels(item, {}, make_rows(), cost=None, gain=None)]
        self.assertEqual(keys, [])


class TestConfirmationDiscipline(unittest.TestCase):
    """盤中穿價 vs 收盤確認是本引擎的核心紀律，必須守住。"""

    def _timing(self, last_close: float, live: float | None, score: float = 10.0) -> dict:
        rows = make_rows()
        rows[-1] = dict(rows[-1], close=last_close)
        quote = {"regularMarketPrice": live} if live is not None else None
        return compute_sell_timing(
            item={"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
            exit_result={"score": score, "suggested_fraction": 0.0},
            rows=rows, live_quote=quote, cost=90.0, gain=0.10, market_open=True,
        )

    def test_intraday_break_does_not_become_a_signal(self) -> None:
        # 收盤 103 高於所有觸發價（回撤線 102 為最高者），只有盤中 94 穿價。
        timing = self._timing(last_close=103.0, live=94.0)
        self.assertEqual(timing["urgency"], "watch_close")
        self.assertIn("尚未收盤確認", timing["headline"])

    def test_close_break_is_actionable(self) -> None:
        timing = self._timing(last_close=94.0, live=94.0)
        self.assertEqual(timing["urgency"], "act")

    def test_untouched_levels_hold(self) -> None:
        timing = self._timing(last_close=115.0, live=115.0)
        self.assertEqual(timing["urgency"], "hold")

    def test_thesis_broken_overrides_technical_levels(self) -> None:
        timing = compute_sell_timing(
            item={"ma20": 99.0}, exit_result={"score": 80.0, "thesis_broken": True},
            rows=make_rows(), live_quote=None, cost=90.0, gain=0.1, market_open=False,
        )
        self.assertEqual(timing["urgency"], "immediate")

    def test_mixed_price_dates_are_flagged(self) -> None:
        # 每日狀態走 TWSE OpenAPI（常延遲一個交易日），本層走日線 OHLC，
        # 兩者不同日時卡片會出現兩個「現價」，必須標明而非讓使用者自己猜。
        rows = make_rows()
        rows[-1] = dict(rows[-1], date="2026-09-01")
        timing = compute_sell_timing(
            item={"ma20": 99.0, "as_of": "2026-08-31"}, exit_result={},
            rows=rows, live_quote=None, cost=90.0, gain=0.1, market_open=False,
        )
        self.assertTrue(timing["basis_mismatch"])
        self.assertIn("2026-08-31", timing["basis_note"])
        self.assertIn("2026-09-01", timing["basis_note"])

    def test_same_date_is_not_flagged(self) -> None:
        rows = make_rows()
        rows[-1] = dict(rows[-1], date="2026-09-01")
        timing = compute_sell_timing(
            item={"ma20": 99.0, "as_of": "2026-09-01"}, exit_result={},
            rows=rows, live_quote=None, cost=90.0, gain=0.1, market_open=False,
        )
        self.assertFalse(timing["basis_mismatch"])
        self.assertIsNone(timing["basis_note"])

    def test_price_basis_is_labelled_honestly(self) -> None:
        intraday = self._timing(last_close=101.0, live=100.0)
        self.assertEqual(intraday["price_basis"], "盤中即時成交價")
        closed = compute_sell_timing(
            item={"ma20": 99.0}, exit_result={}, rows=make_rows(),
            live_quote=None, cost=90.0, gain=0.1, market_open=False,
        )
        self.assertTrue(closed["price_basis"].startswith("日線收盤"))


class TestPlan(unittest.TestCase):
    def test_stages_execute_exit_engine_fraction_when_trimming(self) -> None:
        levels = build_levels({"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
                              {"score": 65.0}, make_rows(), cost=None, gain=None)
        plan = build_plan({"suggested_fraction": 0.40}, levels, is_etf=False)
        self.assertEqual([step["fraction"] for step in plan], [0.2, 0.12, 0.08])
        self.assertTrue(all("收盤跌破" in step["condition"] for step in plan))

    def test_stages_fall_back_to_profit_protection_when_holding(self) -> None:
        levels = build_levels({"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
                              {"score": 10.0}, make_rows(), cost=None, gain=None)
        plan = build_plan({"suggested_fraction": 0.0}, levels, is_etf=False)
        self.assertIn("獲利保護", plan[0]["mode"])

    def test_stages_are_ordered_by_which_triggers_first(self) -> None:
        levels = build_levels({"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
                              {"score": 65.0}, make_rows(), cost=None, gain=None)
        plan = build_plan({"suggested_fraction": 0.40}, levels, is_etf=False)
        prices = [step["price"] for step in plan]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_etf_has_no_execution_plan(self) -> None:
        self.assertEqual(build_plan({"suggested_fraction": 0.4}, [], is_etf=True), [])

    def test_levels_too_close_together_collapse_into_one_stage(self) -> None:
        # 實測 2330 的 20MA／60MA／ATR 停損曾同時落在 1% 內，
        # 若各自成階段，一根長黑就會把三批一次觸發，等於一天出清。
        levels = [
            {"key": "a", "label": "A", "price": 100.0, "kind": "trend_break"},
            {"key": "b", "label": "B", "price": 99.5, "kind": "trend_break"},
            {"key": "c", "label": "C", "price": 90.0, "kind": "drawdown"},
        ]
        plan = build_plan({"suggested_fraction": 0.0}, levels, is_etf=False)
        self.assertEqual([step["price"] for step in plan], [100.0, 90.0])

    def test_protective_plan_never_liquidates_more_than_half(self) -> None:
        # Exit Engine 判定續抱時，價格訊號不得推翻基本面結論而清光部位。
        levels = build_levels({"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
                              {"score": 10.0}, make_rows(), cost=None, gain=None)
        plan = build_plan({"suggested_fraction": 0.0}, levels, is_etf=False)
        self.assertLessEqual(sum(step["fraction"] for step in plan), 0.5 + 1e-9)

    def test_levels_far_below_spot_are_not_staged(self) -> None:
        # 成本 900、現價 2440 時，保本線離現價 -63%，排成一批只是填充。
        levels = [
            {"key": "ma20", "label": "MA20", "price": 2391.0, "kind": "trend_break"},
            {"key": "breakeven", "label": "保本線", "price": 900.0, "kind": "breakeven"},
        ]
        plan = build_plan({"suggested_fraction": 0.0}, levels, is_etf=False,
                          reference_price=2440.0)
        self.assertEqual([step["trigger"] for step in plan], ["ma20"])

    def test_trim_plan_totals_the_exit_engine_fraction(self) -> None:
        levels = [
            {"key": "a", "label": "A", "price": 100.0, "kind": "trend_break"},
            {"key": "b", "label": "B", "price": 90.0, "kind": "drawdown"},
        ]
        plan = build_plan({"suggested_fraction": 0.40}, levels, is_etf=False)
        self.assertAlmostEqual(sum(step["fraction"] for step in plan), 0.40, places=2)


if __name__ == "__main__":
    unittest.main()


class TestVolumePriceConfirmation(unittest.TestCase):
    """收盤跌破仍有真假之分；量價證據只評成色，不得變成第二套買賣判斷。"""

    def _timing(self, snapshot, last_close=94.0):
        from company.model.sell_timing import compute_sell_timing
        rows = make_rows()
        rows[-1] = dict(rows[-1], close=last_close)
        return compute_sell_timing(
            item={"ma20": 99.0, "ma60": 95.0, "high_252": 120.0},
            exit_result={"score": 10.0, "suggested_fraction": 0.0},
            rows=rows, live_quote=None, cost=90.0, gain=0.10,
            market_open=False, market_snapshot=snapshot,
        )

    def test_heavy_volume_below_vwap_is_a_credible_break(self) -> None:
        timing = self._timing({"close_vs_vwap_pct": -1.5, "volume_ratio": 1.8})
        self.assertEqual(timing["confirmation"]["quality"], "strong")
        self.assertEqual(timing["urgency"], "act")
        self.assertIn("帶量", timing["headline"])

    def test_thin_volume_break_waits_for_a_second_bar(self) -> None:
        timing = self._timing({"close_vs_vwap_pct": -0.2, "volume_ratio": 0.5})
        self.assertEqual(timing["confirmation"]["quality"], "weak")
        self.assertEqual(timing["urgency"], "act_low_conviction")
        self.assertIn("第二根", timing["headline"])

    def test_close_above_vwap_without_volume_shows_resilience(self) -> None:
        confirmation = self._timing({"close_vs_vwap_pct": 0.8, "volume_ratio": 0.9})["confirmation"]
        self.assertEqual(confirmation["quality"], "resilient")

    def test_missing_snapshot_leaves_behaviour_unchanged(self) -> None:
        timing = self._timing(None)
        self.assertIsNone(timing["confirmation"])
        self.assertEqual(timing["urgency"], "act")

    def test_snapshot_without_volume_price_fields_is_ignored(self) -> None:
        self.assertIsNone(self._timing({"vwap": 100.0})["confirmation"])

    def test_confirmation_never_changes_the_trigger_prices(self) -> None:
        weak = self._timing({"close_vs_vwap_pct": -0.2, "volume_ratio": 0.5})
        strong = self._timing({"close_vs_vwap_pct": -1.5, "volume_ratio": 1.8})
        self.assertEqual([s["price"] for s in weak["plan"]], [s["price"] for s in strong["plan"]])
        self.assertEqual([s["fraction"] for s in weak["plan"]], [s["fraction"] for s in strong["plan"]])
