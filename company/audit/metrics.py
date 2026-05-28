# -*- coding: utf-8 -*-
"""
防線② — 確定性硬指標。

D 不當『裁判』,只當『分析師』:勝敗先由這裡的純函式用程式算出客觀數字,
D(或 Claude)再在數字之上做敘事與優化建議,杜絕 LLM 憑感覺評分/幻覺。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def _max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min())


def compute(result) -> dict:
    """從 BacktestResult 計算全套績效/風險/交易行為指標。"""
    eq = result.equity
    rets = result.daily_returns.dropna()
    trades = result.trades

    total_return = float(eq.iloc[-1] / result.initial_capital - 1) if len(eq) else 0.0
    cagr = _cagr(eq)
    ann_vol = float(rets.std() * np.sqrt(TRADING_DAYS)) if len(rets) else 0.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS)) if rets.std() else 0.0
    downside = rets[rets < 0]
    sortino = (
        float(rets.mean() / downside.std() * np.sqrt(TRADING_DAYS))
        if len(downside) and downside.std()
        else 0.0
    )
    mdd = _max_drawdown(eq)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    sells = [t for t in trades if t.side == "sell"]
    wins = [t for t in sells if t.pnl > 0]
    losses = [t for t in sells if t.pnl <= 0]
    win_rate = len(wins) / len(sells) if sells else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = float(gross_win / gross_loss) if gross_loss else float("inf")

    # 週轉率:總成交額 / 平均權益 / 年數(年化)
    turnover_amt = sum(t.fill * t.shares for t in trades)
    avg_equity = float(eq.mean()) if len(eq) else result.initial_capital
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9) if len(eq) else 1.0
    turnover = float(turnover_amt / avg_equity / years) if avg_equity else 0.0

    total_fees = sum(t.fee + t.tax for t in trades)

    return {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "turnover": turnover,
        "num_trades": len(trades),
        "total_cost": total_fees,
        "cost_drag": float(total_fees / result.initial_capital),
    }


def to_table(named_metrics: dict[str, dict]) -> str:
    """把多個策略的指標排成對照表(markdown)。"""
    keys = [
        ("total_return", "總報酬", "{:.1%}"),
        ("cagr", "年化報酬(CAGR)", "{:.1%}"),
        ("ann_vol", "年化波動", "{:.1%}"),
        ("sharpe", "Sharpe", "{:.2f}"),
        ("sortino", "Sortino", "{:.2f}"),
        ("max_drawdown", "最大回撤(MDD)", "{:.1%}"),
        ("calmar", "Calmar", "{:.2f}"),
        ("win_rate", "勝率", "{:.1%}"),
        ("profit_factor", "獲利因子", "{:.2f}"),
        ("turnover", "年化週轉率", "{:.1f}x"),
        ("num_trades", "交易次數", "{:.0f}"),
        ("cost_drag", "成本拖累", "{:.2%}"),
    ]
    names = list(named_metrics)
    header = "| 指標 | " + " | ".join(names) + " |"
    sep = "|---|" + "---|" * len(names)
    lines = [header, sep]
    for k, label, fmt in keys:
        cells = []
        for n in names:
            v = named_metrics[n].get(k, 0.0)
            try:
                cells.append(fmt.format(v))
            except (ValueError, TypeError):
                cells.append(str(v))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
