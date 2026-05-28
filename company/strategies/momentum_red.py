# -*- coding: utf-8 -*-
"""
C-2 紅軍:激進動能流。

人格:重技術面與動能,追逐強勢股,週轉較高,以移動停損保護獲利。
選股:N 日報酬最強 + 站上均線 + 量能放大,等權持有 top_n,每週再平衡。
風控:移動停損(自波段高點回落 trail_stop 即出場)。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.interfaces import MarketView
from .base import Strategy


@dataclass
class MomentumParams:
    top_n: int = 6
    lookback: int = 60         # 動能回看
    ma_window: int = 20
    rebalance_days: int = 15   # 拉長再平衡週期以降低成本拖累
    trail_stop: float = 0.15
    keep_band: int = 4         # 遲滯緩衝:持股只要仍在 top_n+keep_band 內就不換,減少churn


class MomentumRed(Strategy):
    name = "C-2 紅軍·動能流"

    def __init__(self, params: MomentumParams | None = None):
        self.p = params or MomentumParams()
        self._last_rebalance: pd.Timestamp | None = None
        self._peak: dict[str, float] = {}

    def on_bar(self, view: MarketView, portfolio) -> dict[str, float] | None:
        t = view.as_of

        # 移動停損:更新波段高點,回落過深即出場
        forced_exit = set()
        for sym in list(portfolio.positions):
            px = view.close(sym)
            if px is None:
                continue
            peak = max(self._peak.get(sym, px), px)
            self._peak[sym] = peak
            if px / peak - 1 <= -self.p.trail_stop:
                forced_exit.add(sym)

        due = (
            self._last_rebalance is None
            or (t - self._last_rebalance).days >= self.p.rebalance_days
        )
        if not due and not forced_exit:
            return None  # HOLD:非再平衡日不調倉
        if not due and forced_exit:
            return self._current_weights(portfolio, exclude=forced_exit)
        self._last_rebalance = t

        ranked = []
        for sym in view.universe():
            h = view.history(sym, lookback=self.p.lookback + 1)
            if len(h) < self.p.lookback:
                continue
            close = h["close"]
            ret = close.iloc[-1] / close.iloc[0] - 1.0
            ma = close.tail(self.p.ma_window).mean()
            above_ma = close.iloc[-1] > ma
            vol_now = h["volume"].tail(5).mean()
            vol_base = h["volume"].mean()
            vol_surge = vol_now / vol_base if vol_base else 1.0
            if ret <= 0 or not above_ma:
                continue
            score = ret * (1.0 + 0.3 * (vol_surge - 1.0))
            ranked.append((score, sym))

        ranked.sort(reverse=True)
        ranked_syms = [s for _, s in ranked]

        # 遲滯(hysteresis):新進場取 top_n;原持股只要還在 top_n+keep_band 內就續抱,
        # 避免名次在邊界微幅進出造成的反覆換股(這是 C-2 成本拖累過高的結構性主因)
        buffer = set(ranked_syms[: self.p.top_n + self.p.keep_band])
        picks = list(ranked_syms[: self.p.top_n])
        for s in portfolio.positions:
            if s not in forced_exit and s in buffer and s not in picks:
                picks.append(s)
        picks = [s for s in picks if s not in forced_exit]

        # 清掉已出場標的的高點紀錄
        for s in forced_exit:
            self._peak.pop(s, None)
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
