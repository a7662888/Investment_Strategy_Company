# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model import daily_jobs as jobs
from company.model.intraday_flash import build_flash, select_candidates
from company.model.value_daily import _consensus_as_of

TAIPEI = timezone(timedelta(hours=8))


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TAIPEI)


class TestConsensusAsOf(unittest.TestCase):
    """2026-09-10 實際故障：少數標的先有新一日 bar，max() 讓整份狀態停擺。"""

    def test_minority_newer_bars_do_not_move_the_headline_date(self) -> None:
        items = [{"as_of": "2026-09-09"}] * 101 + [{"as_of": "2026-09-10"}] * 5
        self.assertEqual(_consensus_as_of(items), "2026-09-09")

    def test_when_everything_updates_the_date_moves(self) -> None:
        self.assertEqual(_consensus_as_of([{"as_of": "2026-09-10"}] * 106), "2026-09-10")

    def test_equal_coverage_moves_to_the_newer_date(self) -> None:
        # 覆蓋相當時要如實前進，否則真正的新交易日會被誤判成尚未到來。
        items = [{"as_of": "2026-09-09"}] * 5 + [{"as_of": "2026-09-10"}] * 5
        self.assertEqual(_consensus_as_of(items), "2026-09-10")

    def test_substantial_partial_update_is_accepted(self) -> None:
        items = [{"as_of": "2026-09-09"}] * 60 + [{"as_of": "2026-09-10"}] * 46
        self.assertEqual(_consensus_as_of(items), "2026-09-10")

    def test_a_single_stale_symbol_does_not_hold_the_date_back(self) -> None:
        items = [{"as_of": "2026-07-28"}, {"as_of": "2026-07-29"}]
        self.assertEqual(_consensus_as_of(items), "2026-07-29")

    def test_no_prices_yields_empty(self) -> None:
        self.assertEqual(_consensus_as_of([]), "")
        self.assertEqual(_consensus_as_of([{"as_of": None}]), "")


class TestJobWindows(unittest.TestCase):
    def test_market_session_boundaries(self) -> None:
        self.assertFalse(jobs.in_market_session(at("2026-09-11T08:59")))
        self.assertTrue(jobs.in_market_session(at("2026-09-11T09:00")))
        self.assertTrue(jobs.in_market_session(at("2026-09-11T13:30")))
        self.assertFalse(jobs.in_market_session(at("2026-09-11T13:31")))

    def test_weekend_is_never_a_job_window(self) -> None:
        saturday = at("2026-09-12T10:00")
        self.assertFalse(jobs.in_market_session(saturday))
        self.assertFalse(jobs.is_after_close(saturday))
        self.assertFalse(jobs.postclose_due(None, saturday)[0])
        self.assertFalse(jobs.intraday_due(None, saturday)[0])

    def test_postclose_waits_until_after_the_close(self) -> None:
        due, why = jobs.postclose_due({"analysis_date_taipei": "2026-09-10"}, at("2026-09-11T10:30"))
        self.assertFalse(due)
        self.assertIn("尚未到盤後", why)

    def test_postclose_runs_once_per_trading_day(self) -> None:
        stale = {"analysis_date_taipei": "2026-09-10"}
        fresh = {"analysis_date_taipei": "2026-09-11"}
        now = at("2026-09-11T14:30")
        self.assertTrue(jobs.postclose_due(stale, now)[0])
        self.assertFalse(jobs.postclose_due(fresh, now)[0])

    def test_intraday_runs_once_per_trading_day(self) -> None:
        now = at("2026-09-11T10:30")
        self.assertTrue(jobs.intraday_due(None, now)[0])
        self.assertFalse(jobs.intraday_due({"date": "2026-09-11"}, now)[0])
        self.assertTrue(jobs.intraday_due({"date": "2026-09-10"}, now)[0])


class TestClaim(unittest.TestCase):
    def setUp(self) -> None:
        jobs._STATE.clear()

    def test_second_claim_is_refused_while_running(self) -> None:
        self.assertTrue(jobs.claim("demo", "2026-09-11"))
        self.assertFalse(jobs.claim("demo", "2026-09-11"))

    def test_completed_day_is_not_reclaimed(self) -> None:
        jobs.claim("demo", "2026-09-11")
        jobs.finish("demo", "2026-09-11", True)
        self.assertFalse(jobs.claim("demo", "2026-09-11"))

    def test_failure_allows_a_retry(self) -> None:
        jobs.claim("demo", "2026-09-11")
        jobs.finish("demo", "2026-09-11", False, "boom")
        self.assertTrue(jobs.claim("demo", "2026-09-11"))

    def test_a_stuck_run_is_released_so_the_day_is_not_lost(self) -> None:
        jobs.claim("demo", "2026-09-11")
        jobs._STATE["demo"]["started_at"] -= 3600
        self.assertTrue(jobs.claim("demo", "2026-09-11"))


