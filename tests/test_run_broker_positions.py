"""CLI tests use only fake SDK/client and fake persistence."""
import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import run_broker_positions as runner
from company.data import broker_positions as broker


class BrokerRunnerTests(unittest.TestCase):
    def run_cli(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = runner.main(args)
        return exit_code, json.loads(out.getvalue())

    def test_default_makes_no_broker_or_storage_calls(self):
        with patch.object(runner, "_fetch") as fetch, patch.object(broker, "record_broker_attempt") as record:
            code, output = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(output["broker_calls"], 0)
        fetch.assert_not_called()
        record.assert_not_called()

    def test_success_logs_counts_only(self):
        snapshot = {"positions": [{"symbol": "PRIVATE", "shares": 7, "cost": 12}]}
        with patch.object(runner, "_fetch", return_value=snapshot), \
             patch.object(broker, "record_broker_attempt", return_value={"saved": True, "durable": True, "error_code": None}):
            code, output = self.run_cli(["--fetch"])
        self.assertEqual(code, 0)
        self.assertEqual(output["position_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(output))

    def test_failure_records_code_and_has_no_raw_exception(self):
        with patch.object(runner, "_fetch", side_effect=RuntimeError("PII secret Account Not Acceptable")), \
             patch.object(broker, "record_broker_attempt", return_value={"saved": True, "durable": True, "error_code": None}) as record:
            code, output = self.run_cli(["--fetch"])
        self.assertEqual(code, 1)
        self.assertEqual(output["error_code"], "BROKER_ACCOUNT_NOT_ACCEPTABLE")
        record.assert_called_once_with(snapshot=None, failure_code="BROKER_ACCOUNT_NOT_ACCEPTABLE")
        self.assertNotIn("PII", json.dumps(output))

    def test_failed_persistence_is_failure_without_traceback(self):
        with patch.object(runner, "_fetch", side_effect=TimeoutError()), \
             patch.object(broker, "record_broker_attempt", side_effect=RuntimeError("PII")):
            code, output = self.run_cli(["--fetch"])
        self.assertEqual(code, 1)
        self.assertEqual(output["storage"]["error_code"], "STORAGE_WRITE_FAILED")

    def test_production_explicit_account_and_no_trade_subscription(self):
        fake_api = SimpleNamespace(login=Mock(), logout=Mock())
        sdk = SimpleNamespace(Shioaji=Mock(return_value=fake_api), constant=SimpleNamespace(Unit=SimpleNamespace(Share="share-enum")))
        settings = {"SHIOAJI_API_KEY": "fake-key", "SHIOAJI_SECRET_KEY": "fake-secret",
                    "SHIOAJI_BROKER_ID": "fake-branch", "SHIOAJI_ACCOUNT_ID": "fake-account"}
        with patch.dict(os.environ, settings, clear=True), \
             patch.object(broker.durable_document, "_config", return_value=("token", "private/repo", "main")), \
             patch.object(runner, "_quiet_sdk", side_effect=contextlib.nullcontext), \
             patch.object(runner.importlib, "import_module", return_value=sdk), \
             patch.object(broker, "fetch_broker_positions", return_value={"positions": []}) as fetch:
            runner._fetch()
        sdk.Shioaji.assert_called_once_with(simulation=False)
        fake_api.login.assert_called_once_with(api_key="fake-key", secret_key="fake-secret", subscribe_trade=False)
        fetch.assert_called_once_with(fake_api, broker_id="fake-branch", account_id="fake-account",
                                      share_unit="share-enum", production_confirmed=True)
        fake_api.logout.assert_called_once()

    def test_logout_on_login_error(self):
        fake_api = SimpleNamespace(login=Mock(side_effect=RuntimeError("PII")), logout=Mock())
        sdk = SimpleNamespace(Shioaji=Mock(return_value=fake_api))
        settings = {"SHIOAJI_API_KEY": "fake-key", "SHIOAJI_SECRET_KEY": "fake-secret",
                    "SHIOAJI_BROKER_ID": "fake-branch", "SHIOAJI_ACCOUNT_ID": "fake-account"}
        with patch.dict(os.environ, settings, clear=True), \
             patch.object(broker.durable_document, "_config", return_value=("token", "private/repo", "main")), \
             patch.object(runner, "_quiet_sdk", side_effect=contextlib.nullcontext), \
             patch.object(runner.importlib, "import_module", return_value=sdk):
            with self.assertRaises(RuntimeError):
                runner._fetch()
        fake_api.logout.assert_called_once()

    def test_preflight_rejects_missing_selector_and_simulation_before_import(self):
        for settings, expected in [({"SHIOAJI_BROKER_ID": "partial"}, "ACCOUNT_SELECTION_REQUIRED"), ({"SHIOAJI_SIMULATION": "1"}, "PRODUCTION_REQUIRED")]:
            with patch.dict(os.environ, settings, clear=True), \
                 patch.object(runner.importlib, "import_module") as imported:
                with self.assertRaisesRegex(broker.BrokerPositionsError, expected):
                    runner._fetch()
                imported.assert_not_called()

    def test_no_ids_and_top_level_unit_allow_adapter_auto_selection(self):
        fake_api = SimpleNamespace(login=Mock(), logout=Mock())
        sdk = SimpleNamespace(Shioaji=Mock(return_value=fake_api), Unit=SimpleNamespace(Share="top-level-share"))
        with patch.dict(os.environ, {"SHIOAJI_API_KEY": "fake-key", "SHIOAJI_SECRET_KEY": "fake-secret"}, clear=True), \
             patch.object(broker.durable_document, "_config", return_value=("token", "private/repo", "main")), \
             patch.object(runner, "_quiet_sdk", side_effect=contextlib.nullcontext), \
             patch.object(runner.importlib, "import_module", return_value=sdk), \
             patch.object(broker, "fetch_broker_positions", return_value={"positions": []}) as fetch:
            runner._fetch()
        fetch.assert_called_once_with(fake_api, broker_id="", account_id="", share_unit="top-level-share",
                                      production_confirmed=True)

    def test_sdk_stdout_stderr_suppressed_and_log_path_restored(self):
        with patch.dict(os.environ, {"SJ_LOG_PATH": "original"}), contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            with runner._quiet_sdk():
                self.assertEqual(os.environ["SJ_LOG_PATH"], os.devnull)
                print("private stdout")
                print("private stderr", file=runner.sys.stderr)
                os.write(1, b"private native stdout\n")
                os.write(2, b"private native stderr\n")
            self.assertEqual(os.environ["SJ_LOG_PATH"], "original")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
