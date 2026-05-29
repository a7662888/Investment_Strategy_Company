# -*- coding: utf-8 -*-
"""
市場感知選股器:依「今天大盤」regime 從股池選出潛力股(含理由)。

對外主要函式:
  market_context(datasets, as_of) -> 大盤狀態(regime / 廣度 / 指數動能)
  screen(datasets, as_of, top_n)  -> {context, picks};picks 已依 regime 調整並排序
"""
from __future__ import annotations

import pandas as pd

from ..allocator import regime as regime_mod
from ..data.single_stock import StockData
from ..model.score import score_series

NAME_MAP = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2303": "聯電",
    "3711": "日月光", "2002": "中鋼", "1301": "台塑", "1303": "南亞", "2412": "中華電",
    "3045": "台灣大", "2881": "富邦金", "2882": "國泰金", "2891": "中信金", "2603": "長榮",
    "2609": "陽明", "2615": "萬海", "2327": "國巨", "2379": "瑞昱", "3034": "聯詠",
}

# 每個 regime 的選股策略
REGIME_POLICY = {
    "BULL_TREND": {"label": "多頭趨勢", "min_prob": 50, "momentum_tilt": 35.0,
                   "vol_penalty": 0.0, "require_uptrend": False, "max_picks_factor": 1.0,
                   "stance": "順勢追動能,可較積極"},
    "RANGE": {"label": "區間盤整", "min_prob": 52, "momentum_tilt": 10.0,
              "vol_penalty": 20.0, "require_uptrend": False, "max_picks_factor": 0.8,
              "stance": "偏穩健,挑站穩均線且不過熱者"},
    "BEAR_TREND": {"label": "空頭趨勢", "min_prob": 56, "momentum_tilt": 5.0,
                   "vol_penalty": 60.0, "require_uptrend": True, "max_picks_factor": 0.4,
                   "stance": "轉守,只留少數逆勢偏強且低波動者,寧可保留現金"},
    "HIGH_VOL": {"label": "高波動", "min_prob": 56, "momentum_tilt": 5.0,
                 "vol_penalty": 90.0, "require_uptrend": True, "max_picks_factor": 0.4,
                 "stance": "降風險,嚴篩低波動,部位收斂"},
}


def _name(sym: str) -> str:
    return NAME_MAP.get(sym, sym)


def build_index(datasets: dict[str, StockData], as_of: pd.Timestamp) -> pd.Series:
    """以股池等權正規化合成大盤指數(只到 as_of)。"""
    series = {}
    for sym, d in datasets.items():
        h = d.prices.history(sym, as_of)
        if len(h) > 0:
            series[sym] = h["close"]
    if not series:
        return pd.Series(dtype=float)
    df = pd.DataFrame(series).sort_index().ffill().dropna(how="all")
    norm = df.div(df.iloc[0])
    return norm.mean(axis=1)


def market_context(datasets: dict[str, StockData], as_of: pd.Timestamp) -> dict:
    idx = build_index(datasets, as_of)
    regime = "RANGE"
    if len(idx) >= 60:
        labels = regime_mod.classify(idx)
        if len(labels):
            regime = str(labels.iloc[-1])
    above, tot = 0, 0
    for sym, d in datasets.items():
        h = d.prices.history(sym, as_of, lookback=20)
        if len(h) >= 20:
            tot += 1
            if h["close"].iloc[-1] > h["close"].mean():
                above += 1
    breadth = above / tot if tot else 0.0
    idx_mom20 = float(idx.iloc[-1] / idx.iloc[-21] - 1) if len(idx) > 21 else 0.0
    return {
        "as_of": str(as_of.date()),
        "regime": regime,
        "regime_label": REGIME_POLICY.get(regime, {}).get("label", regime),
        "stance": REGIME_POLICY.get(regime, {}).get("stance", ""),
        "breadth_above_ma20": round(breadth, 3),
        "index_momentum_20": round(idx_mom20, 4),
        "n_universe": tot,
    }


