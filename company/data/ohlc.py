# -*- coding: utf-8 -*-
"""日線 OHLC 取數（供 ATR／移動停損使用）。

既有的 value_rescreen._yahoo_history 與 value_fundamentals._yahoo_prices 都只保留
close，無 high/low，因此 ATR 無法計算——這正是 Exit Engine 一直把
`atr_trailing_stop` 列在 disabled_inputs 的原因。本模組補上 high/low。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))


def fetch_ohlc(symbol: str, days: int = 400, timeout: float = 30.0) -> list[dict]:
    """回傳日期遞增的 [{date, open, high, low, close}]，已剔除當日未完成 bar。"""
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)
    params = {
        "period1": str(int(start.timestamp())), "period2": str(int(end.timestamp())),
        "interval": "1d", "events": "history",
    }
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    results = (payload.get("chart") or {}).get("result")
    if not results:
        return []
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    opens = quote.get("open") or []

    rows: list[dict] = []
    for idx, stamp in enumerate(timestamps):
        if idx >= len(closes) or closes[idx] is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(stamp, timezone.utc).astimezone(TAIPEI).date().isoformat(),
            "open": float(opens[idx]) if idx < len(opens) and opens[idx] is not None else None,
            "high": float(highs[idx]) if idx < len(highs) and highs[idx] is not None else None,
            "low": float(lows[idx]) if idx < len(lows) and lows[idx] is not None else None,
            "close": float(closes[idx]),
        })

    # Yahoo 在台股開盤前就會先建立當日 K 線，盤中持續更新。若照單全收，
    # 未收盤的當日價會被當成收盤價，讓「收盤確認」規則失去意義
    # （實測 2026-08-31 04:57、台股尚未開盤，Yahoo 已回當日 bar）。
    # 台股 13:30 收盤，保守以台北 14:00 為界。
    taipei_now = datetime.now(TAIPEI)
    if rows and rows[-1]["date"] == taipei_now.date().isoformat() and taipei_now.hour < 14:
        rows.pop()
    return rows