class TestIntradayFlash(unittest.TestCase):
    def _state(self) -> dict:
        return {
            "as_of": "2026-09-11",
            "evaluations": [
                {"symbol": "1513.TW", "name": "中興電", "as_of": "2026-09-11",
                 "quality_pass": True, "action": "accumulate", "decision": "可分批研究",
                 "entry_range": [150.0, 170.0], "rank_score": 90, "valuation_pct": 7.0,
                 "is_etf": False, "fundamental_trend": {"monthly_revenue_yoy_latest": 20.0}},
                {"symbol": "2912.TW", "name": "統一超", "as_of": "2026-09-11",
                 "quality_pass": True, "action": "accumulate", "decision": "等待止跌",
                 "entry_range": [200.0, 220.0], "rank_score": 78, "valuation_pct": 11.8,
                 "is_etf": False, "fundamental_trend": {}},
                {"symbol": "2409.TW", "name": "友達", "as_of": "2026-09-11",
                 "quality_pass": False, "action": "avoid", "decision": "排除／賣出檢查",
                 "entry_range": [20.0, 25.0], "rank_score": 10, "is_etf": False},
                {"symbol": "0050.TW", "name": "元大台灣50", "as_of": "2026-09-11",
                 "quality_pass": True, "action": "watch", "decision": "等待更便宜",
                 "entry_range": [59.0, 65.0], "rank_score": 36, "is_etf": True},
                {"symbol": "2330.TW", "name": "台積電", "as_of": "2026-09-09",
                 "quality_pass": True, "action": "accumulate", "decision": "可分批研究",
                 "entry_range": [1700.0, 2000.0], "rank_score": 95, "is_etf": False},
            ],
        }

    def test_only_researchable_non_etf_current_names_are_candidates(self) -> None:
        symbols = [c["symbol"] for c in select_candidates(self._state())]
        # 排除：avoid、ETF、價格日期落後於共識日的標的
        self.assertEqual(sorted(symbols), ["1513.TW", "2912.TW"])

    def test_position_relative_to_the_existing_entry_zone(self) -> None:
        quotes = {
            "1513.TW": {"regularMarketPrice": 160.0, "source": "TWSE MIS"},
            "2912.TW": {"regularMarketPrice": 240.0, "source": "TWSE MIS"},
        }
        doc = build_flash(self._state(), quotes, now=at("2026-09-11T10:30"))
        by_symbol = {i["symbol"]: i for i in doc["items"]}
        self.assertEqual(by_symbol["1513.TW"]["position"], "落在買進區內")
        self.assertEqual(by_symbol["2912.TW"]["position"], "高於買進區，不追價")
        # 在區間內的排前面，盤中最有行動意義
        self.assertEqual(doc["items"][0]["symbol"], "1513.TW")

    def test_cheaper_than_the_zone_is_distinguished_from_inside_it(self) -> None:
        quotes = {"1513.TW": {"regularMarketPrice": 140.0}}
        doc = build_flash(self._state(), quotes, now=at("2026-09-11T10:30"))
        by_symbol = {i["symbol"]: i for i in doc["items"]}
        self.assertEqual(by_symbol["1513.TW"]["position"], "低於買進區（更便宜）")

    def test_flash_is_marked_provisional_and_never_claims_ledger_status(self) -> None:
        doc = build_flash(self._state(), {}, now=at("2026-09-11T10:30"))
        self.assertTrue(doc["provisional"])
        self.assertIn("不寫入 Decision Ledger", doc["disclaimer"])

    def test_missing_quote_is_not_silently_treated_as_a_price(self) -> None:
        doc = build_flash(self._state(), {}, now=at("2026-09-11T10:30"))
        for item in doc["items"]:
            self.assertIsNone(item["live_price"])
            self.assertEqual(item["position"], "無即時報價")


if __name__ == "__main__":
    unittest.main()
