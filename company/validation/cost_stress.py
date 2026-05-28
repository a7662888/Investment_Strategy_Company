# -*- coding: utf-8 -*-
"""
能力④ — 交易成本加倍壓力測試(延伸防線④)。

真實成本會浮動(滑價在崩盤放大、手續費折數可能談不到)。一個穩健的策略,
edge 不該在成本小幅上升時就蒸發。本測試用成本乘數重跑,觀察績效衰減:
  * 0.5x:成本減半(樂觀)
  * 1.0x:基準
  * 2.0x:成本加倍(壓力)
  * 3.0x:極端

判讀:若策略在 2x 成本下 Sharpe 由正轉負或報酬大幅崩塌 → 脆弱,實盤風險高。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

import pandas as pd

from ..audit import metrics as M
from ..data.interfaces import Dataset
from ..sandbox.circuit_breaker import CircuitBreaker
from ..sandbox.costs import TaiwanCostModel
from ..sandbox.engine import BacktestEngine

DEFAULT_MULTIPLIERS = (0.5, 1.0, 2.0, 3.0)


def _scaled(costs: TaiwanCostModel, m: float) -> TaiwanCostModel:
    return replace(
        costs,
        fee_rate=costs.fee_rate * m,
        tax_rate=costs.tax_rate * m,
        slippage_bps=costs.slippage_bps * m,
    )


def run_cost_stress(
    dataset: Dataset,
    base_costs: TaiwanCostModel,
    build_strategy: Callable[[], object],
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_capital: float = 1_000_000.0,
    multipliers=DEFAULT_MULTIPLIERS,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> dict:
    out = {}
    for m in multipliers:
        engine = BacktestEngine(
            dataset, _scaled(base_costs, m), initial_capital, circuit_breaker=circuit_breaker
        )
        res = engine.run(build_strategy(), start, end)
        out[m] = M.compute(res)
    return out


def to_markdown(name: str, stress: dict) -> str:
    lines = [f"### {name} 成本壓力測試", ""]
    lines.append("| 成本倍率 | 總報酬 | Sharpe | 成本拖累 |")
    lines.append("|---|---|---|---|")
    base = stress.get(1.0, {})
    for m, met in sorted(stress.items()):
        lines.append(
            f"| {m:.1f}x | {met['total_return']:+.1%} | {met['sharpe']:.2f} | {met['cost_drag']:.1%} |"
        )
    # 脆弱性判讀
    s2 = stress.get(2.0)
    verdict = ""
    if s2 is not None and base:
        if base.get("total_return", 0) > 0 and s2["total_return"] <= 0:
            verdict = "⚠️ **脆弱**:成本加倍即由正轉負,edge 高度依賴低成本,實盤風險高。"
        elif s2["sharpe"] < 0:
            verdict = "⚠️ **脆弱**:2x 成本下 Sharpe 轉負。"
        else:
            verdict = "✅ **穩健**:成本加倍仍維持正報酬,edge 不只來自省成本。"
    lines.append("")
    lines.append(verdict)
    return "\n".join(lines)
