# -*- coding: utf-8 -*-
"""價值引擎每週重篩（TASK-018）。

背景：價值論點卡是凍結快照（shadow 驗證需不可竄改），但先前**沒有任何排程重新評估**，
導致清單永遠停在 2026-06-26，且 4 檔 accumulate 有 3 檔早已漲出買進區間仍顯示可買。

本模組每週重跑「規則層」（不需 LLM）：
  1. 以最新價格重算估值位階（個股 PE/PB 百分位、ETF 還原權值價百分位）
  2. 套 tw_value_method v2.2 規則重新判定 action（含金控／景氣／零售 sector schema）
  3. 與現行凍結卡比對 → 只有「判定改變」才凍新卡（revision_of），避免帳本無謂膨脹

基本面（ROE／margins／負債／OCF）取自 data/value_fundamentals.json 季度快照；
季報更新頻率本就是每季，不需每週重抓。
"""
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FUNDAMENTALS = ROOT / "data" / "value_fundamentals.json"

# v2.2 sector schema
FINHOLD = {"2881", "2882", "2891", "2886", "2884"}
CYCLICAL = {"1301", "2002", "1101", "2603"}
RETAIL = {"1216", "2912"}
TH = dict(roe_ttm=12.0, gpm=20.0, opm=8.0, eq=0.6, debt=60.0,
          cyc_pb=25.0, cyc_roe_floor=5.0, ret_roe=12.0, ret_ocfni=0.8, ret_pb=50.0)


