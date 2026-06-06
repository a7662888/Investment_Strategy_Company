# -*- coding: utf-8 -*-
"""
三方策略成效計分 — 從 strategy_archive.json 的每日績效算可比指標。

對齊 Claude 立場:別只看累積報酬(survivor/多頭灌水),要看**風險調整後**。
每個 agent 由其每日 avg_return 序列算:
  累積報酬、年化、Sharpe、最大回撤(MDD)、勝率(上漲日比例)、天數。

純後端;run_scorecard.py 與週排程 Action 會呼叫。
"""
from __future__ import annotations

import json
from pathlib import Path

ANN = 252


def _metrics(returns: list[float]) -> dict:
    n = len(returns)
    if n == 0:
        return {"n_days": 0, "cum_return": None, "cagr": None, "sharpe": None,
                "max_drawdown": None, "win_rate": None}
    eq = []
    v = 1.0
    for r in returns:
        v *= (1.0 + r)
        eq.append(v)
    cum = eq[-1] - 1.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n if n > 1 else 0.0
    sd = var ** 0.5
    sharpe = (mean / sd * (ANN ** 0.5)) if sd > 0 else None
    peak = eq[0]
    mdd = 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    win = sum(1 for r in returns if r > 0) / n
    # 年化僅在樣本足夠(>=20 日)才有意義;否則 (1+r)^(252/n) 會放大成天文數字。
    cagr = ((eq[-1]) ** (ANN / n) - 1.0) if n >= 20 else None
    return {"n_days": n, "cum_return": cum, "cagr": cagr, "sharpe": sharpe,
            "max_drawdown": mdd, "win_rate": win}


def score_agents(archive_path: str = "model_artifacts/strategy_archive.json") -> dict:
    a = json.loads(Path(archive_path).read_text(encoding="utf-8"))
    history = a.get("daily_performance_history", [])
    history = sorted(history, key=lambda r: r.get("eval_date", ""))

    by_agent: dict[str, list[float]] = {}
    dates: list[str] = []
    for rec in history:
        dates.append(rec.get("eval_date", ""))
        for ag in rec.get("agents", []):
            name = ag.get("agent", "?")
            ar = ag.get("avg_return")
            if ar is not None:
                by_agent.setdefault(name, []).append(float(ar))

    scores = {name: _metrics(rets) for name, rets in by_agent.items()}
    span = f"{dates[0]} ~ {dates[-1]}" if dates else "—"
    return {"n_records": len(history), "span": span, "agents": scores}
