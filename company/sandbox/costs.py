# -*- coding: utf-8 -*-
"""
防線④ — 台股交易成本模型。

這是 C-1(低週轉)vs C-2(高週轉)公平對決的前提。
不計成本時高週轉策略往往看起來很神,計入後常常變垃圾。

台股現股成本:
  * 手續費:0.1425%(買+賣),可打折(fee_discount),單筆低消 20 元
  * 證交稅:0.3%(僅賣出);當沖減半 0.15%
  * 滑價:以 bps 模擬(成交價偏離參考價)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaiwanCostModel:
    fee_rate: float = 0.001425
    fee_discount: float = 0.6      # 6 折手續費
    min_fee: float = 20.0
    tax_rate: float = 0.003        # 賣出證交稅
    slippage_bps: float = 5.0      # 單邊滑價(萬分之 5)

    def _fee(self, gross: float) -> float:
        return max(self.min_fee, gross * self.fee_rate * self.fee_discount)

    def buy(self, price: float, shares: float) -> dict:
        """回傳買入的成交價(含滑價)、手續費、總現金支出。"""
        fill = price * (1 + self.slippage_bps / 10_000)
        gross = fill * shares
        fee = self._fee(gross)
        return {"fill": fill, "fee": fee, "tax": 0.0, "cash_out": gross + fee}

    def sell(self, price: float, shares: float) -> dict:
        """回傳賣出的成交價(含滑價)、手續費、證交稅、淨現金流入。"""
        fill = price * (1 - self.slippage_bps / 10_000)
        gross = fill * shares
        fee = self._fee(gross)
        tax = gross * self.tax_rate
        return {"fill": fill, "fee": fee, "tax": tax, "cash_in": gross - fee - tax}
