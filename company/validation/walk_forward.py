# -*- coding: utf-8 -*-
"""
防線③ — Walk-Forward(滾動前進)樣本外驗證。

自我進化迴圈最大的陷阱:一直在同一段歷史上調 prompt/參數,就是在背答案。
正確做法:
  1. 切成多個 [訓練窗 → 測試窗] 的滾動區段。
  2. 在『訓練窗』用小型網格搜尋挑最佳參數(in-sample)。
  3. 用該參數在『緊接其後、從未看過的測試窗』實測(out-of-sample)。
  4. 串接所有測試窗的權益,得到『誠實』的樣本外績效。
若樣本外績效遠差於樣本內 → 該策略是過擬合,不可信。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from typing import Optional

from ..audit import metrics as M
from ..data.interfaces import Dataset
from ..sandbox.circuit_breaker import CircuitBreaker
from ..sandbox.costs import TaiwanCostModel
from ..sandbox.engine import BacktestEngine, BacktestResult


@dataclass
class WalkForwardSegment:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    best_params: dict
    in_sample_sharpe: float
    out_sample_sharpe: float


def run_walk_forward(
    dataset: Dataset,
    costs: TaiwanCostModel,
    build_strategy: Callable[[dict], object],
    param_grid: list[dict],
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_capital: float = 1_000_000.0,
    train_days: int = 180,
    test_days: int = 60,
    circuit_breaker: Optional[CircuitBreaker] = None,
):
    """回傳 (串接後的樣本外 BacktestResult, [各段紀錄])。"""
    days = dataset.prices.trading_days
    days = days[(days >= start) & (days <= end)]
    if len(days) < train_days + test_days:
        raise ValueError("資料期間不足以做 walk-forward")

    # 與正式部署用同一套引擎設定(含熔斷),OOS 才反映真實系統
    engine = BacktestEngine(dataset, costs, initial_capital, circuit_breaker=circuit_breaker)
    segments: list[WalkForwardSegment] = []
    oos_returns = []

    i = 0
    while i + train_days + test_days <= len(days):
        tr_s, tr_e = days[i], days[i + train_days - 1]
        te_s, te_e = days[i + train_days], days[i + train_days + test_days - 1]

        # 1) 訓練窗:網格搜尋挑最佳(以 Sharpe)
        best, best_sharpe = None, -1e9
        for params in param_grid:
            res = engine.run(build_strategy(params), tr_s, tr_e)
            sh = M.compute(res)["sharpe"]
            if sh > best_sharpe:
                best_sharpe, best = sh, params

        # 2) 測試窗:用最佳參數實測(樣本外)
        oos = engine.run(build_strategy(best), te_s, te_e)
        oos_m = M.compute(oos)
        oos_returns.append(oos.daily_returns)

        segments.append(
            WalkForwardSegment(
                tr_s, tr_e, te_s, te_e, best, best_sharpe, oos_m["sharpe"]
            )
        )
        i += test_days  # 滾動前進一個測試窗

    # 3) 串接所有樣本外報酬成誠實權益曲線
    all_ret = pd.concat(oos_returns).sort_index()
    all_ret = all_ret[~all_ret.index.duplicated(keep="first")]
    equity = initial_capital * (1 + all_ret).cumprod()
    stitched = BacktestResult(
        name="樣本外串接(walk-forward)",
        equity=equity,
        daily_returns=all_ret,
        trades=[],
        initial_capital=initial_capital,
    )
    return stitched, segments
