# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import types
import unittest
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory

from company.data.twstock_batch import (
    CollectorConfig,
    TwstockBatchCollector,
    TwstockBatchError,
    validate_against_reference,
)


Data = namedtuple(
    "Data",
    "date capacity turnover open high low close change transaction note",
)


class FakeStock:
    def __init__(self, sid: str, initial_fetch: bool = True):
        self.sid = sid
        self.initial_fetch = initial_fetch

    def fetch(self, year: int, month: int):
        if month == 6:
            return [
                Data(
                    date=types.SimpleNamespace(date=lambda: __import__("datetime").date(2026, 6, 26)),
                    capacity=1000,
                    turnover=2340000,
                    open=2300.0,
                    high=2350.0,
                    low=2290.0,
                    close=2340.0,
                    change=10.0,
                    transaction=100,
                    note="",
                )
            ]
        return []


class EmptyStock:
    def __init__(self, sid: str, initial_fetch: bool = True):
        pass

    def fetch(self, year: int, month: int):
        return []


class FakeRealtime:
    @staticmethod
    def get(code: str, retry: int = 3):
        return {
            "timestamp": 1782455400.0,
            "info": {"code": code, "name": "台積電", "time": "2026-06-26 14:30:00"},
            "realtime": {
                "latest_trade_price": "2340.0000",
                "trade_volume": "5701",
                "accumulate_trade_volume": "39059",
                "best_bid_price": ["2335.0000", "2330.0000"],
                "best_bid_volume": ["325", "1179"],
                "best_ask_price": ["2340.0000", "2345.0000"],
                "best_ask_volume": ["218", "460"],
            },
            "success": True,
        }


class TestTwstockBatch(unittest.TestCase):
    def _collector(self, tmp: str, stock_cls=FakeStock) -> TwstockBatchCollector:
        fake_twstock = types.SimpleNamespace(Stock=stock_cls, realtime=FakeRealtime)
        config = CollectorConfig(
            output_dir=Path(tmp) / "web_cache" / "twstock",
            reference_cache_dir=Path(tmp) / "data_cache",
            min_interval_seconds=0,
            operation_timeout_seconds=2,
            max_attempts=1,
        )
        return TwstockBatchCollector(config=config, twstock_module=fake_twstock, sleeper=lambda _: None)

    def test_realtime_snapshot_serializes_best_five(self):
        with TemporaryDirectory() as tmp:
            collector = self._collector(tmp)
            row = collector.fetch_order_book_snapshot("2330.TW")
            self.assertEqual(row["symbol"], "2330.TW")
            self.assertEqual(row["provider"], "twstock.realtime")
            self.assertIn("2335.0000", row["best_bid_price"])
            path = collector.write_order_book_snapshots([row])
            self.assertTrue(path.exists())

    def test_daily_empty_payload_is_error_and_not_written(self):
        with TemporaryDirectory() as tmp:
            collector = self._collector(tmp, stock_cls=EmptyStock)
            with self.assertRaises(TwstockBatchError):
                collector.fetch_daily_ohlcv("2330.TW", "2026-06-01", "2026-06-30")
            self.assertFalse((Path(tmp) / "web_cache" / "twstock" / "2330_TW_twstock_ohlcv.csv").exists())

    def test_collect_writes_daily_and_validation(self):
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "data_cache"
            cache.mkdir()
            with (cache / "2330_price.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "close"])
                writer.writeheader()
                writer.writerow({"date": "2026-06-26", "close": "2340"})

            collector = self._collector(tmp)
            report = collector.collect(["2330.TW"], start_date="2026-06-01", end_date="2026-06-30")
            self.assertFalse(report["errors"])
            self.assertEqual(report["validation"][0]["status"], "ok")
            self.assertTrue(Path(report["daily_files"]["2330.TW"]).exists())
            self.assertTrue(Path(report["validation_file"]).exists())

    def test_reference_validation_marks_stale(self):
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with (cache / "2330_price.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "close"])
                writer.writeheader()
                writer.writerow({"date": "2026-06-27", "close": "2350"})
            status = validate_against_reference(
                "2330.TW",
                [{"date": "2026-06-26", "close": "2340"}],
                cache,
            )
            self.assertEqual(status["status"], "stale_vs_reference")
            self.assertTrue(status["stale"])


if __name__ == "__main__":
    unittest.main()
