# -*- coding: utf-8 -*-
import unittest
import json
import math

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
        "fundamental_trend": {
            "monthly_revenue_yoy_latest": 10.0, "monthly_revenue_negative_streak": 0,
            "quarterly_revenue_yoy": 8.0, "quarterly_eps_yoy": 8.0,
            "gross_margin_decline_2q": False, "operating_margin_decline_2q": False,
            "earnings_quality_ttm": 1.0, "debt_ratio_change_yoy": 0.0,
            "earnings_quality_method": "ttm_sum_ocf_over_sum_ni_v1",
        },
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
        rich["fundamental_trend"].update({
            "observed_metrics": 5, "monthly_revenue_yoy_latest": 10,
            "monthly_revenue_negative_streak": 0, "earnings_quality_ttm": 1.1,
        })
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

    def test_compound_margin_and_cash_warning_pauses_buys_and_holding_adds(self):
        candidate = _result("2356.TW", pct=29.3, roe=15.78)
        candidate["fundamental_trend"].update(
            gross_margin_decline_2q=True, earnings_quality_ttm=0.5764)
        state = build_daily_state([candidate], {"2356"}, 1)
        self.assertFalse(state["top_picks"])
        row = state["evaluations"][0]
        self.assertEqual(row["fundamental_assessment"]["status"], "pause_new")
        self.assertEqual(row["decision"], "基本面待確認，暫緩新增")
        self.assertEqual(row["action"], "watch")
        advice = portfolio_actions(state, [{"symbol": "2356.TW", "shares": 100, "cost": 99}])[0]
        self.assertEqual(advice["action"], "基本面待確認，暫停追加")

    def test_missing_quality_is_unknown_instead_of_false_or_top_pick(self):
        candidate = _result()
        candidate.pop("quality_pass")
        state = build_daily_state([candidate], {"1111"}, 1)
        self.assertIsNone(state["evaluations"][0]["quality_pass"])
        self.assertEqual(state["evaluations"][0]["decision"], "資料不足")
        self.assertFalse(state["top_picks"])
        advice = portfolio_actions(state, [{"symbol": "1111.TW", "shares": 100, "cost": 99}])[0]
        self.assertEqual(advice["action"], "資料不足，人工檢查")

    def test_missing_cash_trend_cannot_add_even_with_quality_true(self):
        candidate = _result()
        candidate["fundamental_trend"].pop("earnings_quality_ttm")
        state = build_daily_state([candidate], {"1111"}, 1)
        self.assertFalse(state["top_picks"])
        advice = portfolio_actions(state, [{"symbol": "1111.TW", "shares": 100, "cost": 99}])[0]
        self.assertNotIn("追加", advice["action"].replace("暫停追加", ""))
        self.assertFalse(advice["fundamental_assessment"]["allow_new"])

    def test_expected_completed_day_blocks_stale_holding_advice(self):
        state = build_daily_state([_result()], {"1111"}, 1)
        advice = portfolio_actions(state, [{"symbol": "1111.TW", "shares": 100, "cost": 99}],
                                   expected_as_of="2026-07-30")[0]
        self.assertEqual(advice["action"], "資料過期，暫停建議")
        self.assertFalse(advice["reliability"]["executable"])

    def test_malformed_numeric_inputs_fail_closed_and_remain_strict_json(self):
        for field, value in (("price", "not-a-price"), ("valuation_pct", float("nan")),
                             ("roe_ttm", float("inf")), ("ma20", "invalid-ma"),
                             ("ma60", float("-inf"))):
            with self.subTest(field=field):
                candidate = _result()
                candidate[field] = value
                candidate["entry_range"] = [float("nan"), float("inf")]
                state = build_daily_state([candidate], {"1111"}, 1)
                self.assertEqual(state["evaluations"][0]["decision"], "資料不足")
                self.assertEqual(state["evaluations"][0]["action"], "watch")
                self.assertFalse(state["top_picks"])
                json.dumps(state, allow_nan=False)
                if isinstance(value, float) and math.isnan(value):
                    self.assertTrue(math.isnan(candidate[field]))
                else:
                    self.assertEqual(candidate[field], value)
                self.assertTrue(math.isnan(candidate["entry_range"][0]))

    def test_numeric_strings_are_normalized_without_mutating_original(self):
        candidate = _result()
        candidate.update(price="100", valuation_pct="15", roe_ttm="20", ma20="98", ma60="95")
        state = build_daily_state([candidate], {"1111"}, 1)
        self.assertEqual(state["top_picks"][0]["price"], 100.0)
        self.assertEqual(state["evaluations"][0]["valuation_pct"], 15.0)
        self.assertEqual(candidate["price"], "100")
        json.dumps(state, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
