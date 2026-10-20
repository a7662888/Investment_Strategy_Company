# -*- coding: utf-8 -*-
"""籌碼與除息的官方免費來源：解析器與取數（解析器不連網，可單獨測試）。

- 三大法人：證交所 T86（上市）、櫃買 insti/dailyTrade（上櫃）；股數單位
- 已發行股數：證交所 t187ap03_L、櫃買 mopsfin_t187ap03_O（實收資本 ÷ 面額）
- 集保股權分散：opendata 1-5（每週；分級 12–15 = 400 張以上）
- 除權息預告：證交所 TWT48U、櫃買 tpex_exright_prepost
- 價格：Yahoo chart（adjclose = 含息還原，供均線策略回測）
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
BIG_LEVELS = {"12", "13", "14", "15"}      # 400,001 股以上
MIN_HOLDERS = 10                          # 集保總人數太少（如單一帳戶 100%）視為無意義


def _context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # Python 3.13 起預設 VERIFY_X509_STRICT，證交所等憑證缺 SKI 會被拒；仍驗證憑證鏈。
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


def get(url: str, headers: dict | None = None, timeout: float = 60, tries: int = 3) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_context()) as response:
                return response.read()
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            time.sleep(3)
    raise RuntimeError("unreachable")


def num(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _roc(text: str) -> str | None:
    """'115年11月03日'、'1151103'、'115/11/03' → '2026-11-03'。"""
    digits = re.findall(r"\d+", str(text or ""))
    if len(digits) == 1 and len(digits[0]) == 7:
        digits = [digits[0][:3], digits[0][3:5], digits[0][5:]]
    if len(digits) < 3:
        return None
    try:
        return f"{int(digits[0]) + 1911:04d}-{int(digits[1]):02d}-{int(digits[2]):02d}"
    except ValueError:
        return None


# ---------- 三大法人 ----------
def parse_t86(payload: dict) -> dict[str, tuple[float, float]]:
    if payload.get("stat") != "OK":
        return {}
    fields = payload.get("fields") or []
    fi = fields.index("外陸資買賣超股數(不含外資自營商)")
    ti = fields.index("投信買賣超股數")
    return {row[0].strip(): (num(row[fi]) or 0.0, num(row[ti]) or 0.0) for row in payload.get("data") or []}


def parse_tpex_insti(payload: dict) -> dict[str, tuple[float, float]]:
    tables = payload.get("tables") or [{}]
    table = tables[0] or {}
    # 欄位順序：2-4 外資及陸資(不含外資自營商)、5-7 外資自營商、8-10 外資合計、11-13 投信。
    return {row[0].strip(): (num(row[4]) or 0.0, num(row[13]) or 0.0)
            for row in table.get("data") or [] if len(row) > 13}


def fetch_institutional(day: str) -> dict[str, tuple[float, float]]:
    """day = 'YYYY-MM-DD'；回傳上市＋上櫃 {code: (外資買賣超股數, 投信買賣超股數)}，該日無資料回空。"""
    t86 = parse_t86(json.loads(get(
        f"https://www.twse.com.tw/rwd/zh/fund/T86?date={day.replace('-', '')}&selectType=ALLBUT0999&response=json")))
    if not t86:
        return {}
    tpex = parse_tpex_insti(json.loads(get(
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date="
        + urllib.parse.quote(day.replace("-", "/")) + "&response=json")))
    return {**tpex, **t86}


# ---------- 股數 ----------
def parse_shares_twse(rows: list[dict]) -> dict[str, float]:
    return {r["公司代號"].strip(): v for r in rows
            if (v := num(r.get("已發行普通股數或TDR原股發行股數")))}


def parse_shares_tpex(rows: list[dict]) -> dict[str, float]:
    out = {}
    for r in rows:
        capital = num(r.get("Paidin.Capital.NTDollars"))
        match = re.search(r"([\d.]+)", str(r.get("ParValueOfCommonStock") or "10"))
        par = float(match.group(1)) if match else 10.0
        if capital:
            out[str(r.get("SecuritiesCompanyCode") or "").strip()] = capital / (par or 10.0)
    return out


def fetch_shares() -> dict[str, float]:
    twse = parse_shares_twse(json.loads(get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")))
    tpex = parse_shares_tpex(json.loads(get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O")))
    return {**tpex, **twse}


# ---------- 集保大戶 ----------
def parse_tdcc(text: str) -> tuple[str, dict[str, float]]:
    big: dict[str, float] = {}
    holders: dict[str, float] = {}
    date = ""
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(row) < 6 or not row[0].strip().isdigit():
            continue
        date = row[0].strip()
        code, level = row[1].strip(), row[2].strip()
        if level in BIG_LEVELS:
            big[code] = big.get(code, 0.0) + (num(row[5]) or 0.0)
        elif level == "17":
            holders[code] = num(row[3]) or 0.0
    iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else ""
    return iso, {c: round(v, 2) for c, v in big.items() if holders.get(c, 0) >= MIN_HOLDERS}


def fetch_tdcc() -> tuple[str, dict[str, float]]:
    return parse_tdcc(get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", timeout=120).decode("utf-8-sig"))


# ---------- 除權息 ----------
def parse_twt48u(payload: dict) -> list[dict]:
    if payload.get("stat") != "OK":
        return []
    fields = payload.get("fields") or []
    idx = {name: fields.index(name) for name in ("除權除息日期", "股票代號", "名稱", "除權息", "現金股利")}
    out = []
    for row in payload.get("data") or []:
        cash = num(row[idx["現金股利"]])
        out.append({"code": row[idx["股票代號"]].strip(), "name": row[idx["名稱"]].strip(),
                    "ex_date": _roc(row[idx["除權除息日期"]]), "kind": row[idx["除權息"]].strip(),
                    "cash_dividend": cash, "market": "上市"})
    return [r for r in out if r["ex_date"]]


def parse_tpex_exright(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        cash = num(r.get("CashDividend"))
        out.append({"code": str(r.get("SecuritiesCompanyCode") or "").strip(), "name": str(r.get("CompanyName") or "").strip(),
                    "ex_date": _roc(r.get("ExRrightsExDividendDate")), "kind": str(r.get("ExRrightsExDividend") or "").strip(),
                    "cash_dividend": cash if cash else None, "market": "上櫃"})
    return [r for r in out if r["ex_date"] and r["code"]]


def fetch_dividends() -> list[dict]:
    rows = parse_twt48u(json.loads(get("https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json")))
    try:
        rows += parse_tpex_exright(json.loads(get("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost")))
    except Exception as exc:  # noqa: BLE001
        print(f"[dividends] tpex unavailable: {type(exc).__name__}")
    return rows


# ---------- 價格（還原） ----------
def fetch_adjusted(code: str, years: int = 6) -> tuple[str, list[dict]]:
    """回傳 (Yahoo 代號, [{date, close, high, adj}])；先試上市 .TW 再試上櫃 .TWO。"""
    end = int(time.time()) + 86400
    start = end - 86400 * 365 * years
    for suffix in (".TW", ".TWO"):
        symbol = code + suffix
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={start}&period2={end}"
               "&interval=1d&events=div%2Csplit")
        try:
            results = json.loads(get(url, tries=2)).get("chart", {}).get("result")
        except Exception:  # noqa: BLE001
            continue
        if not results or not results[0].get("timestamp"):
            continue
        result = results[0]
        quote = result["indicators"]["quote"][0]
        adj = ((result["indicators"].get("adjclose") or [{}])[0].get("adjclose")) or quote["close"]
        rows = []
        for i, stamp in enumerate(result["timestamp"]):
            close = quote["close"][i]
            if close is None or adj[i] is None:
                continue
            rows.append({"date": datetime.fromtimestamp(stamp, TAIPEI).date().isoformat(), "close": close,
                         "high": quote["high"][i] or close, "adj": adj[i]})
        if len(rows) > 60:
            return symbol, rows
    return "", []


def fetch_monthly_revenue(code: str, token: str, months: int = 26) -> list[dict]:
    start = (datetime.now(TAIPEI) - timedelta(days=31 * months)).date().isoformat()
    url = ("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue"
           f"&data_id={code}&start_date={start}")
    payload = json.loads(get(url, headers={"Authorization": f"Bearer {token}"} if token else None, tries=2))
    return payload.get("data") or []
