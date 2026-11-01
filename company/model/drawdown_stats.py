"""Historical drawdown base rates for the market proxy (0050).

Shown on heavy-down days so long-term holders see how often a decline of the
current size has happened before and how long recovery took. These are
descriptive statistics of the past, not a forecast and not a buy/sell signal.
"""
from __future__ import annotations

import math
import statistics
from datetime import date

THRESHOLDS = (0.10, 0.20, 0.30)


# Taiwan's daily price limit has never exceeded 10%; a larger one-day move in an
# ETF series is a split that was not back-adjusted or a vendor data error.
MAX_DAILY_MOVE = 0.11


def repair_series(rows: list[dict]) -> tuple[list[dict], dict]:
    """Back-adjust unadjusted splits, then keep the longest clean tail.

    Yahoo's 0050 history (checked 2026-11-01) still shows the 2014 split as a
    one-day -75% move, and two impossible +12%/+16% days in early 2009.
    """
    out = [dict(r) for r in rows]
    splits: list[str] = []
    anomalies: list[str] = []
    for i in range(1, len(out)):
        ratio = out[i]["adj_close"] / out[i - 1]["adj_close"]
        if abs(ratio - 1.0) <= MAX_DAILY_MOVE:
            continue
        factor = next((k for k in range(2, 11)
                       for target in (1.0 / k, float(k)) if abs(ratio / target - 1.0) <= 0.03), None)
        if factor:
            nearest = 1.0 / factor if ratio < 1 else float(factor)
            for row in out[:i]:
                row["adj_close"] *= nearest
            splits.append(out[i]["date"])
        else:
            anomalies.append(out[i]["date"])
    if anomalies:
        cut = max(i for i, r in enumerate(out) if r["date"] == anomalies[-1])
        out = out[cut:]
    return out, {"splits_adjusted": splits, "anomalies_dropped_before": anomalies[-1] if anomalies else None,
                 "anomaly_days": anomalies}


def _episodes(rows: list[dict]) -> list[dict]:
    """Split the series into peak→recovery episodes on dividend-adjusted closes."""
    episodes: list[dict] = []
    peak_price = peak_date = None
    current: dict | None = None
    for row in rows:
        price, day = row["adj_close"], row["date"]
        if peak_price is None or price >= peak_price:
            if current is not None:
                current["recovered"] = day
                episodes.append(current)
                current = None
            peak_price, peak_date = price, day
            continue
        depth = price / peak_price - 1.0
        if current is None:
            current = {"peak": peak_date, "trough": day, "depth": depth, "recovered": None}
        elif depth < current["depth"]:
            current.update(trough=day, depth=depth)
    if current is not None:
        episodes.append(current)
    return episodes


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def drawdown_profile(rows: list[dict], thresholds: tuple[float, ...] = THRESHOLDS) -> dict:
    """Current drawdown plus, per threshold, how many past declines reached it and
    how many calendar days they took from the prior peak back to a new high."""
    clean = [{"date": r["date"], "adj_close": float(r["adj_close"])} for r in rows
             if r.get("date") and isinstance(r.get("adj_close"), (int, float))
             and math.isfinite(r["adj_close"]) and r["adj_close"] > 0]
    clean.sort(key=lambda r: r["date"])
    clean, repairs = repair_series(clean)
    if len(clean) < 250:
        return {"available": False, "reason": "price_history_too_short"}
    episodes = _episodes(clean)
    last = clean[-1]
    peak = max(clean, key=lambda r: r["adj_close"])
    ongoing = episodes[-1] if episodes and episodes[-1]["recovered"] is None else None
    current_depth = last["adj_close"] / peak["adj_close"] - 1.0
    bands = []
    for threshold in thresholds:
        hit = [e for e in episodes if e["depth"] <= -threshold]
        done = [e for e in hit if e["recovered"]]
        spans = [_days(e["peak"], e["recovered"]) for e in done]
        bands.append({
            "threshold": threshold,
            "episodes": len(hit),
            "recovered": len(done),
            "median_days_to_new_high": round(statistics.median(spans)) if spans else None,
            "longest_days_to_new_high": max(spans) if spans else None,
            "deepest": round(min(e["depth"] for e in hit), 4) if hit else None,
        })
    return {
        "available": True,
        "history_start": clean[0]["date"],
        "as_of": last["date"],
        "observations": len(clean),
        "current_drawdown": round(current_depth, 4),
        "peak_date": peak["date"],
        "days_since_peak": _days(peak["date"], last["date"]) if ongoing else 0,
        "bands": bands,
        "data_repairs": repairs,
        "method": "含息還原收盤；一次回檔＝從前高跌下到重回前高為止，天數為日曆天",
    }
