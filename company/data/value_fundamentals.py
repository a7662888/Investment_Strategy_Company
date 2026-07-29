# -*- coding: utf-8 -*-
"""Build and incrementally refresh the 100-stock value-fundamentals snapshot."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from company.model.durable_document import load_document, save_document

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH = ROOT / "data" / "value_fundamentals_full.json"
REMOTE_PATH = os.environ.get("VALUE_FUNDAMENTALS_PATH", "value/fundamentals_full.json")
API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASETS = (
    "TaiwanStockFinancialStatements",
    "TaiwanStockBalanceSheet",
    "TaiwanStockCashFlowsStatement",
    "TaiwanStockMonthRevenue",
    "TaiwanStockPER",
)


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _get(items: list[dict], *types: str):
    wanted = set(types)
    for item in items:
        if item.get("type") in wanted:
            return _num(item.get("value"))
    return None


def _fetch(dataset: str, code: str, start_date: str, end_date: str, attempts: int = 3) -> list[dict]:
    params = {"dataset": dataset, "data_id": code, "start_date": start_date, "end_date": end_date}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        params["token"] = token
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "investment-fundamentals-refresh"})
            with urllib.request.urlopen(req, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == 200:
                return payload.get("data") or []
            last_error = payload.get("msg") or f"status_{payload.get('status')}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTPError: HTTP Error {exc.code}: {exc.reason}"
            if exc.code in (402, 403):
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"FinMind {dataset} {code}: {last_error}")


def _quarter_cashflows(cf_by_date: dict[str, list[dict]]) -> dict[str, float | None]:
    """Convert year-to-date cash-flow statements to stand-alone quarters."""
    result: dict[str, float | None] = {}
    previous_by_year: dict[int, float] = {}
    for period in sorted(cf_by_date):
        cumulative = _get(
            cf_by_date[period],
            "NetCashInflowFromOperatingActivities",
            "CashFlowsFromOperatingActivities",
        )
        if cumulative is None:
            result[period] = None
            continue
        year = int(period[:4])
        previous = previous_by_year.get(year)
        result[period] = cumulative if previous is None else cumulative - previous
        previous_by_year[year] = cumulative
    return result


def parse_quarterly(fin: list[dict], balance: list[dict], cashflow: list[dict]) -> list[dict]:
    grouped: dict[str, dict[str, list[dict]]] = {}
    for key, rows in (("fin", fin), ("bs", balance), ("cf", cashflow)):
        for item in rows:
            period = item.get("date")
            if period:
                grouped.setdefault(period, {"fin": [], "bs": [], "cf": []})[key].append(item)
    quarter_ocf = _quarter_cashflows({period: parts["cf"] for period, parts in grouped.items()})
    output = []
    for period in sorted(grouped):
        parts = grouped[period]
        revenue = _get(parts["fin"], "Revenue")
        gross_profit = _get(parts["fin"], "GrossProfit")
        operating_income = _get(parts["fin"], "OperatingIncome")
        net_income = _get(parts["fin"], "IncomeAfterTaxes", "IncomeAfterTax")
        assets = _get(parts["bs"], "TotalAssets")
        liabilities = _get(parts["bs"], "Liabilities")
        equity = _get(parts["bs"], "Equity", "EquityAttributableToOwnersOfParent")
        current_assets = _get(parts["bs"], "CurrentAssets")
        current_liabilities = _get(parts["bs"], "CurrentLiabilities")
        ocf = quarter_ocf.get(period)
        if not any(v is not None for v in (revenue, net_income, assets, equity, ocf)):
            continue

        def ratio(top, bottom, scale=1.0):
            return top / bottom * scale if top is not None and bottom not in (None, 0) else None

        month = int(period[5:7])
        available_from = (date.fromisoformat(period) + timedelta(days=90 if month == 12 else 45)).isoformat()
        output.append({
            "period": period,
            "available_from": available_from,
            "revenue": revenue,
            "gross_profit_margin": ratio(gross_profit, revenue, 100),
            "operating_income_margin": ratio(operating_income, revenue, 100),
            "net_profit_margin": ratio(net_income, revenue, 100),
            "net_income": net_income,
            "eps": _get(parts["fin"], "EPS"),
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "debt_ratio": ratio(liabilities, assets, 100),
            "current_ratio": ratio(current_assets, current_liabilities),
            "roe": ratio(net_income, equity, 100),
            "operating_cash_flow": ocf,
            "earnings_quality": ratio(ocf, net_income),
        })
    return output[-12:]


def parse_monthly_revenue(rows: list[dict]) -> list[dict]:
    values = {}
    for row in rows:
        year, month = row.get("revenue_year"), row.get("revenue_month")
        value = _num(row.get("revenue"))
        if year and month and value is not None:
            values[(int(year), int(month))] = value
    output = []
    for (year, month), value in sorted(values.items()):
        prior = values.get((year - 1, month))
        output.append({
            "date": next((r.get("date") for r in rows if r.get("revenue_year") == year and r.get("revenue_month") == month), None),
            "year": year,
            "month": month,
            "revenue": value,
            "yoy": (value / prior - 1) * 100 if prior else None,
        })
    return output[-13:]


def _quantiles(values: list[float]) -> list[float]:
    ordered = sorted(values)
    if not ordered:
        return []
    result = []
    for percentile in range(0, 101, 5):
        index = round((len(ordered) - 1) * percentile / 100)
        result.append(round(ordered[index], 6))
    return result


def parse_valuation(rows: list[dict]) -> dict:
    valid = [row for row in rows if row.get("date")]
    valid.sort(key=lambda row: row["date"])
    latest = valid[-1] if valid else {}
    pes = [v for row in valid if (v := _num(row.get("PER"))) is not None and v > 0]
    pbs = [v for row in valid if (v := _num(row.get("PBR"))) is not None and v > 0]
    return {
        "date": latest.get("date"),
        "pe": _num(latest.get("PER")),
        "pb": _num(latest.get("PBR")),
        "yield": _num(latest.get("dividend_yield")),
        "pe_quantiles_5pct": _quantiles(pes),
        "pb_quantiles_5pct": _quantiles(pbs),
        "history_start": valid[0].get("date") if valid else None,
        "observations": len(valid),
    }


def completeness(stock: dict) -> dict:
    quarterly = stock.get("quarterly") or []
    monthly = stock.get("monthly_revenue") or []
    valuation = stock.get("valuation") or {}
    checks = {
        "quarterly_4plus": len(quarterly) >= 4,
        "balance_sheet": any(q.get("assets") is not None and q.get("equity") is not None for q in quarterly[-4:]),
        "cash_flow": any(q.get("operating_cash_flow") is not None for q in quarterly[-4:]),
        # 月營收是台灣市場加值欄位；Yahoo fallback 至少須有四季營收，仍可完成品質硬篩。
        "revenue_history": len(monthly) >= 12 or sum(q.get("revenue") is not None for q in quarterly[-4:]) >= 4,
        "valuation": bool(valuation.get("date")) and bool(
            valuation.get("pe_quantiles_5pct") or valuation.get("pb_quantiles_5pct")
        ),
    }
    return {"complete": all(checks.values()), "checks": checks, "score": sum(checks.values()), "total": len(checks)}


YAHOO_QUARTERLY = {
    "quarterlyTotalRevenue": "revenue",
    "quarterlyGrossProfit": "gross_profit",
    "quarterlyOperatingIncome": "operating_income",
    "quarterlyNetIncome": "net_income",
    "quarterlyBasicEPS": "eps",
    "quarterlyTotalAssets": "assets",
    "quarterlyTotalLiabilitiesNetMinorityInterest": "liabilities",
    "quarterlyStockholdersEquity": "equity",
    "quarterlyCurrentAssets": "current_assets",
    "quarterlyCurrentLiabilities": "current_liabilities",
    "quarterlyOperatingCashFlow": "operating_cash_flow",
}
YAHOO_ANNUAL = {
    "annualNetIncome": "net_income",
    "annualStockholdersEquity": "equity",
    "annualDilutedAverageShares": "shares",
    "annualBasicAverageShares": "basic_shares",
}
YAHOO_OTC_CODES = {"8299", "3324", "6182", "3529", "4966"}


def _yahoo_timeseries(symbol: str, types: list[str], years: int = 6) -> dict[str, list[dict]]:
    now = datetime.now().timestamp()
    params = {
        "type": ",".join(types),
        "period1": int(now - years * 365.25 * 86400),
        "period2": int(now + 86400),
    }
    url = (
        "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
        f"{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=35) as response:
        payload = json.loads(response.read().decode("utf-8"))
    output = {}
    for result in (payload.get("timeseries") or {}).get("result") or []:
        key = next((candidate for candidate in types if candidate in result), None)
        if key:
            output[key] = result.get(key) or []
    return output


def _yahoo_prices(symbol: str, days: int = 1300) -> list[dict]:
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)
    params = {
        "period1": int(start.timestamp()), "period2": int(end.timestamp()),
        "interval": "1d", "events": "history",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=35) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
    timestamps = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    return [
        {"date": datetime.fromtimestamp(timestamp).date().isoformat(), "close": float(closes[idx])}
        for idx, timestamp in enumerate(timestamps)
        if idx < len(closes) and closes[idx] is not None
    ]


def _reported_value(item: dict):
    return _num((item.get("reportedValue") or {}).get("raw"))


def _yahoo_quarterly(series: dict[str, list[dict]]) -> list[dict]:
    periods: dict[str, dict] = {}
    for yahoo_key, local_key in YAHOO_QUARTERLY.items():
        for item in series.get(yahoo_key) or []:
            period = item.get("asOfDate")
            if period:
                periods.setdefault(period, {})[local_key] = _reported_value(item)
    output = []
    for period in sorted(periods):
        values = periods[period]
        revenue = values.get("revenue")
        net_income = values.get("net_income")
        equity = values.get("equity")

        def ratio(top, bottom, scale=1.0):
            return top / bottom * scale if top is not None and bottom not in (None, 0) else None

        month = int(period[5:7])
        output.append({
            "period": period,
            "available_from": (date.fromisoformat(period) + timedelta(days=90 if month == 12 else 45)).isoformat(),
            "revenue": revenue,
            "gross_profit_margin": ratio(values.get("gross_profit"), revenue, 100),
            "operating_income_margin": ratio(values.get("operating_income"), revenue, 100),
            "net_profit_margin": ratio(net_income, revenue, 100),
            "net_income": net_income,
            "eps": values.get("eps"),
            "assets": values.get("assets"),
            "liabilities": values.get("liabilities"),
            "equity": equity,
            "debt_ratio": ratio(values.get("liabilities"), values.get("assets"), 100),
            "current_ratio": ratio(values.get("current_assets"), values.get("current_liabilities")),
            "roe": ratio(net_income, equity, 100),
            "operating_cash_flow": values.get("operating_cash_flow"),
            "earnings_quality": ratio(values.get("operating_cash_flow"), net_income),
        })
    usable = [q for q in output if q.get("revenue") is not None or q.get("net_income") is not None]
    return usable[-4:]


def _yahoo_valuation(series: dict[str, list[dict]], prices: list[dict]) -> dict:
    annual: dict[str, dict] = {}
    for yahoo_key, local_key in YAHOO_ANNUAL.items():
        for item in series.get(yahoo_key) or []:
            period = item.get("asOfDate")
            if period:
                annual.setdefault(period, {})[local_key] = _reported_value(item)
    metrics = []
    last_shares = None
    for period in sorted(annual):
        values = annual[period]
        shares = values.get("shares") or values.get("basic_shares") or last_shares
        if shares:
            last_shares = shares
        if shares and values.get("net_income") is not None and values.get("equity") is not None:
            metrics.append({
                "available_from": (date.fromisoformat(period) + timedelta(days=90)).isoformat(),
                "eps": values["net_income"] / shares,
                "bvps": values["equity"] / shares,
            })
    pes, pbs, latest = [], [], None
    for price in prices:
        applicable = next((metric for metric in reversed(metrics) if metric["available_from"] <= price["date"]), None)
        if not applicable:
            continue
        pe = price["close"] / applicable["eps"] if applicable["eps"] > 0 else None
        pb = price["close"] / applicable["bvps"] if applicable["bvps"] > 0 else None
        if pe and pe > 0:
            pes.append(pe)
        if pb and pb > 0:
            pbs.append(pb)
        latest = {"date": price["date"], "pe": pe, "pb": pb}
    latest = latest or {}
    return {
        "date": latest.get("date"), "pe": latest.get("pe"), "pb": latest.get("pb"), "yield": None,
        "pe_quantiles_5pct": _quantiles(pes), "pb_quantiles_5pct": _quantiles(pbs),
        "history_start": prices[0]["date"] if prices else None, "observations": max(len(pes), len(pbs)),
    }


def fetch_stock_yahoo(code: str, name: str, finmind_error: str | None = None) -> dict:
    symbol = f"{code}.TWO" if code in YAHOO_OTC_CODES else f"{code}.TW"
    types = list(YAHOO_QUARTERLY) + list(YAHOO_ANNUAL)
    series = _yahoo_timeseries(symbol, types)
    prices = _yahoo_prices(symbol)
    stock = {
        "symbol": code, "name": name or code, "is_etf": False,
        "quarterly": _yahoo_quarterly(series), "monthly_revenue": [],
        "valuation": _yahoo_valuation(series, prices),
        "source": "Yahoo Finance fallback",
        "source_note": "FinMind quota fallback; quarterly statements and annualized valuation history",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    if finmind_error:
        stock["finmind_error"] = finmind_error
    stock["completeness"] = completeness(stock)
    return stock


def fetch_stock(code: str, name: str, today: date | None = None) -> dict:
    today = today or date.today()
    end = today.isoformat()
    financial_start = (today - timedelta(days=365 * 4)).isoformat()
    revenue_start = (today - timedelta(days=800)).isoformat()
    valuation_start = (today - timedelta(days=365 * 3)).isoformat()
    try:
        fetched = {
            dataset: _fetch(dataset, code, valuation_start if dataset == "TaiwanStockPER" else (
                revenue_start if dataset == "TaiwanStockMonthRevenue" else financial_start
            ), end)
            for dataset in DATASETS
        }
    except Exception as exc:
        return fetch_stock_yahoo(code, name, finmind_error=f"{type(exc).__name__}: {exc}")
    stock = {
        "symbol": code,
        "name": name or code,
        "is_etf": False,
        "quarterly": parse_quarterly(
            fetched["TaiwanStockFinancialStatements"],
            fetched["TaiwanStockBalanceSheet"],
            fetched["TaiwanStockCashFlowsStatement"],
        ),
        "monthly_revenue": parse_monthly_revenue(fetched["TaiwanStockMonthRevenue"]),
        "valuation": parse_valuation(fetched["TaiwanStockPER"]),
        "source": "FinMind",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    stock["completeness"] = completeness(stock)
    return stock


def load_fundamentals(prefer_remote: bool = True) -> tuple[dict, dict]:
    doc, meta = load_document(LOCAL_PATH, REMOTE_PATH, prefer_remote=prefer_remote)
    return (doc or {}), meta


def _refresh_order(pool: list[dict], existing: dict[str, dict]) -> list[dict]:
    def key(item):
        code = item["symbol"].split(".")[0]
        stock = existing.get(code) or {}
        return (0 if not stock.get("completeness", {}).get("complete") else 1, stock.get("refreshed_at") or "")
    return sorted(pool, key=key)


def refresh_pool(pool: dict, existing_doc: dict | None = None, batch_size: int = 5, force_all: bool = False) -> tuple[dict, dict]:
    existing_doc = existing_doc or {}
    stocks = dict(existing_doc.get("stocks") or {})
    pool_rows = pool.get("stocks") or []
    selected = _refresh_order(pool_rows, stocks)
    if not force_all:
        selected = selected[:max(0, batch_size)]
    refreshed, errors = [], {}
    for row in selected:
        code = row["symbol"].split(".")[0]
        try:
            stocks[code] = fetch_stock(code, row.get("name") or code)
            refreshed.append(code)
        except Exception as exc:
            errors[code] = f"{type(exc).__name__}: {exc}"
    pool_codes = {row["symbol"].split(".")[0] for row in pool_rows}
    stocks = {code: stock for code, stock in stocks.items() if code in pool_codes}
    complete_count = sum(bool(stock.get("completeness", {}).get("complete")) for stock in stocks.values())
    doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_as_of": pool.get("as_of"),
        "target_count": len(pool_codes),
        "coverage": {
            "present": len(stocks),
            "complete": complete_count,
            "incomplete": len(stocks) - complete_count,
            "missing": len(pool_codes - set(stocks)),
        },
        "refresh_policy": {
            "mode": "oldest-or-incomplete-first",
            "daily_batch": batch_size,
            "full_cycle_business_days": math.ceil(len(pool_codes) / batch_size) if batch_size else None,
        },
        "stocks": stocks,
        "last_run": {"refreshed": refreshed, "errors": errors},
    }
    return doc, {"refreshed": refreshed, "errors": errors, "coverage": doc["coverage"]}


def save_fundamentals(doc: dict) -> dict:
    return save_document(doc, LOCAL_PATH, REMOTE_PATH, "chore(value): refresh 100-stock fundamentals")
