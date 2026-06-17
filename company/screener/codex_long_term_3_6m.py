# -*- coding: utf-8 -*-
"""Codex independent 3-6 month long-term potential scorer.

This module intentionally avoids optional scientific dependencies such as
pandas/numpy because the Render web app is configured as a lightweight
standard-library deployment.  It keeps the Codex long-term scorer independent
from the existing ``potential_3_6m`` scorer and fails per-symbol, not per-panel.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = ROOT / "model_artifacts" / "active_pool.json"
CACHE_DIR = ROOT / "data_cache"


def load_pool_symbols(limit: int = 100, cached_only: bool = True) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if POOL_PATH.exists():
        try:
            rows = json.loads(POOL_PATH.read_text(encoding="utf-8")).get("stocks", [])
        except Exception:
            rows = []
    if not rows:
        try:
            from company.data.universe import load_active_universe

            rows = load_active_universe()
        except Exception:
            rows = []

    out: list[dict[str, str]] = []
    for row in rows[:limit]:
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            continue
        code = sym.split(".")[0]
        if cached_only and not any(CACHE_DIR.glob(f"{code}*price*.csv")):
            continue
        out.append({
            "symbol": sym if sym.endswith(".TW") else f"{sym}.TW",
            "name": str(row.get("name") or sym),
            "sector": str(row.get("sector") or ""),
        })
    return out


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _mean(values: list[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else 0.0


def _std(values: list[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return 0.0
    avg = _mean(vals)
    return math.sqrt(sum((v - avg) ** 2 for v in vals) / len(vals))


def _median(values: list[float], default: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v) and v > 0)
    if not vals:
        return default
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _load_per_rows(code: str, as_of: str) -> list[dict[str, str]]:
    rows = _read_csv(CACHE_DIR / f"{code}_per.csv")
    out = [r for r in rows if str(r.get("date") or "") <= as_of]
    return out


def _load_revenue_yoy(code: str, as_of: str) -> tuple[float | None, list[float]]:
    rows = _read_csv(CACHE_DIR / f"{code}_revenue.csv")
    parsed = []
    for row in rows:
        year = str(row.get("revenue_year") or "").strip()
        month = str(row.get("revenue_month") or "").strip()
        revenue = _as_float(row.get("revenue"), 0.0)
        if not year or not month or revenue <= 0:
            continue
        period = f"{int(float(year)):04d}-{int(float(month)):02d}-01"
        announce = (datetime.fromisoformat(period) + timedelta(days=40)).date().isoformat()
        if announce <= as_of:
            parsed.append((period, revenue))
    parsed.sort(key=lambda x: x[0])
    yoy_values = []
    for idx in range(12, len(parsed)):
        prev = parsed[idx - 12][1]
        if prev > 0:
            yoy_values.append(parsed[idx][1] / prev - 1.0)
    return (yoy_values[-1] if yoy_values else None), yoy_values[-4:]


def _score_margin(margin: float) -> float:
    if margin >= 0.30:
        return 30.0
    if margin >= 0.15:
        return 22.0 + (margin - 0.15) / 0.15 * 8.0
    if margin >= 0.0:
        return 10.0 + margin / 0.15 * 12.0
    if margin >= -0.20:
        return max(0.0, 10.0 + margin / 0.20 * 10.0)
    return 0.0


def _grade_action(score: float, margin: float, value_trap: bool) -> tuple[str, str, str]:
    if value_trap or margin <= -0.25:
        return "D", "D級 避開", "AVOID"
    if score >= 75.0 and margin >= 0.15:
        return "A", "A級 長投候選", "ACCUMULATE"
    if score >= 62.0 and margin >= 0.0:
        return "B", "B級 等待確認", "WATCH"
    if score >= 62.0 and margin < 0.0:
        return "B", "B級 好公司等拉回", "WAIT_FOR_VALUE"
    if score >= 45.0:
        return "C", "C級 觀察不追", "WAIT"
    return "D", "D級 避開", "AVOID"


def _empty_result(symbol: str, name: str | None, sector: str) -> dict:
    return {
        "symbol": symbol if symbol.endswith(".TW") else f"{symbol}.TW",
        "name": name or symbol,
        "sector": sector,
        "agent": "Codex 3-6M",
        "score": 0.0,
        "grade": "D",
        "grade_label": "D級 避開",
        "action": "AVOID",
        "close": 0.0,
        "fair_value": 0.0,
        "fair_range": [0.0, 0.0],
        "margin_of_safety": 0.0,
        "buy_range": "-",
        "review_trigger": "資料不足時不做長投判斷",
        "scores": {},
        "reasons": [],
        "warnings": [],
    }


def score_symbol(symbol: str, as_of: str, name: str | None = None, sector: str = "") -> dict:
    clean_symbol = symbol if symbol.endswith(".TW") else f"{symbol}.TW"
    code = clean_symbol.split(".")[0]
    base = _empty_result(clean_symbol, name, sector)
    start = (datetime.fromisoformat(as_of) - timedelta(days=420)).date().isoformat()
    end_exclusive = (datetime.fromisoformat(as_of) + timedelta(days=1)).date().isoformat()

    try:
        from app import fetch_history

        rows = fetch_history(clean_symbol, start, end_exclusive)
    except Exception as exc:
        base["warnings"] = [f"價格資料讀取失敗: {exc}"]
        return base
    rows = [r for r in rows if str(r.get("date") or "") <= as_of]
    if len(rows) < 80:
        base["warnings"] = ["歷史價格資料不足，暫不做 3-6M 長投判斷。"]
        return base

    closes = [_as_float(r.get("close")) for r in rows]
    volumes = [_as_float(r.get("volume")) for r in rows]
    close = closes[-1]
    per_rows = _load_per_rows(code, as_of)
    per_vals = [_as_float(r.get("PER")) for r in per_rows]
    pbr_vals = [_as_float(r.get("PBR")) for r in per_rows]
    per = per_vals[-1] if per_vals else None
    pbr = pbr_vals[-1] if pbr_vals else None
    median_per = _median(per_vals[-250:], 15.0)
    median_pbr = _median(pbr_vals[-250:], 2.0)
    div_yield = _as_float(per_rows[-1].get("dividend_yield"), 0.0) if per_rows else 0.0
    rev_yoy, rev_recent = _load_revenue_yoy(code, as_of)

    eps = close / per if per and per > 0 else None
    bps = close / pbr if pbr and pbr > 0 else None
    pe_value = eps * median_per if eps is not None else None
    pb_value = bps * median_pbr if bps is not None else None
    if pe_value and pb_value:
        fair_value = 0.65 * pe_value + 0.35 * pb_value
        valuation_method = "PER/PBR historical median"
    elif pe_value:
        fair_value = pe_value
        valuation_method = "PER historical median"
    elif pb_value:
        fair_value = pb_value
        valuation_method = "PBR historical median"
    else:
        ma120 = _mean(closes[-120:]) if len(closes) >= 120 else _mean(closes)
        fair_value = min(ma120, close) if ma120 > 0 else close
        valuation_method = "price-center fallback"
    fair_value = max(close * 0.35, min(close * 2.2, fair_value))
    margin = (fair_value - close) / fair_value if fair_value > 0 else 0.0

    valuation_score = min(30.0, _score_margin(margin) + (3.0 if div_yield >= 5.0 else 0.0))
    growth_score = 0.0
    if rev_yoy is not None:
        if rev_yoy >= 0.20:
            growth_score += 12.0
        elif rev_yoy >= 0:
            growth_score += 5.0 + rev_yoy / 0.20 * 7.0
        else:
            growth_score += max(0.0, 5.0 + rev_yoy / 0.20 * 5.0)
    if len(rev_recent) >= 3 and rev_recent[-1] > _mean(rev_recent[:-1]):
        growth_score += 5.0
    growth_score = min(20.0, growth_score)

    roe = (pbr / per) if per and per > 0 and pbr and pbr > 0 else 0.0
    if roe >= 0.15:
        quality_score = 12.0
    elif roe >= 0.08:
        quality_score = 5.0 + (roe - 0.08) / 0.07 * 7.0
    else:
        quality_score = max(0.0, roe / 0.08 * 5.0) if roe > 0 else 3.0
    quality_score = min(20.0, quality_score + min(3.0, div_yield / 5.0 * 3.0 if div_yield > 0 else 0.0))

    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else close
    ma60 = _mean(closes[-60:]) if len(closes) >= 60 else close
    ma120 = _mean(closes[-120:]) if len(closes) >= 120 else ma60
    catalyst_score = 0.0
    if rev_yoy is not None and rev_yoy >= 0.10:
        catalyst_score += 4.0
    if close > ma20 > ma60 and ma60 >= ma120:
        catalyst_score += 4.0
    elif close > ma60:
        catalyst_score += 2.0
    if len(volumes) >= 60 and _mean(volumes[-5:]) > 1.4 * _mean(volumes[-60:]):
        catalyst_score += 2.0
    catalyst_score = min(15.0, catalyst_score)

    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(max(1, len(closes) - 20), len(closes)) if closes[i - 1] > 0]
    vol20 = _std(rets) if rets else 0.03
    high120 = max(closes[-120:]) if len(closes) >= 120 else max(closes)
    drawdown = close / high120 - 1.0 if high120 > 0 else 0.0
    risk_score = 0.0
    risk_score += 4.0 if vol20 < 0.025 else 2.5 if vol20 < 0.045 else 1.0
    risk_score += 3.0 if drawdown >= -0.15 else 1.5 if drawdown >= -0.30 else 0.5
    risk_score += 3.0 if close >= ma60 else 0.5
    risk_score = min(10.0, risk_score)

    warnings: list[str] = []
    deductions = 0.0
    value_trap = bool(per is not None and per > 0 and per < 10.0 and rev_yoy is not None and rev_yoy < -0.05)
    if value_trap:
        warnings.append("價值陷阱：低本益比但營收衰退。")
        deductions += 15.0
    if margin < 0:
        warnings.append("安全邊際不足：目前股價高於保守合理價。")
    if close < ma60:
        warnings.append("中期結構偏弱：股價低於 60 日均線。")
        deductions += 5.0
    if not per_rows:
        warnings.append("缺少 PER/PBR 快取，合理價改用保守價格中樞估算。")
    if not warnings:
        warnings.append("未見明顯價值陷阱；仍需追蹤財報與產業循環。")

    score = round(max(0.0, min(100.0, valuation_score + growth_score + quality_score + catalyst_score + risk_score - deductions)), 1)
    grade, grade_label, action = _grade_action(score, margin, value_trap)
    reasons: list[str] = []
    if margin >= 0.15:
        reasons.append(f"安全邊際 {margin * 100:.1f}%")
    if rev_yoy is not None:
        reasons.append(f"營收YoY {rev_yoy * 100:+.1f}%")
    if roe > 0:
        reasons.append(f"ROE proxy {roe * 100:.1f}%")
    if close > ma20 > ma60:
        reasons.append("中期均線結構偏多")
    if not reasons:
        reasons.append(f"估值方法: {valuation_method}")

    buy_low = round(fair_value * 0.75, 1)
    buy_high = round(fair_value * 0.85, 1)
    base.update({
        "score": score,
        "grade": grade,
        "grade_label": grade_label,
        "action": action,
        "close": round(close, 1),
        "fair_value": round(fair_value, 1),
        "fair_range": [round(fair_value * 0.9, 1), round(fair_value * 1.1, 1)],
        "margin_of_safety": round(margin * 100.0, 1),
        "buy_range": f"{buy_low} - {buy_high} 元",
        "review_trigger": "營收YoY連兩月轉弱、跌破季線、或估值假設失效時重新檢查",
        "scores": {
            "valuation": round(valuation_score, 1),
            "growth": round(growth_score, 1),
            "quality": round(quality_score, 1),
            "catalyst": round(catalyst_score, 1),
            "risk": round(risk_score, 1),
        },
        "reasons": reasons,
        "warnings": warnings,
    })
    return base


def scan_codex_long_term(
    as_of: str | None = None,
    symbols: list[str] | None = None,
    limit: int = 10,
    max_scan: int = 100,
    cached_only: bool = True,
) -> dict:
    as_of = as_of or datetime.now().date().isoformat()
    if symbols:
        pool = [{"symbol": s if s.endswith(".TW") else f"{s}.TW", "name": s, "sector": ""} for s in symbols]
    else:
        pool = load_pool_symbols(limit=max_scan, cached_only=cached_only)

    results: list[dict] = []
    errors: list[dict] = []
    for row in pool[:max_scan]:
        try:
            results.append(score_symbol(row["symbol"], as_of, row.get("name"), row.get("sector", "")))
        except Exception as exc:
            errors.append({"symbol": row.get("symbol"), "error": str(exc)})
    results.sort(key=lambda r: ({"A": 4, "B": 3, "C": 2, "D": 1}.get(r.get("grade"), 0), r.get("score", 0)), reverse=True)
    return {
        "agent": "Codex 3-6M Long-Term Scorer",
        "as_of": as_of,
        "universe_basis": "active_pool top-100 investable universe; Codex valuation-first quality scoring",
        "cached_only": cached_only,
        "scanned": len(results),
        "errors": errors[:10],
        "picks": results[:limit],
        "all": results,
    }
