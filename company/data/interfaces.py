# -*- coding: utf-8 -*-
"""
防線① — Point-in-Time(時點)資料介面。

這是整套系統「可信」的地基。核心原則:
    策略(C-1 / C-2)永遠只拿得到 MarketView,而 MarketView 結構性地
    只暴露 as_of(含)之前的資料。未來的列根本不在它能存取的範圍內,
    因此「未來函數 / look-ahead bias」在型別層級就被擋掉,而不是靠約定。

引擎(A 沙盒)是唯一的特權者:它能看到「下一根 K 棒」用來成交,
這不是 look-ahead,而是「在 T 日決策、T+1 開盤成交」的真實交易順序。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

# 各資料表必備欄位
PRICE_COLS = ["date", "symbol", "open", "high", "low", "close", "volume"]


class PriceData:
    """全期間價格面板。對外一律以 as_of 切片,杜絕未來資料外洩。"""

    def __init__(self, df: pd.DataFrame):
        missing = set(PRICE_COLS) - set(df.columns)
        if missing:
            raise ValueError(f"價格資料缺少欄位:{missing}")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        self._df = df
        self._by_symbol = {s: g.set_index("date") for s, g in df.groupby("symbol")}
        self._trading_days = pd.DatetimeIndex(sorted(df["date"].unique()))

    @property
    def trading_days(self) -> pd.DatetimeIndex:
        return self._trading_days

    def history(
        self, symbol: str, as_of: pd.Timestamp, lookback: Optional[int] = None
    ) -> pd.DataFrame:
        """回傳 symbol 在 as_of(含)之前的 OHLCV。lookback 限制最近 N 筆。"""
        g = self._by_symbol.get(symbol)
        if g is None:
            return pd.DataFrame(columns=PRICE_COLS[2:])
        sub = g.loc[g.index <= as_of]
        if lookback is not None:
            sub = sub.tail(lookback)
        return sub

    def universe(self, as_of: pd.Timestamp) -> list[str]:
        """as_of 當日有成交資料的可交易標的。"""
        return sorted(
            s for s, g in self._by_symbol.items() if as_of in g.index
        )

    # --- 以下為引擎特權方法:策略拿不到 MarketView,故無法呼叫 ---

    def bar(self, symbol: str, date: pd.Timestamp) -> Optional[pd.Series]:
        g = self._by_symbol.get(symbol)
        if g is None or date not in g.index:
            return None
        return g.loc[date]

    def next_trading_day(self, date: pd.Timestamp) -> Optional[pd.Timestamp]:
        idx = self._trading_days.searchsorted(date, side="right")
        if idx >= len(self._trading_days):
            return None
        return self._trading_days[idx]


class FundamentalData:
    """
    基本面(本益比、營收成長等)。

    關鍵可信細節:以「實際公布日 announce_date」做切片,而非財報所屬季季末。
    避免「用尚未公布的財報做決策」這種隱性 look-ahead。
    """

    def __init__(self, df: Optional[pd.DataFrame]):
        if df is None or len(df) == 0:
            self._by_symbol: dict[str, pd.DataFrame] = {}
            return
        df = df.copy()
        if "announce_date" not in df.columns:
            raise ValueError("基本面資料必須含 announce_date(實際公布日)")
        df["announce_date"] = pd.to_datetime(df["announce_date"])
        df = df.sort_values(["symbol", "announce_date"])
        self._by_symbol = {
            s: g.set_index("announce_date") for s, g in df.groupby("symbol")
        }

    def latest(self, symbol: str, as_of: pd.Timestamp) -> Optional[pd.Series]:
        """as_of 前最近一次『已公布』的財報快照。"""
        g = self._by_symbol.get(symbol)
        if g is None:
            return None
        sub = g.loc[g.index <= as_of]
        if len(sub) == 0:
            return None
        return sub.iloc[-1]


class ChipData:
    """籌碼面(三大法人買賣超)。同樣以日期切片。"""

    def __init__(self, df: Optional[pd.DataFrame]):
        if df is None or len(df) == 0:
            self._by_symbol: dict[str, pd.DataFrame] = {}
            return
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])
        self._by_symbol = {s: g.set_index("date") for s, g in df.groupby("symbol")}

    def history(
        self, symbol: str, as_of: pd.Timestamp, lookback: Optional[int] = None
    ) -> pd.DataFrame:
        g = self._by_symbol.get(symbol)
        if g is None:
            return pd.DataFrame()
        sub = g.loc[g.index <= as_of]
        if lookback is not None:
            sub = sub.tail(lookback)
        return sub


class NewsData:
    """
    新聞 / 輿情面。

    可信關鍵:以「可取得時點 available_date」切片,而非新聞描述的事件發生時點。
    一則「事後才報導」的新聞,available_date 必須是『見報日』,否則就是 look-ahead。
    sentiment ∈ [-1, 1](負=偏空、正=偏多)。
    """

    def __init__(self, df: Optional[pd.DataFrame]):
        if df is None or len(df) == 0:
            self._by_symbol: dict[str, pd.DataFrame] = {}
            return
        df = df.copy()
        if "available_date" not in df.columns:
            raise ValueError("新聞資料必須含 available_date(可取得時點)")
        df["available_date"] = pd.to_datetime(df["available_date"])
        df = df.sort_values(["symbol", "available_date"])
        self._by_symbol = {
            s: g.set_index("available_date") for s, g in df.groupby("symbol")
        }

    def history(
        self, symbol: str, as_of: pd.Timestamp, lookback: Optional[int] = None
    ) -> pd.DataFrame:
        g = self._by_symbol.get(symbol)
        if g is None:
            return pd.DataFrame()
        sub = g.loc[g.index <= as_of]
        if lookback is not None:
            sub = sub.tail(lookback)
        return sub

    def sentiment(
        self, symbol: str, as_of: pd.Timestamp, lookback: int = 10
    ) -> Optional[float]:
        """as_of 前 lookback 筆新聞的平均情緒;無資料回 None。"""
        sub = self.history(symbol, as_of, lookback)
        if len(sub) == 0 or "sentiment" not in sub.columns:
            return None
        return float(sub["sentiment"].mean())


@dataclass(frozen=True)
class MarketView:
    """
    T 日當下的市場視角 —— 交給策略的唯一資料入口。

    凍結(frozen)且只委派給 *_data 的 as_of 切片方法,
    策略無法繞過 as_of 取得未來資料。
    """

    as_of: pd.Timestamp
    _prices: PriceData
    _funds: FundamentalData
    _chips: ChipData
    _news: "NewsData"

    def universe(self) -> list[str]:
        return self._prices.universe(self.as_of)

    def history(self, symbol: str, lookback: Optional[int] = None) -> pd.DataFrame:
        return self._prices.history(symbol, self.as_of, lookback)

    def close(self, symbol: str) -> Optional[float]:
        h = self._prices.history(symbol, self.as_of, lookback=1)
        if len(h) == 0:
            return None
        return float(h["close"].iloc[-1])

    def fundamentals(self, symbol: str) -> Optional[pd.Series]:
        return self._funds.latest(symbol, self.as_of)

    def chips(self, symbol: str, lookback: Optional[int] = None) -> pd.DataFrame:
        return self._chips.history(symbol, self.as_of, lookback)

    def news(self, symbol: str, lookback: Optional[int] = None) -> pd.DataFrame:
        return self._news.history(symbol, self.as_of, lookback)

    def sentiment(self, symbol: str, lookback: int = 10) -> Optional[float]:
        return self._news.sentiment(symbol, self.as_of, lookback)

    def context(self, ma_window: int = 20) -> dict:
        """
        市場上下文摘要(全 PIT)。供策略/regime/D 參考:
          breadth     —— 站上均線的個股比例(市場廣度)
          avg_sentiment —— 全市場平均輿情
          n_universe  —— 當日可交易檔數
        """
        syms = self.universe()
        above, sents = 0, []
        for s in syms:
            h = self._prices.history(s, self.as_of, lookback=ma_window)
            if len(h) >= ma_window:
                if h["close"].iloc[-1] > h["close"].mean():
                    above += 1
            sv = self._news.sentiment(s, self.as_of, lookback=5)
            if sv is not None:
                sents.append(sv)
        breadth = above / len(syms) if syms else 0.0
        avg_sent = float(sum(sents) / len(sents)) if sents else 0.0
        return {"breadth": breadth, "avg_sentiment": avg_sent, "n_universe": len(syms)}


@dataclass(frozen=True)
class Dataset:
    """B 交付給 A 的完整資料包。"""

    prices: PriceData
    fundamentals: FundamentalData
    chips: ChipData
    news: NewsData

    def view(self, as_of: pd.Timestamp) -> MarketView:
        return MarketView(as_of, self.prices, self.fundamentals, self.chips, self.news)
