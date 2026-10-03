import copy
import json
import unittest
import urllib.error
from datetime import datetime, timedelta
from unittest.mock import patch

from company.model import daily_history as history


NOW = datetime.fromisoformat("2026-10-02T17:00:00+08:00")


def state(day="2026-10-02", analysis_day="2026-10-02"):
    item = {"symbol": "1111.TW", "name": "Test market", "as_of": day, "price": 100.0,
            "action": "accumulate", "decision": "research", "quality_pass": True,
            "data_provenance": {"price_date": day, "price_source": "test"}}
    return {"schema_version": 1, "as_of": day, "analysis_date_taipei": analysis_day,
            "generated_at": analysis_day + "T08:30:00Z", "market_expected_as_of": day,
            "market_data_complete": True, "shadow": True,
            "coverage": {"mother_pool": 1, "price_current": 1, "price_total": 1,
                         "price_stale": 0, "quality_covered": 1},
            "evaluations": [item], "top_picks": [item], "waiting_list": [], "etf_candidates": []}


class DailyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.docs = {}
        self.writes = []
        self.calendar = patch.object(history, "is_trading_day", side_effect=lambda now: now.weekday() < 5)
        self.config = patch.object(history.store, "_config", return_value=("test-only", "private/data", "main"))
        self.reader = patch.object(history, "_read", side_effect=self.read)
        self.writer = patch.object(history, "_write", side_effect=self.write)
        for p in (self.calendar, self.config, self.reader, self.writer):
            p.start()
            self.addCleanup(p.stop)

    def read(self, local, remote, config):
        return copy.deepcopy(self.docs.get(remote)), "sha" if remote in self.docs else None

    def write(self, doc, local, remote, config, sha):
        self.writes.append(remote)
        self.docs[remote] = copy.deepcopy(doc)
        return True

    def record(self, document=None, now=NOW):
        return history.record_daily_history(document or state(), ["1111.TW"], now=now)

    def test_stores_daily_decisions_separately_and_index_is_metadata_only(self):
        self.assertTrue(self.record()["ok"])
        day = self.docs["value/daily/2026-10-02.json"]
        self.assertEqual(day["evaluations"][0]["decision"], "research")
        self.assertEqual(day["mode"], "daily-analysis-history")
        index = self.docs[history.INDEX_REMOTE]
        self.assertEqual(index["latest"], "2026-10-02")
        self.assertEqual(set(index["dates"][0]), {"date", "as_of", "coverage", "picks"})
        self.assertEqual(index["dates"][0]["picks"], ["1111.TW"])
        self.assertNotIn("outcomes", day)
        self.assertNotIn("signal_id", day)

    def test_rerun_preserves_first_daily_decision_and_does_not_rewrite_index(self):
        self.record()
        original = copy.deepcopy(self.docs)
        changed = state()
        changed["evaluations"][0]["decision"] = "wait"
        changed["generated_at"] = "2026-10-02T08:45:00Z"
        self.assertEqual(self.record(changed)["status"], "already_exists")
        self.assertEqual(self.docs, original)
        self.assertEqual(len(self.writes), 2)

    def test_next_day_appends_daily_status_even_if_action_did_not_change(self):
        self.assertTrue(self.record()["ok"])
        next_day = datetime.fromisoformat("2026-10-05T17:00:00+08:00")
        self.assertTrue(self.record(state("2026-10-05", "2026-10-05"), next_day)["ok"])
        self.assertEqual(self.docs[history.INDEX_REMOTE]["latest"], "2026-10-05")
        self.assertEqual(len(self.docs[history.INDEX_REMOTE]["dates"]), 2)
        self.assertEqual(self.docs["value/daily/2026-10-02.json"]["evaluations"][0]["action"],
                         self.docs["value/daily/2026-10-05.json"]["evaluations"][0]["action"])

    def test_holiday_and_weekend_use_completed_market_date_not_capture_date(self):
        with patch.object(history, "is_trading_day", side_effect=lambda now:
                          now.weekday() < 5 and now.date().isoformat() != "2026-09-28"):
            holiday = datetime.fromisoformat("2026-09-28T17:00:00+08:00")
            self.assertEqual(history.latest_completed_market_date(holiday), "2026-09-25")
        saturday = NOW + timedelta(days=1)
        self.assertTrue(self.record(state(analysis_day="2026-10-03"), now=saturday)["ok"])
        self.assertIn("value/daily/2026-10-02.json", self.docs)
        self.assertNotIn("value/daily/2026-10-03.json", self.docs)

    def test_explicit_bootstrap_keeps_friday_analysis_and_truthful_saturday_capture(self):
        saturday = datetime.fromisoformat("2026-10-03T17:00:00+08:00")
        self.assertFalse(self.record(state(), saturday)["ok"])
        result = history.record_daily_history(state(), ["1111.TW"], now=saturday,
                                               allow_existing_analysis=True)
        self.assertTrue(result["ok"])
        saved = self.docs["value/daily/2026-10-02.json"]
        self.assertEqual(saved["analysis_date_taipei"], "2026-10-02")
        self.assertEqual(saved["generated_at"], "2026-10-02T08:30:00Z")
        self.assertEqual(saved["captured_at"], saturday.isoformat())
        self.assertEqual(saved["date"], "2026-10-02")

    def test_bootstrap_is_not_arbitrary_backfill_or_future_analysis(self):
        saturday = datetime.fromisoformat("2026-10-03T17:00:00+08:00")
        documents = [state("2026-10-01", "2026-10-01"), state("2026-10-05", "2026-10-05"),
                     state(analysis_day="2026-10-05")]
        for timestamp in ("2026-10-02T13:59:59+08:00", "2026-10-03T18:00:00+08:00",
                          "2026-10-01T17:00:00+08:00", "2026-10-03T16:00:00+08:00"):
            document = state()
            document["generated_at"] = timestamp
            documents.append(document)
        for document in documents:
            with self.subTest(day=document["as_of"], generated=document["generated_at"]):
                result = history.record_daily_history(document, ["1111.TW"], now=saturday,
                                                       allow_existing_analysis=True)
                self.assertFalse(result["ok"])
        self.assertEqual(self.writes, [])

    def test_bootstrap_validation_helper_requires_opt_in(self):
        saturday = NOW + timedelta(days=1)
        with self.assertRaises(history.HistoryValidationError):
            history._complete_document(state(), saturday, ["1111.TW"])
        saved = history._complete_document(state(), saturday, ["1111.TW"], allow_existing_analysis=True)
        self.assertEqual(saved["captured_at"], saturday.isoformat())

    def test_future_stale_and_uncompleted_market_dates_never_write(self):
        for document, now in [(state("2026-10-05"), NOW), (state("2026-10-01"), NOW),
                              (state(), NOW.replace(hour=13))]:
            with self.subTest(day=document["as_of"], hour=now.hour):
                self.assertFalse(self.record(document, now)["ok"])
        self.assertEqual(self.writes, [])

    def test_partial_or_future_symbol_cannot_hide_behind_consensus(self):
        for change in ({"as_of": "2026-10-01"}, {"as_of": "2026-10-05"},
                       {"price": None}, {"price": float("nan")}, {"price": float("inf")},
                       {"price": 0}, {"data_provenance": {"price_date": "2026-10-01"}}):
            document = state()
            document["evaluations"][0].update(change)
            self.assertFalse(self.record(document)["ok"])
        self.assertEqual(self.writes, [])

    def test_no_symbols_missing_symbols_and_duplicates_fail(self):
        for items in ([], state()["evaluations"] * 2, [{**state()["evaluations"][0], "symbol": "2222.TW"}]):
            document = state()
            document["evaluations"] = items
            self.assertFalse(self.record(document)["ok"])
        self.assertEqual(self.writes, [])

    def test_freshness_and_coverage_must_be_explicit_and_consistent(self):
        for field, value in (("market_data_complete", False), ("market_expected_as_of", None),
                             ("analysis_date_taipei", "2026-10-01"),
                             ("generated_at", "2026-10-02T18:00:00+08:00"),
                             ("generated_at", "2026-10-02T16:00:00"),
                             ("generated_at", "2026-10-02T13:00:00+08:00"),
                             ("coverage", {"price_current": 1, "price_total": 2, "price_stale": 0})):
            document = state()
            document[field] = value
            self.assertFalse(self.record(document)["ok"])
        self.assertEqual(self.writes, [])

    def test_private_unknown_fields_do_not_reach_day_or_index(self):
        document = state()
        document.update(positions=[{"symbol": "PRIVATE", "cost": 3}], token="test-secret")
        document["evaluations"][0].update(shares=55, cost=77, account="private-account")
        document["evaluations"][0]["data_provenance"]["token"] = "private-provenance"
        document["coverage"]["positions"] = "private-coverage"
        self.record(document)
        encoded = json.dumps(self.docs)
        for value in ("PRIVATE", "test-secret", "private-account", "private-provenance", "private-coverage"):
            self.assertNotIn(value, encoded)

    def test_index_failure_is_error_and_rerun_repairs_without_rewriting_day(self):
        def fail_index(doc, local, remote, config, sha):
            if remote == history.INDEX_REMOTE:
                raise RuntimeError("history_remote_write_http_503")
            return self.write(doc, local, remote, config, sha)
        with patch.object(history, "_write", side_effect=fail_index):
            self.assertFalse(self.record()["ok"])
        self.assertIn("value/daily/2026-10-02.json", self.docs)
        self.assertTrue(self.record()["ok"])
        self.assertEqual(self.writes.count("value/daily/2026-10-02.json"), 1)

    def test_concurrent_day_create_keeps_winner(self):
        winner = history._complete_document(state(), NOW, ["1111.TW"])
        winner["evaluations"][0]["decision"] = "winner"
        def conflict(doc, local, remote, config, sha):
            if remote.endswith("2026-10-02.json"):
                self.docs[remote] = winner
                return False
            return self.write(doc, local, remote, config, sha)
        with patch.object(history, "_write", side_effect=conflict):
            self.assertEqual(self.record()["status"], "already_exists")
        self.assertEqual(self.docs["value/daily/2026-10-02.json"]["evaluations"][0]["decision"], "winner")

    def test_index_cas_conflict_merges_other_day(self):
        calls = []
        def conflict(doc, local, remote, config, sha):
            if remote == history.INDEX_REMOTE and not calls:
                calls.append(1)
                self.docs[remote] = {"dates": [{"date": "2026-10-01", "as_of": "2026-10-01",
                                               "coverage": {}, "picks": []}]}
                return False
            return self.write(doc, local, remote, config, sha)
        with patch.object(history, "_write", side_effect=conflict):
            self.assertTrue(self.record()["ok"])
        self.assertEqual([entry["date"] for entry in self.docs[history.INDEX_REMOTE]["dates"]],
                         ["2026-10-02", "2026-10-01"])

    def test_index_is_capped_sorted_deduplicated_and_latest_never_regresses(self):
        entries = []
        for offset in range(400):
            day = (NOW.date() - timedelta(days=offset)).isoformat()
            entries.append({"date": day, "as_of": day, "coverage": {}, "picks": []})
        index = history._public_index({"dates": entries + entries[:1]})
        self.assertEqual(len(index["dates"]), history.INDEX_LIMIT)
        self.assertEqual(index["latest"], "2026-10-02")

    def test_remote_read_failure_never_falls_back_to_overwriting(self):
        with patch.object(history, "_read", side_effect=RuntimeError("history_remote_read_failed")):
            self.assertFalse(self.record()["ok"])
        self.assertEqual(self.writes, [])

    def test_durable_required_without_config_fails(self):
        with patch.object(history.store, "_config", return_value=None):
            result = history.record_daily_history(state(), ["1111.TW"], now=NOW, require_durable=True)
        self.assertFalse(result["ok"])
        self.assertEqual(self.writes, [])

    def test_loader_contract_sanitizes_and_does_not_write(self):
        document = history._complete_document(state(), NOW, ["1111.TW"])
        document["positions"] = [{"cost": 123}]
        with patch.object(history.store, "load_document", return_value=(document, {"durable": True})):
            loaded, storage = history.load_daily_history("2026-10-02")
            self.assertNotIn("positions", loaded)
            self.assertTrue(storage["durable"])
        with patch.object(history.store, "load_document", return_value=(None, {"source": "none"})):
            self.assertIsNone(history.load_daily_history()[0])
        self.assertEqual(self.writes, [])

    def test_loader_rejects_noncanonical_dates_and_path_traversal(self):
        for day in ("../current_state", "2026-10-2", "20261002", "2026-02-30", ""):
            with self.subTest(day=day), self.assertRaises(ValueError):
                history.load_history_day(day)

    def test_loader_and_rerun_reject_a_partial_existing_document(self):
        document = history._complete_document(state(), NOW, ["1111.TW"])
        document["evaluations"][0]["as_of"] = "2026-10-01"
        self.docs["value/daily/2026-10-02.json"] = document
        self.assertFalse(self.record()["ok"])
        with patch.object(history.store, "load_document", return_value=(document, {})):
            with self.assertRaises(ValueError):
                history.load_history_day("2026-10-02")
        self.assertEqual(self.writes, [])


