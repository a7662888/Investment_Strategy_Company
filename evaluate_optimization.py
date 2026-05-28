# -*- coding: utf-8 -*-
"""
D 審計官的優化驗證腳本 —— 比較 C-2 動能流「優化前 vs 優化後」。

D 的診斷:C-2 測試期成本拖累 36.8% 是結構性過度交易(週轉 61x)。
優化(理論驅動,非挖數據):拉長再平衡週期 + 遲滯緩衝(keep_band),只在名次明顯掉出才換股。

驗證紀律(防線③):
  * 測試期比較 —— 看成本拖累是否真的下降(比較兩個『固定設計』,非在測試期挑參數)。
  * Walk-Forward OOS 比較 —— 看優化是否能『泛化』到從未看過的區段,而非只在測試期好看。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml

from company.audit import metrics as M
from company.data import synthetic
from company.sandbox.costs import TaiwanCostModel
from company.sandbox.engine import BacktestEngine
from company.strategies.momentum_red import MomentumParams, MomentumRed
from company.validation.walk_forward import run_walk_forward

cfg = yaml.safe_load(open(ROOT / "config" / "settings.yaml", encoding="utf-8"))
capital = cfg["capital"]["initial"]
costs = TaiwanCostModel(**cfg["costs"])
ds = synthetic.generate(n_symbols=30, start=cfg["data"]["start"], days=720)
engine = BacktestEngine(ds, costs, capital)

days = ds.prices.trading_days
test_start = pd.Timestamp(cfg["periods"]["validation_end"]) + pd.Timedelta(days=1)
test_end = days[-1]

# 優化前:週轉高(每週再平衡、無遲滯緩衝)
BASELINE = MomentumParams(rebalance_days=5, keep_band=0)
# 優化後:拉長週期 + 遲滯緩衝
IMPROVED = MomentumParams(rebalance_days=15, keep_band=4)


def fmt(m):
    return (f"報酬 {m['total_return']:+.1%} | Sharpe {m['sharpe']:.2f} | "
            f"MDD {m['max_drawdown']:.1%} | 週轉 {m['turnover']:.1f}x | "
            f"成本拖累 {m['cost_drag']:.1%} | 交易 {m['num_trades']:.0f}")


print("=== 測試期比較(固定設計,非挑參數)===")
b = M.compute(engine.run(MomentumRed(BASELINE), test_start, test_end))
i = M.compute(engine.run(MomentumRed(IMPROVED), test_start, test_end))
print(f"  優化前:{fmt(b)}")
print(f"  優化後:{fmt(i)}")

print("\n=== Walk-Forward 樣本外比較(泛化判斷,防線③)===")
grid_base = [
    MomentumParams(top_n=n, lookback=lb, trail_stop=ts, rebalance_days=5, keep_band=0).__dict__
    for n in (5, 8) for lb in (40, 60) for ts in (0.12, 0.18)
]
grid_impr = [
    MomentumParams(top_n=n, lookback=lb, trail_stop=ts, rebalance_days=15, keep_band=4).__dict__
    for n in (5, 8) for lb in (40, 60) for ts in (0.12, 0.18)
]
for label, grid in (("優化前", grid_base), ("優化後", grid_impr)):
    stitched, segs = run_walk_forward(
        ds, costs, lambda p: MomentumRed(MomentumParams(**p)), grid,
        start=days[0], end=test_end, initial_capital=capital,
        train_days=cfg["walk_forward"]["train_days"], test_days=cfg["walk_forward"]["test_days"],
    )
    sm = M.compute(stitched)
    avg_oos = sum(s.out_sample_sharpe for s in segs) / len(segs)
    print(f"  {label}:樣本外串接報酬 {sm['total_return']:+.1%} | OOS Sharpe {sm['sharpe']:.2f} | "
          f"平均區段 OOS Sharpe {avg_oos:.2f} | MDD {sm['max_drawdown']:.1%}")
