# -*- coding: utf-8 -*-
"""
C-2 紅軍·趨勢跟隨操盤手(單股版)。

人格:站上均線且動能轉強才進,跌破長均線或移動停損就出,順勢不猜頭摸底。
每日輸出明確曝險與理由,作為「強化操盤手」的教材。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.single_stock import StockView
from .base import Decision, Operator, PositionState


@dataclass
class TrendParams:
    ma_fast: int = 20
    ma_slow: int = 60
    mom_lookback: int = 60
    strong_mom: float = 0.20    # 動能 > 20% 視為強勢,滿倉
    trail_stop: float = 0.15    # 自波段高點回落 15% 出場


class TrendOperator(Operator):
    name = "C-2 趨勢跟隨"

    def __init__(self, params: TrendParams | None = None):
        self.p = params or TrendParams()

    def decide(self, view: StockView, state: PositionState) -> Decision:
        h = view.history(lookback=self.p.mom_lookback + 1)
        if len(h) < self.p.ma_slow:
            return Decision(0.0, "資料不足,觀望", {})

        close = float(h["close"].iloc[-1])
        ma_f = float(h["close"].tail(self.p.ma_fast).mean())
        ma_s = float(h["close"].tail(self.p.ma_slow).mean())
        mom = close / float(h["close"].iloc[0]) - 1.0
        sig = {"close": round(close, 1), "ma20": round(ma_f, 1),
               "ma60": round(ma_s, 1), "mom60": round(mom, 3)}
        trend_up = close > ma_f and ma_f > ma_s

        # 持倉中:先顧停損 / 跌破長均線
        if state.exposure > 0:
            peak = max(state.peak_price, close)
            draw = close / peak - 1.0 if peak else 0.0
            if close < ma_s:
                return Decision(0.0, f"跌破 MA60({ma_s:.0f}),趨勢轉弱出場", sig)
            if draw <= -self.p.trail_stop:
                return Decision(0.0, f"自波段高點回落 {draw:.0%},觸發移動停損出場", sig)
            return Decision(state.exposure, f"趨勢延續(收>{ma_f:.0f}>{ma_s:.0f}),續抱", sig)

        # 空手:等趨勢+動能
        if trend_up and mom > 0:
            if mom >= self.p.strong_mom:
                return Decision(1.0, f"站上均線且 60 日動能 +{mom:.0%}(強勢),滿倉進場", sig)
            return Decision(0.6, f"站上均線、動能 +{mom:.0%}(溫和),半倉試單", sig)
        return Decision(0.0, f"未站上均線或動能不足(收{close:.0f} vs MA20 {ma_f:.0f}),觀望", sig)
