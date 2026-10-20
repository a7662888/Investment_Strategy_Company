# -*- coding: utf-8 -*-
"""盤後一次：母池＋節目觀察清單的籌碼與動能研究表，以及除息行事曆 → 私有資料庫。

只用官方免費資料（證交所、櫃買、集保）＋FinMind 月營收＋Yahoo 還原價。
輸出只作研究脈絡，不改任何買賣判定（見 company/model/chip_flow.py 的回測說明）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from company.data import chip_sources as src
from company.model import chip_flow as cf
from company.model.durable_document import load_document, save_document

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
HISTORY_LOCAL, HISTORY_REMOTE = ROOT / "data" / "chip_history.json", "market/chip_history.json"
REPORT_LOCAL, REPORT_REMOTE = ROOT / "data" / "chip_report.json", "market/chip_report.json"
DIVIDEND_LOCAL, DIVIDEND_REMOTE = ROOT / "data" / "dividend_calendar.json", "market/dividend_calendar.json"
WINDOW = 20
MAX_BACKFILL = 25


def tracked() -> tuple[list[str], dict, dict, set[str]]:
    pool = json.loads((ROOT / "model_artifacts" / "active_pool.json").read_text(encoding="utf-8"))
    names = {s["symbol"].split(".")[0]: s.get("name", "") for s in pool.get("stocks", [])}
    pool_codes = list(names)
    watch = json.loads((ROOT / "data" / "research_watchlist.json").read_text(encoding="utf-8"))
    names = {**watch.get("names", {}), **names}
    groups = {}
    for group, codes in (watch.get("groups") or {}).items():
        for code in codes:
            groups.setdefault(code, []).append(group)
    codes = list(dict.fromkeys(pool_codes + list(groups)))
    return codes, names, groups, set(pool_codes)


def recent_trading_days(end: str, count: int) -> list[str]:
    from company.data.market_calendar import is_trading_day

    days, current = [], date.fromisoformat(end)
    while len(days) < count:
        if is_trading_day(datetime(current.year, current.month, current.day, 12, tzinfo=TAIPEI)):
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return sorted(days)


def main() -> int:
    from company.data.market_calendar import is_trading_day
    from company.model.daily_jobs import latest_completed_session, session_unsettled

    now = datetime.now(TAIPEI)
    if session_unsettled(now):
        print(json.dumps({"skipped": "session not settled; chip flow runs after 14:00"}))
        return 0
    end = latest_completed_session(now)
    if not end:
        print(json.dumps({"error": "no completed session"}))
        return 1
    codes, names, groups, pool = tracked()
    history, _ = load_document(HISTORY_LOCAL, HISTORY_REMOTE)
    history = history or {"schema_version": 1}
    window = recent_trading_days(end, WINDOW)
    errors = {}

    fetched = 0
    for day in cf.missing_days(history, window)[-MAX_BACKFILL:]:
        try:
            flows = src.fetch_institutional(day)
        except Exception as exc:  # noqa: BLE001
            errors[f"institutional:{day}"] = type(exc).__name__
            flows = {}
        if flows:
            cf.merge_day(history, day, flows, codes)
            fetched += 1
        time.sleep(2.5)                      # 證交所 rwd 對短時間大量請求會暫時封鎖
    try:
        shares = src.fetch_shares()
    except Exception as exc:  # noqa: BLE001
        errors["shares"] = type(exc).__name__
        shares = history.get("shares") or {}
    history["shares"] = {c: shares[c] for c in codes if c in shares}
    try:
        tdcc_date, big = src.fetch_tdcc()
        cf.merge_tdcc(history, tdcc_date, big, codes)
    except Exception as exc:  # noqa: BLE001
        errors["tdcc"] = type(exc).__name__

    token = os.environ.get("FINMIND_TOKEN", "")
    revenue = dict(history.get("revenue") or {})
    today = now.date()
    for code in codes:
        if not cf.revenue_stale(revenue.get(code), today):
            continue
        try:
            signal = cf.revenue_signal(src.fetch_monthly_revenue(code, token))
            if signal:
                revenue[code] = {**signal, "fetched": today.isoformat()}
        except Exception as exc:  # noqa: BLE001
            errors.setdefault("revenue", []).append(code)
            print(f"[revenue] {code}: {type(exc).__name__}")
        time.sleep(0.3)
    history["revenue"] = revenue
    history["updated_at"] = now.isoformat()
    save_document(history, HISTORY_LOCAL, HISTORY_REMOTE, f"chore(market): chip history {end}")

    rows = []
    for code in codes:
        symbol, prices = "", []
        try:
            symbol, prices = src.fetch_adjusted(code)
        except Exception as exc:  # noqa: BLE001
            print(f"[price] {code}: {type(exc).__name__}")
        time.sleep(0.3)
        rev = {k: v for k, v in (revenue.get(code) or {}).items() if k != "fetched"}
        rows.append({"code": code, "name": names.get(code, ""), "symbol": symbol, "in_pool": code in pool,
                     "watch_groups": groups.get(code, []),
                     **cf.price_metrics(prices), **rev,
                     **cf.flow_metrics(history, code, history["shares"].get(code), window),
                     **cf.big_holder(history, code)})
    have = [d for d in window if d in (history.get("days") or {})]
    report = {
        "schema_version": 1, "as_of": end, "generated_at": now.isoformat(),
        "flow_window": {"from": have[0] if have else None, "to": have[-1] if have else None, "days": len(have)},
        "tdcc_dates": sorted((history.get("tdcc") or {}).keys())[-2:],
        "watchlist_source": json.loads((ROOT / "data" / "research_watchlist.json").read_text(encoding="utf-8")).get("source"),
        "rows": rows, "errors": errors,
        "definitions": {
            "discount_pct": "收盤價相對近 252 交易日最高價（未還原）的距離",
            "foreign_trust": "外本比／投本比 = 期間買賣超股數 ÷ 已發行股數；外資不含外資自營商",
            "trust_streak": "投信連續買超（+）或賣超（−）天數",
            "big400": "集保 400 張以上持股比例（每週五資料），週變化 = 與前一週差（百分點）",
            "ma_strategy": "近 5 年：收盤站上均線隔日收盤買、跌破隔日收盤賣；含息還原，扣手續費 0.1425%×2 與證交稅 0.3%",
        },
        "disclaimer": "研究參考，尚未驗證，不改變任何買賣判定。月線／季線進出在 2026-10-20 回測的 38 檔中，扣成本後年化幾乎全數落後買進持有。",
        "sources": {"flows": "證交所 T86、櫃買三大法人日報", "shares": "證交所／櫃買公司基本資料",
                    "big400": "集保結算所股權分散表 opendata 1-5", "revenue": "FinMind TaiwanStockMonthRevenue",
                    "price": "Yahoo Finance（還原價）"},
    }
    storage = save_document(report, REPORT_LOCAL, REPORT_REMOTE, f"chore(market): chip report {end}")

    try:
        calendar = cf.dividend_calendar(src.fetch_dividends(), today.isoformat(), is_trading_day, set(codes))
        save_document({"schema_version": 1, "as_of": now.isoformat(), "rows": calendar,
                       "sources": "證交所 TWT48U 除權除息預告、櫃買除權除息預告"},
                      DIVIDEND_LOCAL, DIVIDEND_REMOTE, f"chore(market): dividend calendar {end}")
    except Exception as exc:  # noqa: BLE001
        errors["dividends"] = type(exc).__name__

    print(json.dumps({"as_of": end, "codes": len(codes), "flow_days_fetched": fetched, "flow_days": len(have),
                      "errors": errors, "durable": storage.get("durable")}, ensure_ascii=False))
    return 0 if storage.get("local_saved") else 1


if __name__ == "__main__":
    sys.exit(main())
