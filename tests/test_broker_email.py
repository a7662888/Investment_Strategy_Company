"""Email and API sync consume the same portfolio resolver; no SMTP or broker calls."""
import json
import unittest
from unittest.mock import patch

import run_daily_email as email
from company.model import positions
from tests.test_portfolio_resolution import manual, snapshot, status, NOW


class Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.value).encode()


class BrokerEmailTests(unittest.TestCase):
    def test_enabled_email_uses_dynamic_effective_positions(self):
        def resolver(*args):
            with patch("company.model.daily_history.latest_completed_market_date", return_value="2026-10-02"):
                return positions.resolve_portfolio(*args, now=NOW)
        with patch.object(email, "load_positions", return_value=(manual(), {"source": "github", "durable": True})), \
             patch.object(email, "load_broker_positions", return_value=(snapshot(), status())), \
             patch.object(email, "resolve_portfolio", side_effect=resolver) as resolved:
            effective, meta = email.resolve_positions_with_meta()
        resolved.assert_called_once()
        self.assertEqual(effective[0]["shares"], 5)
        self.assertEqual(meta["source"], "manual+shioaji")
        self.assertTrue(meta["broker_enabled"])

    def test_failure_rejects_both_manual_only_and_legacy_fallback(self):
        with patch.object(email, "load_positions", return_value=(manual(), {"source": "github", "durable": True})), \
             patch.object(email, "load_broker_positions", return_value=(snapshot(), status(last_attempt_ok=False, error_code="BROKER_TIMEOUT"))), \
             patch.object(email, "synced_positions") as legacy:
            with self.assertRaisesRegex(RuntimeError, "broker inventory unavailable"):
                email.resolve_positions_with_meta()
        legacy.assert_not_called()

    def test_local_manual_fallback_is_rejected_once_enabled(self):
        with patch.object(email, "load_positions", return_value=(manual(), {"source": "local", "durable": False})), \
             patch.object(email, "load_broker_positions") as loaded:
            with self.assertRaisesRegex(RuntimeError, "not durable"):
                email.resolve_positions_with_meta()
        loaded.assert_not_called()

    def test_api_sync_fetches_broker_and_uses_same_resolver(self):
        responses = [Response({**manual(), "storage": {"durable": True}}),
                     Response({**snapshot(), "storage": {"durable": True}})]
        def resolver(*args):
            with patch("company.model.daily_history.latest_completed_market_date", return_value="2026-10-02"):
                return positions.resolve_portfolio(*args, now=NOW)
        with patch.object(email.urllib.request, "urlopen", side_effect=responses) as opened, \
             patch.object(email, "resolve_portfolio", side_effect=resolver):
            effective, meta = email.synced_positions("synthetic-token")
        self.assertEqual(effective[0]["shares"], 5)
        self.assertTrue(meta["broker_enabled"])
        self.assertTrue(opened.call_args.args[0].full_url.endswith("/api/broker-positions"))

    def test_api_sync_stale_broker_stops_email(self):
        responses = [Response({**manual(), "storage": {"durable": True}}),
                     Response({**snapshot(), "status": "unavailable", "storage": {"durable": True}})]
        with patch.object(email.urllib.request, "urlopen", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                email.synced_positions("synthetic-token")


if __name__ == "__main__":
    unittest.main()
