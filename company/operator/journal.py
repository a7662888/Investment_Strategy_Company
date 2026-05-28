# -*- coding: utf-8 -*-
"""
逐日操盤日誌引擎(單股)。

交易順序同組合引擎:T 日收盤後決策 → T+1 開盤成交(防線①),計入台股成本(防線④),
並套用組合層熔斷(能力②)。逐日記錄完整操盤日誌,作為操盤手學習與 D 復盤的素材。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..data.single_stock import StockData
from ..sandbox.circuit_breaker import CircuitBreaker
from ..sandbox.costs import TaiwanCostModel
from ..sandbox.engine import BacktestResult
from ..sandbox.portfolio import Portfolio, Trade
from .base import Decision, Operator, PositionState


def _action_label(prev: float, target: float, halted: bool) -> str:
    if halted:
        return "熔斷出場"
    if target > prev + 1e-6:
        return "進場" if prev <= 1e-6 else "加碼"
    if target < prev - 1e-6:
        return "出場" if target <= 1e-6 else "減碼"
    if target > 1e-6:
        return "續抱"
    return "觀望"


@dataclass
class JournalResult:
    name: str
    journal: pd.DataFrame          # 逐日操盤日誌
    result: BacktestResult         # 供 metrics / attribution 重用


class JournalEngine:
    def __init__(self, data: StockData, costs: TaiwanCostModel,
                 initial_capital: float = 1_000_000.0,
                 circuit_breaker: Optional[CircuitBreaker] = None):
        self.data = data
        self.costs = costs
        self.initial_capital = initial_capital
        self.cb_template = circuit_breaker

    def run(self, operator: Operator, start: pd.Timestamp, end: pd.Timestamp) -> JournalResult:
        sym = self.data.symbol
        days = self.data.prices.trading_days
        days = days[(days >= start) & (days <= end)]
        pf = Portfolio(cash=self.initial_capital, costs=self.costs)

        breaker = None
        if self.cb_template is not None:
            cb = self.cb_template
            breaker = CircuitBreaker(cb.halt_drawdown, cb.cooldown_days, cb.enabled)

        state = PositionState()
        rows = []
        equity_idx, equity_val = [], []
        pending: Optional[float] = None  # 待今日開盤達成的目標曝險
        pending_reason, pending_sig, pending_action = "", {}, ""
        prev_equity = self.initial_capital

        for t in days:
            bar = self.data.prices.bar(sym, t)
            o, c = float(bar["open"]), float(bar["close"])

            # 1) 開盤執行昨日決策
            if pending is not None:
                self._to_exposure(pf, t, sym, pending, o)

            # 2) 更新部位狀態
            shares = pf.positions.get(sym, 0.0)
            equity = pf.cash + shares * c
            pos_val = shares * c
            state.exposure = pos_val / equity if equity > 0 else 0.0
            state.avg_cost = pf.cost_basis.get(sym, 0.0)
            if shares > 0:
                state.peak_price = max(state.peak_price, c) if state.peak_price else c
                state.days_held += 1
                if state.entry_price == 0.0:
                    state.entry_price = state.avg_cost
            else:
                state.peak_price = 0.0
                state.days_held = 0
                state.entry_price = 0.0

            equity_idx.append(t)
            equity_val.append(equity)
            daily_pnl = equity - prev_equity
            prev_equity = equity

            # 3) 熔斷:凌駕操盤手
            halted = breaker.update(equity) if breaker else False
            if halted:
                decision = Decision(0.0, "組合熔斷:回撤超標,強制轉現金", {})
            else:
                decision = operator.decide(self.data.view(t), state)

            action = _action_label(state.exposure, decision.target_exposure, halted)

            unreal = (c / state.avg_cost - 1) if (shares > 0 and state.avg_cost) else 0.0
            rows.append({
                "date": t, "open": o, "high": float(bar["high"]),
                "low": float(bar["low"]), "close": c,
                "action": action,
                "target_exposure": round(decision.target_exposure, 2),
                "exposure": round(state.exposure, 2),
                "shares": int(shares), "avg_cost": round(state.avg_cost, 1),
                "unrealized_pct": round(unreal, 4),
                "cash": int(pf.cash),
                "equity": int(equity), "daily_pnl": int(daily_pnl),
                "reason": decision.reason,
                **{f"sig_{k}": v for k, v in decision.signals.items()},
            })
            pending = decision.target_exposure

        journal = pd.DataFrame(rows).set_index("date")
        equity = pd.Series(equity_val, index=pd.DatetimeIndex(equity_idx), name=operator.name)
        result = BacktestResult(
            name=operator.name, equity=equity,
            daily_returns=equity.pct_change().fillna(0.0),
            trades=pf.trades, initial_capital=self.initial_capital,
            breaker_trips=breaker.trips if breaker else 0,
            breaker_halted_days=breaker.halted_days if breaker else 0,
        )
        return JournalResult(operator.name, journal, result)

    def _to_exposure(self, pf: Portfolio, t, sym: str, target: float, ref_price: float) -> None:
        shares = pf.positions.get(sym, 0.0)
        equity = pf.cash + shares * ref_price
        target_shares = (equity * target) / ref_price if ref_price > 0 else 0.0
        if target_shares < shares:
            pf.sell(t, sym, shares - target_shares, ref_price)
        elif target_shares > shares:
            pf.buy(t, sym, target_shares - shares, ref_price)
