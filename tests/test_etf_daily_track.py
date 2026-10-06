# -*- coding: utf-8 -*-
"""首頁盤前／盤中的 ETF 子軌：沿用盤後判定，只換算位置，不混進個股排序。"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_research
from company.model.intraday_flash import build_flash, select_candidates, select_etf_candidates
from company.model.premarket import build_brief, build_etf_watch

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 7, 10, 30, tzinfo=TAIPEI)


def state() -> dict:
    return {
        "as_of": "2026-10-06",
        "evaluations": [
            {"symbol": "1513.TW", "name": "中興電", "as_of": "2026-10-06", "quality_pass": True,
             "action": "accumulate", "decision": "可分批研究", "entry_range": [150.0, 170.0],
             "rank_score": 90, "is_etf": False},
        ],
        "etf_candidates": [
            {"symbol": "0050.TW", "name": "元大台灣50", "as_of": "2026-10-06", "price": 116.5,
             "decision": "定期定額", "action": "accumulate", "dca": True, "ma240": 90.0, "is_etf": True},
            {"symbol": "00878.TW", "name": "國泰永續高股息", "as_of": "2026-10-06", "price": 20.5,
             "decision": "定期定額", "action": "accumulate", "dca": True, "ma240": 21.0, "is_etf": True},
            {"symbol": "00919.TW", "name": "群益台灣精選高息", "as_of": "2026-10-03", "price": 31.8,
             "dca": True, "ma240": 28.0, "is_etf": True},
            {"symbol": "0056.TW", "name": "元大高股息", "as_of": "2026-10-06", "error": "no_price"},
            {"symbol": "00713.TW", "name": "元大台灣高息低波", "as_of": "2026-10-06", "price": 50.0,
             "decision": "定期定額", "dca": True, "ma240": None, "is_etf": True},
        ],
    }


class EtfCandidates(unittest.TestCase):
    def test_only_current_etfs_without_errors(self):
        self.assertEqual([i["symbol"] for i in select_etf_candidates(state())], ["0050.TW", "00878.TW", "00713.TW"])

    def test_etfs_never_enter_the_stock_ranking(self):
        self.assertEqual([i["symbol"] for i in select_candidates(state())], ["1513.TW"])


class IntradayEtfItems(unittest.TestCase):
    def test_live_deviation_from_the_post_close_ma(self):
        quotes = {"0050.TW": {"regularMarketPrice": 117.0}, "00878.TW": {"regularMarketPrice": 20.0}}
        doc = build_flash(state(), quotes, now=NOW)
        by_symbol = {i["symbol"]: i for i in doc["etf_items"]}
        self.assertEqual(by_symbol["0050.TW"]["ma240_deviation_pct"], 30.0)
        self.assertEqual(by_symbol["0050.TW"]["ma240_band"], "大幅高於年線（偏熱）")
        self.assertEqual(by_symbol["00878.TW"]["ma240_band"], "低於年線（市場回檔）")
        self.assertEqual(by_symbol["00713.TW"]["ma240_band"], "年線資料不足")
        self.assertEqual(doc["etf_items"][0]["symbol"], "00878.TW")
        self.assertTrue(all(i["decision"] in ("定期定額", None) for i in doc["etf_items"]))
        self.assertEqual([i["symbol"] for i in doc["items"]], ["1513.TW"])

    def test_missing_quote_is_reported_not_guessed(self):
        doc = build_flash(state(), {}, now=NOW)
        self.assertTrue(all(i["live_price"] is None and i["ma240_deviation_pct"] is None for i in doc["etf_items"]))


class PremarketEtf(unittest.TestCase):
    def test_close_relative_to_ma_without_changing_decision(self):
        rows = {r["symbol"]: r for r in build_etf_watch(state())}
        self.assertEqual(rows["0050.TW"]["decision"], "定期定額")
        self.assertEqual(rows["00878.TW"]["ma240_band"], "低於年線（市場回檔）")

    def test_brief_carries_etf_watch_and_active_digest(self):
        digest = {"items": [{"code": "00981A"}], "note": "n"}
        brief = build_brief(state(), {}, {"level": "neutral"}, active_etf=digest, now=NOW)
        self.assertEqual(len(brief["etf_watch"]), 3)
        self.assertIs(brief["active_etf"], digest)


class EtfDcaRule(unittest.TestCase):
    def rows(self, closes):
        return [{"date": f"d{i}", "close": c, "adj_close": c / 2} for i, c in enumerate(closes)]

    def test_always_dca_with_ma_as_information(self):
        from company.screener.value_rescreen import _evaluate_etf_dca
        out = _evaluate_etf_dca({"reasons": []}, self.rows([100.0] * 239 + [130.0]))
        self.assertEqual(out["action"], "accumulate")
        self.assertTrue(out["dca"])
        self.assertIsNone(out["entry_range"])
        self.assertIsNone(out["valuation_pct"])
        self.assertAlmostEqual(out["ma240"], 100.125, places=3)   # 現價尺度，非還原價尺度
        self.assertEqual(out["ma240_band"], "大幅高於年線（偏熱）")

    def test_short_history_still_dca_but_says_ma_missing(self):
        from company.screener.value_rescreen import _evaluate_etf_dca
        out = _evaluate_etf_dca({"reasons": []}, self.rows([10.0] * 50))
        self.assertEqual(out["action"], "accumulate")
        self.assertEqual(out["ma240_band"], "年線資料不足")

    def test_daily_state_and_holding_advice(self):
        from company.model.value_daily import build_daily_state, portfolio_actions
        etf = {"symbol": "0050.TW", "name": "元大台灣50", "as_of": "2026-10-06", "price": 116.5,
               "action": "accumulate", "is_etf": True, "dca": True, "ma240": 90.0,
               "ma240_deviation_pct": 29.4, "ma240_band": "大幅高於年線（偏熱）", "valuation_pct": None,
               "entry_range": None, "reasons": []}
        stock = {"symbol": "2330.TW", "name": "台積電", "as_of": "2026-10-06", "price": 1000.0,
                 "action": "watch", "quality_pass": True, "valuation_pct": 80.0, "roe_ttm": 25.0,
                 "ma20": 990.0, "ma60": 950.0, "reasons": []}
        state_doc = build_daily_state([etf, stock], {"2330"}, 100)
        item = state_doc["etf_candidates"][0]
        self.assertEqual(item["decision"], "定期定額")
        self.assertEqual(item["valuation_zone"], "大幅高於年線（偏熱）")
        advice = portfolio_actions(state_doc, [{"symbol": "0050.TW", "shares": 100, "cost": 80},
                                               {"symbol": "2330.TW", "shares": 100, "cost": 900}])[0]
        self.assertEqual(advice["action"], "定期定額續扣")


class ActiveDigest(unittest.TestCase):
    def test_summarises_top_moves_and_isolates_failures(self):
        catalog = {"rows": [
            {"code": "00981A", "name": "主動統一台股增長", "active": True, "holdings_supported": True},
            {"code": "00991A", "name": "主動復華未來50", "active": True, "holdings_supported": True},
            {"code": "00400A", "name": "未串接", "active": True, "holdings_supported": False},
        ]}
        rows = [
            {"code": "2330", "name": "台積電", "weight_change_pp": 0.8, "change": "持續持有"},
            {"code": "2454", "name": "聯發科", "weight_change_pp": -0.5, "change": "持續持有"},
            {"code": "3017", "name": "奇鋐", "weight_change_pp": 1.2, "change": "新進揭露"},
            {"code": "2317", "name": "鴻海", "weight_change_pp": -0.9, "change": "不再揭露"},
            {"code": "2308", "name": "台達電", "weight_change_pp": 0, "change": "持續持有"},
            {"code": "BOND", "name": "雜訊", "weight_change_pp": 0.004, "change": "持續持有"},
        ]

        def holdings(code):
            if code == "00991A":
                raise TimeoutError("slow")
            return {"status": "official_snapshot", "as_of": "2026-10-06", "freshness": "recent",
                    "comparison": {"status": "comparable", "previous_as_of": "2026-10-03", "rows": rows}}

        with patch.object(etf_research, "catalog", return_value=catalog), \
                patch.object(etf_research, "holdings", side_effect=holdings):
            digest = etf_research.active_digest(top=2)
        by_code = {i["code"]: i for i in digest["items"]}
        self.assertEqual(set(by_code), {"00981A", "00991A"})
        self.assertEqual([r["code"] for r in by_code["00981A"]["increases"]], ["3017", "2330"])
        self.assertEqual([r["code"] for r in by_code["00981A"]["decreases"]], ["2317", "2454"])
        self.assertEqual(by_code["00981A"]["new"], ["奇鋐"])
        self.assertEqual(by_code["00981A"]["dropped"], ["鴻海"])
        self.assertEqual(by_code["00991A"]["status"], "unavailable")
        self.assertIn("不等於實際買賣", digest["note"])


if __name__ == "__main__":
    unittest.main()
