# -*- coding: utf-8 -*-
"""規則式母池候選（pool_rules_v2）。

原本的母池是從一份手寫的約 120 檔名單依成交金額挑 100 檔：名單靠人維護，
而且成交熱的多半是當紅科技股，天生容易卡在品質篩（2026-11-12 實測 100 檔中
62 檔未過、只有 1 檔可分批研究）。

這裡改成全部用官方公開資料、可重現的規則：
  1. 範圍：證交所／櫃買「公司基本資料」中的普通股（四碼代號），排除管理股票與存託憑證。
  2. 上市（櫃）滿三年：估值位階需要三年歷史，不滿三年只會得到「資料不足」。
  3. 以「已發行股數 × 收盤價」估市值，依市值排序。
每個來源各一次請求，不逐檔打 API。流動性門檻與產業分散在 universe.refresh_all 處理。
"""
from __future__ import annotations

import json
import re
from datetime import date

from company.data.chip_sources import get, num, parse_shares_tpex, parse_shares_twse

TWSE_BASICS = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_BASICS = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TWSE_QUOTES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

# 證交所／櫃買共用的產業別代碼。未列入者以「其他(代碼)」呈現，不猜測。
INDUSTRY = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "14": "建材營造業", "15": "航運業", "16": "觀光餐旅", "17": "金融保險業",
    "18": "貿易百貨業", "19": "綜合", "20": "其他業", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}
EXCLUDED_INDUSTRY = {"80", "91"}          # 管理股票、存託憑證
FINANCIAL_SECTOR = "金融保險業"
MIN_LISTED_YEARS = 3
_COMMON_CODE = re.compile(r"[1-9]\d{3}")


def sector_name(code: object) -> str:
    key = str(code or "").strip().zfill(2)
    return INDUSTRY.get(key, f"其他({key})")


def _ymd(text: object) -> str | None:
    digits = re.sub(r"\D", "", str(text or ""))
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
    except ValueError:
        return None


def parse_basics_twse(rows: list[dict]) -> dict[str, dict]:
    shares = parse_shares_twse(rows)
    out = {}
    for r in rows:
        code = str(r.get("公司代號") or "").strip()
        out[code] = {"name": str(r.get("公司簡稱") or "").strip(), "industry": str(r.get("產業別") or "").strip(),
                     "listed": _ymd(r.get("上市日期")), "shares": shares.get(code), "suffix": ".TW"}
    return out


def parse_basics_tpex(rows: list[dict]) -> dict[str, dict]:
    shares = parse_shares_tpex(rows)
    out = {}
    for r in rows:
        code = str(r.get("SecuritiesCompanyCode") or "").strip()
        out[code] = {"name": str(r.get("CompanyAbbreviation") or "").strip(),
                     "industry": str(r.get("SecuritiesIndustryCode") or "").strip(),
                     "listed": _ymd(r.get("DateOfListing")), "shares": shares.get(code), "suffix": ".TWO"}
    return out


def parse_quotes(rows: list[dict], code_key: str, close_key: str, value_key: str) -> dict[str, dict]:
    out = {}
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get(code_key) or "").strip()
        close, value = num(r.get(close_key)), num(r.get(value_key))
        if code and close and close > 0:
            out[code] = {"close": close, "day_turnover": value or 0.0}
    return out


def build_candidates(basics: dict[str, dict], quotes: dict[str, dict], today: date,
                     min_listed_years: int = MIN_LISTED_YEARS,
                     exempt_listing_age: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """套用範圍與上市年資規則，回傳依估計市值由大到小排序的候選。

    exempt_listing_age：既有母池成員（代號）。上櫃轉上市會讓「上市日期」重新起算
    （例：藥華藥），但估值歷史其實連續，不因此被剔除。
    """
    try:
        cutoff = today.replace(year=today.year - min_listed_years).isoformat()
    except ValueError:                       # 2/29
        cutoff = today.replace(year=today.year - min_listed_years, day=28).isoformat()
    out = []
    for code, info in basics.items():
        quote = quotes.get(code)
        if (not _COMMON_CODE.fullmatch(code) or info.get("industry") in EXCLUDED_INDUSTRY
                or not quote or not info.get("shares")):
            continue
        if code not in exempt_listing_age and (not info.get("listed") or info["listed"] > cutoff):
            continue
        out.append({"code": code, "symbol": code + info["suffix"], "name": info["name"] or code,
                    "sector": sector_name(info["industry"]), "listed": info["listed"],
                    "market_cap": round(info["shares"] * quote["close"], 0),
                    "day_turnover": quote["day_turnover"]})
    return sorted(out, key=lambda r: r["market_cap"], reverse=True)


def fetch_candidates(today: date | None = None,
                     exempt_listing_age: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    basics = {**parse_basics_tpex(json.loads(get(TPEX_BASICS))), **parse_basics_twse(json.loads(get(TWSE_BASICS)))}
    quotes = {**parse_quotes(json.loads(get(TPEX_QUOTES)), "SecuritiesCompanyCode", "Close", "TransactionAmount"),
              **parse_quotes(json.loads(get(TWSE_QUOTES)), "Code", "ClosingPrice", "TradeValue")}
    return build_candidates(basics, quotes, today or date.today(), exempt_listing_age=exempt_listing_age)
