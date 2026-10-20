# -*- coding: utf-8 -*-
"""籌碼與動能研究表（理財達人秀式指標）＋均線策略真實報酬＋除息行事曆。

全部是研究脈絡，**不改任何買賣判定**。2026-10-20 對 38 檔 AI 題材股的回測顯示：
收盤站上／跌破 MA20、MA60 進出，扣成本後 38 檔中 37–38 檔年化落後買進持有；
「跌破季線後買」60 日報酬與任一天買幾乎相同（16.4% vs 14.8%，勝率 63.6% vs 62.7%）。
這些指標要先累積資料、回測證明有用，才考慮進入規則。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

FEE, TAX = 0.001425, 0.003
KEEP_DAYS = 30
KEEP_WEEKS = 8


# ---------- 法人歷史 ----------
def merge_day(history: dict, day: str, flows: dict, codes: list[str]) -> dict:
    days = dict(history.get("days") or {})
    days[day] = {c: [round(flows[c][0]), round(flows[c][1])] for c in codes if c in flows}
    history["days"] = {d: days[d] for d in sorted(days)[-KEEP_DAYS:]}
    return history


def missing_days(history: dict, trading_days: list[str]) -> list[str]:
    have = set((history.get("days") or {}).keys())
    return [d for d in trading_days if d not in have]


def streak(values: list[float]) -> int:
    """最近連續同向天數：正 = 連買、負 = 連賣、0 = 最近一天沒有買賣超。"""
    if not values or not values[-1]:
        return 0
    sign = 1 if values[-1] > 0 else -1
    count = 0
    for value in reversed(values):
        if value * sign > 0:
            count += 1
        else:
            break
    return sign * count


def flow_metrics(history: dict, code: str, shares: float | None, window_days: list[str]) -> dict:
    days = history.get("days") or {}
    series = [days[d].get(code, [0, 0]) for d in window_days if d in days]
    foreign = [f for f, _ in series]
    trust = [t for _, t in series]
    out = {"flow_days": len(series), "trust_streak": streak(trust), "foreign_streak": streak(foreign)}
    if shares:
        pct = lambda values: round(sum(values) / shares * 100, 2)  # noqa: E731
        out.update(foreign5=pct(foreign[-5:]), trust5=pct(trust[-5:]),
                   foreign20=pct(foreign[-20:]) if len(series) >= 20 else None,
                   trust20=pct(trust[-20:]) if len(series) >= 20 else None)
    return out


# ---------- 集保大戶 ----------
def merge_tdcc(history: dict, tdcc_date: str, big: dict, codes: list[str]) -> dict:
    if not tdcc_date:
        return history
    weeks = dict(history.get("tdcc") or {})
    weeks[tdcc_date] = {c: big[c] for c in codes if c in big}
    history["tdcc"] = {d: weeks[d] for d in sorted(weeks)[-KEEP_WEEKS:]}
    return history


def big_holder(history: dict, code: str) -> dict:
    weeks = history.get("tdcc") or {}
    dates = [d for d in sorted(weeks) if code in weeks[d]]
    if not dates:
        return {}
    now = weeks[dates[-1]][code]
    out = {"big400_pct": now, "big400_date": dates[-1]}
    if len(dates) >= 2:
        out["big400_change_pp"] = round(now - weeks[dates[-2]][code], 2)
    return out


# ---------- 月營收 ----------
def revenue_signal(rows: list[dict]) -> dict:
    values = {}
    for r in rows:
        try:
            values[(int(r["revenue_year"]), int(r["revenue_month"]))] = float(r["revenue"])
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return {}
    keys = sorted(values)
    year, month = keys[-1]
    prior = values.get((year - 1, month))
    last12 = [values[k] for k in keys[-12:]]
    return {"revenue_month": f"{year}-{month:02d}",
            "revenue_yoy_pct": round((values[(year, month)] / prior - 1) * 100, 2) if prior else None,
            "revenue_high_12m": len(last12) == 12 and values[(year, month)] >= max(last12)}


def revenue_stale(cached: dict | None, today: date) -> bool:
    """月營收每月 10 日前公布：1–12 日每天重抓，其餘每 7 天重抓一次。"""
    if not cached or not cached.get("fetched"):
        return True
    fetched = date.fromisoformat(cached["fetched"])
    if fetched == today:
        return False
    return today.day <= 12 or (today - fetched).days >= 7


# ---------- 價格與均線 ----------
def sma(values: list[float], n: int) -> list[float | None]:
    out, total = [], 0.0
    for i, value in enumerate(values):
        total += value
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def _mdd(curve: list[float]) -> float:
    peak, worst = curve[0], 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return round(worst * 100, 1)


def ma_strategy(rows: list[dict], n: int, years: int = 5) -> dict | None:
    """收盤站上 n 日線 → 隔日收盤買；跌破 → 隔日收盤賣。含息還原、含手續費與證交稅。"""
    if len(rows) < n + 245:
        return None
    adj = [r["adj"] for r in rows]
    ma = sma(adj, n)
    cutoff = (date.fromisoformat(rows[-1]["date"]) - timedelta(days=365 * years)).isoformat()
    first = max(n, next(i for i, r in enumerate(rows) if r["date"] >= cutoff))
    equity, hold = [1.0], [1.0]
    held, trades = False, 0
    signal = adj[first - 1] > ma[first - 1]
    for i in range(first, len(rows)):
        ret = adj[i] / adj[i - 1] - 1
        hold.append(hold[-1] * (1 + ret))
        value = equity[-1] * (1 + ret) if held else equity[-1]
        if signal and not held:
            value *= 1 - FEE
            held, trades = True, trades + 1
        elif not signal and held:
            value *= 1 - FEE - TAX
            held = False
        equity.append(value)
        signal = adj[i] > ma[i]
    span = (len(rows) - first) / 245
    cagr = lambda curve: round((curve[-1] ** (1 / span) - 1) * 100, 1)  # noqa: E731
    return {"years": round(span, 1), "trades": trades, "cagr_pct": cagr(equity), "mdd_pct": _mdd(equity),
            "hold_cagr_pct": cagr(hold), "hold_mdd_pct": _mdd(hold)}


def price_metrics(rows: list[dict]) -> dict:
    if len(rows) < 60:
        return {}
    closes = [r["close"] for r in rows]
    last = closes[-1]
    high = max(r["high"] for r in rows[-252:])
    ma20, ma60 = sma(closes, 20)[-1], sma(closes, 60)[-1]
    out = {"price_date": rows[-1]["date"], "close": round(last, 2),
           "discount_pct": round((last / high - 1) * 100, 1),
           "vs_ma20_pct": round((last / ma20 - 1) * 100, 1), "vs_ma60_pct": round((last / ma60 - 1) * 100, 1)}
    for n in (20, 60):
        result = ma_strategy(rows, n)
        if result:
            out[f"ma{n}_strategy"] = result
    return out


# ---------- 除息行事曆 ----------
def previous_trading_day(day: str, is_trading) -> str:
    current = date.fromisoformat(day) - timedelta(days=1)
    for _ in range(15):
        if is_trading(datetime(current.year, current.month, current.day, 12)):
            return current.isoformat()
        current -= timedelta(days=1)
    return current.isoformat()


def dividend_calendar(rows: list[dict], today: str, is_trading, highlight: set[str]) -> list[dict]:
    """未來（含今天）的除息列，附最後買進日；ETF 與關注標的放前面。"""
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: (x["ex_date"], x["code"])):
        key = (r["code"], r["ex_date"])
        if r["ex_date"] < today or key in seen:
            continue
        seen.add(key)
        out.append({**r, "last_buy_date": previous_trading_day(r["ex_date"], is_trading),
                    "is_etf": r["code"].startswith("00"), "tracked": r["code"] in highlight})
    return out
