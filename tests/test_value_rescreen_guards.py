import copy
import os
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

import run_value_rescreen as rescreen


DAY = "2026-10-02"
NOW = datetime.fromisoformat("2026-10-02T17:00:00+08:00")


def result(**overrides):
    return {"symbol": "1111.TW", "as_of": DAY, "price": 100.0,
            "action": "accumulate", "entry_range": [90, 110], "reasons": [], **overrides}


class RescreenContextGuards(unittest.TestCase):
    def context(self, rows, day=DAY, now=NOW):
        with patch("company.model.durable_document.load_document",
                   return_value=({"trade_date": day, "snapshots": rows}, {})):
            return rescreen.market_context(now=now)

    def test_valid_per_row_date_is_retained(self):
        rows = {"1111.TW": {"trade_date": DAY, "timestamp_status": "valid", "volume_ratio": 1.2}}
        self.assertEqual(self.context(rows)["1111.TW"]["trade_date"], DAY)

    def test_stale_row_cannot_be_relabelled_with_batch_date(self):
        rows = {"1111.TW": {"trade_date": "2026-10-01", "timestamp_status": "valid"}}
        self.assertEqual(self.context(rows), {})

    def test_invalid_future_stale_and_legacy_unverified_context_are_omitted(self):
        for status in ("invalid", "future", "stale", None):
            with self.subTest(status=status):
                self.assertEqual(self.context({"1111.TW": {"trade_date": DAY, "timestamp_status": status}}), {})
        self.assertEqual(self.context({"1111.TW": {"volume_ratio": 1.2}}), {})

    def test_future_batch_and_uncompleted_today_are_omitted(self):
        for day, now in (("2026-10-05", NOW), (DAY, NOW.replace(hour=13))):
            rows = {"1111.TW": {"trade_date": day, "timestamp_status": "valid"}}
            self.assertEqual(self.context(rows, day, now), {})


