# -*- coding: utf-8 -*-
"""操盤手基類:每日決策契約。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..data.single_stock import StockView


@dataclass
class PositionState:
    """引擎維護、傳給操盤手唯讀參考的部位狀態。"""
    exposure: float = 0.0       # 目前曝險(佔權益比例 0~1)
    entry_price: float = 0.0    # 進場參考價
    avg_cost: float = 0.0       # 每股加權成本
    peak_price: float = 0.0     # 進場後波段最高(供移動停損)
    days_held: int = 0


@dataclass
class Decision:
    """操盤手每日輸出。target_exposure=下一交易日要達成的曝險(0~1)。"""
    target_exposure: float
    reason: str
    signals: dict = field(default_factory=dict)


class Operator(ABC):
    name: str = "operator"

    @abstractmethod
    def decide(self, view: StockView, state: PositionState) -> Decision:
        """在 T 日收盤後決策,回傳下一交易日要達成的目標曝險與理由。"""
        raise NotImplementedError
