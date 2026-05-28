# -*- coding: utf-8 -*-
"""
防線⑤ + 新角色 E — 投資長 / 資本配置官。

紅藍對抗適合『研發競賽』;但真正營運時不會永遠兩派各跑一半。
E 依市場 regime 動態配置資金:
  * 趨勢盤(大盤站上長期均線且斜率為正)→ 加碼動能流 C-2
  * 盤整/空頭 → 加碼價值流 C-1
做法上,先各自跑出 C-1 / C-2 的權益曲線,再依每日 regime 權重做『日報酬混合』,
得到綜合基金的權益曲線與綜合績效。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.interfaces import Dataset
from . import regime as regime_mod


def market_index(dataset: Dataset, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """以全市場等權收盤價合成大盤指數。"""
    days = dataset.prices.trading_days
    days = days[(days >= start) & (days <= end)]
    vals = []
    for t in days:
        closes = [
            float(dataset.prices.bar(s, t)["close"])
            for s in dataset.prices.universe(t)
        ]
        vals.append(np.mean(closes) if closes else np.nan)
    return pd.Series(vals, index=pd.DatetimeIndex(days)).ffill()


def regime_weights(index: pd.Series) -> pd.DataFrame:
    """
    依四類 regime 決定每日 [w_value, w_momentum]:
      多頭→動能 0.80、空頭→0.15、盤整→0.45、高波動→0.20(見 regime.REGIME_MOM_WEIGHT)。
    比舊版單純的 [0.25,0.75] 夾擠更貼近市場狀態,且敢在強趨勢時加重贏家。
    """
    labels = regime_mod.classify(index)
    w_mom = regime_mod.momentum_weight(labels)
    out = pd.DataFrame({"w_value": 1 - w_mom, "w_momentum": w_mom})
    out["regime"] = labels
    return out


def blend(value_result, momentum_result, weights: pd.DataFrame, initial_capital: float):
    """依每日 regime 權重混合兩派日報酬,輸出綜合 BacktestResult。"""
    from ..sandbox.engine import BacktestResult

    rv = value_result.daily_returns
    rm = momentum_result.daily_returns
    idx = rv.index.union(rm.index)
    rv = rv.reindex(idx).fillna(0.0)
    rm = rm.reindex(idx).fillna(0.0)
    w = weights.reindex(idx).ffill().fillna(0.5)

    blended_ret = w["w_value"].values * rv.values + w["w_momentum"].values * rm.values
    blended_ret = pd.Series(blended_ret, index=idx)
    equity = initial_capital * (1 + blended_ret).cumprod()

    # 綜合基金是「兩派『稅後/費後』日報酬」的加權混合,成本已內含在各自報酬中。
    # 故此處不附帶 trades(避免把兩份成交日誌相加 → 重複計算成本/週轉,誤導審計)。
    return BacktestResult(
        name="E 綜合基金(regime 配置)",
        equity=equity,
        daily_returns=blended_ret,
        trades=[],
        initial_capital=initial_capital,
    )
