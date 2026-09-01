# -*- coding: utf-8 -*-
import unittest

from company.model.value_daily import build_daily_state, portfolio_actions


def _result(symbol="1111.TW", pct=15.0, roe=20.0, price=100.0, ma20=98.0, ma60=95.0,
            momentum20=0.03, distance_from_high_252=-0.10):
    return {
        "symbol": symbol, "name": "測試股", "as_of": "2026-07-29", "price": price,
        "action": "accumulate", "quality_pass": True, "roe_ttm": roe,
        "valuation_basis": "PER", "valuation_pct": pct, "entry_range": [95, 110],
        "ma20": ma20, "ma60": ma60, "momentum20": momentum20,
        "distance_from_high_252": distance_from_high_252,
        "high_252": price / (1 + distance_from_high_252), "price_pct_252": 80.0,
        "reasons": ["test"],
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
        self.assertIn("exit_engine", advice2)

    def test_profitable_overvalued_holding_gets_exit_score(self):
        rich = _result(pct=96, price=145, ma20=140, ma60=130)
        rich["fundamental_trend"] = {
            "observed_metrics": 5, "monthly_revenue_yoy_latest": 10,
            "monthly_revenue_negative_streak": 0, "earnings_quality_ttm": 1.1,
        }
        state = build_daily_state([rich], {"1111"}, 1)
        advice = portfolio_actions(state, [{"symbol": "1111.TW", "shares": 100, "cost": 100}])[0]
        self.assertEqual(advice["exit_engine"]["status"], "watch_profit")
        self.assertEqual(advice["action"], "獲利續抱，密切觀察")

    def test_etf_is_not_sold_on_trend_alone(self):
        etf = _result("0056.TW", pct=85, roe=None, price=40, ma20=42, ma60=43)
        etf.update(action="watch", is_etf=True)
        state = build_daily_state([etf], set(), 100)
        advice = portfolio_actions(state, [{"symbol": "0056.TW", "shares": 1000, "cost": 45}])[0]
        # 單一輸入持股權重 100%，會走配置再平衡，而非因跌破均線直接賣出。
        self.assertEqual(advice["action"], "配置過高，減碼再平衡檢查")

    def test_etf_subpool_is_separate_from_stock_picks(self):
        etf = _result("0056.TW", pct=25, roe=None, price=40, ma20=39, ma60=38)
        etf.update(action="accumulate", is_etf=True)
        state = build_daily_state([_result(), etf], {"1111"}, 100)
        self.assertEqual([p["symbol"] for p in state["top_picks"]], ["1111.TW"])
        self.assertEqual([p["symbol"] for p in state["etf_candidates"]], ["0056.TW"])

    def test_near_high_fast_riser_waits_instead_of_becoming_top_pick(self):
        hot = _result(momentum20=0.14, distance_from_high_252=-0.01)
        state = build_daily_state([hot], {"1111"}, 100)
        self.assertFalse(state["top_picks"])
        self.assertEqual(state["waiting_list"][0]["decision"], "高檔，等待拉回")
        self.assertTrue(state["waiting_list"][0]["chase_risk"])

    def test_cyclical_stock_is_always_marked_high_risk(self):
        cyclical = _result(symbol="2027.TW", roe=18.0)
        state = build_daily_state([cyclical], {"2027"}, 100)
        self.assertFalse(state["top_picks"])
        self.assertEqual(state["waiting_list"][0]["risk_tier"], "高")
        self.assertEqual(state["waiting_list"][0]["decision"], "高風險反轉觀察")

    def test_stale_symbol_is_not_promoted_into_todays_picks(self):
        stale = _result("1111.TW")
        fresh = _result("2222.TW", pct=55)
        fresh["action"] = "hold"
        stale["as_of"] = "2026-07-28"
        state = build_daily_state([stale, fresh], {"1111", "2222"}, 2)
        self.assertEqual(state["as_of"], "2026-07-29")
        self.assertFalse(state["top_picks"])
        self.assertEqual(state["coverage"]["price_current"], 1)
        self.assertEqual(state["coverage"]["price_stale"], 1)


if __name__ == "__main__":
    unittest.main()
