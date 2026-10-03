"""Attributed market rankings; descriptive evidence, never a trading signal."""
from __future__ import annotations

import math


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def build_market_research(state: dict, snapshots: dict, scanners: dict | None = None) -> dict:
    as_of = state.get("as_of")
    evaluated = {row.get("symbol"): row for row in state.get("evaluations", [])}
    rows = []
    for symbol, snap in snapshots.items():
        if snap.get("trade_date") != as_of:
            continue
        item = evaluated.get(symbol, {})
        row = {key: snap.get(key) for key in (
            "close", "change_rate", "total_volume", "total_amount", "volume_ratio",
            "close_vs_vwap_pct", "spread_pct", "trade_date", "source",
        )}
        row.update(symbol=symbol, name=item.get("name") or snap.get("name"),
                   eligible_pool=item.get("eligible_pool", False),
                   decision=item.get("decision", "未納入品質評估"))
        rows.append(row)

    def ranked(key, reverse=True):
        valid = [row for row in rows if number(row.get(key)) is not None]
        return sorted(valid, key=lambda row: number(row[key]), reverse=reverse)[:20]

    market_scan = scanners if scanners and scanners.get("trade_date") == as_of else None
    return {
        "as_of": as_of, "scope": "mother-pool-and-etf-subpool", "coverage": len(rows),
        "rankings": {"amount": ranked("total_amount"), "volume": ranked("total_volume"),
                     "gainers": ranked("change_rate"), "losers": ranked("change_rate", False),
                     "volume_ratio": ranked("volume_ratio")},
        "market_scanners": market_scan,
        "capabilities": {
            "pool_rankings": bool(rows), "market_scanners": bool(market_scan),
            "fundamentals": True, "institutional_streaks": False,
            "constituent_weights": False, "prediction_validated": False,
        },
        "policy": "行情排行不等於推薦；母池不是指數權重前百名。新量價因子僅供研究，不改買賣判定。",
    }
