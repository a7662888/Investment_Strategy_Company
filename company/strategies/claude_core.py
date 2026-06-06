# -*- coding: utf-8 -*-
"""
Claude 明日決策模型 v1 — 「風險優先 · 分散再平衡 · 控回撤」。

三方角力中 Claude 的差異化立場(有實證支撐,非追熱門):
  - Codex:趨勢/多因子追動能。Antigravity:VCP/量能突破。
  - **Claude:不賭選股 alpha**。walk-forward 實證(company/validation):
      * 零選股的「等權定期再平衡」Sharpe 1.50 / MDD-34% / 換手4% = 全面最佳。
      * 模型機率最差(Sharpe1.39/MDD-60%/換手63%) + P2-1 OOS AUC 0.47 → **移除模型機率**。
      * Anti②+④(波動調整動能+產業分散)是最乖的衛星(MDD-46%),但非 Sharpe edge。

模型設計(對齊上述證據):
  1. **Base(主體)**:分散股池等權 + 定期再平衡(報酬風險比與成本最佳的核心)。
  2. **Satellite(衛星,小比例)**:波動調整動能 top-K + 單一產業上限,純為溫和報酬傾斜,
     明確標示「追報酬、非降風險」,且比例受限以壓住換手/回撤。
  3. **Risk overlay(曝險縮放)**:依風險燈號縮放總曝險(GREEN1.0/YELLOW0.5/RED0/BLACK0),
     其餘為現金。這是 Claude 模型的回撤剎車,呼應 Phase 1 風險儀表板。
  4. **不使用模型機率排序**(P2-1+回測雙證無 edge)。

輸出「明日決策」:風險狀態、總曝險、目標配置(每檔權重)、衛星名單、現金%、理由。
純後端(Claude lane);前端/接線交 Antigravity/Codex。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..validation.walkforward_selection import SECTOR, _feat_at, build_close_matrix

MODEL_NAME = "Claude 風險優先分散配置 v1"
EXPOSURE_BY_RISK = {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0, "BLACK": 0.0}


def _risk_state_from_index(C: pd.DataFrame) -> tuple[str, list[str]]:
    """以股池等權合成指數的價格結構判風險(GREEN/YELLOW/RED)。
    BLACK 需隔夜 ADR/期指(app.py 端),此處不判;可由呼叫端 risk_level 覆蓋。"""
    idx = (C / C.iloc[0]).mean(axis=1)
    reasons = []
    if len(idx) < 65:
        return "YELLOW", ["指數資料不足，保守起見半倉"]
    last = float(idx.iloc[-1])
    ma20 = float(idx.iloc[-20:].mean())
    ma60 = float(idx.iloc[-60:].mean())
    chg = float(idx.iloc[-1] / idx.iloc[-2] - 1.0) if len(idx) > 1 else 0.0
    rets = idx.pct_change().dropna().iloc[-20:]
    vol = float(rets.std()) if len(rets) > 1 else 0.0

    if chg <= -0.03 or (last < ma60 and ma20 < ma60):
        reasons.append(f"指數單日 {chg*100:+.1f}% 或跌破季線且均線空頭排列 → 停止新買進")
        return "RED", reasons
    if last < ma20 or vol >= 0.025:
        reasons.append(f"指數收於 20 日線下或波動偏高(20日σ {vol*100:.1f}%) → 減半觀察")
        return "YELLOW", reasons
    reasons.append("指數站穩均線、波動正常 → 正常配置")
    return "GREEN", reasons


def claude_decision(
    datasets: dict, as_of: pd.Timestamp,
    risk_level: Optional[str] = None,
    satellite_frac: float = 0.30,
    k_satellite: int = 5,
    sector_cap: int = 2,
) -> dict:
    """產出 Claude 明日決策。risk_level 若給(如 app.py 的 BLACK)則覆蓋內部判定。"""
    C = build_close_matrix(datasets, as_of)
    syms = list(C.columns)
    t = len(C) - 1

    # 風險狀態與曝險
    if risk_level:
        state = risk_level.upper()
        risk_reasons = [f"採用外部風險燈號 {state}"]
    else:
        state, risk_reasons = _risk_state_from_index(C)
    exposure = EXPOSURE_BY_RISK.get(state, 0.5)

    # Base:分散等權
    base_w = {s: 1.0 / len(syms) for s in syms}

    # Satellite:波動調整動能 top-K(產業上限)
    feats = {}
    for s in syms:
        f = _feat_at(C[s], t)
        if f is not None:
            feats[s] = f[2]  # vol_adj_mom
    ranked = sorted(feats.items(), key=lambda x: x[1], reverse=True)
    sat_picks, used = [], {}
    for s, _ in ranked:
        sec = SECTOR.get(s, s)
        if used.get(sec, 0) >= sector_cap:
            continue
        sat_picks.append(s)
        used[sec] = used.get(sec, 0) + 1
        if len(sat_picks) >= k_satellite:
            break
    sat_w = {s: 1.0 / len(sat_picks) for s in sat_picks} if sat_picks else {}

    # 混合 base + satellite,再乘總曝險
    target = {}
    for s in syms:
        w = (1 - satellite_frac) * base_w.get(s, 0.0) + satellite_frac * sat_w.get(s, 0.0)
        target[s] = w * exposure
    cash = 1.0 - sum(target.values())

    # 名稱對照(若有)
    try:
        from ..screener.market_screener import NAME_MAP
    except Exception:
        NAME_MAP = {}

    holdings = [
        {"symbol": s, "name": NAME_MAP.get(s, s), "weight": round(target[s], 4),
         "sector": SECTOR.get(s, "—"), "satellite": s in sat_w}
        for s in sorted(target, key=lambda x: -target[x]) if target[s] > 1e-9
    ]

    rationale = risk_reasons + [
        f"Base(分散等權再平衡)占 {int((1-satellite_frac)*100)}%、Satellite(波動調整動能傾斜)占 {int(satellite_frac*100)}%",
        "不使用模型機率排序(P2-1 OOS AUC 0.47 + walk-forward 證實無 edge)",
        f"總曝險 {exposure*100:.0f}%(風險狀態 {state})，其餘 {cash*100:.0f}% 現金作回撤緩衝",
    ]

    return {
        "model": MODEL_NAME,
        "as_of": str(pd.Timestamp(as_of).date()),
        "risk_state": state,
        "exposure": round(exposure, 3),
        "cash_pct": round(max(0.0, cash), 4),
        "satellite_picks": [{"symbol": s, "name": NAME_MAP.get(s, s),
                             "sector": SECTOR.get(s, "—")} for s in sat_picks],
        "holdings": holdings,
        "rationale": rationale,
        "params": {"satellite_frac": satellite_frac, "k_satellite": k_satellite,
                   "sector_cap": sector_cap, "rebalance": "monthly(21d)"},
        "future_knowledge_used": False,
    }
