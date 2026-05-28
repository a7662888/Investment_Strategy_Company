# -*- coding: utf-8 -*-
"""策略基類。策略唯一的資料入口是 MarketView(PIT),拿不到未來資料。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..data.interfaces import MarketView


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_bar(self, view: MarketView, portfolio) -> dict[str, float] | None:
        """
        在 T 日收盤後呼叫。回傳「下一交易日要達成的目標權重」。

        契約(三態,刻意區分以控制週轉):
          * dict {symbol: weight} —— 調倉至此目標(權重總和 ≤ 1,其餘為現金)
          * {}(空 dict)         —— 全數出清、轉持現金
          * None                 —— 維持現狀(HOLD),不調倉、不產生交易成本

        portfolio 為唯讀參考,可用來判斷現有持倉/停損。
        """
        raise NotImplementedError
