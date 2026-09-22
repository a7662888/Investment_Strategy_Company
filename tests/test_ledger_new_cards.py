# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run_value_rescreen as rescreen


class TestCandidateSymbols(unittest.TestCase):
    """帳本原本只重篩既有卡片，新標的永遠進不來，驗證樣本因此不代表系統判斷。"""

    def test_both_buckets_are_collected(self) -> None:
        state = {"top_picks": [{"symbol": "1513.TW"}, {"symbol": "5269.TW"}],
                 "waiting_list": [{"symbol": "6781.TW"}]}
        with patch("company.model.current_state.load_current_state", return_value=(state, {})):
            self.assertEqual(rescreen.candidate_symbols(), ["1513.TW", "5269.TW", "6781.TW"])

    def test_missing_state_is_not_an_error(self) -> None:
        with patch("company.model.current_state.load_current_state", return_value=(None, {})):
            self.assertEqual(rescreen.candidate_symbols(), [])

    def test_entries_without_symbol_are_skipped(self) -> None:
        state = {"top_picks": [{"name": "無代號"}, {"symbol": "1513.TW"}], "waiting_list": []}
        with patch("company.model.current_state.load_current_state", return_value=(state, {})):
            self.assertEqual(rescreen.candidate_symbols(), ["1513.TW"])


class TestFundamentalsAreShared(unittest.TestCase):
    """帳本與每日狀態必須餵同一份基本面，否則同一引擎會給出兩種判定。"""

    def test_seed_is_overlaid_with_refreshed_quarters(self) -> None:
        seed = {"2330": {"quarterly": [1] * 9}, "9999": {"quarterly": [1]}}
        full = {"stocks": {"2330": {"quarterly": [1] * 12}}}
        with patch.object(rescreen, "load_fundamentals", return_value=(full, {})):
            with patch.object(Path, "read_text", return_value=__import__("json").dumps(seed)):
                with patch.object(Path, "exists", return_value=True):
                    merged = rescreen._current_fundamentals()
        # 刷新後的季度資料必須勝出，未刷新的標的仍保留 seed
        self.assertEqual(len(merged["2330"]["quarterly"]), 12)
        self.assertIn("9999", merged)


if __name__ == "__main__":
    unittest.main()


class TestMarketContextSurvivesTheWhitelist(unittest.TestCase):
    """build_signal_event 是白名單式輸出：未列入的欄位會被靜默丟棄。

    量價脈絡必須在凍結當下存下來——帳本 append-only，事後補不了。
    """

    def _signal(self, **extra):
        return {"agent_id": "claude-value", "model_version": "t", "symbol": "1513.TW",
                "data_cutoff": "2026-09-22", "action": "accumulate", "horizon": "120D",
                "reference_price": 166.0, **extra}

    def test_market_context_is_persisted(self) -> None:
        from company.model.ledger import build_signal_event
        context = {"vwap": 166.49, "close_vs_vwap_pct": -0.29, "volume_ratio": 1.04,
                   "spread_pct": 0.3, "tick_pressure": "sell", "trade_date": "2026-09-22"}
        event = build_signal_event(self._signal(market_context=context))
        self.assertEqual(event["market_context"], context)

    def test_absent_context_becomes_an_empty_dict_not_a_crash(self) -> None:
        from company.model.ledger import build_signal_event
        self.assertEqual(build_signal_event(self._signal())["market_context"], {})

    def test_context_does_not_alter_the_signal_identity(self) -> None:
        """脈絡只是存證，不得改變 signal_id——否則同一判定會被視為不同卡。"""
        from company.model.ledger import build_signal_event
        bare = build_signal_event(self._signal())
        with_context = build_signal_event(self._signal(market_context={"volume_ratio": 1.04}))
        self.assertEqual(bare["signal_id"], with_context["signal_id"])


class TestMarketContextLoader(unittest.TestCase):
    def test_only_the_selected_fields_are_kept(self) -> None:
        document = {"trade_date": "2026-09-22", "snapshots": {"1513.TW": {
            "vwap": 166.49, "close_vs_vwap_pct": -0.29, "volume_ratio": 1.04,
            "spread_pct": 0.3, "tick_pressure": "sell", "source": "Shioaji snapshots",
            "open": 1, "high": 2, "low": 3, "close": 4, "ts": 5}}}
        with patch("company.model.durable_document.load_document", return_value=(document, {})):
            context = rescreen.market_context()["1513.TW"]
        # 帳本體積曾因過大被覆蓋，欄位要挑過，不整包塞進去
        self.assertNotIn("ts", context)
        self.assertNotIn("open", context)
        self.assertEqual(context["volume_ratio"], 1.04)
        self.assertEqual(context["trade_date"], "2026-09-22")

    def test_missing_snapshot_does_not_block_freezing(self) -> None:
        with patch("company.model.durable_document.load_document", return_value=(None, {})):
            self.assertEqual(rescreen.market_context(), {})
