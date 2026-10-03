"""Manual/broker isolation, persistent opt-in, and fail-closed aggregation."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from company.model import positions
from company.model import daily_history

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 2, 16, tzinfo=TAIPEI)


def manual(enabled=True):
    return {"version": 3, "positions": [{"symbol": "1234.TW", "shares": 2, "cost": 20},
                                       {"symbol": "5678.TWO", "shares": 1, "cost": 7}],
            "broker_enabled": enabled}


def snapshot(shares=3, cost=10, at="2026-10-02T14:00:00+08:00"):
    return {"source": "shioaji", "simulation": False, "unit": "Share", "status": "ok", "as_of": at,
            "positions": [{"symbol": "1234.TW", "shares": shares, "cost": cost}]}


def status(**changes):
    data = {"last_attempt_ok": True, "stale": False, "storage": {"durable": True}}
    data.update(changes)
    return data


def resolve(doc, broker, state, now=NOW):
    with patch.object(daily_history, "is_trading_day", side_effect=lambda moment: moment.weekday() < 5):
        return positions.resolve_portfolio(doc, broker, state, now=now)


class PortfolioResolutionTests(unittest.TestCase):
    def test_existing_documents_default_to_manual_only(self):
        doc = manual()
        del doc["broker_enabled"]
        actual = resolve(doc, None, None)
        self.assertFalse(actual["broker_enabled"])
        self.assertEqual(actual["positions"], doc["positions"])
        self.assertEqual(actual["status"], "ok")

    def test_weighted_aggregation_is_pure_and_updates_after_next_trade(self):
        doc, broker = manual(), snapshot()
        original = copy.deepcopy((doc, broker))
        resolved = resolve(doc, broker, status())
        self.assertEqual(resolved["status"], "ok")
        self.assertEqual(resolved["positions"][0], {"symbol": "1234.TW", "shares": 5, "cost": 14})
        self.assertEqual((doc, broker), original)
        changed = resolve(doc, snapshot(shares=1, cost=30), status())
        self.assertEqual(changed["positions"][0]["shares"], 3)
        self.assertAlmostEqual(changed["positions"][0]["cost"], 70 / 3)
        self.assertEqual(changed["manual_positions"], resolved["manual_positions"])

    def test_empty_verified_inventory_retains_only_other_broker_manual(self):
        broker = snapshot()
        broker["positions"] = []
        resolved = resolve(manual(), broker, status())
        self.assertEqual(resolved["status"], "ok")
        self.assertEqual(resolved["positions"], manual()["positions"])

    def test_failure_or_non_durable_suppresses_entire_actionable_aggregate(self):
        for state in (status(last_attempt_ok=False, error_code="BROKER_UNAUTHORIZED"),
                      status(stale=True), status(storage={"durable": False}), None):
            doc = manual()
            before = copy.deepcopy(doc)
            resolved = resolve(doc, snapshot(), state)
            self.assertEqual(resolved["status"], "unavailable")
            self.assertEqual(resolved["positions"], [])
            self.assertEqual(resolved["manual_positions"], doc["positions"])
            self.assertEqual(doc, before)

    def test_exact_latest_completed_date_and_afterclose_gate(self):
        for at, now, expected in [
            ("2026-10-02T10:00:00+08:00", NOW, "BROKER_SNAPSHOT_NOT_POST_CLOSE"),
            ("2026-10-01T16:00:00+08:00", NOW, "BROKER_SNAPSHOT_STALE"),
            ("2026-10-02T17:00:00+08:00", NOW, "BROKER_SNAPSHOT_FUTURE"),
            ("2026-10-02T14:00:00", NOW, "BROKER_INVALID_SNAPSHOT"),
        ]:
            result = resolve(manual(), snapshot(at=at), status(), now)
            self.assertEqual(result["broker_status"]["error_code"], expected)
            self.assertEqual(result["positions"], [])
        for now in (datetime(2026, 10, 3, 16, tzinfo=TAIPEI), datetime(2026, 10, 5, 10, tzinfo=TAIPEI)):
            self.assertEqual(resolve(manual(), snapshot(), status(), now)["status"], "ok")
        self.assertEqual(resolve(manual(), snapshot(), status(), datetime(2026, 10, 5, 16, tzinfo=TAIPEI))["status"], "unavailable")

    def test_fresh_weekend_bootstrap_inventory_is_inside_completed_day_window(self):
        saturday = datetime(2026, 10, 3, 12, tzinfo=TAIPEI)
        for at in ("2026-10-03T10:00:00+08:00", "2026-10-02T16:00:00+08:00", "2026-10-02T14:00:00+08:00"):
            with self.subTest(at=at):
                result = resolve(manual(), snapshot(at=at), status(), saturday)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["broker_status"]["expected_completed_date"], "2026-10-02")
        result = resolve(manual(), snapshot(at="2026-10-02T13:00:00+08:00"), status(), saturday)
        self.assertEqual(result["status"], "unavailable")
        result = resolve(manual(), snapshot(at="2026-10-03T13:00:00+08:00"), status(), saturday)
        self.assertEqual(result["broker_status"]["error_code"], "BROKER_SNAPSHOT_FUTURE")

    def test_calendar_errors_fail_closed_and_utility_is_shared(self):
        with patch.object(daily_history, "latest_completed_market_date", side_effect=ValueError("private")) as calendar:
            result = positions.resolve_portfolio(manual(), snapshot(), status(), now=NOW)
        calendar.assert_called_once_with(NOW)
        self.assertEqual(result["broker_status"]["error_code"], "BROKER_CALENDAR_UNAVAILABLE")

    def test_invalid_or_failed_snapshot_never_adopted(self):
        for changes in ({"status": "error"}, {"simulation": True}, {"unit": "Common"}, {"positions": None},
                        {"positions": [{"symbol": "1234.TW", "shares": 1, "cost": float("nan")}]},
                        {"positions": [{"symbol": "1234.TW", "shares": 1.5, "cost": 1}]}):
            result = resolve(manual(), {**snapshot(), **changes}, status())
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["positions"], [])

    def test_flag_is_boolean_only_and_preserved_on_manual_save(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(positions, "LOCAL_PATH", Path(tmp) / "manual.json"), \
             patch.object(positions.durable_document, "_config", return_value=None):
            first, _ = positions.save_positions([], 0, broker_enabled=True)
            self.assertTrue(first["broker_enabled"])
            second, _ = positions.save_positions(manual()["positions"], first["version"])
            self.assertTrue(second["broker_enabled"])
            self.assertEqual(second["positions"], manual()["positions"])
            third, _ = positions.save_positions([], second["version"], broker_enabled=False)
            self.assertFalse(third["broker_enabled"])
            before = positions.LOCAL_PATH.read_bytes()
            with self.assertRaisesRegex(ValueError, "boolean"):
                positions.save_positions([], third["version"], broker_enabled="true")
            self.assertEqual(positions.LOCAL_PATH.read_bytes(), before)
            self.assertEqual(json.loads(before)["positions"], [])


if __name__ == "__main__":
    unittest.main()
