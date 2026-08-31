from __future__ import annotations

import unittest
from unittest.mock import patch

from company.data.value_fundamentals import completeness, parse_quarterly, refresh_pool
from company.screener.value_rescreen import _pct_rank_quantiles, _roc_date, evaluate


def row(period, kind, value):
    return {"date": period, "type": kind, "value": value}


class ValueFundamentalsTests(unittest.TestCase):
    def test_roc_exchange_date_is_normalized(self):
        self.assertEqual(_roc_date("1150828"), "2026-08-28")
        self.assertIsNone(_roc_date("bad"))

    def test_cumulative_cash_flow_is_converted_to_standalone_quarters(self):
        periods = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
        fin = []
        balance = []
        cash = []
        for period in periods:
            fin += [row(period, "Revenue", 100), row(period, "IncomeAfterTaxes", 10)]
            balance += [row(period, "TotalAssets", 200), row(period, "Liabilities", 80), row(period, "Equity", 120)]
        for period, cumulative in zip(periods, [12, 25, 39, 54]):
            cash.append(row(period, "NetCashInflowFromOperatingActivities", cumulative))
        quarterly = parse_quarterly(fin, balance, cash)
        self.assertEqual([q["operating_cash_flow"] for q in quarterly], [12, 13, 14, 15])
        self.assertEqual([q["earnings_quality"] for q in quarterly], [1.2, 1.3, 1.4, 1.5])

    def test_complete_requires_all_five_applicable_groups(self):
        stock = {
            "quarterly": [
                {"assets": 100, "equity": 50, "operating_cash_flow": 10}
                for _ in range(4)
            ],
            "monthly_revenue": [{} for _ in range(12)],
            "valuation": {"date": "2026-07-29", "pb_quantiles_5pct": [1, 2]},
        }
        self.assertTrue(completeness(stock)["complete"])
        stock["monthly_revenue"] = stock["monthly_revenue"][:11]
        self.assertFalse(completeness(stock)["complete"])

    def test_refresh_prioritizes_missing_and_keeps_pool_at_100_scope(self):
        pool = {"as_of": "2026-07-29", "stocks": [
            {"symbol": "1111.TW", "name": "A"},
            {"symbol": "2222.TW", "name": "B"},
        ]}
        existing = {"stocks": {
            "1111": {"symbol": "1111", "refreshed_at": "2026-01-01", "completeness": {"complete": True}},
            "9999": {"symbol": "9999", "completeness": {"complete": True}},
        }}
        fetched = {
            "symbol": "2222", "quarterly": [], "monthly_revenue": [], "valuation": {},
            "completeness": {"complete": False}, "refreshed_at": "2026-07-29",
        }
        with patch("company.data.value_fundamentals.fetch_stock", return_value=fetched) as mocked:
            doc, meta = refresh_pool(pool, existing, batch_size=1)
        mocked.assert_called_once_with("2222", "B")
        self.assertEqual(set(doc["stocks"]), {"1111", "2222"})
        self.assertEqual(meta["coverage"]["present"], 2)

    def test_compact_quantiles_interpolate_percentile(self):
        self.assertEqual(_pct_rank_quantiles([10, 20, 30], 15), 25.0)
        self.assertEqual(_pct_rank_quantiles([10, 20, 30], 5), 0.0)
        self.assertEqual(_pct_rank_quantiles([10, 20, 30], 35), 100.0)

    def test_ta_chen_uses_cyclical_pbr_and_is_not_a_low_pe_buy(self):
        rows = [
            {"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": 40 + i / 10,
             "adj_close": 40 + i / 10}
            for i in range(84)
        ]
        fundamentals = {"2027": {
            "name": "大成鋼",
            "quarterly": [
                {"roe": 4.5, "gross_profit_margin": 25, "operating_income_margin": 15,
                 "earnings_quality": 1.0, "debt_ratio": 44}
                for _ in range(4)
            ],
            "valuation": {
                "date": rows[-1]["date"], "pe": 10.0, "pb": 1.5,
                "pe_quantiles_5pct": [9, 10, 15, 20, 30],
                "pb_quantiles_5pct": [0.8, 0.9, 1.0, 1.2, 1.5],
            },
            "completeness": {"complete": True},
        }}
        with patch("company.screener.value_rescreen._yahoo_history", return_value=rows):
            result = evaluate("2027.TW", fundamentals)
        self.assertEqual(result["valuation_basis"], "PBR(景氣)")
        self.assertEqual(result["action"], "avoid")
        self.assertFalse(result["quality_pass"])
        self.assertGreaterEqual(result["valuation_pct"], 80)

    def test_ems_uses_cash_conversion_not_generic_low_margin_gates(self):
        rows = [
            {"date": f"2026-08-{day:02d}", "close": 100 + day, "adj_close": 100 + day,
             "source": "TWSE OpenAPI"}
            for day in range(1, 29)
        ]
        fundamentals = {"2317": {
            "name": "鴻海",
            "quarterly": [
                {"period": f"2025-{month:02d}-30", "roe": 3.1, "gross_profit_margin": 6.1,
                 "operating_income_margin": 3.4, "earnings_quality": 0.99, "debt_ratio": 62.0}
                for month in (3, 6, 9, 12)
            ],
            "valuation": {"date": rows[-1]["date"], "pe": 15.0, "pb": 1.5,
                          "pe_quantiles_5pct": [10, 12, 15, 18, 22],
                          "pb_quantiles_5pct": [1, 1.2, 1.5, 1.8, 2.0]},
            "completeness": {"complete": True},
        }}
        with patch("company.screener.value_rescreen._yahoo_history", return_value=rows):
            result = evaluate("2317.TW", fundamentals)
        self.assertTrue(result["quality_pass"])
        self.assertEqual(result["valuation_basis"], "PER(EMS)")
        self.assertEqual(result["data_provenance"]["price_source"], "TWSE OpenAPI")

    def test_leasing_uses_roe_and_pbr_instead_of_generic_debt_gate(self):
        rows = [
            {"date": f"2026-08-{day:02d}", "close": 100 + day, "adj_close": 100 + day,
             "source": "TWSE OpenAPI"}
            for day in range(1, 29)
        ]
        fundamentals = {"5871": {
            "name": "中租-KY",
            "quarterly": [
                {"period": f"2025-{month:02d}-30", "roe": 2.95, "gross_profit_margin": 5,
                 "operating_income_margin": 4, "earnings_quality": 0.2, "debt_ratio": 79.1}
                for month in (3, 6, 9, 12)
            ],
            "valuation": {"date": rows[-1]["date"], "pe": 12.0, "pb": 1.5,
                          "pe_quantiles_5pct": [8, 10, 12, 14, 16],
                          "pb_quantiles_5pct": [1, 1.2, 1.5, 1.8, 2.0]},
            "completeness": {"complete": True},
        }}
        with patch("company.screener.value_rescreen._yahoo_history", return_value=rows):
            result = evaluate("5871.TW", fundamentals)
        self.assertTrue(result["quality_pass"])
        self.assertEqual(result["valuation_basis"], "PBR(租賃)")
        self.assertEqual(result["action"], "hold")


if __name__ == "__main__":
    unittest.main()