class DailyHistoryStorageTests(unittest.TestCase):
    def test_conditional_create_omits_sha_and_update_preserves_read_sha(self):
        for sha in (None, "read-version"):
            with patch.object(history.urllib.request, "urlopen") as opened:
                self.assertTrue(history._write({"dates": []}, history.INDEX_LOCAL, history.INDEX_REMOTE,
                                               ("test-only", "private/data", "main"), sha))
                request = opened.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(payload.get("sha"), sha)
                self.assertEqual("sha" in payload, sha is not None)
                self.assertEqual(request.method, "PUT")

    def test_conflicts_retry_but_other_errors_are_sanitized(self):
        for code in (409, 422, 401, 503):
            error = urllib.error.HTTPError("https://example.invalid", code, "private-body", {}, None)
            with patch.object(history.urllib.request, "urlopen", side_effect=error):
                args = ({}, history.INDEX_LOCAL, history.INDEX_REMOTE, ("test-only", "private/data", "main"), None)
                if code in (409, 422):
                    self.assertFalse(history._write(*args))
                else:
                    with self.assertRaisesRegex(RuntimeError, f"history_remote_write_http_{code}"):
                        history._write(*args)

    def test_authoritative_read_failure_does_not_touch_local_fallback(self):
        with patch.object(history.store, "_remote_get", return_value=(None, None, "remote_read_http_401")), \
             patch.object(history.store, "load_document") as local:
            with self.assertRaisesRegex(RuntimeError, "history_remote_read_failed"):
                history._read(history.INDEX_LOCAL, history.INDEX_REMOTE, ("test-only", "private/data", "main"))
            local.assert_not_called()


