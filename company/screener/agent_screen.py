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
    # exposure = 建議總曝險(其餘現金);trail_stop = 組合自波段高點回落多少即轉現金(風險感知核心)
    "BULL_TREND": {"label": "多頭趨勢", "min_prob": 50, "mom_tilt": 35.0, "vol_pen": 0.0,
                   "require_uptrend": False, "picks_factor": 1.0, "exposure": 1.0, "trail_stop": 0.18,
                   "stance": "順勢追動能,可較積極(滿倉,移動停損 18%)"},
    "RANGE": {"label": "區間盤整", "min_prob": 52, "mom_tilt": 10.0, "vol_pen": 20.0,
              "require_uptrend": False, "picks_factor": 0.8, "exposure": 0.7, "trail_stop": 0.12,
              "stance": "偏穩健,挑站穩均線且不過熱者(7 成倉,移動停損 12%)"},
    "BEAR_TREND": {"label": "空頭趨勢", "min_prob": 56, "mom_tilt": 5.0, "vol_pen": 60.0,
                   "require_uptrend": True, "picks_factor": 0.4, "exposure": 0.35, "trail_stop": 0.08,
                   "stance": "轉守,僅 3.5 成倉、嚴格 8% 停損,其餘保留現金"},
    "HIGH_VOL": {"label": "高波動", "min_prob": 56, "mom_tilt": 5.0, "vol_pen": 90.0,
                 "require_uptrend": True, "picks_factor": 0.4, "exposure": 0.35, "trail_stop": 0.08,
                 "stance": "降風險,3.5 成倉、嚴格 8% 停損,部位收斂"},
}


def _ma(v: list[float], w: int):
    return sum(v[-w:]) / w if len(v) >= w else None


def _vol(closes: list[float], w: int = 20) -> float:
    if len(closes) < w + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))]
    avg = sum(rets) / len(rets)
    return math.sqrt(sum((r - avg) ** 2 for r in rets) / len(rets))


def _score10(prob: float, mom: float, vol: float, above_ma20: bool) -> float:
    """0–10 評分(與 Codex/Antigravity 滿分一致)。透明:校準機率為主,動能加分、波動扣分(風險感知)。"""
    s = 5.0
    s += (prob - 50.0) * 0.12               # 校準偏多機率(±6)
    s += max(-0.3, min(0.6, mom)) * 5.0     # 動能
    s -= min(max(vol, 0.0), 0.06) * 25.0    # 波動懲罰(風險感知特色)
    s += 0.8 if above_ma20 else -0.8        # 趨勢
    return round(max(0.0, min(10.0, s)), 1)


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
        ev = score_series(
            closes, vols,
            symbol=sym,
            dates=c.get("dates"),
            foreign_net_buy=c.get("foreign_net_buy"),
            trust_net_buy=c.get("trust_net_buy"),
            margin_purchase=c.get("margin_purchase"),
            short_sale=c.get("short_sale"),
            revenue_yoy=c.get("revenue_yoy")
        )
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

    # 0–10 評分(與另兩家一致)
    for r in scored:
        r["score"] = _score10(r["probability_up"], r["momentum_20"],
                              r["volatility_20"], r["above_ma20"])
    ranked = sorted(scored, key=lambda r: r["score"], reverse=True)

    # regime 風險感知:挑出「本日建議實際進場」的子集(其餘僅供比較=觀望)
    qualified = [r for r in ranked if r["qualifies"]]
    max_picks = max(1, int(round(top_n * policy["picks_factor"])))
    rec_syms = {r["symbol"] for r in qualified[:max_picks]}

    # 與另兩家一致:一律回傳前 top_n(預設 5)檔,逐檔標「建議/觀望」
    picks = ranked[:top_n]
    for r in picks:
        r["recommended"] = r["symbol"] in rec_syms
        bits = []
        if not r["recommended"]:
            bits.append("觀望:未達本日 regime 進場條件")
        if r["above_ma20"]:
            bits.append("站上 20 日均線")
        if r["momentum_20"] > 0.05:
            bits.append(f"20 日動能 +{r['momentum_20']:.0%}")
        if r["calibrated_up_rate"] is not None:
            bits.append(f"校準機率 {r['probability_up']:.0f}%(該桶歷史上漲率 {r['calibrated_up_rate']:.0%})")
        bits += r["reasons"]
        r["why_selected"] = ";".join(bits)

    note = (f"大盤 {policy['label']}:建議實際進場 {len(rec_syms)} 檔、總曝險約 "
            f"{policy['exposure']:.0%}、移動停損 {policy['trail_stop']:.0%};"
            f"以下 {len(picks)} 檔為評分排序,未標『建議』者本日觀望。")

    return {
        "agent": "Claude Agent(校準模型 + 風險感知)",
        "context": {
            "regime": regime, "regime_label": policy["label"], "stance": policy["stance"],
            "breadth_above_ma20": round(breadth, 3), "index_momentum_20": round(idx_mom20, 4),
            "n_universe": tot,
            "target_exposure": policy["exposure"],   # 建議總曝險(其餘現金)
            "trail_stop": policy["trail_stop"],       # 組合移動停損
        },
        "policy": {"min_prob": policy["min_prob"], "max_picks": max_picks},
        "candidates_scored": len(scored), "qualified": len(qualified),
        "recommended_count": len(rec_syms),
        "picks": picks, "note": note, "future_knowledge_used": False,
    }