class RescreenFreezeGuards(unittest.TestCase):
    def invoke(self, rows=None, *, old=None, contexts=None, env=None, configured=False,
               stored=None, dry_run=False):
        cards = {"1111.TW": old or {"action": "watch", "data_cutoff": "2026-10-01", "signal_id": "old"}}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env or {}, clear=True))
            stack.enter_context(patch.object(rescreen.sys, "argv", ["rescreen"] + (["--dry-run"] if dry_run else [])))
            stack.enter_context(patch.object(rescreen.durable_document, "_config",
                                             return_value=("test-only", "private/data", "main") if configured else None))
            stack.enter_context(patch.object(rescreen, "current_cards", return_value=cards))
            stack.enter_context(patch.object(rescreen, "candidate_symbols", return_value=[]))
            stack.enter_context(patch.object(rescreen, "_current_fundamentals", return_value={}))
            stack.enter_context(patch.object(rescreen, "rescreen_all", return_value=rows if rows is not None else [result()]))
            stack.enter_context(patch.object(rescreen, "latest_completed_market_date", return_value=DAY))
            stack.enter_context(patch.object(rescreen, "market_context", return_value=contexts or {}))
            stack.enter_context(patch.object(rescreen, "qualitative", return_value=[]))
            freeze = stack.enter_context(patch.object(rescreen.ledger, "freeze_signals",
                return_value=stored or {"durable": True, "local_saved": True}))
            stack.enter_context(patch("builtins.print"))
            rc = rescreen.main()
        return rc, freeze

    def test_ci_remote_failure_is_nonzero_despite_local_save(self):
        rc, freeze = self.invoke(env={"GITHUB_ACTIONS": "true"}, configured=True,
                                stored={"local_saved": True, "durable": False, "error": "github_http_503"})
        self.assertEqual(rc, 1)
        freeze.assert_called_once()

    def test_private_config_requires_durable_outside_ci_too(self):
        rc, _ = self.invoke(configured=True, stored={"local_saved": True, "durable": False})
        self.assertEqual(rc, 1)

    def test_ci_without_durable_config_stops_before_freeze(self):
        rc, freeze = self.invoke(env={"GITHUB_ACTIONS": "true"})
        self.assertEqual(rc, 1)
        freeze.assert_not_called()

    def test_local_only_mode_is_allowed_outside_ci(self):
        rc, _ = self.invoke(stored={"local_saved": True, "durable": False})
        self.assertEqual(rc, 0)

    def test_incomplete_stale_future_or_missing_cutoff_cannot_freeze(self):
        for day in ("2026-10-01", "2026-10-05", None, ""):
            with self.subTest(day=day):
                rc, freeze = self.invoke([result(as_of=day)])
                self.assertEqual(rc, 1)
                freeze.assert_not_called()

    def test_cutoff_older_than_previous_signal_is_skipped(self):
        rc, freeze = self.invoke(old={"action": "watch", "data_cutoff": "2026-10-05"})
        self.assertEqual(rc, 1)
        freeze.assert_not_called()

    def test_valid_changed_action_freezes_only_same_day_verified_context(self):
        context = {"trade_date": DAY, "timestamp_status": "valid", "volume_ratio": 1.2}
        rc, freeze = self.invoke(contexts={"1111.TW": context})
        self.assertEqual(rc, 0)
        self.assertEqual(freeze.call_args.args[0][0]["market_context"], context)

    def test_mismatched_or_unverified_context_never_becomes_immutable_evidence(self):
        for context in ({"trade_date": "2026-10-01", "timestamp_status": "valid", "volume_ratio": 1.2},
                        {"trade_date": DAY, "timestamp_status": "stale", "volume_ratio": 1.2},
                        {"trade_date": DAY, "volume_ratio": 1.2}):
            rc, freeze = self.invoke(contexts={"1111.TW": context})
            self.assertEqual(rc, 0)
            signal = freeze.call_args.args[0][0]
            self.assertEqual(signal["market_context"], {})
            self.assertFalse(any("volume_ratio" in str(ev) for ev in signal["evidence"]))

    def test_context_alone_does_not_refreeze_historical_signal(self):
        old = {"action": "accumulate", "data_cutoff": "2026-09-22", "model_version": "historical"}
        original = copy.deepcopy(old)
        rc, freeze = self.invoke(old=old, contexts={"1111.TW": {"trade_date": DAY, "timestamp_status": "valid"}})
        self.assertEqual(rc, 0)
        freeze.assert_not_called()
        self.assertEqual(old, original)

    def test_invalid_freeze_payloads_do_not_report_success(self):
        rc, _ = self.invoke(stored={"durable": True, "invalid": [{"index": 0, "error": "test"}]})
        self.assertEqual(rc, 1)

    def test_isolated_symbol_failure_is_partial_and_still_freezes_the_rest(self):
        rows = [result(symbol=f"{2000 + i}.TW") for i in range(10)] + [result(symbol="1476.TW", as_of="2026-10-01")]
        rc, freeze = self.invoke(rows)
        self.assertEqual(rc, rescreen.EXIT_PARTIAL)
        frozen = {signal["symbol"] for signal in freeze.call_args.args[0]}
        self.assertNotIn("1476.TW", frozen)
        self.assertEqual(len(frozen), 10)

    def test_widespread_failure_stays_fatal(self):
        rows = [result(symbol=f"{2000 + i}.TW") for i in range(8)] + [
            result(symbol=f"{3000 + i}.TW", as_of="2026-10-01") for i in range(3)]
        rc, _ = self.invoke(rows)
        self.assertEqual(rc, 1)

    def test_partial_failure_cannot_mask_a_failed_freeze(self):
        rows = [result(symbol=f"{2000 + i}.TW") for i in range(10)] + [result(symbol="1476.TW", as_of="2026-10-01")]
        rc, _ = self.invoke(rows, stored={"durable": False, "local_saved": True}, configured=True)
        self.assertEqual(rc, 1)

    def test_dry_run_never_writes(self):
        rc, freeze = self.invoke(dry_run=True)
        self.assertEqual(rc, 0)
        freeze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
