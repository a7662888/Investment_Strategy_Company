# -*- coding: utf-8 -*-
"""ETF 每日資料、研究候選與停扣檢查。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data.etf_market import build_daily, parse_mis, parse_tpex_day, parse_twse_day, spread_pct
from company.model.etf_screen import holding_checks, screen, stop_checks, update_history
from company.model.intraday_flash import build_flash

DAY = "2026-10-06"
MIS = {"a1": [{"msgArray": [
    {"a": "0050", "b": "元大台灣50", "c": 22233000000, "d": 11500000, "e": 116.5, "f": 116.44,
     "g": 0.05, "h": "116.2200", "i": "20261006", "j": "13:31:00"},
    {"a": "00981A", "b": "主動統一台股增長", "c": "9158709000", "d": "-100000000", "e": "32.60",
     "f": "32.81", "g": "-1.64", "h": "32.79", "i": "20261006", "j": "16:59:54"},
    {"a": "00999", "b": "舊資料", "c": "1,000", "d": "0", "e": "10", "f": "10", "g": "0", "h": "10",
     "i": "20261005", "j": "13:30:00"},
]}]}


class Sources(unittest.TestCase):
    def test_mis_fields_match_the_published_table(self):
        rows = parse_mis(MIS)
        self.assertEqual(rows["0050"]["units"], 22233000000)
        self.assertEqual(rows["0050"]["prev_nav"], 116.22)
        self.assertEqual(rows["00981A"]["units_change"], -100000000)
        self.assertEqual(rows["00981A"]["premium_pct"], -1.64)
        self.assertEqual(rows["0050"]["date"], DAY)

    def test_roc_dates_and_spread(self):
        twse = parse_twse_day([{"Date": "1151005", "Code": "0050", "ClosingPrice": "115.95", "TradeValue": "11921576082"}])
        self.assertEqual(twse["0050"]["date"], "2026-10-05")
        tpex = parse_tpex_day([{"Date": "1151006", "SecuritiesCompanyCode": "00679B", "Close": "24.33",
                                "TransactionAmount": "709495612", "LatestBidPrice": "24.32", "LatesAskPrice": "24.33"},
                               {"Date": "1151006", "SecuritiesCompanyCode": "6488"}], {"00679B"})
        self.assertEqual(set(tpex), {"00679B"})
        self.assertEqual(spread_pct(24.32, 24.33), 0.041)
        self.assertIsNone(spread_pct(None, 24.33))

    def test_only_same_day_data_is_used_and_shioaji_wins(self):
        mis = parse_mis(MIS)
        twse = {"0050": {"date": "2026-10-05", "amount": 1e10}}
        snaps = {"0050": {"trade_date": DAY, "total_amount": 1.2e10, "bid": 116.45, "ask": 116.5}}
        daily = build_daily(["0050", "00999"], DAY, mis, twse, {}, snaps)
        self.assertEqual(daily["0050"]["amount_billion_twd"], 120.0)
        self.assertEqual(daily["0050"]["sources"]["trading"], "Shioaji snapshots")
        self.assertAlmostEqual(daily["0050"]["aum_billion_twd"], 25839.19, places=1)
        self.assertIsNone(daily["00999"]["aum_billion_twd"])   # MIS 舊日期不採用
        no_snap = build_daily(["0050"], DAY, mis, twse, {}, {})
        self.assertIsNone(no_snap["0050"]["amount_billion_twd"])  # 落後一日的 OpenAPI 不冒充當日


def catalog_row(code, **kw):
    base = {"code": code, "name": code, "category": "核心市值", "holders": 100_000,
            "ranking_as_of": "2026-10-02", "listing_date": "2015-01-01", "active": False}
    return {**base, **kw}


class History(unittest.TestCase):
    def test_lagged_amount_is_stored_under_its_own_date(self):
        h = update_history(None, DAY, {"0050": {"units": 1.0, "amount_billion_twd": None, "premium_pct": 0.1}},
                           {"0050": ("2026-10-02", 3_000_000)}, {"2026-10-05": {"0050": 119.2}})
        days = h["codes"]["0050"]["daily"]
        self.assertEqual(days, [["2026-10-05", None, 119.2, None], [DAY, 1.0, None, 0.1]])
        again = update_history(h, DAY, {"0050": {"units": 1.0, "amount_billion_twd": None, "premium_pct": 0.1}},
                               {"0050": ("2026-10-02", 3_000_000)})
        self.assertEqual(len(again["codes"]["0050"]["daily"]), 2)   # 重跑同日不重複
        self.assertEqual(len(again["codes"]["0050"]["holders"]), 1)

    def test_history_is_rolling(self):
        h = None
        for i in range(30):
            h = update_history(h, f"2026-09-{i + 1:02d}" if i < 30 else DAY,
                               {"0050": {"units": 1.0, "amount_billion_twd": 1.0, "premium_pct": 0}}, {})
        self.assertEqual(len(h["codes"]["0050"]["daily"]), 25)


class Screen(unittest.TestCase):
    def history(self, amount=1.0):
        return {"codes": {c: {"daily": [[DAY, 1.0, amount, 0.1]], "holders": []} for c in ("0050", "0056", "00999")}}

    def daily(self, aum=100.0):
        return {c: {"aum_billion_twd": aum, "premium_pct": 0.1} for c in ("0050", "0056", "00999")}

    def test_candidate_criteria(self):
        rows = [catalog_row("0050"), catalog_row("0056", holders=10_000), catalog_row("00999", listing_date="2025-01-01")]
        doc = screen(rows, self.daily(), self.history(), DAY, ["0050"])
        self.assertEqual([c["code"] for c in doc["candidates"]], ["0050"])
        self.assertTrue(doc["candidates"][0]["in_subpool"])
        self.assertIn("費用待查", doc["candidates"][0]["fee_status"])
        self.assertEqual(doc["stop_checks"][0]["code"], "0050")
        self.assertEqual(doc["new_entries"], [])        # 沒有前次結果時不宣稱新進
        named = screen([catalog_row("0050", name="元大S&amp;P500")], self.daily(), self.history(), DAY, [])
        self.assertEqual(named["candidates"][0]["name"], "元大S&P500")

    def test_low_liquidity_is_excluded(self):
        doc = screen([catalog_row("0050")], self.daily(), self.history(amount=0.1), DAY, [])
        self.assertEqual(doc["candidates"], [])

    def test_new_entries_and_exits_against_previous_day(self):
        previous = {"trade_date": "2026-10-05", "candidates": [{"code": "0056"}]}
        doc = screen([catalog_row("0050"), catalog_row("0056", holders=1)], self.daily(), self.history(), DAY, [], previous)
        self.assertEqual(doc["new_entries"], ["0050"])
        self.assertEqual(doc["exits"], ["0056"])
        self.assertEqual(doc["compared_with"], "2026-10-05")
        rerun = screen([catalog_row("0050")], self.daily(), self.history(), DAY, [], {"trade_date": DAY, "candidates": []})
        self.assertEqual(rerun["new_entries"], [])      # 同日重跑不算新進


class StopChecks(unittest.TestCase):
    def test_flags(self):
        days = [[f"2026-09-{i + 1:02d}", 100.0 - i, 1.0, 1.5 if i >= 17 else 0.1] for i in range(21)]
        entry = {"daily": days, "holders": [["w1", 50], ["w2", 49], ["w3", 48], ["w4", 47], ["w5", 46]]}
        flags = stop_checks(entry, {"aum_billion_twd": 8.0})
        text = "；".join(flags)
        self.assertIn("規模約 8.0 億", text)
        self.assertIn("減少 20.0%", text)
        self.assertIn("|預估折溢價| 超過", text)
        self.assertIn("連續 4 週減少", text)

    def test_insufficient_history_is_not_judged(self):
        entry = {"daily": [["2026-10-05", 100.0, 1.0, 2.0], [DAY, 50.0, 1.0, 2.0]], "holders": [["w1", 10], ["w2", 5]]}
        self.assertEqual(stop_checks(entry, {"aum_billion_twd": 100.0}), [])

    def test_holding_checks_cover_any_code_with_data(self):
        doc = {"latest": {"00999": [5.0, 0.0]}}
        self.assertEqual(list(holding_checks(doc, {}, ["00999", "00000"])), ["00999"])
        self.assertTrue(holding_checks(doc, {}, ["00999"])["00999"])


class FlashPremium(unittest.TestCase):
    def test_premium_note_only_beyond_one_percent(self):
        state = {"as_of": DAY, "evaluations": [], "etf_candidates": [
            {"symbol": "0050.TW", "as_of": DAY, "price": 116.5, "ma240": 90.0, "decision": "定期定額"},
            {"symbol": "00981A.TW", "as_of": DAY, "price": 32.6, "ma240": 30.0, "decision": "定期定額"}]}
        doc = build_flash(state, {}, etf_nav=parse_mis(MIS))
        by = {i["symbol"]: i for i in doc["etf_items"]}
        self.assertIsNone(by["0050.TW"]["premium_note"])
        self.assertIn("折價 1.64%", by["00981A.TW"]["premium_note"])


class EmailSection(unittest.TestCase):
    def test_section_lists_changes_and_held_etf_checks(self):
        import run_daily_email
        doc = {"trade_date": DAY, "candidates": [{"code": "0050", "name": "元大台灣50"}], "new_entries": ["0050"],
               "exits": [], "stop_checks": [], "latest": {"00999": [5.0, 0.0]}, "disclaimer": "d"}
        html = run_daily_email.etf_screen_html(doc, ["00999.TW", "2330.TW"])
        self.assertIn("新進研究候選", html)
        self.assertIn("建議檢查是否停扣 00999（持有）", html)
        self.assertEqual(run_daily_email.etf_screen_html(None, []), "")


if __name__ == "__main__":
    unittest.main()
