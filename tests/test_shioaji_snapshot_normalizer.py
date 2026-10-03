import unittest
from datetime import datetime
from types import SimpleNamespace

from company.data.shioaji_source import normalize_snapshot, snapshot_timestamp


NOW = datetime.fromisoformat("2026-10-02T17:00:00+08:00")


def nanoseconds(text):
    return int(datetime.fromisoformat(text).timestamp()) * 1_000_000_000


class SnapshotNormalizerTests(unittest.TestCase):
    def test_turnover_is_total_amount_not_last_tick_amount(self):
        snap = SimpleNamespace(close=100, average_price=99, amount=300,
                               total_amount=987654321, ts=nanoseconds("2026-10-02T13:30:00+08:00"))
        result = normalize_snapshot(snap, "1111.TW", now=NOW, expected_date="2026-10-02")
        self.assertEqual(result["total_amount"], 987654321)
        self.assertNotIn("amount", result)
        self.assertEqual(result["trade_date"], "2026-10-02")
        self.assertEqual(result["timestamp_status"], "valid")

    def test_missing_total_amount_is_not_replaced_with_last_tick(self):
        result = normalize_snapshot(SimpleNamespace(amount=300), "1111.TW", now=NOW)
        self.assertIsNone(result["total_amount"])
        self.assertEqual(result["timestamp_status"], "invalid")

    def test_invalid_timestamp_is_not_allowed_to_define_batch_trade_date(self):
        for ts in (None, 0, -1, "123", True, float("nan"), 1.5, 10**100):
            with self.subTest(ts=ts):
                result = snapshot_timestamp(ts, now=NOW)
                self.assertIsNone(result["ts"])
                self.assertIsNone(result["trade_date"])
                self.assertEqual(result["timestamp_status"], "invalid")

    def test_stale_contract_keeps_its_own_date_but_not_valid_ts(self):
        ts = nanoseconds("2026-10-01T13:30:00+08:00")
        result = snapshot_timestamp(ts, now=NOW, expected_date="2026-10-02")
        self.assertEqual(result["trade_date"], "2026-10-01")
        self.assertEqual(result["timestamp_status"], "stale")
        self.assertIsNone(result["ts"])

    def test_future_timestamp_is_rejected(self):
        result = snapshot_timestamp(nanoseconds("2026-10-05T13:30:00+08:00"), now=NOW)
        self.assertEqual(result["timestamp_status"], "future")
        self.assertIsNone(result["ts"])

    def test_nanoseconds_are_converted_to_taipei_not_utc_date(self):
        result = snapshot_timestamp(nanoseconds("2026-10-01T23:00:00+00:00"), now=NOW)
        self.assertEqual(result["trade_date"], "2026-10-02")


if __name__ == "__main__":
    unittest.main()