def _score_one(sym: str, data: StockData, as_of: pd.Timestamp, policy: dict) -> dict | None:
    h = data.prices.history(sym, as_of)
    if len(h) < 130:
        return None
    closes = h["close"].astype(float).tolist()
    vols = h["volume"].astype(float).tolist()
    
    days = h.index
    fn_series = data._foreign_net.reindex(days).fillna(0.0).tolist()
    tn_series = data._trust_net.reindex(days).fillna(0.0).tolist()
    
    if len(data._margin) > 0 and "MarginPurchaseTodayBalance" in data._margin.columns:
        mp_series = data._margin["MarginPurchaseTodayBalance"].reindex(days).ffill().fillna(0.0).tolist()
        ss_series = data._margin["ShortSaleTodayBalance"].reindex(days).ffill().fillna(0.0).tolist()
    else:
        mp_series = [0.0] * len(days)
        ss_series = [0.0] * len(days)
        
    ry_series = data._rev_yoy.reindex(days).ffill().fillna(0.0).tolist()
    dates_str = [d.strftime("%Y-%m-%d") for d in days]
    
    ev = score_series(
        closes, vols,
        symbol=sym,
        dates=dates_str,
        foreign_net_buy=fn_series,
        trust_net_buy=tn_series,
        margin_purchase=mp_series,
        short_sale=ss_series,
        revenue_yoy=ry_series
    )
    if ev is None:
        return None
    last = closes[-1]
    mom20 = last / closes[-21] - 1 if len(closes) > 21 else 0.0
    ma20 = sum(closes[-20:]) / 20
    above_ma20 = last > ma20
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    avg = sum(rets) / len(rets)
    vol20 = (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5

    prob = ev["probability_up"]
    # regime 調整後的篩選分數
    screen_score = prob + policy["momentum_tilt"] * mom20 - policy["vol_penalty"] * vol20
    qualifies = prob >= policy["min_prob"] and (above_ma20 or not policy["require_uptrend"])

    cal = ev.get("calibrated")
    return {
        "symbol": sym, "name": _name(sym), "close": round(last, 1),
        "probability_up": prob, "screen_score": round(screen_score, 2),
        "momentum_20": round(mom20, 4), "above_ma20": above_ma20,
        "volatility_20": round(vol20, 4), "qualifies": qualifies,
        "calibrated_up_rate": cal.get("empirical_up_rate") if cal else None,
        "model_reasons": ev.get("reasons", [])[:2],
    }


def screen(datasets: dict[str, StockData], as_of: pd.Timestamp, top_n: int = 5) -> dict:
    ctx = market_context(datasets, as_of)
    policy = REGIME_POLICY.get(ctx["regime"], REGIME_POLICY["RANGE"])

    scored = []
    for sym, data in datasets.items():
        row = _score_one(sym, data, as_of, policy)
        if row is not None:
            scored.append(row)

    qualified = [r for r in scored if r["qualifies"]]
    qualified.sort(key=lambda r: r["screen_score"], reverse=True)
    max_picks = max(1, int(round(top_n * policy["max_picks_factor"])))
    picks = qualified[:max_picks]

    for r in picks:
        bits = [f"{ctx['regime_label']}下{policy['stance']}"]
        if r["above_ma20"]:
            bits.append("站上 20 日均線")
        if r["momentum_20"] > 0.05:
            bits.append(f"20 日動能 +{r['momentum_20']:.0%}")
        if r["calibrated_up_rate"] is not None:
            bits.append(f"模型機率 {r['probability_up']:.0f}%(該桶歷史上漲率 {r['calibrated_up_rate']:.0%})")
        bits += r["model_reasons"]
        r["why_selected"] = ";".join(bits)

    note = ""
    if ctx["regime"] in ("BEAR_TREND", "HIGH_VOL"):
        note = f"⚠️ 大盤為{ctx['regime_label']},轉守:僅選出 {len(picks)} 檔逆勢偏強且低波動者,其餘建議保留現金。"
    elif len(picks) == 0:
        note = "今日無標的通過門檻,建議觀望。"

    return {
        "context": ctx,
        "policy": {"min_prob": policy["min_prob"], "stance": policy["stance"],
                   "max_picks": max_picks},
        "candidates_scored": len(scored),
        "qualified": len(qualified),
        "picks": picks,
        "note": note,
        "future_knowledge_used": False,
    }