class DailyHistoryPipelineTests(unittest.TestCase):
    def test_postclose_dispatch_uses_only_the_ordered_daily_pipeline(self):
        from company.model.daily_jobs import POSTCLOSE_WORKFLOWS
        self.assertEqual(POSTCLOSE_WORKFLOWS, ("email-daily.yml",))

    def test_history_error_is_nonzero_and_storage_failure_skips_history(self):
        import run_daily_value_state as runner
        for current_saved, expected_calls in (({"local_saved": True, "durable": True}, 1),
                                               ({"local_saved": True, "remote_error": "offline"}, 0)):
            with patch.object(runner.Path, "read_text", side_effect=["{}", json.dumps({"stocks": [{"symbol": "1111.TW"}], "n": 1})]), \
                 patch.object(runner, "load_fundamentals", return_value=({}, {})), \
                 patch.object(runner, "refresh_pool", return_value=({}, {})), \
                 patch.object(runner, "save_fundamentals", return_value={"local_saved": True, "durable": True}), \
                 patch.object(runner, "rescreen_all", return_value=[]), \
                 patch.object(runner, "build_daily_state", return_value=state()), \
                 patch.object(runner, "latest_official_close_date", return_value="2026-10-02"), \
                 patch.object(runner, "load_current_state", return_value=(None, {})), \
                 patch.object(runner, "save_current_state", return_value=current_saved), \
                 patch.object(runner, "record_daily_history", return_value={"ok": False, "error": "test"}) as record, \
                 patch("builtins.print"):
                self.assertEqual(runner.main(), 1)
                self.assertEqual(record.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main()
