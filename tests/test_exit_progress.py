# -*- coding: utf-8 -*-
import copy
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from company.model import exit_progress as progress


NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)


def timing():
    return {"executable": True, "urgency": "act", "last_close_date": "2026-10-27", "last_close": 90,
            "value_state_as_of": "2026-10-27", "basis_mismatch": False,
            "plan": [{"stage": 1, "trigger": "ma20", "label": "月線", "price": 100, "fraction": 0.2},
                     {"stage": 2, "trigger": "ma60", "label": "季線", "price": 95, "fraction": 0.3}]}


class ExitProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.tmp.name) / "progress.json"
        self.path_patch = patch.object(progress, "LOCAL_PATH", self.path)
        self.config_patch = patch.object(progress.durable_document, "_config", return_value=None)
        self.path_patch.start()
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def start(self):
        return progress.start_plan("2356.TW", timing(), 1000, 0, now=NOW)[0]

    def test_frozen_original_shares_not_recalculated_against_remaining(self):
        doc = self.start()
        plan = doc["plans"]["2356.TW"]
        self.assertEqual(plan["stages"][0]["target_shares"], 200)
        doc, _ = progress.complete_stage("2356.TW", plan["plan_id"], 1, 1, now=NOW)
        changed = timing()
        changed["last_close_date"] = "2026-10-28"
        changed["plan"][1].update(price=80, fraction=0.5)
        view = progress.apply_progress(changed, "2356.TW", doc, shares=800, now=NOW + timedelta(days=1))
        self.assertEqual(view["plan"][0]["price"], 95)
        self.assertEqual(view["plan"][0]["target_shares"], 300)
        self.assertEqual(view["plan"][0]["fraction"], 0.3)
        self.assertEqual(view["exit_progress"]["remaining_fraction"], 0.3)
        self.assertTrue(view["plan"][0]["confirmed_break"])

    def test_duplicate_complete_idempotent_even_with_old_version(self):
        doc = self.start()
        pid = doc["plans"]["2356.TW"]["plan_id"]
        first, _ = progress.complete_stage("2356.TW", pid, 1, 1, now=NOW)
        repeated, storage = progress.complete_stage("2356.TW", pid, 1, 1, now=NOW)
        self.assertEqual(first, repeated)
        self.assertEqual(repeated["version"], 2)
        self.assertTrue(storage["unchanged"])

    def test_post_execution_quantity_enables_reporting_but_no_new_trade_plan(self):
        doc = self.start()
        view = progress.apply_progress(timing(), "2356.TW", doc, shares=800, now=NOW)
        self.assertEqual(view["exit_progress"]["status"], "completion_pending")
        self.assertEqual(view["completion_report"]["stage"], 1)
        self.assertTrue(view["completion_report"]["requires_server_verification"])
        self.assertFalse(view["executable"])
        self.assertEqual(view["plan"], [])
        wrong = progress.apply_progress(timing(), "2356.TW", doc, shares=777, now=NOW)
        self.assertNotIn("completion_report", wrong)

    def test_taiwan_odd_share_targets_use_cumulative_floor_and_frozen_quantities(self):
        candidate = timing()
        for row in candidate["plan"]:
            row["fraction"] = .25
        doc, _ = progress.start_plan("2356.TW", candidate, 999, 0, now=NOW)
        plan = doc["plans"]["2356.TW"]
        self.assertEqual(plan["share_rounding"], "whole_share_cumulative_floor_v1")
        self.assertEqual([row["target_shares"] for row in plan["stages"]], [249, 250])
        self.assertEqual([row["fraction"] for row in plan["stages"]], [.25, .25])
        report = progress.apply_progress(candidate, "2356.TW", doc, shares=750, now=NOW)
        self.assertEqual(report["completion_report"]["stage"], 1)
        self.assertFalse(report["executable"])
        doc, _ = progress.complete_stage("2356.TW", plan["plan_id"], 1, 1, now=NOW)
        view = progress.apply_progress(candidate, "2356.TW", doc, shares=750, now=NOW)
        self.assertEqual(view["plan"][0]["target_shares"], 250)
        self.assertEqual(view["exit_progress"]["completed_shares"], 249)
        self.assertEqual(view["exit_progress"]["remaining_shares"], 250)
        self.assertAlmostEqual(view["exit_progress"]["completed_fraction"], 249 / 999, places=7)
        self.assertAlmostEqual(view["exit_progress"]["remaining_fraction"], 250 / 999, places=7)
        self.assertEqual(progress.completion_reports(doc, [{"symbol": "2356.TW", "shares": 500}], now=NOW)["2356.TW"]["stage"], 2)

    def test_whole_share_plan_rejects_fractional_original_or_zero_size_stage(self):
        with self.assertRaisesRegex(ValueError, "整數股"):
            progress.start_plan("2356.TW", timing(), 999.5, 0, now=NOW)
        with self.assertRaisesRegex(ValueError, "不足一股"):
            progress.start_plan("2356.TW", timing(), 1, 0, now=NOW)

    def test_cumulative_targets_never_exceed_original_and_marker_is_validated(self):
        candidate = timing()
        candidate["plan"] = [{"stage": i + 1, "trigger": f"level{i}", "label": "保護",
                               "price": 100 - i * 5, "fraction": fraction}
                              for i, fraction in enumerate((.33333333, .33333333, .33333334))]
        doc, _ = progress.start_plan("2356.TWO", candidate, 999, 0, now=NOW)
        self.assertEqual(sum(row["target_shares"] for row in doc["plans"]["2356.TWO"]["stages"]), 999)
        self.assertTrue(all(type(row["target_shares"]) is int for row in doc["plans"]["2356.TWO"]["stages"]))
        corrupted = copy.deepcopy(doc)
        corrupted["plans"]["2356.TWO"]["stages"][0]["target_shares"] = 332.5
        with self.assertRaises(ValueError):
            progress.apply_progress(candidate, "2356.TWO", corrupted, now=NOW)

    def test_legacy_fractional_targets_remain_readable_with_original_method(self):
        candidate = timing()
        for row in candidate["plan"]:
            row["fraction"] = .25
        doc, _ = progress.start_plan("2356.TW", candidate, 999, 0, now=NOW)
        legacy = copy.deepcopy(doc)
        plan = legacy["plans"]["2356.TW"]
        del plan["share_rounding"]
        for row in plan["stages"]:
            row["target_shares"] = 249.75
        report = progress.apply_progress(candidate, "2356.TW", legacy, shares=749.25, now=NOW)
        self.assertEqual(report["completion_report"]["stage"], 1)
        self.assertFalse(report["executable"])

    def test_absent_holding_can_report_only_the_final_full_exit(self):
        full = timing()
        full["plan"] = [{"stage": 1, "trigger": "ma20", "label": "退出", "price": 100, "fraction": 1.0}]
        doc, _ = progress.start_plan("2356.TW", full, 1000, 0, now=NOW)
        report = progress.completion_reports(doc, [], now=NOW)
        self.assertEqual(report["2356.TW"]["stage"], 1)
        self.assertEqual(progress.completion_reports(doc, [], now=NOW + timedelta(days=8)), {})
        partial = copy.deepcopy(doc)
        partial["plans"]["2356.TW"]["stages"][0].update(fraction=.5, target_shares=500)
        self.assertEqual(progress.completion_reports(partial, [], now=NOW), {})

    def test_completed_old_plan_does_not_hide_a_new_thesis_failure(self):
        doc = self.start()
        plan = doc["plans"]["2356.TW"]
        doc, _ = progress.complete_stage("2356.TW", plan["plan_id"], 1, 1, now=NOW)
        doc, _ = progress.complete_stage("2356.TW", plan["plan_id"], 2, 2, now=NOW)
        fresh = timing()
        fresh.update(urgency="immediate", headline="投資論點失效")
        view = progress.apply_progress(fresh, "2356.TW", doc, shares=500, now=NOW)
        self.assertEqual(view["urgency"], "immediate")
        self.assertIn("投資論點失效", view["headline"])
        self.assertEqual(view["plan"], [])

    def test_new_thesis_failure_cannot_be_hidden_by_old_protection_plan(self):
        doc = self.start()
        fresh = timing()
        fresh.update(urgency="immediate", headline="投資論點失效")
        view = progress.apply_progress(fresh, "2356.TW", doc, shares=1000, now=NOW)
        self.assertEqual(view["exit_progress"]["status"], "needs_review")
        self.assertFalse(view["executable"])
        self.assertEqual(view["plan"], [])
        self.assertIn("論點失效", view["headline"])

    def test_conflicting_write_and_out_of_order_completion(self):
        doc = self.start()
        pid = doc["plans"]["2356.TW"]["plan_id"]
        with self.assertRaises(ValueError):
            progress.complete_stage("2356.TW", pid, 2, 1, now=NOW)
        with self.assertRaises(progress.ProgressConflict):
            progress.complete_stage("2356.TW", pid, 1, 0, now=NOW)
        new = timing()
        new["plan"][0]["price"] = 110
        with self.assertRaises(progress.ProgressConflict):
            progress.start_plan("2356.TW", new, 1000, 1, now=NOW)
        repeated, storage = progress.start_plan("2356.TW", timing(), 1000, 0, now=NOW)
        self.assertEqual(repeated, doc)
        self.assertTrue(storage["unchanged"])

    def test_finished_plan_does_not_regenerate_daily_sell_batches(self):
        doc = self.start()
        pid = doc["plans"]["2356.TW"]["plan_id"]
        doc, _ = progress.complete_stage("2356.TW", pid, 1, 1, now=NOW)
        doc, _ = progress.complete_stage("2356.TW", pid, 2, 2, now=NOW)
        view = progress.apply_progress(timing(), "2356.TW", doc, shares=500, now=NOW)
        self.assertEqual(view["plan"], [])
        self.assertFalse(view["executable"])
        self.assertEqual(view["exit_progress"]["status"], "completed")
        self.assertEqual(view["exit_progress"]["remaining_fraction"], 0)

    def test_expired_or_stale_timing_is_not_actionable(self):
        doc = self.start()
        view = progress.apply_progress(timing(), "2356.TW", doc, now=NOW + timedelta(days=7))
        self.assertEqual(view["exit_progress"]["status"], "expired")
        self.assertFalse(view["executable"])
        self.assertEqual(view["plan"], [])
        stale = timing()
        stale.update(executable=False, urgency="stale_data")
        view = progress.apply_progress(stale, "2356.TW", doc, now=NOW)
        self.assertEqual(view["exit_progress"]["status"], "suspended")
        self.assertEqual(view["plan"], [])
        with self.assertRaises(ValueError):
            progress.start_plan("1111.TW", stale, 1000, 1, now=NOW)

    def test_position_change_needs_review_and_cancel_is_idempotent(self):
        doc = self.start()
        view = progress.apply_progress(timing(), "2356.TW", doc, shares=1200, now=NOW)
        self.assertEqual(view["exit_progress"]["status"], "position_changed")
        self.assertEqual(view["plan"], [])
        pid = doc["plans"]["2356.TW"]["plan_id"]
        doc, _ = progress.cancel_plan("2356.TW", pid, 1, now=NOW)
        again, storage = progress.cancel_plan("2356.TW", pid, 1, now=NOW)
        self.assertEqual(again, doc)
        self.assertTrue(storage["unchanged"])

    def test_rejects_nonfinite_fraction_bad_schema_and_unsafe_symbols(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(shares=value), self.assertRaises(ValueError):
                progress.start_plan("2356.TW", timing(), value, 0, now=NOW)
        for stages in ([{"stage": 1, "price": 10, "fraction": 1.1}],
                       [{"stage": 1, "price": float("nan"), "fraction": 0.2}],
                       [{"stage": True, "price": 10, "fraction": 0.2}]):
            candidate = timing()
            candidate["plan"] = stages
            with self.assertRaises(ValueError):
                progress.start_plan("2356.TW", candidate, 1000, 0, now=NOW)
        with self.assertRaises(ValueError):
            progress.start_plan("<script>", timing(), 1000, 0, now=NOW)
        self.path.write_text('{"schema_version":99}', encoding="utf-8")
        _, storage = progress.load_progress()
        self.assertFalse(storage["available"])

    def test_remote_read_failure_does_not_fall_back_to_local_cache(self):
        self.start()
        with patch.object(progress.durable_document, "_config", return_value=("test", "owner/private", "main")), \
             patch.object(progress.durable_document, "_remote_get", return_value=(None, None, "remote_read_http_503")):
            doc, storage = progress.load_progress()
            self.assertFalse(storage["available"])
            self.assertEqual(doc["plans"], {})
            with self.assertRaises(progress.ProgressUnavailable):
                progress.start_plan("1111.TW", timing(), 1000, 0, now=NOW)

    def test_remote_cas_failure_never_overwrites_local_cache(self):
        original = self.start()
        before = self.path.read_bytes()
        error = urllib.error.HTTPError("https://example.invalid", 409, "conflict", {}, io.BytesIO())
        with patch.object(progress.durable_document, "_config", return_value=("test", "owner/private", "main")), \
             patch.object(progress.durable_document, "_remote_get", return_value=(original, "sha-v1", None)), \
             patch.object(progress.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(progress.ProgressConflict):
                progress.complete_stage("2356.TW", original["plans"]["2356.TW"]["plan_id"], 1, 1, now=NOW)
        self.assertEqual(before, self.path.read_bytes())

    def test_remote_save_uses_observed_sha_and_caches_only_after_success(self):
        original = self.start()
        before = self.path.read_bytes()
        seen = []

        def remote(request, timeout):
            self.assertEqual(before, self.path.read_bytes())
            payload = json.loads(request.data)
            seen.append(payload)
            response = MagicMock()
            response.__enter__.return_value.read.return_value = b"{}"
            return response

        with patch.object(progress.durable_document, "_config", return_value=("test", "owner/private", "main")), \
             patch.object(progress.durable_document, "_remote_get", return_value=(original, "sha-v1", None)), \
             patch.object(progress.urllib.request, "urlopen", side_effect=remote):
            doc, storage = progress.complete_stage("2356.TW", original["plans"]["2356.TW"]["plan_id"], 1, 1, now=NOW)
        self.assertEqual(seen[0]["sha"], "sha-v1")
        self.assertTrue(storage["durable"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), doc)


if __name__ == "__main__":
    unittest.main()
