# -*- coding: utf-8 -*-
"""
合成資料產生器 —— 讓整套系統零金鑰、離線即可端到端跑通。

刻意內建市場情境(regime):
  * 前段:盤整(讓價值流 C-1 有優勢)
  * 中段:多頭噴出(航海王式,讓動能流 C-2 發光)
  * 末段:急跌(測試兩派的耐震度與停損紀律)
這樣 E(PM)的 regime 配置才有東西可切換。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .interfaces import ChipData, Dataset, FundamentalData, NewsData, PriceData


def _regime_drift(n: int) -> np.ndarray:
    """產生隨時間變化的日報酬漂移率(年化轉日)。"""
    seg = n // 3
    calm = np.full(seg, 0.02 / 252)          # 盤整微多
    boom = np.full(seg, 0.55 / 252)          # 多頭噴出
    crash = np.full(n - 2 * seg, -0.45 / 252)  # 急跌
    return np.concatenate([calm, boom, crash])


def generate(
    n_symbols: int = 30,
    start: str = "2019-01-01",
    days: int = 720,
    seed: int = 42,
) -> Dataset:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=days)
    drift = _regime_drift(days)

    price_rows = []
    fund_rows = []
    chip_rows = []
    news_rows = []

    for i in range(n_symbols):
        symbol = f"{1101 + i * 7}"  # 假台股代號
        beta = rng.uniform(0.5, 1.8)          # 個股對市場 regime 的敏感度
        idio_vol = rng.uniform(0.012, 0.030)  # 特異波動
        quality = rng.uniform(0.0, 1.0)       # 0=爛股 1=績優,影響基本面

        rets = beta * drift + rng.normal(0, idio_vol, days)
        price = 50.0 * np.exp(np.cumsum(rets))
        intraday = rng.uniform(0.005, 0.02, days)

        for d, p, ir in zip(dates, price, intraday):
            o = p * (1 + rng.normal(0, ir / 2))
            c = p
            hi = max(o, c) * (1 + ir)
            lo = min(o, c) * (1 - ir)
            vol = int(rng.uniform(2_000, 60_000) * (1 + beta))
            price_rows.append([d, symbol, o, hi, lo, c, vol])

        # 基本面:每季一筆,公布日 = 季末 + 45 天(保守模擬實際公布落差)
        for q_end in pd.date_range(start=start, periods=days // 63 + 2, freq="QE"):
            announce = q_end + pd.Timedelta(days=45)
            pe = rng.uniform(8, 18) if quality > 0.5 else rng.uniform(18, 45)
            rev_yoy = rng.normal(0.15 if quality > 0.5 else -0.05, 0.12)
            fund_rows.append([announce, symbol, q_end, pe, rev_yoy])

        # 籌碼面:法人買賣超,績優股偏買超
        inst = rng.normal(quality - 0.5, 1.0, days) * 1000
        for d, x in zip(dates, inst):
            chip_rows.append([d, symbol, int(x)])

        # 新聞/輿情:約每 3 天一則,情緒 = 近 5 日報酬(過去資訊)+ 品質偏誤 + 雜訊
        # available_date 即見報日;PIT 切片確保策略只看得到 ≤T 的新聞
        for j in range(5, days, 3):
            recent = price[j] / price[j - 5] - 1
            sent = float(np.clip(recent * 8 + (quality - 0.5) * 0.4 + rng.normal(0, 0.3), -1, 1))
            tone = "看多" if sent > 0.15 else ("看空" if sent < -0.15 else "中性")
            news_rows.append([dates[j], symbol, sent, f"{symbol} 法人動向{tone}"])

    prices = PriceData(
        pd.DataFrame(
            price_rows,
            columns=["date", "symbol", "open", "high", "low", "close", "volume"],
        )
    )
    funds = FundamentalData(
        pd.DataFrame(
            fund_rows,
            columns=["announce_date", "symbol", "period", "pe", "rev_yoy"],
        )
    )
    chips = ChipData(
        pd.DataFrame(chip_rows, columns=["date", "symbol", "inst_net"])
    )
    news = NewsData(
        pd.DataFrame(
            news_rows, columns=["available_date", "symbol", "sentiment", "headline"]
        )
    )
    return Dataset(prices=prices, fundamentals=funds, chips=chips, news=news)
