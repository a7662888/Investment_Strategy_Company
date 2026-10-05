import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from company.data.shioaji_source import normalize_snapshot, snapshot_timestamp


NOW = datetime.fromisoformat("2026-10-02T17:00:00+08:00")


def nanoseconds(text):
    return int(datetime.fromisoformat(text).timestamp()) * 1_000_000_000


class SnapshotNormalizerTests(unittest.TestCase):
    def test_turnover_is_total_amount_not_last_tick_amount(self):
        snap = SimpleNamespace(close=100, average_price=99, amount=300, volume=3,
                               total_volume=1234, yesterday_volume=1000,
                               buy_price=99.5, buy_volume=12,
                               sell_price=100.0, sell_volume=8,
                               total_amount=987654321, ts=nanoseconds("2026-10-02T13:30:00+08:00"))
        result = normalize_snapshot(snap, "1111.TW", now=NOW, expected_date="2026-10-02")
        self.assertEqual(result["total_amount"], 987654321)
        self.assertEqual(result["last_amount"], 300)
        self.assertEqual(result["last_volume"], 3)
        self.assertEqual(result["yesterday_volume"], 1000)
        self.assertEqual(result["bid_volume"], 12)
        self.assertEqual(result["ask_volume"], 8)
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

    def test_shioaji_taipei_wall_clock_integer_is_normalized(self):
        # SDK 1.7.7 production example: 14:30 Taipei is encoded as though 14:30 UTC.
        raw = int(datetime.fromisoformat("2026-10-02T14:30:00+00:00").timestamp()) * 1_000_000_000
        result = snapshot_timestamp(raw, now=NOW, expected_date="2026-10-02")
        self.assertEqual(result["timestamp_status"], "valid")
        self.assertEqual(result["trade_date"], "2026-10-02")
        corrected = datetime.fromtimestamp(result["ts"] / 1_000_000_000, timezone.utc)
        self.assertEqual(corrected.isoformat(), "2026-10-02T06:30:00+00:00")

    def test_nanoseconds_are_converted_to_taipei_not_utc_date(self):
        result = snapshot_timestamp(nanoseconds("2026-10-01T23:00:00+00:00"), now=NOW)
        self.assertEqual(result["trade_date"], "2026-10-02")


if __name__ == "__main__":
    unittest.main()
