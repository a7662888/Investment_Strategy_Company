# -*- coding: utf-8 -*-
"""籌碼來源解析、法人比率與連買天數、集保大戶、月營收、均線策略、除息行事曆。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data import chip_sources as src
from company.model import chip_flow as cf


class Parsers(unittest.TestCase):
    def test_t86_uses_foreign_excluding_dealer_and_trust_columns(self):
        fields = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                  "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
                  "投信買進股數", "投信賣出股數", "投信買賣超股數"]
        row = ["2330  ", "台積電", "1", "2", "-9,364,622", "0", "0", "5", "3", "4", "1,029,673"]
        self.assertEqual(src.parse_t86({"stat": "OK", "fields": fields, "data": [row]}),
                         {"2330": (-9364622.0, 1029673.0)})
        self.assertEqual(src.parse_t86({"stat": "很抱歉，沒有符合條件的資料!"}), {})

    def test_tpex_columns(self):
        row = ["5274", "信驊", "193,847", "222,291", "-28,444", "0", "0", "0", "193,847", "222,291", "-28,444",
               "28,065", "9,100", "18,965", "2,300", "3,154", "-854"]
        self.assertEqual(src.parse_tpex_insti({"tables": [{"data": [row]}]}), {"5274": (-28444.0, 18965.0)})

    def test_shares_from_capital_and_par(self):
        self.assertEqual(src.parse_shares_tpex([{"SecuritiesCompanyCode": "5274", "Paidin.Capital.NTDollars": "376000000",
                                                 "ParValueOfCommonStock": "新台幣 10.0000元"}]), {"5274": 37600000.0})
        self.assertEqual(src.parse_shares_tpex([{"SecuritiesCompanyCode": "X", "Paidin.Capital.NTDollars": "1000",
                                                 "ParValueOfCommonStock": "新台幣 1.0000元"}]), {"X": 1000.0})

    def test_tdcc_sums_levels_12_to_15_and_drops_single_holder(self):
        text = "﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" + "\n".join(
            [f"20261016,2330  ,{lv},10,10,{pct}" for lv, pct in ((11, 1.44), (12, 1.08), (13, 0.92), (14, 0.76), (15, 84.56))]
            + ["20261016,2330  ,17,3098860,1,100.00"]
            + ["20261016,6173  ,15,1,160928000,100.00", "20261016,6173  ,17,1,160928000,100.00"])
        day, big = src.parse_tdcc(text)
        self.assertEqual(day, "2026-10-16")
        self.assertEqual(big, {"2330": 87.32})

    def test_dividend_parsers(self):
        fields = ["除權除息日期", "股票代號", "名稱", "除權息", "無償配股率", "現金增資配股率", "現金增資認購價", "現金股利"]
        rows = src.parse_twt48u({"stat": "OK", "fields": fields, "data": [
            ["115年10月22日", "0056", "元大高股息", "息", "0", "0", "0", "<p>待公告實際收益分配金額</p>"],
            ["115年10月21日", "00408A", "主動第一金優股息", "息", "0", "0", "0", "0.21000000"]]})
        self.assertEqual(rows[0]["ex_date"], "2026-10-22")
        self.assertIsNone(rows[0]["cash_dividend"])
        self.assertEqual(rows[1]["cash_dividend"], 0.21)
        tpex = src.parse_tpex_exright([{"ExRrightsExDividendDate": "1151103", "SecuritiesCompanyCode": "00953B",
                                        "CompanyName": "群益優選非投等債", "ExRrightsExDividend": "除息", "CashDividend": "0.06700000"}])
        self.assertEqual((tpex[0]["ex_date"], tpex[0]["cash_dividend"]), ("2026-11-03", 0.067))


class Flows(unittest.TestCase):
    def test_ratios_streak_and_window(self):
        history = {}
        days = [f"2026-09-{d:02d}" for d in range(1, 21)]
        for i, d in enumerate(days):
            cf.merge_day(history, d, {"A": (1000.0, -500.0 if i < 6 else 2000.0)}, ["A"])
        m = cf.flow_metrics(history, "A", 1_000_000, days)
        self.assertEqual(m["flow_days"], 20)
        self.assertEqual(m["trust_streak"], 14)
        self.assertEqual(m["foreign5"], 0.5)
        self.assertEqual(m["trust20"], round((6 * -500 + 14 * 2000) / 1e6 * 100, 2))
        self.assertEqual(cf.missing_days(history, days + ["2026-09-21"]), ["2026-09-21"])

    def test_twenty_day_ratio_waits_for_full_window(self):
        history = {}
        cf.merge_day(history, "2026-09-01", {"A": (1.0, 1.0)}, ["A"])
        m = cf.flow_metrics(history, "A", 100.0, ["2026-09-01"])
        self.assertIsNone(m["trust20"])
        self.assertEqual(m["trust5"], 1.0)

    def test_streak_signs(self):
        self.assertEqual(cf.streak([1, -1, -2, -3]), -3)
        self.assertEqual(cf.streak([5, 0]), 0)

    def test_history_keeps_last_days(self):
        history = {}
        for d in range(1, 41):
            cf.merge_day(history, f"2026-08-{d:02d}" if d <= 31 else f"2026-09-{d - 31:02d}", {"A": (1, 1)}, ["A"])
        self.assertEqual(len(history["days"]), cf.KEEP_DAYS)

    def test_big_holder_weekly_change(self):
        history = {}
        cf.merge_tdcc(history, "2026-10-09", {"A": 60.0}, ["A"])
        cf.merge_tdcc(history, "2026-10-16", {"A": 61.25}, ["A"])
        self.assertEqual(cf.big_holder(history, "A"), {"big400_pct": 61.25, "big400_date": "2026-10-16", "big400_change_pp": 1.25})


class Revenue(unittest.TestCase):
    def test_yoy_and_twelve_month_high(self):
        rows = [{"revenue_year": 2025 + (m > 12), "revenue_month": (m - 1) % 12 + 1, "revenue": 100 + m} for m in range(1, 22)]
        sig = cf.revenue_signal(rows)
        self.assertEqual(sig["revenue_month"], "2026-09")
        self.assertEqual(sig["revenue_yoy_pct"], round((121 / 109 - 1) * 100, 2))
        self.assertTrue(sig["revenue_high_12m"])

    def test_staleness(self):
        self.assertTrue(cf.revenue_stale(None, date(2026, 10, 20)))
        self.assertFalse(cf.revenue_stale({"fetched": "2026-10-16"}, date(2026, 10, 20)))
        self.assertTrue(cf.revenue_stale({"fetched": "2026-10-13"}, date(2026, 10, 20)))
        self.assertTrue(cf.revenue_stale({"fetched": "2026-11-04"}, date(2026, 11, 5)))


class MaStrategy(unittest.TestCase):
    def rows(self, adj):
        return [{"date": f"{2020 + i // 245}-{1 + (i % 245) // 21:02d}-{1 + i % 21:02d}", "adj": v, "close": v, "high": v}
                for i, v in enumerate(adj)]

    def test_always_above_ma_pays_entry_fee_only(self):
        adj = [100 * 1.001 ** i for i in range(245 * 6)]
        out = cf.ma_strategy(self.rows(adj), 20)
        self.assertEqual(out["trades"], 1)
        self.assertLess(out["cagr_pct"], out["hold_cagr_pct"] + 0.01)
        self.assertGreater(out["cagr_pct"], out["hold_cagr_pct"] - 0.5)

    def test_choppy_series_bleeds_costs(self):
        adj = [100 + (5 if (i // 10) % 2 else -5) for i in range(245 * 6)]
        out = cf.ma_strategy(self.rows(adj), 20)
        self.assertGreater(out["trades"], 50)
        self.assertLess(out["cagr_pct"], out["hold_cagr_pct"])

    def test_price_metrics_discount_and_deviation(self):
        rows = self.rows([100.0] * 300 + [80.0] * 20)
        m = cf.price_metrics(rows)
        self.assertEqual(m["discount_pct"], -20.0)
        self.assertEqual(m["vs_ma20_pct"], 0.0)


class Dividends(unittest.TestCase):
    def test_last_buy_date_skips_weekend_and_past_rows(self):
        weekday = lambda d: d.weekday() < 5  # noqa: E731
        rows = [{"code": "0056", "name": "x", "ex_date": "2026-10-26", "kind": "息", "cash_dividend": None, "market": "上市"},
                {"code": "2330", "name": "y", "ex_date": "2026-10-01", "kind": "息", "cash_dividend": 5, "market": "上市"},
                {"code": "1234", "name": "z", "ex_date": "2026-10-22", "kind": "息", "cash_dividend": 1, "market": "上櫃"}]
        out = cf.dividend_calendar(rows, "2026-10-20", weekday, {"0056"})
        self.assertEqual([r["code"] for r in out], ["1234", "0056"])
        self.assertEqual(out[1]["last_buy_date"], "2026-10-23")
        self.assertTrue(out[1]["is_etf"] and out[1]["tracked"])
        self.assertEqual(cf.previous_trading_day("2026-10-22", weekday), "2026-10-21")


if __name__ == "__main__":
    unittest.main()
