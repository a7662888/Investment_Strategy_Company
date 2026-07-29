# -*- coding: utf-8 -*-
import unittest

from company.model.value_daily import build_daily_state, portfolio_actions


def _result(symbol="1111.TW", pct=15.0, roe=20.0, price=100.0, ma20=98.0, ma60=95.0):
    return {
        "symbol": symbol, "name": "測試股", "as_of": "2026-07-29", "price": price,
        "action": "accumulate", "quality_pass": True, "roe_ttm": roe,
        "valuation_basis": "PER", "valuation_pct": pct, "entry_range": [95, 110],
        "ma20": ma20, "ma60": ma60, "momentum20": 0.03, "reasons": ["test"],
    }


class ValueDailyTests(unittest.TestCase):
    def test_daily_pick_and_coverage(self):
        state = build_daily_state([_result()], {"1111"}, 100)
        self.assertEqual(state["coverage"]["quality_covered"], 1)
        self.assertEqual(state["coverage"]["not_yet_covered"], 99)
        self.assertEqual(state["top_picks"][0]["decision"], "可分批研究")

    def test_falling_cheap_stock_waits(self):
        state = build_daily_state([_result(ma20=105, ma60=110)], {"1111"}, 100)
        self.assertFalse(state["top_picks"])
        self.assertEqual(state["waiting_list"][0]["decision"], "等待止跌")

    def test_portfolio_add_and_exit_review(self):
        state = build_daily_state([_result()], {"1111"}, 100)
        advice = portfolio_actions(state, [{"symbol": "1111.TW", "shares": 1000, "cost": 98}])[0]
        self.assertEqual(advice["action"], "可小額分批追加")
        bad = _result("2222.TW", pct=90)
        bad.update(action="avoid", quality_pass=False, failed=["ROE"])
        state2 = build_daily_state([bad], {"2222"}, 100)
        advice2 = portfolio_actions(state2, [{"symbol": "2222.TW", "shares": 1000, "cost": 80}])[0]
        self.assertEqual(advice2["action"], "賣出／減碼檢查")

    def test_etf_is_not_sold_on_trend_alone(self):
        etf = _result("0056.TW", pct=85, roe=None, price=40, ma20=42, ma60=43)
        etf.update(action="watch", is_etf=True)
        state = build_daily_state([etf], set(), 100)
        advice = portfolio_actions(state, [{"symbol": "0056.TW", "shares": 1000, "cost": 45}])[0]
        # 單一輸入持股權重 100%，會走配置再平衡，而非因跌破均線直接賣出。
        self.assertEqual(advice["action"], "配置過高，減碼再平衡檢查")


if __name__ == "__main__":
    unittest.main()
