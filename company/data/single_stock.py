# -*- coding: utf-8 -*-
"""
單股真實資料(FinMind)抓取 + 快取 + Point-in-Time 視角。

抓四個資料集(以 2327 國巨為例):
  TaiwanStockPrice                         日 OHLCV
  TaiwanStockPER                           日 PER / PBR / 殖利率
  TaiwanStockInstitutionalInvestorsBuySell 三大法人買賣(多列,需彙總淨額)
  TaiwanStockMonthRevenue                  月營收(用來算 YoY)

可信原則(防線①):
  * 只抓一次、快取到 data_cache/,之後離線迭代(省 token / 省 API 額度)。
  * StockView 只暴露 as_of(含)之前的資料。
  * 月營收以「揭露日」切片:台股當月營收於次月 10 日前公布 → announce = 營收月+約 40 天(寧晚勿早)。
  * PER 為日資料,announce 即當日。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from .interfaces import PriceData

API = "https://api.finmindtrade.com/api/v4/data"
REVENUE_LAG_DAYS = 40  # 月營收揭露保守落差


def _fetch(dataset: str, symbol: str, start: str, end: str, token: Optional[str]) -> pd.DataFrame:
    params = {"dataset": dataset, "data_id": symbol, "start_date": start, "end_date": end}
    if token:
        params["token"] = token
    r = requests.get(API, params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} 異常:{j.get('msg')}")
    return pd.DataFrame(j.get("data", []))


def fetch_and_cache(
    symbol: str, start: str, end: str, cache_dir: str = "data_cache",
    token: Optional[str] = None, force: bool = False,
) -> dict[str, Path]:
    token = token or os.environ.get("FINMIND_TOKEN")
    cdir = Path(cache_dir)
    cdir.mkdir(exist_ok=True)
    specs = {
        "price": "TaiwanStockPrice",
        "per": "TaiwanStockPER",
        "chips": "TaiwanStockInstitutionalInvestorsBuySell",
        "revenue": "TaiwanStockMonthRevenue",
        "margin": "TaiwanStockMarginPurchaseShortSale",
    }
    # 月營收需多抓 2 年前資料,YoY 才能從分析起點就算得出來
    rev_start = f"{int(start[:4]) - 2}{start[4:]}"
    paths = {}
    for key, ds in specs.items():
        p = cdir / f"{symbol}_{key}.csv"
        if p.exists() and not force:
            paths[key] = p
            continue
        ds_start = rev_start if key == "revenue" else start
        try:
            df = _fetch(ds, symbol, ds_start, end, token)
            df.to_csv(p, index=False, encoding="utf-8-sig")
        except Exception as e:
            # If fetch fails (e.g. rate limit), output empty fallback file if it doesn't exist
            if not p.exists():
                pd.DataFrame().to_csv(p, index=False, encoding="utf-8-sig")
        paths[key] = p
    return paths


class StockData:
    """單股全期間資料 + PIT 切片來源。"""

    def __init__(self, symbol: str, price: pd.DataFrame, per: pd.DataFrame,
                 chips: pd.DataFrame, revenue: pd.DataFrame, margin: pd.DataFrame):
        self.symbol = symbol

        # 價格:轉成 PriceData(沿用既有 PIT 與成交介面)
        p = price.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
        p["symbol"] = symbol
        # 清掉停牌/無成交的 0 價列,避免除以零與假訊號
        p = p[(p["open"] > 0) & (p["close"] > 0) & (p["high"] > 0) & (p["low"] > 0)]
        self.prices = PriceData(p[["date", "symbol", "open", "high", "low", "close", "volume"]])

        # PER:日資料
        self._per = per.copy()
        if len(self._per):
            self._per["date"] = pd.to_datetime(self._per["date"])
            self._per = self._per.sort_values("date").set_index("date")

        # 法人:彙總每日淨買超(所有法人別 buy-sell 加總)
        if len(chips) and "date" in chips.columns:
            c = chips.copy()
            c["date"] = pd.to_datetime(c["date"])
            c["net"] = c["buy"] - c["sell"]
            self._chip_net = c.groupby("date")["net"].sum().sort_index()
            # 外資與投信單獨提取
            foreign = c[c["name"] == "Foreign_Investor"]
            self._foreign_net = foreign.groupby("date")["net"].sum().sort_index() if not foreign.empty else pd.Series(dtype=float)
            trust = c[c["name"] == "Investment_Trust"]
            self._trust_net = trust.groupby("date")["net"].sum().sort_index() if not trust.empty else pd.Series(dtype=float)
        else:
            self._chip_net = pd.Series(dtype=float)
            self._foreign_net = pd.Series(dtype=float)
            self._trust_net = pd.Series(dtype=float)

        # 融資融券
        self._margin = margin.copy()
        if len(self._margin) and "date" in self._margin.columns:
            self._margin["date"] = pd.to_datetime(self._margin["date"])
            self._margin = self._margin.sort_values("date").set_index("date")

        # 月營收 → YoY,以揭露日為索引
        self._rev_yoy = self._build_revenue_yoy(revenue)

    def _build_revenue_yoy(self, revenue: pd.DataFrame) -> pd.Series:
        if len(revenue) == 0:
            return pd.Series(dtype=float)
        r = revenue.copy()
        # 以 revenue_year/month 組出營收所屬月份,announce = 該月 + 落差
        r["rev_period"] = pd.to_datetime(
            r["revenue_year"].astype(str) + "-" + r["revenue_month"].astype(str) + "-01"
        )
        r = r.sort_values("rev_period")
        r["yoy"] = r["revenue"].pct_change(12)
        r["announce"] = r["rev_period"] + pd.Timedelta(days=REVENUE_LAG_DAYS)
        s = r.dropna(subset=["yoy"]).set_index("announce")["yoy"].sort_index()
        return s

    def view(self, as_of: pd.Timestamp) -> "StockView":
        return StockView(as_of, self)


@dataclass(frozen=True)
class StockView:
    """T 日當下的單股視角。所有存取一律切到 ≤ as_of。"""

    as_of: pd.Timestamp
    _data: StockData

    def history(self, lookback: Optional[int] = None) -> pd.DataFrame:
        return self._data.prices.history(self._data.symbol, self.as_of, lookback)

    def close(self) -> Optional[float]:
        h = self.history(lookback=1)
        return float(h["close"].iloc[-1]) if len(h) else None

    def per(self) -> Optional[float]:
        s = self._data._per
        if len(s) == 0:
            return None
        sub = s.loc[s.index <= self.as_of]
        return float(sub["PER"].iloc[-1]) if len(sub) else None

    def pbr(self) -> Optional[float]:
        s = self._data._per
        if len(s) == 0:
            return None
        sub = s.loc[s.index <= self.as_of]
        return float(sub["PBR"].iloc[-1]) if len(sub) else None

    def inst_net(self, lookback: int = 20) -> float:
        s = self._data._chip_net
        if len(s) == 0:
            return 0.0
        sub = s.loc[s.index <= self.as_of].tail(lookback)
        return float(sub.sum())

    def foreign_net_daily(self) -> float:
        s = self._data._foreign_net
        if len(s) == 0:
            return 0.0
        return float(s.loc[self.as_of]) if self.as_of in s.index else 0.0

    def trust_net_daily(self) -> float:
        s = self._data._trust_net
        if len(s) == 0:
            return 0.0
        return float(s.loc[self.as_of]) if self.as_of in s.index else 0.0

    def margin_purchase_bal(self) -> float:
        s = self._data._margin
        if len(s) == 0 or "MarginPurchaseTodayBalance" not in s.columns:
            return 0.0
        sub = s.loc[s.index <= self.as_of]
        return float(sub["MarginPurchaseTodayBalance"].iloc[-1]) if len(sub) else 0.0

    def short_sale_bal(self) -> float:
        s = self._data._margin
        if len(s) == 0 or "ShortSaleTodayBalance" not in s.columns:
            return 0.0
        sub = s.loc[s.index <= self.as_of]
        return float(sub["ShortSaleTodayBalance"].iloc[-1]) if len(sub) else 0.0

    def margin_balance_chg_5(self) -> float:
        s = self._data._margin
        if len(s) == 0 or "MarginPurchaseTodayBalance" not in s.columns:
            return 0.0
        sub = s.loc[s.index <= self.as_of]
        if len(sub) < 6:
            return 0.0
        cur_bal = float(sub["MarginPurchaseTodayBalance"].iloc[-1])
        prev_bal = float(sub["MarginPurchaseTodayBalance"].iloc[-6])
        h = self.history(lookback=20)
        if len(h) == 0:
            return 0.0
        avg_vol = h["volume"].mean()
        if avg_vol == 0:
            return 0.0
        return (cur_bal - prev_bal) / (avg_vol / 1000.0)

    def rev_yoy(self) -> Optional[float]:
        s = self._data._rev_yoy
        if len(s) == 0:
            return None
        sub = s.loc[s.index <= self.as_of]
        return float(sub.iloc[-1]) if len(sub) else None


def load(symbol: str, start: str, end: str, cache_dir: str = "data_cache",
         token: Optional[str] = None) -> StockData:
    paths = fetch_and_cache(symbol, start, end, cache_dir, token)
    frames = {}
    for k, v in paths.items():
        try:
            frames[k] = pd.read_csv(v)
        except Exception:
            frames[k] = pd.DataFrame()
            
    margin_df = frames.get("margin")
    if margin_df is None or margin_df.empty:
        margin_df = pd.DataFrame(columns=["date", "MarginPurchaseTodayBalance", "ShortSaleTodayBalance"])
        
    return StockData(symbol, frames["price"], frames["per"], frames["chips"], frames["revenue"], margin_df)
