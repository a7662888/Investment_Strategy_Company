# -*- coding: utf-8 -*-
"""部位與現金管理。記錄成交明細供 D 審計(防線②的原始素材)。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .costs import TaiwanCostModel


@dataclass
class Trade:
    date: pd.Timestamp
    symbol: str
    side: str          # "buy" / "sell"
    shares: float
    fill: float
    fee: float
    tax: float
    pnl: float = 0.0   # 賣出時實現損益(相對加權成本)


@dataclass
class Portfolio:
    cash: float
    costs: TaiwanCostModel
    positions: dict[str, float] = field(default_factory=dict)   # symbol -> shares
    cost_basis: dict[str, float] = field(default_factory=dict)  # symbol -> 每股加權成本
    trades: list[Trade] = field(default_factory=list)

    def value(self, prices: dict[str, float]) -> float:
        eq = self.cash
        for sym, sh in self.positions.items():
            px = prices.get(sym)
            if px is not None:
                eq += sh * px
        return eq

    def buy(self, date, symbol: str, shares: float, ref_price: float) -> None:
        if shares <= 0:
            return
        r = self.costs.buy(ref_price, shares)
        if r["cash_out"] > self.cash:  # 現金不足則按比例縮量
            shares = self.cash / (r["cash_out"] / shares)
            if shares <= 0:
                return
            r = self.costs.buy(ref_price, shares)
        self.cash -= r["cash_out"]
        prev_sh = self.positions.get(symbol, 0.0)
        prev_basis = self.cost_basis.get(symbol, 0.0)
        new_sh = prev_sh + shares
        self.cost_basis[symbol] = (prev_sh * prev_basis + r["cash_out"]) / new_sh
        self.positions[symbol] = new_sh
        self.trades.append(
            Trade(date, symbol, "buy", shares, r["fill"], r["fee"], 0.0)
        )

    def sell(self, date, symbol: str, shares: float, ref_price: float) -> None:
        held = self.positions.get(symbol, 0.0)
        shares = min(shares, held)
        if shares <= 0:
            return
        r = self.costs.sell(ref_price, shares)
        self.cash += r["cash_in"]
        basis = self.cost_basis.get(symbol, r["fill"])
        pnl = r["cash_in"] - basis * shares
        remaining = held - shares
        if remaining <= 1e-9:
            self.positions.pop(symbol, None)
            self.cost_basis.pop(symbol, None)
        else:
            self.positions[symbol] = remaining
        self.trades.append(
            Trade(date, symbol, "sell", shares, r["fill"], r["fee"], r["tax"], pnl)
        )
