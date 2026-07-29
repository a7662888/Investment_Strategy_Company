from __future__ import annotations

import unittest
from unittest.mock import patch

from company.data.value_fundamentals import completeness, parse_quarterly, refresh_pool
from company.screener.value_rescreen import _pct_rank_quantiles


def row(period, kind, value):
    return {"date": period, "type": kind, "value": value}


class ValueFundamentalsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
