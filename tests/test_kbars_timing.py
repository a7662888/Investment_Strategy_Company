# -*- coding: utf-8 -*-
"""分 K 彙整（單位與時間戳）、儲存格式、假跌破與成交價研究。"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data.kbar_daily import aggregate_days, decode_days, encode_days, parse_kbars
from company.model.timing_research import execution_study, false_break_study


def wall_ns(text: str) -> int:
    """官方範例的編碼：台北牆上時間直接當 UTC。"""
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1e9)


class Kbars:
    def __init__(self, rows):
        self.rows = rows

    def dict(self):
        return {"ts": [wall_ns(r[0]) for r in self.rows],
                **{k: [r[i + 1] for r in self.rows] for i, k in enumerate(("Open", "High", "Low", "Close", "Volume", "Amount"))}}


class Aggregate(unittest.TestCase):
    def test_official_example_units_and_session_features(self):
        bars = parse_kbars(Kbars([
            ("2026-05-18T09:01:00", 2225, 2235, 2225, 2230, 2565, 5708965000),
            ("2026-05-18T09:02:00", 2235, 2235, 2225, 2230, 377, 840260000),
            ("2026-05-18T13:25:00", 2240, 2245, 2238, 2244, 1000, 2242000000),
            ("2026-05-18T13:40:00", 1, 1, 1, 1, 1, 1),           # 盤後零星：不計入
        ]))
        day = aggregate_days(bars)["2026-05-18"]
        self.assertEqual(day["bars"], 3)
        self.assertEqual(day["open"], 2225)
        self.assertEqual(day["close"], 2244)
        self.assertTrue(2225 <= day["vwap"] <= 2245)
        self.assertAlmostEqual(day["vwap_first15"], (5708965000 + 840260000) / ((2565 + 377) * 1000), places=3)
        self.assertEqual(day["vwap_last30"], 2242.0)
        self.assertEqual(day["low_time"], "09:01")

    def test_inconsistent_units_are_dropped(self):
        bars = parse_kbars(Kbars([("2026-05-18T09:01:00", 100, 101, 99, 100, 10, 1000)]))  # 均價 0.1
        self.assertEqual(aggregate_days(bars), {})

    def test_storage_round_trip_is_compact(self):
        days = {f"2026-01-{d:02d}": {"open": 100.5, "high": 101, "low": 99.5, "close": 100, "volume_lots": 123456,
                                     "amount": 12345678901, "vwap": 100.1234, "vwap_first15": None,
                                     "vwap_last30": 100.2, "low_time": "09:05", "high_time": "13:20", "bars": 270}
                for d in range(1, 29)}
        text = encode_days(days)
        self.assertEqual(decode_days(text), {d: {**v, "volume_lots": float(v["volume_lots"]), "amount": float(v["amount"]),
                                                 "bars": float(v["bars"]), "high": float(v["high"]), "close": float(v["close"])}
                                             for d, v in days.items()})
        self.assertLess(len(json.dumps({"codes": {s: text for s in range(30)}})) * 250 / 28, 1_000_000)


def series(closes, lows=None):
    return {f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}": {"open": c, "high": c * 1.01, "low": (lows or {}).get(i, c), "close": c}
            for i, c in enumerate(closes)}


class FalseBreaks(unittest.TestCase):
    def test_recovered_and_confirmed_breaks_of_ma20(self):
        closes = [100.0] * 70 + [100.0, 100.0] + [100.0] * 25
        lows = {70: 95.0, 71: 95.0}
        closes[71] = 97.0          # 第 71 天收盤確認跌破
        study = false_break_study({"X": series(closes, lows)})
        ma = study["ma20"]
        self.assertEqual(ma["events"], 2)
        self.assertEqual(ma["recovered_share_pct"], 50.0)
        self.assertAlmostEqual(ma["confirmed_edge_pct"]["mean"], 3.0, places=1)

    def test_not_enough_history_yields_nothing(self):
        self.assertEqual(false_break_study({"X": series([100.0] * 30, {29: 90.0})}), {})


class Execution(unittest.TestCase):
    def test_fill_vs_vwap_excludes_fee(self):
        days = {"0056.TW": {"2026-10-08": {"high": 59.0, "low": 57.0, "vwap": 58.0, "vwap_first15": 58.29, "vwap_last30": 57.8}}}
        lots = {"0056.TW": [{"date": "2026-10-08", "shares": 1000, "cost_per_share": 58.42, "fee": 20}]}
        out = execution_study(lots, days)
        fill = out["fills"][0]
        self.assertEqual(fill["price"], 58.4)
        self.assertAlmostEqual(fill["vs_vwap_pct"], 0.69, places=2)
        self.assertEqual(fill["range_position"], 0.7)
        self.assertAlmostEqual(out["session_first15_vs_day_pct"]["mean"], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
