"""Synthetic, offline tests: no broker login or private data is accessed."""
import base64
import copy
import json
import os
import tempfile
import unittest
import urllib.error
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from company.data import broker_positions as broker


class Unit(Enum):
    Share = "Share"
    Common = "Common"


def account(**changes):
    fields = dict(account_type="S", broker_id="test-branch", account_id="test-account", signed=True)
    fields.update(changes)
    return SimpleNamespace(**fields)


def row(**changes):
    fields = dict(code="1234", cond="Cash", direction="Buy", quantity=3, price=10)
    fields.update(changes)
    return SimpleNamespace(**fields)


def api(rows=None, accounts=None):
    contracts = {"1234": SimpleNamespace(code="1234", exchange="TSE"),
                 "5678": SimpleNamespace(code="5678", exchange="OTC")}
    return SimpleNamespace(simulation=False, list_accounts=Mock(return_value=accounts if accounts is not None else [account()]),
        list_positions=Mock(return_value=rows if rows is not None else [row()]),
        contracts=SimpleNamespace(Stocks=contracts))


def fetch(client, **changes):
    args = dict(broker_id="test-branch", account_id="test-account", share_unit=Unit.Share)
    args.update(changes)
    return broker.fetch_broker_positions(client, **args)


def document(snapshot=None, **extra):
    doc = {"schema_version": 1, "last_attempt": {"at": "2026-10-03T00:00:00+00:00", "ok": True, "error_code": None},
           "last_successful_snapshot": snapshot if snapshot is not None else fetch(api())}
    doc.update(extra)
    return doc


class BrokerAdapterTests(unittest.TestCase):
    def test_explicit_account_and_share_unit_no_lot_conversion(self):
        selected = account()
        client = api(accounts=[account(account_type="F"), account(account_id="other"), selected])
        result = fetch(client)
        client.list_positions.assert_called_once_with(account=selected, unit=Unit.Share, timeout=5000)
        self.assertEqual(result["positions"], [{"symbol": "1234.TW", "shares": 3, "cost": 10.0}])
        self.assertNotIn("test-account", json.dumps(result))

    def test_duplicate_cash_rows_aggregate_weighted_cost_and_exchange(self):
        result = fetch(api([row(quantity=3, price=10), row(quantity=2, price=20), row(code="5678", quantity=1, price=7)]))
        self.assertEqual(result["positions"], [{"symbol": "1234.TW", "shares": 5, "cost": 14.0},
                                                {"symbol": "5678.TWO", "shares": 1, "cost": 7.0}])

    def test_empty_inventory_is_explicit_success(self):
        self.assertEqual(fetch(api([]))["positions"], [])

    def test_auto_selection_requires_exactly_one_signed_stock_account(self):
        selected = account()
        client = api(accounts=[account(account_type="F"), account(account_id="unsigned", signed=False), selected])
        result = fetch(client, broker_id="", account_id="")
        self.assertEqual(result["positions"][0]["shares"], 3)
        client.list_positions.assert_called_once_with(account=selected, unit=Unit.Share, timeout=5000)
        self.assertEqual(result["account_key"], broker._account_key("test-branch", "test-account"))

    def test_auto_selection_rejects_ambiguity_or_unsigned_accounts(self):
        for accounts, code in [([account(), account(account_id="other")], "ACCOUNT_AMBIGUOUS"),
                               ([account(signed=False)], "ACCOUNT_NOT_SIGNED"),
                               ([account(account_type="F")], "ACCOUNT_NOT_FOUND")]:
            client = api(accounts=accounts)
            with self.subTest(code=code), self.assertRaisesRegex(broker.BrokerPositionsError, code):
                fetch(client, broker_id="", account_id="")
            client.list_positions.assert_not_called()

    def test_no_default_account_fallback(self):
        for accounts, code in [([], "ACCOUNT_NOT_FOUND"), ([account(), account()], "ACCOUNT_AMBIGUOUS"),
                               ([account(signed=False)], "ACCOUNT_NOT_SIGNED"), ([account(account_type="F")], "ACCOUNT_NOT_FOUND")]:
            with self.subTest(code=code):
                client = api(accounts=accounts)
                with self.assertRaisesRegex(broker.BrokerPositionsError, code):
                    fetch(client)
                client.list_positions.assert_not_called()

    def test_selector_production_and_unit_gates_run_before_accounts(self):
        for changes, mode, code in [({"account_id": ""}, False, "ACCOUNT_SELECTION_REQUIRED"),
                                   ({}, True, "PRODUCTION_REQUIRED"), ({}, None, "PRODUCTION_REQUIRED"),
                                   ({"share_unit": Unit.Common}, False, "SHARE_UNIT_REQUIRED")]:
            with self.subTest(code=code):
                client = api()
                client.simulation = mode
                with self.assertRaisesRegex(broker.BrokerPositionsError, code):
                    fetch(client, **changes)
                client.list_accounts.assert_not_called()

    def test_rejects_any_unsupported_row_instead_of_partial_inventory(self):
        for changes in ({"cond": "MarginTrading"}, {"cond": "ShortSelling"}, {"cond": "Netting"},
                        {"cond": "Emerging"}, {"direction": "Sell"}, {"cond": None}, {"direction": None}):
            with self.subTest(changes=changes), self.assertRaisesRegex(broker.BrokerPositionsError, "UNSUPPORTED_POSITION"):
                fetch(api([row(), row(**changes)]))

    def test_invalid_numeric_values(self):
        for field, bad in [(field, value) for field in ("quantity", "price") for value in (None, True, 0, -1, "NaN", "Infinity", "-Infinity", "bad", "1e9999")]:
            with self.subTest(field=field, value=bad), self.assertRaisesRegex(broker.BrokerPositionsError, "INVALID_POSITION"):
                fetch(api([row(**{field: bad})]))
        with self.assertRaisesRegex(broker.BrokerPositionsError, "INVALID_POSITION"):
            fetch(api([row(quantity=1.5)]))

    def test_unknown_exchange_contract_and_bad_code(self):
        for code, expected in [("9999", "UNKNOWN_CONTRACT"), ("<bad>", "INVALID_POSITION")]:
            with self.assertRaisesRegex(broker.BrokerPositionsError, expected):
                fetch(api([row(code=code)]))
        client = api()
        client.contracts.Stocks["1234"].exchange = "OES"
        with self.assertRaisesRegex(broker.BrokerPositionsError, "UNSUPPORTED_EXCHANGE"):
            fetch(client)

    def test_no_response_or_oversized_response_rejected(self):
        for value in (None, {}, "error", [row()] * (broker.MAX_ROWS + 1)):
            client = api()
            client.list_positions.return_value = value
            with self.assertRaisesRegex(broker.BrokerPositionsError, "INVALID_RESPONSE"):
                fetch(client)

    def test_error_codes_never_contain_raw_exception(self):
        for exc, code in [(RuntimeError("secret-account production permission"), "BROKER_PRODUCTION_PERMISSION"),
                          (RuntimeError("private Account Not Acceptable"), "BROKER_ACCOUNT_NOT_ACCEPTABLE"),
                          (RuntimeError("private Token doesn't have permission"), "BROKER_UNAUTHORIZED"),
                          (RuntimeError("secret private raw position"), "BROKER_QUERY_FAILED"),
                          (TimeoutError("private"), "BROKER_TIMEOUT")]:
            self.assertEqual(broker.error_code(exc), code)
        for status, expected in [(401, "BROKER_UNAUTHORIZED"), (403, "BROKER_FORBIDDEN"), (406, "BROKER_ACCOUNT_NOT_ACCEPTABLE")]:
            self.assertEqual(broker.error_code(urllib.error.HTTPError("private", status, "private", {}, None)), expected)


class BrokerStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cache.json"
        self.paths = patch.object(broker, "LOCAL_PATH", self.path)
        self.paths.start()
        self.config = patch.object(broker.durable_document, "_config", return_value=None)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.paths.stop()
        self.tmp.cleanup()

    def test_failure_preserves_success_and_loader_status(self):
        original = fetch(api())
        self.assertTrue(broker.record_broker_attempt(snapshot=original)["saved"])
        self.assertTrue(broker.record_broker_attempt(failure_code="BROKER_ACCOUNT_NOT_ACCEPTABLE")["saved"])
        actual, storage = broker.load_broker_positions(prefer_remote=False)
        status = actual["public_status"]
        self.assertEqual(actual["positions"], original["positions"])
        self.assertEqual(status["last_success_at"], original["as_of"])
        self.assertFalse(status["last_attempt_ok"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["error_code"], "BROKER_ACCOUNT_NOT_ACCEPTABLE")
        self.assertEqual(actual["status"], "error")
        self.assertFalse(storage["durable"])
        self.assertEqual(actual["last_successful_status"]["status"], "ok")
        self.assertEqual(actual["fetched_at"], original["as_of"])
        self.assertEqual(actual["last_successful_snapshot"]["positions"], original["positions"])
        self.assertEqual(broker.broker_positions_status(prefer_remote=False), status)
        self.assertNotIn("account_key", actual)

    def test_first_failure_has_no_snapshot(self):
        broker.record_broker_attempt(failure_code="BROKER_UNAUTHORIZED")
        doc, storage = broker.load_broker_positions(prefer_remote=False)
        status = doc["public_status"]
        self.assertIsNone(doc["last_successful_snapshot"])
        self.assertEqual(doc["positions"], [])
        self.assertEqual(doc["status"], "error")
        self.assertFalse(status["has_snapshot"])

    def test_account_change_cannot_display_previous_account(self):
        broker.record_broker_attempt(snapshot=fetch(api()))
        doc, _ = broker.load_broker_positions(prefer_remote=False, broker_id="test-branch", account_id="other")
        status = doc["public_status"]
        self.assertIsNone(doc["last_successful_snapshot"])
        self.assertEqual(doc["positions"], [])
        self.assertEqual(status["error_code"], "ACCOUNT_SELECTION_CHANGED")
        self.assertTrue(status["has_snapshot"])

    def test_no_manual_portfolio_paths_touched(self):
        with patch.object(broker.durable_document, "load_document", return_value=(None, {"source": "none"})) as loaded:
            broker.record_broker_attempt(snapshot=fetch(api()))
        self.assertEqual(loaded.call_args.args, (self.path, "private/broker_positions.json"))
        self.assertNotEqual(broker.REMOTE_PATH, "private/positions.json")

    def test_remote_read_failure_must_not_write_or_erase_cache(self):
        broker.record_broker_attempt(snapshot=fetch(api()))
        previous = self.path.read_bytes()
        with patch.object(broker.durable_document, "_config", return_value=("token", "test/private", "main")), \
             patch.object(broker.durable_document, "_remote_get", return_value=(None, None, "remote_read_http_401")), \
             patch.object(broker.urllib.request, "urlopen") as opened:
            result = broker.record_broker_attempt(failure_code="BROKER_UNAUTHORIZED")
        self.assertEqual(result["error_code"], "STORAGE_READ_FAILED")
        opened.assert_not_called()
        self.assertEqual(self.path.read_bytes(), previous)

    def test_remote_conflict_must_not_replace_local_success(self):
        broker.record_broker_attempt(snapshot=fetch(api()))
        previous = self.path.read_bytes()
        with patch.object(broker.durable_document, "_config", return_value=("token", "test/private", "main")), \
             patch.object(broker.durable_document, "_remote_get", return_value=(document(), "old-sha", None)), \
             patch.object(broker.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("private", 409, "private", {}, None)):
            result = broker.record_broker_attempt(failure_code="BROKER_TIMEOUT")
        self.assertEqual(result["error_code"], "STORAGE_CONFLICT")
        self.assertEqual(self.path.read_bytes(), previous)

    def test_remote_status_commit_preserves_success_and_uses_cas(self):
        previous = document()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(broker.durable_document, "_config", return_value=("token", "test/private", "main")), \
             patch.object(broker.durable_document, "_remote_get", return_value=(previous, "old-sha", None)), \
             patch.object(broker.urllib.request, "urlopen", return_value=response) as opened:
            result = broker.record_broker_attempt(failure_code="BROKER_TIMEOUT")
        self.assertTrue(result["durable"])
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["sha"], "old-sha")
        self.assertTrue(request.full_url.endswith("/private/broker_positions.json"))
        committed = json.loads(base64.b64decode(payload["content"]))
        self.assertEqual(committed["last_successful_snapshot"], previous["last_successful_snapshot"])
        self.assertNotIn("test-account", json.dumps(committed))
        self.assertEqual(json.loads(self.path.read_text()), committed)

    def test_public_status_whitelist_and_remote_fallback(self):
        doc = document(secret="must-not-leak", account_id="must-not-leak")
        with patch.object(broker.durable_document, "load_document", return_value=(doc, {"source": "local", "durable": False, "remote_error": "raw-private-error"})):
            status = broker.broker_positions_status()
        self.assertEqual(status["error_code"], "STORAGE_READ_FAILED")
        self.assertTrue(status["stale"])
        text = json.dumps(status)
        for value in ("1234", "test-account", "raw-private-error", "must-not-leak", "account_key", "positions"):
            self.assertNotIn(value, text)

    def test_corrupt_document_never_replaced(self):
        self.path.write_text('{"schema_version": 999}')
        before = self.path.read_bytes()
        result = broker.record_broker_attempt(failure_code="BROKER_TIMEOUT")
        self.assertEqual(result["error_code"], "INVALID_SNAPSHOT_DOCUMENT")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(broker.load_broker_positions(prefer_remote=False)[0]["status"], "error")

    def test_ok_requires_latest_success_durable_read_and_empty_is_valid(self):
        for rows in ([row()], []):
            doc = document(snapshot=fetch(api(rows)))
            with patch.object(broker.durable_document, "load_document", return_value=(doc, {"source": "github", "durable": True})):
                loaded, storage = broker.load_broker_positions()
            self.assertEqual(loaded["status"], "ok")
            self.assertTrue(storage["durable"])
            self.assertEqual(loaded["as_of"], loaded["fetched_at"])
            doc["last_attempt"] = {"at": "2026-10-03T01:00:00+00:00", "ok": False, "error_code": "BROKER_TIMEOUT"}
            with patch.object(broker.durable_document, "load_document", return_value=(doc, {"source": "github", "durable": True})):
                loaded, _ = broker.load_broker_positions()
            self.assertEqual(loaded["status"], "error")
            self.assertEqual(len(loaded["positions"]), len(rows))

    def test_local_success_alone_is_not_ok_for_auto_adoption(self):
        broker.record_broker_attempt(snapshot=fetch(api()))
        doc, _ = broker.load_broker_positions(prefer_remote=False)
        self.assertEqual(doc["status"], "stale")

    def test_invalid_success_does_not_modify_prior_snapshot(self):
        original = fetch(api())
        broker.record_broker_attempt(snapshot=original)
        before = self.path.read_bytes()
        invalid = copy.deepcopy(original)
        invalid["positions"][0]["cost"] = float("nan")
        with self.assertRaises(broker.BrokerPositionsError):
            broker.record_broker_attempt(snapshot=invalid)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
