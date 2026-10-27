import unittest
from unittest.mock import patch

from company.model.fundamental_policy import evaluate_fundamentals
from company.screener.value_rescreen import _earnings_quality_ttm, _fundamental_trend, _ttm, evaluate


def quarters():
    return [{"period": period, "operating_cash_flow": ocf, "net_income": profit,
             "earnings_quality": ocf / profit, "roe": 4.0, "gross_profit_margin": 6.0,
             "operating_income_margin": 3.0, "debt_ratio": 60.0}
            for period, ocf, profit in zip(
                ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"),
                (1.0, 90.0, 90.0, 90.0), (1.0, 100.0, 100.0, 100.0))]


def candidate():
    return {"symbol": "2356.TW", "quality_pass": True, "fundamental_trend": {
        "gross_margin_decline_2q": False, "operating_margin_decline_2q": False,
        "earnings_quality_ttm": 1.0, "monthly_revenue_negative_streak": 0}}


class FundamentalPolicyTests(unittest.TestCase):
    def test_ttm_is_ratio_of_sums_not_mean_of_ratios(self):
        rows = quarters()
        rows[0].update(operating_cash_flow=10.0, earnings_quality=10.0)
        value, reason = _earnings_quality_ttm(rows)
        self.assertIsNone(reason)
        self.assertAlmostEqual(value, 280 / 301)
        self.assertNotAlmostEqual(value, sum(row["earnings_quality"] for row in rows) / 4)
        trend = _fundamental_trend({"quarterly": rows})
        self.assertAlmostEqual(trend["earnings_quality_ttm"], round(value, 4))
        self.assertEqual(trend["earnings_quality_method"], "ttm_sum_ocf_over_sum_ni_v1")

    def test_ttm_unknown_for_missing_nonfinite_nonconsecutive_or_nonpositive_denominator(self):
        fixtures = []
        fixtures.append(quarters()[:3])
        missing = quarters(); missing[1].pop("operating_cash_flow"); fixtures.append(missing)
        nonfinite = quarters(); nonfinite[1]["net_income"] = float("nan"); fixtures.append(nonfinite)
        gap = quarters(); gap[1]["period"] = "2025-09-30"; fixtures.append(gap)
        negative = quarters()
        for row in negative:
            row["net_income"] = -1
        fixtures.append(negative)
        for rows in fixtures:
            with self.subTest(rows=rows):
                value, reason = _earnings_quality_ttm(rows)
                self.assertIsNone(value)
                self.assertTrue(reason)

    def test_roe_ttm_requires_four_distinct_consecutive_quarters_for_financial_schema(self):
        rows = quarters()
        for row in rows:
            row["period"] = "2026-06-30"
        self.assertIsNone(_ttm(rows, "roe"))
        prices = [{"date": "2026-10-23", "close": 60.5, "adj_close": 60.5} for _ in range(60)]
        for symbol in ("2881.TW", "5871.TW"):
            code = symbol.split(".")[0]
            info = {code: {"quarterly": rows, "valuation": {
                "date": "2026-10-23", "pe": 10, "pe_quantiles_5pct": [10, 15, 20],
                "pb": 1.0, "pb_quantiles_5pct": [1, 2, 3]}}}
            with patch("company.screener.value_rescreen._yahoo_history", return_value=prices):
                result = evaluate(symbol, info)
            self.assertIsNone(result["quality_pass"])
            self.assertEqual(result["action"], "watch")
            self.assertIn("ROE 四季資料不足", result["quality_missing"])

    def test_nonfinite_latest_debt_does_not_fall_back_or_pass_quality(self):
        prices = [{"date": "2026-10-23", "close": 60.5, "adj_close": 60.5} for _ in range(60)]
        for value in (float("nan"), float("inf"), None):
            rows = quarters(); rows[-1]["debt_ratio"] = value
            info = {"2356": {"quarterly": rows, "valuation": {
                "date": "2026-10-23", "pe": 10, "pe_quantiles_5pct": [10, 15, 20],
                "pb": 1.0, "pb_quantiles_5pct": [1, 2, 3]}}}
            with patch("company.screener.value_rescreen._yahoo_history", return_value=prices):
                result = evaluate("2356.TW", info)
            self.assertIsNone(result["quality_pass"])
            self.assertEqual(result["action"], "watch")
            self.assertIn("負債比資料不足", result["quality_missing"])

    def test_nonfinite_trend_is_unknown_and_yoy_matches_exact_prior_year(self):
        rows = quarters()
        rows[-1].update(gross_profit_margin=float("nan"), revenue=float("inf"), eps=float("nan"))
        rows.insert(0, {"period": "2025-03-31", "revenue": 100, "eps": 1, "debt_ratio": 10})
        trend = _fundamental_trend({"quarterly": rows, "monthly_revenue": [{"yoy": float("nan")} ]})
        self.assertIsNone(trend["gross_margin_decline_2q"])
        self.assertIsNone(trend["quarterly_revenue_yoy"])
        self.assertIsNone(trend["quarterly_eps_yoy"])
        self.assertIsNone(trend["debt_ratio_change_yoy"])
        self.assertIsNone(trend["monthly_revenue_yoy_latest"])
        self.assertIsNone(trend["monthly_revenue_negative_streak"])

    def test_single_warning_observes_and_combination_pauses_new(self):
        item = candidate()
        item["fundamental_trend"]["gross_margin_decline_2q"] = True
        self.assertEqual(evaluate_fundamentals(item)["status"], "observe")
        item["fundamental_trend"]["earnings_quality_ttm"] = 0.57
        result = evaluate_fundamentals(item)
        self.assertEqual(result["status"], "pause_new")
        self.assertFalse(result["allow_new"])

    def test_missing_cash_evidence_is_unknown_not_safe(self):
        item = candidate()
        item["fundamental_trend"].pop("earnings_quality_ttm")
        self.assertEqual(evaluate_fundamentals(item)["status"], "insufficient")

    def test_quality_failure_requests_review_without_automatic_sale(self):
        item = candidate(); item["quality_pass"] = False
        result = evaluate_fundamentals(item)
        self.assertEqual(result["status"], "thesis_review")
        self.assertFalse(result["allow_new"])

    def test_etf_and_financial_schema_keep_applicable_rules(self):
        self.assertTrue(evaluate_fundamentals({"is_etf": True})["allow_new"])
        for symbol in ("2881.TW", "5871.TW"):
            self.assertEqual(evaluate_fundamentals({"symbol": symbol, "quality_pass": True})["status"], "clear")

    def test_rescreen_uses_raw_sums_and_cannot_buy_when_cash_missing(self):
        price_rows = [{"date": "2026-10-23", "close": 60.5, "adj_close": 60.5} for _ in range(60)]
        info = {"2356": {"quarterly": quarters(), "valuation": {
            "date": "2026-10-23", "pe": 10, "pe_quantiles_5pct": [10, 15, 20],
            "pb": 1.0, "pb_quantiles_5pct": [1, 2, 3]}, "completeness": {"complete": True}}}
        with patch("company.screener.value_rescreen._yahoo_history", return_value=price_rows):
            good = evaluate("2356.TW", info)
            self.assertTrue(good["quality_pass"])
            info["2356"]["quarterly"][0].pop("net_income")
            unknown = evaluate("2356.TW", info)
        self.assertIsNone(unknown["quality_pass"])
        self.assertFalse(unknown["fundamentals_complete"])
        self.assertEqual(unknown["action"], "watch")
        self.assertEqual(unknown["data_incomplete"], "quality_inputs_unavailable")

    def test_rescreen_does_not_pass_when_average_ratios_hide_poor_cash_conversion(self):
        rows = quarters()
        for index, row in enumerate(rows):
            row["operating_cash_flow"] = 10.0
            row["earnings_quality"] = 10.0 / row["net_income"]
        self.assertGreater(sum(row["earnings_quality"] for row in rows) / 4, 0.5)
        true_ratio, reason = _earnings_quality_ttm(rows)
        self.assertIsNone(reason)
        self.assertLess(true_ratio, 0.5)
        prices = [{"date": "2026-10-23", "close": 60.5, "adj_close": 60.5} for _ in range(60)]
        info = {"2356": {"quarterly": rows, "valuation": {
            "date": "2026-10-23", "pe": 10, "pe_quantiles_5pct": [10, 15, 20],
            "pb": 1.0, "pb_quantiles_5pct": [1, 2, 3]}, "completeness": {"complete": True}}}
        with patch("company.screener.value_rescreen._yahoo_history", return_value=prices):
            result = evaluate("2356.TW", info)
        self.assertFalse(result["quality_pass"])
        self.assertEqual(result["fundamental_assessment"]["status"], "thesis_review")
        self.assertNotEqual(result["action"], "accumulate")


if __name__ == "__main__":
    unittest.main()
