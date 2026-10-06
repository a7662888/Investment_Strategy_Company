# -*- coding: utf-8 -*-
"""ETF 研究候選與停扣檢查（每日盤後）。

門檻依 2026-10-06 橫斷面分布訂定（301 檔有完整資料）：規模中位數約 46 億、
日成交值中位數約 0.27 億、受益人數第 90 百分位約 12 萬、|預估折溢價| 第 90
百分位約 0.66%。候選門檻約落在前四分之一，當日 31 檔符合。

紀律：
- 只用規模、流動性、受益人數、上市年資等「可不可以長期定期定額」的條件，
  不以過去報酬排序，避免追逐近期績效。
- 費用是長期報酬的重要因素，但官方表只涵蓋少數基金，缺值一律標「費用待查」，
  不以其他基金或推估補值。
- 停扣檢查只是提醒人工檢查，不是賣出訊號；ETF 子軌仍為定期定額。
"""
from __future__ import annotations

import html
from datetime import date

CRITERIA = {
    "min_aum_billion_twd": 50.0,
    "min_avg_amount_billion_twd": 0.5,
    "min_holders": 50_000,
    "min_listing_years": 3,
    "amount_window_days": 20,
}
CHECKS = {
    "small_aum_billion_twd": 10.0,
    "units_drop_pct": -10.0,          # 近 20 個交易日已發行單位數變化
    "units_window_days": 20,
    "holders_down_weeks": 4,
    "premium_abs_pct": 1.0,           # |預估折溢價| 超過即記一次
    "premium_days": 3,                # 近 5 日中至少幾次
    "premium_window_days": 5,
}
HISTORY_DAYS = 25
HOLDER_WEEKS = 8
EXCLUDED_CATEGORIES = set()   # 槓桿／反向／期貨已在 catalog 層排除


def update_history(history: dict | None, trade_date: str, daily: dict[str, dict],
                   holders: dict[str, tuple[str, int]],
                   lagged_amounts: dict[str, dict[str, float]] | None = None) -> dict:
    """滾動保存每檔近 HISTORY_DAYS 日的單位數／成交值／折溢價，與近 HOLDER_WEEKS 週受益人數。

    lagged_amounts＝{資料日: {代號: 成交值(億)}}：交易所 OpenAPI 常落後一日，
    其成交值依「它自己的日期」補進歷史，不冒充當日資料。
    """
    history = {"schema_version": 1, "codes": {}, **(history or {})}
    codes = history.setdefault("codes", {})
    for code, row in daily.items():
        entry = codes.setdefault(code, {"daily": [], "holders": []})
        days = {d[0]: d for d in entry["daily"]}
        old = days.get(trade_date)
        days[trade_date] = [trade_date, row.get("units"),
                            row.get("amount_billion_twd") if row.get("amount_billion_twd") is not None
                            else (old[2] if old else None),
                            row.get("premium_pct")]
        for day, amounts in (lagged_amounts or {}).items():
            if code in amounts and day < trade_date:
                prior = days.get(day) or [day, None, None, None]
                if prior[2] is None:
                    days[day] = [day, prior[1], amounts[code], prior[3]]
        entry["daily"] = sorted(days.values())[-HISTORY_DAYS:]
        if code in holders:
            as_of, count = holders[code]
            weeks = [w for w in entry["holders"] if w[0] != as_of]
            weeks.append([as_of, count])
            entry["holders"] = sorted(weeks)[-HOLDER_WEEKS:]
    history["last_trade_date"] = trade_date
    return history


def _avg_amount(entry: dict) -> tuple[float | None, int]:
    values = [d[2] for d in entry.get("daily", [])[-CRITERIA["amount_window_days"]:] if d[2] is not None]
    return (round(sum(values) / len(values), 4), len(values)) if values else (None, 0)


def _years_listed(listing_date: str | None, as_of: str) -> float | None:
    try:
        return (date.fromisoformat(as_of) - date.fromisoformat(listing_date)).days / 365.25
    except (TypeError, ValueError):
        return None


def stop_checks(entry: dict, daily_row: dict) -> list[str]:
    """回傳觸發的檢查項目（文字）。資料天數不足的項目不判定。"""
    flags = []
    aum = daily_row.get("aum_billion_twd")
    if aum is not None and aum < CHECKS["small_aum_billion_twd"]:
        flags.append(f"規模約 {aum:.1f} 億，低於 {CHECKS['small_aum_billion_twd']:.0f} 億")
    days = entry.get("daily", [])
    window = [d for d in days[-(CHECKS["units_window_days"] + 1):] if d[1]]
    if len(window) >= 11:   # 至少約兩週資料才比較
        change = (window[-1][1] / window[0][1] - 1) * 100
        if change <= CHECKS["units_drop_pct"]:
            flags.append(f"已發行單位數 {window[0][0]}→{window[-1][0]} 減少 {abs(change):.1f}%（資金淨流出）")
    recent = [d[3] for d in days[-CHECKS["premium_window_days"]:] if d[3] is not None]
    hot = sum(abs(p) > CHECKS["premium_abs_pct"] for p in recent)
    if hot >= CHECKS["premium_days"]:
        flags.append(f"近 {len(recent)} 日有 {hot} 日 |預估折溢價| 超過 {CHECKS['premium_abs_pct']}%")
    weeks = [w[1] for w in entry.get("holders", [])]
    need = CHECKS["holders_down_weeks"] + 1
    if len(weeks) >= need and all(b < a for a, b in zip(weeks[-need:], weeks[-need + 1:])):
        flags.append(f"受益人數連續 {CHECKS['holders_down_weeks']} 週減少")
    return flags


