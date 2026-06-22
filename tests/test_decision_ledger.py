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
    with tempfile.TemporaryDirectory() as tmp:
        ledger.LEDGER_PATH = Path(tmp) / "decision_ledger.jsonl"
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
    ledger.LEDGER_PATH = original_path
    print("Decision Ledger tests passed")


if __name__ == "__main__":
    main()
