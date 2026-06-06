# -*- coding: utf-8 -*-
"""
C-1 藍軍·價值籌碼擇時操盤手(單股版)。

人格:本益比合理、營收成長、法人買超才進;估值過高或法人轉賣、跌破停損就退。
重視「便宜且有人買」,不追高。每日輸出曝險與理由。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.single_stock import StockView
from .base import Decision, Operator, PositionState


@dataclass
class ValueChipParams:
    max_pe: float = 20.0        # 進場 PER 上限
    expensive_pe: float = 40.0  # 估值過高、減碼/退出
    min_yoy: float = 0.0        # 營收 YoY 下限
    chip_lookback: int = 20
    stop_loss: float = 0.15     # 自成本跌破 15% 停損


class ValueChipOperator(Operator):
    name = "C-1 價值籌碼"

    def __init__(self, params: ValueChipParams | None = None):
        self.p = params or ValueChipParams()

    def decide(self, view: StockView, state: PositionState) -> Decision:
        close = view.close()
        if close is None:
            return Decision(0.0, "資料不足,觀望", {})
        per = view.per()
        yoy = view.rev_yoy()
        inst = view.inst_net(self.p.chip_lookback)
        sig = {"close": round(close, 1), "per": per,
               "rev_yoy": round(yoy, 3) if yoy is not None else None,
               "inst20": int(inst)}

        # 空手與加碼條件判斷所需基本面
        cheap = per is not None and 0 < per <= self.p.max_pe
        growing = (yoy or -1) >= self.p.min_yoy
        bought = inst > 0

        # 持倉中:停損 / 估值過高 / 逢低加碼 / 法人轉賣
        if state.exposure > 0:
            cost_drawdown = (close / state.avg_cost - 1.0) if state.avg_cost > 0 else 0.0
            if state.avg_cost and cost_drawdown <= -self.p.stop_loss:
                return Decision(0.0, f"自加權成本 {state.avg_cost:.0f} 跌破 {self.p.stop_loss:.0%},停損出場", sig)
            if per is not None and per >= self.p.expensive_pe:
                return Decision(0.0, f"PER {per:.1f} 過高(>{self.p.expensive_pe:.0f}),獲利了結", sig)
            
            # 逢低加碼條件: 目前非滿倉、基本面籌碼良好、價格較加權成本回落 5% 到 12% 之間
            if state.exposure < 0.7 and cheap and growing and bought and (-0.12 <= cost_drawdown <= -0.05):
                return Decision(1.0, f"價格較成本 {state.avg_cost:.1f} 回落 {cost_drawdown * 100:+.1f}%，基本面良好，觸發逢低加碼至滿倉", sig)
                
            if inst < 0:
                return Decision(0.3, f"法人 20 日轉賣超({inst:,.0f}),減碼至 3 成倉", sig)
            return Decision(state.exposure, f"基本面/籌碼良好(PER {per},法人買超),續抱", sig)

        # 空手:便宜 + 成長 + 法人買超 (半倉試單)
        if cheap and growing and bought:
            return Decision(0.5, f"PER {per:.1f} 合理、營收YoY {(yoy or 0):+.0%}、法人買超，半倉進場試單", sig)
        why = []
        if not cheap:
            why.append(f"PER {per} 偏貴" if per else "無PER")
        if not growing:
            why.append(f"營收YoY {yoy}")
        if not bought:
            why.append("法人未買超")
        return Decision(0.0, "條件未齊(" + "、".join(why) + "),觀望", sig)
