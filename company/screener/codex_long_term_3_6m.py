# -*- coding: utf-8 -*-
"""Codex independent 3-6 month long-term potential scorer.

This scorer is intentionally separate from ``potential_3_6m.py``.  It treats the
active pool as an investable top-100 universe, then applies a valuation-first
discipline: a good company can be a watchlist name, but it is not an
accumulation candidate without a real margin of safety.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from company.data.single_stock import load as load_single_stock


ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = ROOT / "model_artifacts" / "active_pool.json"
CACHE_DIR = ROOT / "data_cache"

ASSET_SECTORS = {"金融", "電信", "塑化", "鋼鐵", "航運", "水泥"}


def load_pool_symbols(limit: int = 100, cached_only: bool = True) -> list[dict[str, str]]:
    """Load the investable top pool used by the project.

    The pool is a liquidity/sector-diversified investable universe, not a final
    quality verdict.  Quality is determined by this scorer.
    """
    rows: list[dict[str, str]] = []
    if POOL_PATH.exists():
        try:
            doc = json.loads(POOL_PATH.read_text(encoding="utf-8"))
            rows = doc.get("stocks", [])
        except Exception:
            rows = []
    if not rows:
        from company.data.universe import load_active_universe

        rows = load_active_universe()

    out = []
    for row in rows[:limit]:
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            continue
        code = sym.split(".")[0]
        if cached_only and not (CACHE_DIR / f"{code}_price.csv").exists():
            continue
        out.append({
            "symbol": sym if sym.endswith(".TW") else f"{sym}.TW",
            "name": str(row.get("name") or sym),
            "sector": str(row.get("sector") or ""),
        })
    return out


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


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


def score_symbol(symbol: str, as_of: str, name: str | None = None, sector: str = "") -> dict:
    code = symbol.split(".")[0]
    as_of_ts = pd.Timestamp(as_of)
    start = (as_of_ts - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
    end = as_of_ts.strftime("%Y-%m-%d")
    base = {
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
        "scores": {},
        "reasons": [],
        "warnings": [],
    }

    try:
        data = load_single_stock(code, start, end)
        view = data.view(as_of_ts)
        hist = view.history()
    except Exception as exc:
        base["warnings"] = [f"資料讀取失敗: {exc}"]
        return base
    if hist is None or len(hist) < 80:
        base["warnings"] = ["歷史資料不足，無法評估 3-6M 長投分數。"]
        return base

    closes = hist["close"].astype(float).tolist()
    volumes = hist["volume"].astype(float).tolist() if "volume" in hist else []
    close = closes[-1]
    per = view.per()
    pbr = view.pbr()
    rev_yoy = view.rev_yoy()
    per_df = data._per.loc[data._per.index <= as_of_ts] if len(data._per) else pd.DataFrame()
    hist_per = per_df.tail(250)
    median_per = _safe_float(hist_per["PER"].replace([np.inf, -np.inf], np.nan).dropna().median(), 15.0) if len(hist_per) and "PER" in hist_per else 15.0
    median_pbr = _safe_float(hist_per["PBR"].replace([np.inf, -np.inf], np.nan).dropna().median(), 2.0) if len(hist_per) and "PBR" in hist_per else 2.0
    div_yield = _safe_float(per_df["dividend_yield"].iloc[-1], 0.0) if len(per_df) and "dividend_yield" in per_df else 0.0

    eps = close / per if per and per > 0 else None
    bps = close / pbr if pbr and pbr > 0 else None
    pe_value = eps * median_per if eps is not None and median_per > 0 else None
    pb_value = bps * median_pbr if bps is not None and median_pbr > 0 else None
    asset_like = any(key in sector for key in ASSET_SECTORS) or per is None or per <= 0
    if pe_value and pb_value:
        fair_value = (0.35 * pe_value + 0.65 * pb_value) if asset_like else (0.65 * pe_value + 0.35 * pb_value)
    else:
        fair_value = pe_value or pb_value or close
    fair_value = max(close * 0.35, min(close * 2.2, fair_value))
    margin = (fair_value - close) / fair_value if fair_value > 0 else 0.0

    valuation_score = _score_margin(margin)
    if div_yield >= 5.0:
        valuation_score += 3.0
    valuation_score = min(30.0, valuation_score)

    rev_score = 0.0
    if rev_yoy is not None:
        if rev_yoy >= 0.20:
            rev_score = 12.0
        elif rev_yoy >= 0.0:
            rev_score = 5.0 + rev_yoy / 0.20 * 7.0
        else:
            rev_score = max(0.0, 5.0 + rev_yoy / 0.20 * 5.0)
    rev_series = data._rev_yoy.loc[data._rev_yoy.index <= as_of_ts].tail(4) if len(data._rev_yoy) else pd.Series(dtype=float)
    accel_score = 0.0
    if len(rev_series) >= 3 and _safe_float(rev_series.iloc[-1]) > _safe_float(rev_series.iloc[:-1].mean()):
        accel_score = 5.0
    eps_score = 0.0
    if len(per_df) >= 90 and per and per > 0:
        price_df = pd.DataFrame({"close": closes}, index=hist.index)
        merged = pd.merge(price_df, per_df[["PER", "PBR"]], left_index=True, right_index=True, how="inner")
        merged = merged[(merged["PER"] > 0) & (merged["PBR"] > 0)]
        if len(merged) >= 90:
            eps_series = merged["close"] / merged["PER"]
            if eps_series.tail(10).mean() > eps_series.iloc[-90:-60].mean():
                eps_score = 3.0
    growth_score = min(20.0, rev_score + accel_score + eps_score)

    roe = (pbr / per) if per and per > 0 and pbr and pbr > 0 else 0.0
    roe_score = 0.0
    if roe >= 0.15:
        roe_score = 12.0
    elif roe >= 0.08:
        roe_score = 5.0 + (roe - 0.08) / 0.07 * 7.0
    else:
        roe_score = max(0.0, roe / 0.08 * 5.0)
    margin_chg = view.margin_balance_chg_5()
    leverage_score = 5.0 if margin_chg <= 0 else max(0.0, 5.0 - margin_chg * 2.0)
    dividend_score = min(3.0, div_yield / 5.0 * 3.0) if div_yield > 0 else 0.0
    quality_score = min(20.0, roe_score + leverage_score + dividend_score)

    inst20 = view.inst_net(20)
    avg_vol20 = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1.0
    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else close
    ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else close
    ma120 = float(np.mean(closes[-120:])) if len(closes) >= 120 else ma60
    catalyst_score = 0.0
    if rev_yoy is not None and rev_yoy >= 0.10:
        catalyst_score += 4.0
    if accel_score > 0:
        catalyst_score += 3.0
    if inst20 > 0:
        catalyst_score += 3.0 if inst20 <= avg_vol20 * 0.01 else 5.0
    if close > ma20 > ma60 and ma60 >= ma120:
        catalyst_score += 3.0
    elif close > ma60:
        catalyst_score += 1.5
    catalyst_score = min(15.0, catalyst_score)

    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(max(1, len(closes) - 20), len(closes))]
    vol20 = float(np.std(rets)) if rets else 0.03
    high120 = max(closes[-120:]) if len(closes) >= 120 else max(closes)
    drawdown = close / high120 - 1.0 if high120 > 0 else 0.0
    risk_score = 0.0
    risk_score += 4.0 if vol20 < 0.025 else 2.5 if vol20 < 0.045 else 1.0
    risk_score += 3.0 if drawdown >= -0.15 else 1.5 if drawdown >= -0.30 else 0.5
    risk_score += 3.0 if close >= ma60 else 0.5
    risk_score = min(10.0, risk_score)

    deductions = 0.0
    warnings: list[str] = []
    value_trap = bool(per is not None and per > 0 and per < 10.0 and rev_yoy is not None and rev_yoy < -0.05)
    if value_trap:
        deductions += 15.0
        warnings.append("價值陷阱：低本益比伴隨營收衰退，便宜可能反映基本面惡化。")
    if margin < 0:
        warnings.append("安全邊際不足：目前股價高於保守合理價中位數，適合等待拉回。")
    if close < ma60:
        deductions += 5.0
        warnings.append("中期結構偏弱：股價低於 60 日均線。")

    score = round(max(0.0, min(100.0, valuation_score + growth_score + quality_score + catalyst_score + risk_score - deductions)), 1)
    grade, grade_label, action = _grade_action(score, margin, value_trap)

    reasons: list[str] = []
    if margin >= 0.15:
        reasons.append(f"安全邊際 {margin * 100:.1f}%")
    if rev_yoy is not None and rev_yoy > 0:
        reasons.append(f"營收YoY {rev_yoy * 100:+.1f}%")
    if roe > 0:
        reasons.append(f"ROE proxy {roe * 100:.1f}%")
    if inst20 > 0:
        reasons.append("法人近20日淨買")
    if close > ma20 > ma60:
        reasons.append("中期均線結構偏多")
    if not reasons:
        reasons.append("缺少明確長投催化，先觀察")
    if not warnings:
        warnings.append("未見明顯價值陷阱；仍需追蹤財報與產業循環。")

    buy_low = round(fair_value * 0.75, 1)
    buy_high = round(fair_value * 0.85, 1)
    base.update({
        "name": name or base["name"],
        "sector": sector,
        "score": score,
        "grade": grade,
        "grade_label": grade_label,
        "action": action,
        "close": round(close, 1),
        "fair_value": round(fair_value, 1),
        "fair_range": [round(fair_value * 0.9, 1), round(fair_value * 1.1, 1)],
        "margin_of_safety": round(margin * 100.0, 1),
        "buy_range": f"{buy_low} - {buy_high} 元",
        "review_trigger": "營收YoY連兩月轉弱、跌破季線、或法人連續賣超時重新檢查",
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

    results = []
    errors = []
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
