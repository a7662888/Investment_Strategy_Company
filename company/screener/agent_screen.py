# -*- coding: utf-8 -*-
"""
Claude Agent 選股核心(純標準函式庫,可被 Render app.py 直接引用,零新增相依)。

與 Codex `/api/discover`(啟發式趨勢/動能/波動排序)差異化:
  * 以 **校準 logistic 模型**(company.model.score)算偏多機率,並附「該機率桶歷史樣本外上漲率」當依據。
  * **風險感知**:依大盤 regime(多頭/空頭/盤整/高波動)調整門檻與選股數;空頭/高波動自動轉守、傾向保留現金。
  * **可解釋**:每檔附校準機率 + 因子貢獻理由 + 為何在當下 regime 被選。
資料由呼叫端(app.py 的 fetch_history)提供,本模組不自行抓資料、不依賴 pandas/numpy。
"""
from __future__ import annotations

import math

from ..model.score import score_series

REGIME_POLICY = {
    "BULL_TREND": {"label": "多頭趨勢", "min_prob": 50, "mom_tilt": 35.0, "vol_pen": 0.0,
                   "require_uptrend": False, "picks_factor": 1.0, "stance": "順勢追動能,可較積極"},
    "RANGE": {"label": "區間盤整", "min_prob": 52, "mom_tilt": 10.0, "vol_pen": 20.0,
              "require_uptrend": False, "picks_factor": 0.8, "stance": "偏穩健,挑站穩均線且不過熱者"},
    "BEAR_TREND": {"label": "空頭趨勢", "min_prob": 56, "mom_tilt": 5.0, "vol_pen": 60.0,
                   "require_uptrend": True, "picks_factor": 0.4, "stance": "轉守,只留少數逆勢偏強且低波動者,寧可保留現金"},
    "HIGH_VOL": {"label": "高波動", "min_prob": 56, "mom_tilt": 5.0, "vol_pen": 90.0,
                 "require_uptrend": True, "picks_factor": 0.4, "stance": "降風險,嚴篩低波動,部位收斂"},
}


def _ma(v: list[float], w: int):
    return sum(v[-w:]) / w if len(v) >= w else None


def _vol(closes: list[float], w: int = 20) -> float:
    if len(closes) < w + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))]
    avg = sum(rets) / len(rets)
    return math.sqrt(sum((r - avg) ** 2 for r in rets) / len(rets))


def detect_regime(index_closes: list[float]) -> str:
    """純 stdlib regime 判讀:高波動 > 多頭/空頭趨勢 > 盤整。"""
    if len(index_closes) < 60:
        return "RANGE"
    last = index_closes[-1]
    ma60 = _ma(index_closes, 60)
    slope = (ma60 - _ma(index_closes[:-20], 60)) if (ma60 and len(index_closes) > 80) else 0.0
    vol20 = _vol(index_closes, 20)
    # 波動分位(最近 120 日)
    vols = [_vol(index_closes[: i + 1], 20) for i in range(max(20, len(index_closes) - 120), len(index_closes))]
    pct = (sum(1 for x in vols if x <= vol20) / len(vols)) if vols else 0.5
    if pct >= 0.8:
        return "HIGH_VOL"
    above = ma60 is not None and last > ma60
    if above and slope > 0:
        return "BULL_TREND"
    if (ma60 is not None and last < ma60) and slope < 0:
        return "BEAR_TREND"
    return "RANGE"


def _equal_weight_index(candidates: dict[str, dict]) -> list[float]:
    series = [c["closes"] for c in candidates.values() if len(c.get("closes", [])) >= 60]
    if not series:
        return []
    n = min(len(s) for s in series)
    series = [s[-n:] for s in series]
    idx = []
    for t in range(n):
        idx.append(sum(s[t] / s[0] for s in series) / len(series))
    return idx


def claude_screen(candidates: dict[str, dict], top_n: int = 5,
                  market_index_closes: list[float] | None = None,
                  names: dict[str, str] | None = None) -> dict:
    """
    candidates: {symbol: {"closes": [...], "volumes": [...](選填)}}
    回傳 {context, policy, picks, note, agent}。
    """
    names = names or {}
    index_closes = market_index_closes or _equal_weight_index(candidates)
    regime = detect_regime(index_closes)
    policy = REGIME_POLICY.get(regime, REGIME_POLICY["RANGE"])

    # 大盤廣度
    above, tot = 0, 0
    for c in candidates.values():
        closes = c.get("closes", [])
        ma20 = _ma(closes, 20)
        if ma20:
            tot += 1
            if closes[-1] > ma20:
                above += 1
    breadth = above / tot if tot else 0.0
    idx_mom20 = (index_closes[-1] / index_closes[-21] - 1) if len(index_closes) > 21 else 0.0

    scored = []
    for sym, c in candidates.items():
        closes = c.get("closes", [])
        vols = c.get("volumes")
        if len(closes) < 130:
            continue
        ev = score_series(closes, vols)
        if ev is None:
            continue
        last = closes[-1]
        mom20 = last / closes[-21] - 1 if len(closes) > 21 else 0.0
        ma20 = _ma(closes, 20) or last
        above_ma20 = last > ma20
        vol20 = _vol(closes, 20)
        prob = ev["probability_up"]
        sscore = prob + policy["mom_tilt"] * mom20 - policy["vol_pen"] * vol20
        qualifies = prob >= policy["min_prob"] and (above_ma20 or not policy["require_uptrend"])
        cal = ev.get("calibrated")
        scored.append({
            "symbol": sym, "name": names.get(sym, sym), "close": round(last, 1),
            "probability_up": prob, "screen_score": round(sscore, 2),
            "momentum_20": round(mom20, 4), "volatility_20": round(vol20, 4),
            "above_ma20": above_ma20, "qualifies": qualifies,
            "calibrated_up_rate": cal.get("empirical_up_rate") if cal else None,
            "reasons": ev.get("reasons", [])[:2],
        })

    qualified = sorted([s for s in scored if s["qualifies"]],
                       key=lambda r: r["screen_score"], reverse=True)
    max_picks = max(1, int(round(top_n * policy["picks_factor"])))
    picks = qualified[:max_picks]
    for r in picks:
        bits = [f"{policy['label']}下{policy['stance']}"]
        if r["above_ma20"]:
            bits.append("站上 20 日均線")
        if r["momentum_20"] > 0.05:
            bits.append(f"20 日動能 +{r['momentum_20']:.0%}")
        if r["calibrated_up_rate"] is not None:
            bits.append(f"校準機率 {r['probability_up']:.0f}%(該桶歷史上漲率 {r['calibrated_up_rate']:.0%})")
        bits += r["reasons"]
        r["why_selected"] = ";".join(bits)

    note = ""
    if regime in ("BEAR_TREND", "HIGH_VOL"):
        note = f"⚠️ 大盤為{policy['label']},Claude Agent 轉守:僅選 {len(picks)} 檔逆勢偏強且低波動者,其餘建議保留現金。"
    elif not picks:
        note = "今日無標的通過 Claude Agent 門檻,建議觀望。"

    return {
        "agent": "Claude Agent(校準模型 + 風險感知)",
        "context": {
            "regime": regime, "regime_label": policy["label"], "stance": policy["stance"],
            "breadth_above_ma20": round(breadth, 3), "index_momentum_20": round(idx_mom20, 4),
            "n_universe": tot,
        },
        "policy": {"min_prob": policy["min_prob"], "max_picks": max_picks},
        "candidates_scored": len(scored), "qualified": len(qualified),
        "picks": picks, "note": note, "future_knowledge_used": False,
    }
