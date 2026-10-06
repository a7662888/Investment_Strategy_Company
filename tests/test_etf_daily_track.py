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
             "decision": "等待更便宜", "action": "watch", "entry_range": [61.55, 72.61],
             "valuation_pct": 100.0, "is_etf": True},
            {"symbol": "00878.TW", "name": "國泰永續高股息", "as_of": "2026-10-06", "price": 20.5,
             "decision": "可分批", "action": "accumulate", "entry_range": [19.64, 21.12], "is_etf": True},
            {"symbol": "00919.TW", "name": "群益台灣精選高息", "as_of": "2026-10-03", "price": 31.8,
             "entry_range": [19.0, 21.0], "is_etf": True},
            {"symbol": "0056.TW", "name": "元大高股息", "as_of": "2026-10-06", "error": "no_price"},
        ],
    }


class EtfCandidates(unittest.TestCase):
    def test_only_current_priced_etfs_with_a_zone(self):
        self.assertEqual([i["symbol"] for i in select_etf_candidates(state())], ["0050.TW", "00878.TW"])

    def test_etfs_never_enter_the_stock_ranking(self):
        self.assertEqual([i["symbol"] for i in select_candidates(state())], ["1513.TW"])


class IntradayEtfItems(unittest.TestCase):
    def test_live_position_against_the_post_close_zone(self):
        quotes = {"0050.TW": {"regularMarketPrice": 117.0}, "00878.TW": {"regularMarketPrice": 20.0}}
        doc = build_flash(state(), quotes, now=NOW)
        by_symbol = {i["symbol"]: i for i in doc["etf_items"]}
        self.assertEqual(by_symbol["00878.TW"]["position"], "落在買進區內")
        self.assertEqual(by_symbol["0050.TW"]["position"], "高於買進區，不追價")
        self.assertEqual(doc["etf_items"][0]["symbol"], "00878.TW")
        self.assertEqual(doc["basis"]["etf_quoted"], 2)
        self.assertEqual([i["symbol"] for i in doc["items"]], ["1513.TW"])

    def test_missing_quote_is_reported_not_guessed(self):
        doc = build_flash(state(), {}, now=NOW)
        self.assertTrue(all(i["live_price"] is None and i["position"] == "無即時報價" for i in doc["etf_items"]))


class PremarketEtf(unittest.TestCase):
    def test_close_relative_to_zone_without_changing_decision(self):
        rows = {r["symbol"]: r for r in build_etf_watch(state())}
        self.assertEqual(rows["0050.TW"]["decision"], "等待更便宜")
        self.assertEqual(rows["0050.TW"]["position"], "高於買進區，不追價")
        self.assertEqual(rows["00878.TW"]["position"], "落在買進區內")

    def test_brief_carries_etf_watch_and_active_digest(self):
        digest = {"items": [{"code": "00981A"}], "note": "n"}
        brief = build_brief(state(), {}, {"level": "neutral"}, active_etf=digest, now=NOW)
        self.assertEqual(len(brief["etf_watch"]), 2)
        self.assertIs(brief["active_etf"], digest)


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
