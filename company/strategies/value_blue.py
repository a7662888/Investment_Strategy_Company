# -*- coding: utf-8 -*-
"""
C-1 藍軍:保守價值流。

人格:重基本面與籌碼面,低週轉,嚴格停損,追求穩健。
選股:低本益比 + 正營收成長 + 法人買超,等權持有 top_n,每月再平衡。
風控:單檔自進場跌幅超過 stop_loss 即剔除。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.interfaces import MarketView
from .base import Strategy


@dataclass
class ValueParams:
    top_n: int = 8
    max_pe: float = 20.0
    min_rev_yoy: float = 0.0
    chip_lookback: int = 20
    rebalance_days: int = 21    # 約一個月
    stop_loss: float = 0.12


class ValueBlue(Strategy):
    name = "C-1 藍軍·價值流"

    def __init__(self, params: ValueParams | None = None):
        self.p = params or ValueParams()
        self._last_rebalance: pd.Timestamp | None = None

    def on_bar(self, view: MarketView, portfolio) -> dict[str, float] | None:
        t = view.as_of

        # 停損優先:每日檢查,觸發即出場(以權重 0 表示)
        forced_exit = set()
        for sym, sh in list(portfolio.positions.items()):
            basis = portfolio.cost_basis.get(sym)
            px = view.close(sym)
            if basis and px and (px / basis - 1) <= -self.p.stop_loss:
                forced_exit.add(sym)

        # 僅每月再平衡(低週轉)
        due = (
            self._last_rebalance is None
            or (t - self._last_rebalance).days >= self.p.rebalance_days
        )
        if not due and not forced_exit:
            return None  # HOLD:低週轉的關鍵,非再平衡日不調倉
        if not due and forced_exit:
            # 僅因停損出場:剔除停損標的,其餘維持等權
            return self._current_weights(portfolio, exclude=forced_exit)
        self._last_rebalance = t

        # 選股:基本面 + 籌碼面打分
        ranked = []
        for sym in view.universe():
            f = view.fundamentals(sym)
            if f is None:
                continue
            pe = float(f.get("pe", 1e9))
            rev = float(f.get("rev_yoy", -1))
            if pe <= 0 or pe > self.p.max_pe or rev < self.p.min_rev_yoy:
                continue
            chips = view.chips(sym, self.p.chip_lookback)
            inst = float(chips["inst_net"].sum()) if len(chips) else 0.0
            sent = view.sentiment(sym, lookback=10) or 0.0  # 新聞輿情(PIT,可能為 None)
            score = (rev * 2.0) + (inst / 50_000.0) - (pe / 100.0) + (sent * 0.5)
            ranked.append((score, sym))

        ranked.sort(reverse=True)
        picks = [s for _, s in ranked[: self.p.top_n] if s not in forced_exit]
        if not picks:
            return {}
        w = 1.0 / len(picks)
        return {s: w for s in picks}

    def _current_weights(self, portfolio, exclude) -> dict[str, float]:
        held = [s for s in portfolio.positions if s not in exclude]
        if not held:
            return {}
        w = 1.0 / len(held)
        return {s: w for s in held}