def screen(catalog_rows: list[dict], daily: dict[str, dict], history: dict, trade_date: str,
           subpool: list[str], previous: dict | None = None) -> dict:
    codes_hist = history.get("codes", {})
    candidates, checks = [], []
    for row in catalog_rows:
        code = row["code"]
        if row.get("category") in EXCLUDED_CATEGORIES:
            continue
        today = daily.get(code) or {}
        entry = codes_hist.get(code, {})
        avg_amount, amount_days = _avg_amount(entry)
        years = _years_listed(row.get("listing_date"), trade_date)
        info = {
            "code": code, "name": html.unescape(row.get("name") or ""), "category": row.get("category"),
            "active": bool(row.get("active")),
            "aum_billion_twd": today.get("aum_billion_twd"),
            "avg_amount_billion_twd": avg_amount, "amount_days": amount_days,
            "holders": row.get("holders"), "holders_as_of": row.get("ranking_as_of"),
            "years_listed": round(years, 1) if years is not None else None,
            "premium_pct": today.get("premium_pct"), "spread_pct": today.get("spread_pct"),
            "fee": None if row.get("management_fee") is None else row.get("management_fee"),
            "fee_status": "已取得官方費率原文" if row.get("management_fee") else "費用待查（請核對公開說明書）",
            "in_subpool": code in subpool,
        }
        passed = (
            (info["aum_billion_twd"] or 0) >= CRITERIA["min_aum_billion_twd"]
            and (avg_amount or 0) >= CRITERIA["min_avg_amount_billion_twd"]
            and (info["holders"] or 0) >= CRITERIA["min_holders"]
            and years is not None and years >= CRITERIA["min_listing_years"]
        )
        if passed:
            candidates.append(info)
        if code in subpool:
            flags = stop_checks(entry, today)
            checks.append({**info, "meets_candidate_criteria": passed, "flags": flags})
    candidates.sort(key=lambda r: (r["category"] or "", -(r["holders"] or 0)))
    prev_codes = {c["code"] for c in (previous or {}).get("candidates", [])}
    now_codes = {c["code"] for c in candidates}
    has_prev = bool(previous and previous.get("trade_date") and previous["trade_date"] != trade_date)
    return {
        "schema_version": 1,
        "trade_date": trade_date,
        "criteria": CRITERIA, "checks_criteria": CHECKS,
        "candidates": candidates,
        "new_entries": sorted(now_codes - prev_codes) if has_prev else [],
        "exits": sorted(prev_codes - now_codes) if has_prev else [],
        "compared_with": previous.get("trade_date") if has_prev else None,
        "stop_checks": checks,
        # 每檔最新規模／折溢價，供 Email 對「使用者持有」的 ETF 套同一套停扣檢查。
        "latest": {code: [row.get("aum_billion_twd"), row.get("premium_pct")] for code, row in daily.items()},
        "coverage": {
            "catalog": len(catalog_rows),
            "with_nav": sum(1 for r in daily.values() if r.get("aum_billion_twd") is not None),
            "with_trading": sum(1 for r in daily.values() if r.get("amount_billion_twd") is not None),
            "amount_history_days": max((_avg_amount(e)[1] for e in codes_hist.values()), default=0),
        },
        "method": (
            "研究候選：規模 ≥ 50 億、近 20 日平均成交值 ≥ 0.5 億、受益人數 ≥ 5 萬、上市滿 3 年；"
            "不以過去報酬排序。停扣檢查：規模 < 10 億、近 20 日單位數減少 ≥ 10%、近 5 日有 3 日 "
            "|預估折溢價| > 1%、受益人數連 4 週減少。歷史資料自 2026-10 起逐日累積，天數不足的項目不判定。"
        ),
        "disclaimer": (
            "依規則篩出的研究候選與檢查提醒，不是個人化投資建議，也不是買賣訊號；"
            "費用與追蹤方式請核對公開說明書。"
        ),
    }


def holding_checks(screen_doc: dict, history: dict, codes: list[str]) -> dict[str, list[str]]:
    """對任意 ETF 代號（例如使用者持股）套用停扣檢查；沒有資料的代號不判定。"""
    latest = (screen_doc or {}).get("latest") or {}
    out = {}
    for code in codes:
        if code not in latest:
            continue
        aum, premium = latest[code]
        out[code] = stop_checks((history or {}).get("codes", {}).get(code, {}),
                                {"aum_billion_twd": aum, "premium_pct": premium})
    return out
