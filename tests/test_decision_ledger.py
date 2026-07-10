# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model import ledger


def _rows(symbol: str, start: str, count: int = 130) -> list[dict]:
    current = date.fromisoformat(start)
    rows = []
    price = 100.0
    while len(rows) < count:
        if current.weekday() < 5:
            price *= 1.001
            rows.append({
                "date": current.isoformat(),
                "symbol": symbol,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000,
            })
        current += timedelta(days=1)
    return rows


def main() -> None:
    original_path = ledger.LEDGER_PATH
    original_fetch_remote = ledger._fetch_remote
    original_remote_config = ledger._remote_config
    with tempfile.TemporaryDirectory() as tmp:
        ledger.LEDGER_PATH = Path(tmp) / "decision_ledger.jsonl"
        # 隔離：本機若設有 GITHUB_DATA_TOKEN，未斷開 remote 會把測試訊號寫進真帳本（曾發生 'test' 污染）。
        ledger._remote_config = lambda: None
        ledger._invalidate_read_cache()
        signal = {
            "agent_id": "codex-long-term",
            "model_version": "test-v1",
            "symbol": "2330.TW",
            "data_cutoff": "2026-01-02",
            "action": "BUY_ZONE",
            "horizon": "60D",
            "reference_price": 100.0,
            "evidence": ["test evidence"],
        }
        first = ledger.freeze_signals([signal])
        second = ledger.freeze_signals([signal])
        assert first["added"] == 1
        assert first["status"] == "degraded"
        assert second["added"] == 0

        before, _ = ledger.load_events(prefer_remote=False)
        frozen = dict(before[0])
        histories = {
            "2330.TW": _rows("2330.TW", "2026-01-02"),
            "0050.TW": _rows("0050.TW", "2026-01-02"),
        }

        def fetch(symbol: str, start: str, end: str) -> list[dict]:
            return [row for row in histories[symbol] if start <= row["date"] < end]

        result = ledger.update_outcomes("2026-07-31", fetch)
        assert result["added"] == 5
        after, _ = ledger.load_events(prefer_remote=False)
        assert after[0] == frozen, "Outcome填補不得修改原始signal事件"
        materialized = ledger.materialize(after)
        assert set(materialized[0]["outcomes"]) == set(ledger.HORIZONS)
        assert materialized[0]["outcomes"]["20D"]["mae"] is not None
        assert materialized[0]["outcomes"]["20D"]["mfe"] is not None

        adjusted_rows = [
            {"date": "2026-01-02", "close": 100, "adj_close": 99, "high": 101, "low": 99},
            {"date": "2026-01-05", "close": 98, "adj_close": 100, "high": 99, "low": 97},
            {"date": "2026-01-06", "close": 100, "adj_close": 102, "high": 101, "low": 99},
        ]
        raw_benchmark = [
            {"date": "2026-01-02", "close": 50, "high": 51, "low": 49},
            {"date": "2026-01-06", "close": 51, "high": 52, "low": 50},
        ]
        adjusted_event = ledger._outcome_event(
            {"signal_id": "SIG-adjusted", "data_cutoff": "2026-01-02", "reference_price": 100},
            "2D",
            adjusted_rows,
            raw_benchmark,
            0,
        )
        assert adjusted_event is not None
        assert adjusted_event["return_basis"] == "adjusted"
        assert adjusted_event["gross_return"] == round(102 / 99 - 1, 8)
        assert adjusted_event["benchmark_return_basis"] == "raw"
        assert adjusted_event["benchmark_return"] == 0.02

        raw_rows = [{key: value for key, value in row.items() if key != "adj_close"} for row in adjusted_rows]
        raw_event = ledger._outcome_event(
            {"signal_id": "SIG-raw", "data_cutoff": "2026-01-02", "reference_price": 101},
            "2D",
            raw_rows,
            raw_benchmark,
            0,
        )
        assert raw_event is not None
        assert raw_event["return_basis"] == "raw"
        assert raw_event["gross_return"] == round(100 / 101 - 1, 8)

        local_event = ledger.build_signal_event({**signal, "symbol": "2317.TW"})
        ledger.LEDGER_PATH.write_text(ledger._serialize_jsonl([local_event]), encoding="utf-8")
        remote_event = ledger.build_signal_event({**signal, "symbol": "2454.TW"})
        ledger._invalidate_read_cache()
        ledger._fetch_remote = lambda: ([remote_event], "sha", None)
        remote_loaded, remote_storage = ledger.load_events(prefer_remote=True)
        # A successful remote read refreshes the local mirror by design. Restore a
        # distinct local fixture to verify the cache does not cross read modes.
        ledger.LEDGER_PATH.write_text(ledger._serialize_jsonl([local_event]), encoding="utf-8")
        local_loaded, local_storage = ledger.load_events(prefer_remote=False)
        assert remote_loaded[0]["symbol"] == "2454.TW"
        assert remote_storage["source"] == "github"
        assert local_loaded[0]["symbol"] == "2317.TW", "Cache must not cross prefer_remote modes"
        assert local_storage["source"] == "local"

        ledger._invalidate_read_cache()
        ledger._fetch_remote = lambda: (None, None, "github_read:URLError")
        ledger._remote_config = lambda: ("token", "owner/repo", "main")
        durability = ledger.append_events([local_event])
        assert durability["added"] == 0
        assert durability["durable"] is False
        assert durability["error"] == "github_read:URLError"
    ledger.LEDGER_PATH = original_path
    ledger._fetch_remote = original_fetch_remote
    ledger._remote_config = original_remote_config
    ledger._invalidate_read_cache()
    print("Decision Ledger tests passed")


if __name__ == "__main__":
    main()
