# -*- coding: utf-8 -*-
"""
B 數據機工 — FinMind 真實台股資料介接。

可信細節:
  * 基本面 announce_date 採「財報所屬季季末 + DISCLOSURE_LAG_DAYS」保守估計,
    因為 FinMind 財報資料給的是期別、非實際公布日。寧可晚看到、不可早看到。
  * token 從參數或環境變數 FINMIND_TOKEN 取得;無 token 時仍可少量試用。

注意:本檔需要網路與(建議)FinMind token,本機離線時請改用 synthetic.generate()。
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import requests

from .interfaces import ChipData, Dataset, FundamentalData, NewsData, PriceData

API = "https://api.finmindtrade.com/api/v4/data"
DISCLOSURE_LAG_DAYS = 45  # 財報季末到實際公布的保守落差


def _fetch(dataset: str, params: dict, token: Optional[str]) -> pd.DataFrame:
    q = {"dataset": dataset, **params}
    if token:
        q["token"] = token
    resp = requests.get(API, params=q, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != 200:
        raise RuntimeError(f"FinMind 回應異常:{payload.get('msg')}")
    return pd.DataFrame(payload.get("data", []))


def load(
    symbols: list[str],
    start: str,
    end: str,
    token: Optional[str] = None,
) -> Dataset:
    token = token or os.environ.get("FINMIND_TOKEN")

    price_frames, fund_frames, chip_frames = [], [], []
    for sym in symbols:
        base = {"data_id": sym, "start_date": start, "end_date": end}

        p = _fetch("TaiwanStockPrice", base, token)
        if not p.empty:
            p = p.rename(
                columns={
                    "stock_id": "symbol",
                    "open": "open",
                    "max": "high",
                    "min": "low",
                    "close": "close",
                    "Trading_Volume": "volume",
                }
            )
            price_frames.append(
                p[["date", "symbol", "open", "high", "low", "close", "volume"]]
            )

        f = _fetch("TaiwanStockFinancialStatements", base, token)
        if not f.empty and "type" in f.columns:
            # 取每期 EPS / 營收等;此處示範以期末日 + lag 當公布日
            piv = f.pivot_table(
                index=["stock_id", "date"], columns="type", values="value"
            ).reset_index()
            piv["announce_date"] = pd.to_datetime(piv["date"]) + pd.Timedelta(
                days=DISCLOSURE_LAG_DAYS
            )
            piv = piv.rename(columns={"stock_id": "symbol", "date": "period"})
            fund_frames.append(piv)

        c = _fetch("TaiwanStockInstitutionalInvestorsBuySell", base, token)
        if not c.empty:
            c["inst_net"] = c["buy"] - c["sell"]
            c = c.rename(columns={"stock_id": "symbol"})
            chip_frames.append(
                c.groupby(["date", "symbol"], as_index=False)["inst_net"].sum()
            )

    prices = PriceData(pd.concat(price_frames, ignore_index=True))
    funds = FundamentalData(
        pd.concat(fund_frames, ignore_index=True) if fund_frames else None
    )
    chips = ChipData(
        pd.concat(chip_frames, ignore_index=True) if chip_frames else None
    )
    # 新聞/輿情:FinMind 免費層無結構化新聞情緒;此處留空。
    # 接 PTT 股版/新聞 API 時,務必以『見報日』填 available_date(防線①)。
    news = NewsData(None)
    return Dataset(prices=prices, fundamentals=funds, chips=chips, news=news)
