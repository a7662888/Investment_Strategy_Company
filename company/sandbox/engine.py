# -*- coding: utf-8 -*-
"""
防線① 的執行核心 — Point-in-Time 回測引擎。

交易順序(可信關鍵):
    在 T 日「收盤後」,引擎用 MarketView(只含 ≤T 的資料)請策略給目標權重,
    然後在 T+1 日「開盤」依該權重調倉並計入成本。
    決策資訊與成交時點分離 → 杜絕「用當天收盤決策又用當天收盤成交」的偏誤。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from typing import Optional

from ..data.interfaces import Dataset
from ..strategies.base import Strategy
from .circuit_breaker import CircuitBreaker
from .costs import TaiwanCostModel
from .portfolio import Portfolio, Trade


@dataclass
class BacktestResult:
    name: str
    equity: pd.Series          # 每日權益曲線(index=date)
    daily_returns: pd.Series
    trades: list[Trade]
    initial_capital: float
    breaker_trips: int = 0     # 熔斷觸發次數
    breaker_halted_days: int = 0

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.initial_capital


class BacktestEngine:
    def __init__(
        self,
        dataset: Dataset,
        costs: TaiwanCostModel,
        initial_capital: float = 1_000_000.0,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.dataset = dataset
        self.costs = costs
        self.initial_capital = initial_capital
        self.circuit_breaker = circuit_breaker

    def run(
        self,
        strategy: Strategy,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> BacktestResult:
        days = self.dataset.prices.trading_days
        days = days[(days >= start) & (days <= end)]
        pf = Portfolio(cash=self.initial_capital, costs=self.costs)
        # 每次 run 都用全新熔斷狀態,避免 walk-forward 多次呼叫時狀態殘留
        breaker = None
        if self.circuit_breaker is not None:
            cb = self.circuit_breaker
            breaker = CircuitBreaker(
                halt_drawdown=cb.halt_drawdown,
                cooldown_days=cb.cooldown_days,
                enabled=cb.enabled,
            )

        equity_idx, equity_val = [], []
        pending: dict[str, float] | None = None  # 待今日開盤執行的目標權重(None = 無動作)

        for t in days:
            # 1) 先用今日開盤執行上一根的決策(T+1 開盤成交)
            #    pending 為 None 表示 HOLD(不調倉);空 dict 表示出清轉現金
            if pending is not None:
                self._rebalance(pf, t, pending, price_field="open")
                pending = None

            # 2) 收盤估值並記錄權益
            closes = self._closes(t)
            equity_idx.append(t)
            equity = pf.value(closes)
            equity_val.append(equity)

            # 3) 組合層熔斷(能力②):凌駕策略 —— 觸發時強制下一日開盤轉現金
            halted = breaker.update(equity) if breaker else False
            if halted:
                pending = {}
                continue

            # 4) 收盤後用 PIT 視角請策略產生「下一個交易日要達成」的目標權重
            view = self.dataset.view(t)
            target = strategy.on_bar(view, pf)
            if target is None:
                pending = None
            else:
                pending = {k: v for k, v in target.items() if v > 0}

        equity = pd.Series(equity_val, index=pd.DatetimeIndex(equity_idx), name=strategy.name)
        rets = equity.pct_change().fillna(0.0)
        return BacktestResult(
            name=strategy.name,
            equity=equity,
            daily_returns=rets,
            trades=pf.trades,
            initial_capital=self.initial_capital,
            breaker_trips=breaker.trips if breaker else 0,
            breaker_halted_days=breaker.halted_days if breaker else 0,
        )

    # --- 內部 ---

    def _closes(self, date: pd.Timestamp) -> dict[str, float]:
        out = {}
        for sym in self.dataset.prices.universe(date):
            bar = self.dataset.prices.bar(sym, date)
            if bar is not None:
                out[sym] = float(bar["close"])
        return out

    def _rebalance(
        self, pf: Portfolio, date: pd.Timestamp, target_w: dict[str, float], price_field: str
    ) -> None:
        # 以今日成交價(開盤)換算目標股數;先賣後買以釋放現金
        ref = {}
        for sym in set(list(target_w) + list(pf.positions)):
            bar = self.dataset.prices.bar(sym, date)
            if bar is not None:
                ref[sym] = float(bar[price_field])

        equity = pf.cash + sum(
            sh * ref.get(s, 0.0) for s, sh in pf.positions.items()
        )

        target_shares = {}
        for sym, w in target_w.items():
            px = ref.get(sym)
            if px and px > 0:
                target_shares[sym] = (equity * w) / px

        # 賣出:目前持有但目標較低或歸零
        for sym in list(pf.positions):
            cur = pf.positions[sym]
            tgt = target_shares.get(sym, 0.0)
            if tgt < cur and sym in ref:
                pf.sell(date, sym, cur - tgt, ref[sym])

        # 買入:目標高於目前
        for sym, tgt in target_shares.items():
            cur = pf.positions.get(sym, 0.0)
            if tgt > cur and sym in ref:
                pf.buy(date, sym, tgt - cur, ref[sym])