# ---------- 資料取得 ----------
def _yahoo_history(symbol: str, days: int = 400) -> list[dict]:
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)
    params = {"period1": str(int(start.timestamp())), "period2": str(int(end.timestamp())),
              "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode())
    res = (payload.get("chart") or {}).get("result")
    if not res:
        return []
    r0 = res[0]
    ts = r0.get("timestamp") or []
    q = r0["indicators"]["quote"][0]
    adj = ((r0.get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    rows = []
    for i, t in enumerate(ts):
        if q["close"][i] is None:
            continue
        rows.append({"date": datetime.fromtimestamp(t).date().isoformat(),
                     "close": float(q["close"][i]),
                     "adj_close": float(adj[i]) if i < len(adj) and adj[i] is not None else float(q["close"][i])})
    return rows


def _finmind_per(symbol: str, years: int = 3, attempts: int = 3) -> list[dict]:
    """個股 PER/PBR 歷史（估值百分位用）。

    FinMind 免費層會限流；若這裡靜默回空，呼叫端會把「查不到估值」誤判成「估值偏貴→watch」，
    在每週自動排程中可能凍下錯誤的卡。故加重試與節流，並讓呼叫端能區分「真的沒資料」。
    """
    token = os.environ.get("FINMIND_TOKEN", "")
    code = symbol.replace(".TW", "").replace(".TWO", "")
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    params = {"dataset": "TaiwanStockPER", "data_id": code, "start_date": start}
    if token:
        params["token"] = token
    url = "https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(params)
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = (json.loads(r.read().decode()) or {}).get("data") or []
            if data:
                return data
        except Exception:
            pass
        time.sleep(2.0 * (i + 1))          # 退避後重試
    return []


# ---------- 指標 ----------
def _ttm(quarterly: list[dict], key: str):
    vals = [q.get(key) for q in quarterly[-4:] if q.get(key) is not None]
    return sum(vals) if len(vals) >= 3 else None


def _avg(quarterly: list[dict], key: str):
    vals = [q.get(key) for q in quarterly[-4:] if q.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _pct_rank(series: list[float], value: float):
    if not series or value is None:
        return None
    return round(sum(1 for v in series if v <= value) / len(series) * 100, 1)


def _pct_rank_quantiles(quantiles: list[float], value: float):
    """Approximate a percentile from compact 0,5,...,100 percentile points."""
    if not quantiles or value is None:
        return None
    if value <= quantiles[0]:
        return 0.0
    if value >= quantiles[-1]:
        return 100.0
    step = 100.0 / (len(quantiles) - 1)
    for idx in range(1, len(quantiles)):
        if value <= quantiles[idx]:
            low, high = quantiles[idx - 1], quantiles[idx]
            fraction = 0.0 if high == low else (value - low) / (high - low)
            return round((idx - 1 + fraction) * step, 1)
    return 100.0


def _at_pct(sorted_vals: list[float], p: float):
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[idx]


# ---------- 核心 ----------
def evaluate(symbol: str, fundamentals: dict) -> dict:
    """回傳最新判定：action／位階／買進區間／依據。純規則、可重現。"""
    code = symbol.replace(".TWO", "").replace(".TW", "")
    info = fundamentals.get(code) or {}
    is_etf = bool(info.get("is_etf")) or code.startswith("00")
    rows = _yahoo_history(symbol)
    if not rows:
        return {"symbol": symbol, "error": "no_price"}
    cur_raw, cur_adj = rows[-1]["close"], rows[-1]["adj_close"]
    as_of = rows[-1]["date"]
    closes = [r["close"] for r in rows]
    ma20 = statistics.mean(closes[-20:]) if len(closes) >= 20 else None
    ma60 = statistics.mean(closes[-60:]) if len(closes) >= 60 else None
    momentum20 = (cur_raw / closes[-21] - 1.0) if len(closes) >= 21 and closes[-21] else None
    out = {"symbol": symbol, "name": info.get("name") or code, "as_of": as_of,
           "price": cur_raw, "is_etf": is_etf, "reasons": [],
           "fundamentals_complete": bool(info.get("completeness", {}).get("complete", info.get("quarterly"))),
           "ma20": round(ma20, 4) if ma20 is not None else None,
           "ma60": round(ma60, 4) if ma60 is not None else None,
           "momentum20": round(momentum20, 6) if momentum20 is not None else None}

    if is_etf:
        adjs = sorted(r["adj_close"] for r in rows)
        p = _pct_rank(adjs, cur_adj)
        ratio = cur_raw / cur_adj if cur_adj else 1.0
        lo, hi = round(_at_pct(adjs, 20) * ratio, 2), round(_at_pct(adjs, 40) * ratio, 2)
        out.update(valuation_pct=p, entry_range=[lo, hi])
        out["action"] = "accumulate" if p is not None and p <= 40 else ("hold" if p is not None and p <= 70 else "watch")
        out["reasons"].append(f"還原權值價位階：現價 {cur_raw} 位於近一年第 {p} 百分位；便宜區 {lo}–{hi}")
        return out

    # 個股：估值百分位（FinMind PER/PBR），基本面沿用季度快照
    cached = info.get("valuation") or {}
    pe_quantiles = cached.get("pe_quantiles_5pct") or []
    pb_quantiles = cached.get("pb_quantiles_5pct") or []
    valuation_date = cached.get("date")
    cached_available = bool(valuation_date and (pe_quantiles or pb_quantiles))
    if cached_available:
        reference = next((row for row in reversed(rows) if row["date"] <= valuation_date), None)
        price_ratio = cur_raw / reference["close"] if reference and reference["close"] else 1.0
        cached_pe = float(cached["pe"]) if cached.get("pe") not in (None, 0) else None
        cached_pb = float(cached["pb"]) if cached.get("pb") not in (None, 0) else None
        cur_pe = cached_pe * price_ratio if cached_pe else None
        cur_pb = cached_pb * price_ratio if cached_pb else None
        pes, pbs = pe_quantiles, pb_quantiles
        pe_pct = _pct_rank_quantiles(pe_quantiles, cur_pe) if cur_pe else None
        pb_pct = _pct_rank_quantiles(pb_quantiles, cur_pb) if cur_pb else None
        out["valuation_as_of"] = valuation_date
    else:
        per_rows = _finmind_per(symbol)
        pes = [float(r["PER"]) for r in per_rows if r.get("PER") and float(r["PER"]) > 0]
        pbs = [float(r["PBR"]) for r in per_rows if r.get("PBR") and float(r["PBR"]) > 0]
        cur_pe = pes[-1] if pes else None
        cur_pb = pbs[-1] if pbs else None
        pe_pct = _pct_rank(sorted(pes), cur_pe) if cur_pe else None
        pb_pct = _pct_rank(sorted(pbs), cur_pb) if cur_pb else None
    if not pes and not pbs:
        out["data_incomplete"] = "valuation_unavailable"
        out["action"] = None          # 交由呼叫端跳過，不得改判定
        out["reasons"].append("估值資料取得失敗（FinMind 限流或無資料）→ 本次不重新判定")
        return out
    out.update(pe=cur_pe, pb=cur_pb, pe_pct=pe_pct, pb_pct=pb_pct)

    qs = info.get("quarterly") or []
    roe = _ttm(qs, "roe")
    gpm, opm, eq = _avg(qs, "gross_profit_margin"), _avg(qs, "operating_income_margin"), _avg(qs, "earnings_quality")
    debt = next((q.get("debt_ratio") for q in reversed(qs) if q.get("debt_ratio") is not None), None)
    out.update(roe_ttm=roe)

    # --- 硬篩（含 sector schema）---
    fails = []
    if code in FINHOLD:
        if roe is None or roe < TH["roe_ttm"]:
            fails.append(f"金控 ROE_ttm {roe}<{TH['roe_ttm']}")
        valuation_pct, basis = pb_pct, "PBR"          # 金控走 PBR 主
    elif code in CYCLICAL:
        cheap = pb_pct is not None and pb_pct <= TH["cyc_pb"]
        trough = roe is not None and roe < TH["cyc_roe_floor"]
        ever_pos = any((q.get("roe") or 0) > 0 for q in qs)
        if not (cheap and trough and ever_pos):
            fails.append("景氣子軌未達（需 PB 低位＋ROE 谷底＋歷史有獲利力）")
        valuation_pct, basis = pb_pct, "PBR(景氣)"
    elif code in RETAIL:
        if roe is None or roe < TH["ret_roe"]:
            fails.append(f"零售 ROE {roe}<{TH['ret_roe']}")
        if eq is not None and eq < TH["ret_ocfni"]:
            fails.append(f"零售 OCF/NI {eq:.2f}<{TH['ret_ocfni']}")
        valuation_pct, basis = pb_pct, "PBR(零售)"
    else:
        if roe is None or roe < TH["roe_ttm"]:
            fails.append(f"ROE_ttm {roe}<{TH['roe_ttm']}")
        if gpm is not None and gpm < TH["gpm"]:
            fails.append(f"GPM {gpm:.1f}<{TH['gpm']}")
        if opm is not None and opm < TH["opm"]:
            fails.append(f"OPM {opm:.1f}<{TH['opm']}")
        if debt is not None and debt > TH["debt"]:
            fails.append(f"負債比 {debt:.1f}>{TH['debt']}")
        if eq is not None and eq < TH["eq"]:
            fails.append(f"OCF/NI {eq:.2f}<{TH['eq']}")
        valuation_pct, basis = pe_pct, "PER"

    out.update(quality_pass=not fails, failed=fails, valuation_basis=basis, valuation_pct=valuation_pct)

    # --- 估值 → action ---
    if fails:
        out["action"] = "avoid" if (valuation_pct or 0) > 80 else "watch"
        out["reasons"].append("品質硬篩未過：" + "；".join(fails))
    elif valuation_pct is None:
        out["action"] = "watch"
        out["reasons"].append("估值百分位無資料 → 依 §5.1 上限 watch")
    elif valuation_pct <= 40:
        out["action"] = "accumulate"
    elif valuation_pct <= 70:
        out["action"] = "hold"
    else:
        out["action"] = "watch"

    # 買進區間：估值 P20–P40 對應價位（以現價/現值等比換算）
    src = pbs if basis.startswith("PBR") else pes
    cur_v = cur_pb if basis.startswith("PBR") else cur_pe
    if src and cur_v:
        s = sorted(src)
        out["entry_range"] = [round(cur_raw * _at_pct(s, 20) / cur_v, 2),
                              round(cur_raw * _at_pct(s, 40) / cur_v, 2)]
    if valuation_pct is not None:
        out["reasons"].append(f"{basis} 位階：現值 {cur_v} 位於近 3 年第 {valuation_pct} 百分位")
    if roe is not None:
        out["reasons"].append(f"ROE_ttm {roe:.1f}%")
    return out


def rescreen_all(symbols: list[str], fundamentals: dict | None = None) -> list[dict]:
    fundamentals = fundamentals if fundamentals is not None else (
        json.load(open(FUNDAMENTALS, encoding="utf-8")) if FUNDAMENTALS.exists() else {}
    )
    results = []
    for i, sym in enumerate(symbols):
        if i:
            code = sym.replace(".TWO", "").replace(".TW", "")
            cached = (fundamentals.get(code) or {}).get("valuation") or {}
            time.sleep(0.1 if cached.get("pe_quantiles_5pct") or cached.get("pb_quantiles_5pct") else 1.5)
        try:
            results.append(evaluate(sym, fundamentals))
        except Exception as exc:  # 單檔失敗不得中斷整批
            results.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
    return results
