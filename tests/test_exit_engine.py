# -*- coding: utf-8 -*-
import unittest

from company.model.exit_engine import score_exit


def item(**updates):
    value = {
        "symbol": "1111.TW", "price": 120.0, "valuation_pct": 50.0,
        "quality_pass": True, "ma20": 115.0, "ma60": 110.0,
        "distance_from_high_252": -0.05, "is_etf": False,
        "fundamental_trend": {
            "observed_metrics": 7, "monthly_revenue_yoy_latest": 8.0,
            "monthly_revenue_negative_streak": 0, "quarterly_revenue_yoy": 5.0,
            "quarterly_eps_yoy": 6.0, "gross_margin_decline_2q": False,
            "operating_margin_decline_2q": False, "earnings_quality_ttm": 1.1,
            "debt_ratio_change_yoy": 1.0,
        },
    }
    value.update(updates)
    return value


class ExitEngineTests(unittest.TestCase):
    def test_high_valuation_alone_only_watches_profit(self):
        result = score_exit(item(valuation_pct=96), gain=0.45, weight=0.08)
        self.assertEqual(result["score"], 30.0)
        self.assertEqual(result["status"], "watch_profit")
        self.assertEqual(result["suggested_fraction"], 0.0)

    def test_multiple_independent_deteriorations_break_thesis(self):
        trend = {
            "observed_metrics": 7, "monthly_revenue_yoy_latest": -15.0,
            "monthly_revenue_negative_streak": 3, "quarterly_revenue_yoy": -12.0,
            "quarterly_eps_yoy": -20.0, "gross_margin_decline_2q": True,
            "operating_margin_decline_2q": True, "earnings_quality_ttm": 0.4,
            "debt_ratio_change_yoy": 12.0,
        }
        result = score_exit(item(valuation_pct=96, quality_pass=False, price=80, ma20=95,
                                 ma60=100, distance_from_high_252=-0.25,
                                 fundamental_trend=trend), gain=0.45, weight=0.18)
        self.assertEqual(result["status"], "exit")
        self.assertTrue(result["thesis_broken"])
        self.assertGreaterEqual(result["score"], 75)

    def test_missing_data_caps_trim_to_watch(self):
        sparse = item(valuation_pct=96, price=80, ma20=95, ma60=100,
                      distance_from_high_252=-0.25, fundamental_trend={})
        result = score_exit(sparse, gain=0.30, weight=None)
        self.assertGreaterEqual(result["score"], 45)
        self.assertLess(result["coverage"], 70)
        self.assertEqual(result["status"], "watch_profit")

    def test_partial_fields_do_not_claim_full_fundamental_coverage(self):
        partial = item(fundamental_trend={"monthly_revenue_yoy_latest": -15.0})
        result = score_exit(partial, gain=0.30, weight=0.10)
        self.assertLess(result["coverage_by_component"]["fundamentals"], 30)
        self.assertIn("fundamentals", result["missing"])

    def test_correlated_monthly_signals_are_one_deterioration_category(self):
        trend = {
            "monthly_revenue_yoy_latest": -15.0,
            "monthly_revenue_negative_streak": 4,
            "quarterly_revenue_yoy": 5.0,
            "quarterly_eps_yoy": 5.0,
            "gross_margin_decline_2q": False,
            "operating_margin_decline_2q": False,
            "earnings_quality_ttm": 1.0,
            "debt_ratio_change_yoy": 0.0,
        }
        result = score_exit(item(quality_pass=False, fundamental_trend=trend),
                            gain=0.30, weight=0.10)
        self.assertFalse(result["thesis_broken"])

    def test_position_concentration_is_evidence_not_automatic_exit(self):
        result = score_exit(item(), gain=0.50, weight=0.30)
        self.assertEqual(result["components"]["position_risk"], 10.0)
        self.assertEqual(result["status"], "hold")


if __name__ == "__main__":
    unittest.main()
