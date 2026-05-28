# -*- coding: utf-8 -*-
"""
能力① — 市場 regime 分類器。

把每日市場狀態分為四類,作為 E 配置資金與 D 解讀績效的依據:
  BULL_TREND  多頭趨勢  —— 站上均線且斜率向上     → 偏重動能流 C-2
  BEAR_TREND  空頭趨勢  —— 跌破均線且斜率向下     → 偏重價值流 C-1、降風險
  RANGE       盤整      —— 無明確方向             → 均衡
  HIGH_VOL    高波動    —— 波動度進入高分位(防禦) → 偏防禦(價值流)

全程 PIT:每日的 MA / 斜率 / 波動度只用到當日(含)為止的資料。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BULL_TREND = "BULL_TREND"
BEAR_TREND = "BEAR_TREND"
RANGE = "RANGE"
HIGH_VOL = "HIGH_VOL"

# 各 regime 對應的動能流(C-2)資金權重;其餘給價值流(C-1)
REGIME_MOM_WEIGHT = {
    BULL_TREND: 0.80,
    BEAR_TREND: 0.15,
    RANGE: 0.45,
    HIGH_VOL: 0.20,
}


def classify(
    index: pd.Series,
    ma_window: int = 60,
    slope_window: int = 20,
    vol_window: int = 20,
    vol_lookback: int = 120,
    vol_pct_threshold: float = 0.80,
) -> pd.Series:
    """回傳每日 regime 標籤(與 index 同索引)。"""
    ma = index.rolling(ma_window, min_periods=ma_window // 2).mean()
    slope = ma.diff(slope_window)
    rets = index.pct_change()
    vol = rets.rolling(vol_window, min_periods=vol_window // 2).std()
    # 波動度在過去 vol_lookback 的分位(只用過去資料 → PIT)
    vol_pct = vol.rolling(vol_lookback, min_periods=vol_window).apply(
        lambda x: (x.iloc[-1] >= x).mean(), raw=False
    )

    labels = []
    for i in range(len(index)):
        v_pct = vol_pct.iloc[i]
        above = index.iloc[i] > ma.iloc[i] if not np.isnan(ma.iloc[i]) else False
        up = slope.iloc[i] > 0 if not np.isnan(slope.iloc[i]) else False
        if not np.isnan(v_pct) and v_pct >= vol_pct_threshold:
            labels.append(HIGH_VOL)
        elif above and up:
            labels.append(BULL_TREND)
        elif (not above) and (not up):
            labels.append(BEAR_TREND)
        else:
            labels.append(RANGE)
    return pd.Series(labels, index=index.index, name="regime")


def momentum_weight(regime_series: pd.Series, smooth: int = 5) -> pd.Series:
    """把 regime 標籤轉成平滑後的動能流資金權重(避免突兀切換)。"""
    raw = regime_series.map(REGIME_MOM_WEIGHT).astype(float)
    return raw.rolling(smooth, min_periods=1).mean()
