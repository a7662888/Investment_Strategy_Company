# -*- coding: utf-8 -*-
"""ETF 每日市場與基金資料（標準函式庫，無金鑰）。

來源與欄位都是官方公開資料，各自保留資料日期，不混用不同日的數字：

- 證交所 MIS「ETF 單位變動及淨值揭露」（all_etf.txt）：已發行受益權單位數、
  與前日差異數、成交價、投信或總代理人預估淨值、預估折溢價幅度、前一營業日
  單位淨值。資料由發行人提供，證交所僅轉載（頁面聲明）。欄位對應已於
  2026-10-07 與頁面表頭核對：c 單位數、d 差異數、e 成交價、f 預估淨值、
  g 預估折溢價（%）、h 前一營業日淨值、i/j 資料日期時間。
- 證交所 OpenAPI STOCK_DAY_ALL（上市）與櫃買 OpenAPI 每日收盤行情（上櫃）：
  成交值；櫃買另有最後買賣價。證交所 OpenAPI 常落後一個交易日，故必須比對日期。
- 永豐 Shioaji 快照（選用，只在 GitHub Actions 批次）：同日成交值與最佳買賣價，
  由呼叫端傳入，優先於 OpenAPI。
"""
from __future__ import annotations

import json
import urllib.request

MIS_URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"
TWSE_DAY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def _get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _num(value) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _roc_date(text) -> str | None:
    """民國年 1151006 → 2026-10-06。"""
    raw = str(text or "").strip()
    if len(raw) != 7 or not raw.isdigit():
        return None
    return f"{int(raw[:3]) + 1911:04d}-{raw[3:5]}-{raw[5:]}"


def parse_mis(payload: dict) -> dict[str, dict]:
    rows = {}
    for block in payload.get("a1") or []:
        for r in block.get("msgArray") or []:
            code = str(r.get("a") or "").strip()
            date = str(r.get("i") or "")
            if not code or len(date) != 8:
                continue
            rows[code] = {
                "units": _num(r.get("c")),
                "units_change": _num(r.get("d")),
                "price": _num(r.get("e")),
                "inav": _num(r.get("f")),
                "premium_pct": _num(r.get("g")),
                "prev_nav": _num(r.get("h")),
                "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                "time": r.get("j"),
            }
    return rows


def parse_twse_day(payload: list) -> dict[str, dict]:
    return {
        r["Code"]: {"date": _roc_date(r.get("Date")), "close": _num(r.get("ClosingPrice")),
                    "amount": _num(r.get("TradeValue")), "bid": None, "ask": None}
        for r in payload or [] if r.get("Code")
    }


def parse_tpex_day(payload: list, codes: set[str]) -> dict[str, dict]:
    return {
        r["SecuritiesCompanyCode"]: {
            "date": _roc_date(r.get("Date")), "close": _num(r.get("Close")),
            "amount": _num(r.get("TransactionAmount")),
            "bid": _num(r.get("LatestBidPrice")), "ask": _num(r.get("LatesAskPrice")),
        }
        for r in payload or [] if r.get("SecuritiesCompanyCode") in codes
    }


def fetch_sources(codes: set[str]) -> tuple[dict, dict, dict, dict]:
    """回傳 (mis, twse, tpex, errors)。任何單一來源失敗只記錄，不中斷。"""
    errors: dict[str, str] = {}
    out = []
    for name, url, parser in (
        ("mis", MIS_URL, parse_mis),
        ("twse", TWSE_DAY_URL, parse_twse_day),
        ("tpex", TPEX_DAY_URL, lambda p: parse_tpex_day(p, codes)),
    ):
        try:
            out.append(parser(_get_json(url)))
        except Exception as exc:  # noqa: BLE001
            errors[name] = type(exc).__name__
            out.append({})
    return out[0], out[1], out[2], errors


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid and ask and ask >= bid > 0:
        return round((ask - bid) / ask * 100, 3)
    return None


def build_daily(codes: list[str], trade_date: str, mis: dict, twse: dict, tpex: dict,
                snapshots: dict | None = None) -> dict[str, dict]:
    """同日資料才採用；成交值與價差優先用永豐快照，其次交易所 OpenAPI。"""
    snapshots = snapshots or {}
    rows = {}
    for code in codes:
        nav = mis.get(code) if (mis.get(code) or {}).get("date") == trade_date else None
        snap = snapshots.get(code) if (snapshots.get(code) or {}).get("trade_date") == trade_date else None
        board = next((src[code] for src in (twse, tpex) if (src.get(code) or {}).get("date") == trade_date), None)
        amount = (snap or {}).get("total_amount") or (board or {}).get("amount")
        bid = (snap or {}).get("bid") or (board or {}).get("bid")
        ask = (snap or {}).get("ask") or (board or {}).get("ask")
        units = (nav or {}).get("units")
        prev_nav = (nav or {}).get("prev_nav")
        rows[code] = {
            "units": units,
            "units_change": (nav or {}).get("units_change"),
            "prev_nav": prev_nav,
            "inav": (nav or {}).get("inav"),
            "premium_pct": (nav or {}).get("premium_pct"),
            "aum_billion_twd": round(units * prev_nav / 1e8, 2) if units and prev_nav else None,
            "amount_billion_twd": round(float(amount) / 1e8, 4) if amount else None,
            "spread_pct": spread_pct(bid, ask),
            "sources": {
                "nav": "TWSE MIS" if nav else None,
                "trading": "Shioaji snapshots" if snap else ("TWSE/TPEx OpenAPI" if board else None),
            },
        }
    return rows
