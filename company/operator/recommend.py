# -*- coding: utf-8 -*-
"""
今日收盤後 → 明日操盤推薦引擎。

原則(對齊使用者需求):不炒當沖。對 watchlist 每檔股票,用操盤手把歷史逐日跑到「最新交易日」,
取得目前模擬部位與最新決策,轉成『明日』的具體建議:
  BUY          明日買進潛力股(空手 → 操盤手要進場)
  SELL_PROFIT  獲利了結(持倉 → 要出場且帳上獲利 → 提醒落袋)
  SELL_LOSS    停損出場(持倉 → 要出場且帳上虧損)
  HOLD         續抱通知(持倉 → 操盤手要續抱,附目前獲利%)
  TRIM         減碼(降低曝險)
  WATCH        觀望(空手且無進場訊號)
每筆都附理由與訊號,讓使用者知道為什麼。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..data.single_stock import StockData
from ..sandbox.circuit_breaker import CircuitBreaker
from ..sandbox.costs import TaiwanCostModel
from .base import Operator
from .journal import JournalEngine

CATEGORY_LABEL = {
    "BUY": "明日買進",
    "ADD": "分批加碼",
    "SELL_PROFIT": "獲利了結",
    "SELL_LOSS": "停損出場",
    "TRIM": "減碼",
    "HOLD": "續抱",
    "WATCH": "觀望",
}


@dataclass
class Recommendation:
    symbol: str
    operator: str
    category: str
    as_of: pd.Timestamp
    close: float
    exposure: float          # 目前曝險
    target_exposure: float   # 明日目標曝險
    unrealized_pct: float     # 持倉未實現損益%
    reason: str
    signals: dict

    @property
    def label(self) -> str:
        return CATEGORY_LABEL.get(self.category, self.category)


def _categorize(exposure: float, target: float, unreal: float) -> str:
    holding = exposure > 1e-6
    if not holding:
        return "BUY" if target > 1e-6 else "WATCH"
    # 持倉中
    if target <= 1e-6:
        return "SELL_PROFIT" if unreal >= 0 else "SELL_LOSS"
    if target < exposure - 1e-6:
        return "TRIM"
    if target > exposure + 1e-6:
        return "ADD"
    return "HOLD"


def recommend_one(
    data: StockData, operator: Operator, costs: TaiwanCostModel,
    capital: float, breaker: Optional[CircuitBreaker], end: pd.Timestamp,
    start: Optional[pd.Timestamp] = None,
) -> Optional[Recommendation]:
    days = data.prices.trading_days
    if len(days) == 0:
        return None
    start = start or days[0]
    end = min(end, days[-1])
    engine = JournalEngine(data, costs, capital, circuit_breaker=breaker)
    jr = engine.run(operator, start, end)
    if len(jr.journal) == 0:
        return None
    last = jr.journal.iloc[-1]
    sigs = {k[4:]: last[k] for k in jr.journal.columns if k.startswith("sig_")}
    cat = _categorize(float(last["exposure"]), float(last["target_exposure"]),
                      float(last["unrealized_pct"]))
    return Recommendation(
        symbol=data.symbol, operator=operator.name, category=cat,
        as_of=jr.journal.index[-1], close=float(last["close"]),
        exposure=float(last["exposure"]), target_exposure=float(last["target_exposure"]),
        unrealized_pct=float(last["unrealized_pct"]),
        reason=str(last["reason"]), signals=sigs,
    )


def scan(
    datasets: dict[str, StockData], operators: list[Operator],
    costs: TaiwanCostModel, capital: float, breaker: Optional[CircuitBreaker],
    end: pd.Timestamp, start: Optional[pd.Timestamp] = None,
) -> list[Recommendation]:
    """對多檔 × 多操盤手掃描,回傳所有推薦(呼叫端再依 category 分組顯示)。"""
    out = []
    for sym, data in datasets.items():
        for op in operators:
            rec = recommend_one(data, op, costs, capital, breaker, end, start)
            if rec is not None:
                out.append(rec)
    return out
