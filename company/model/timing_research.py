# -*- coding: utf-8 -*-
"""買賣時機研究（shadow，不改任何規則）。

1. 假跌破研究：檢驗賣出時機的紀律「盤中穿價只提醒、收盤跌破才算」。
   對關注清單每一天，用「前一日為止」的資料算出觸發價（與 sell_timing 同口徑：
   MA20、MA60、ATR 移動停損＝近 60 日最高 − 3×ATR14），只看「前一日收盤仍在
   觸發價之上、當日盤中最低跌破」的新跌破事件：
   - 收盤站回（假跌破）：若盤中在觸發價賣出，相對等到收盤少賺 (收盤−觸發價)/觸發價。
   - 收盤確認跌破：若等到收盤才賣，相對盤中觸發價多損失 (觸發價−收盤)/觸發價。
   假設盤中能剛好成交在觸發價（停損單理想值），實際滑價會讓盤中賣出更差。
2. 成交價研究：每筆永豐買進紀錄的每股成本（扣手續費）對照當日成交量加權均價，
   以及開盤 15 分鐘、收盤前 30 分鐘均價相對全日均價的平均差（時段成本）。

樣本少、期間短，結果只描述過去，不保證未來；未通過樣本外驗證前不改規則。
"""
from __future__ import annotations

from statistics import mean, median

from company.model.sell_timing import wilder_atr

ATR_MULTIPLE = 3.0
MIN_HISTORY = 60


def _rows(days: dict[str, dict]) -> list[dict]:
    return [{"date": d, **v} for d, v in sorted(days.items())]


def _levels(prior: list[dict]) -> dict[str, float]:
    closes = [r["close"] for r in prior]
    levels = {}
    if len(closes) >= 20:
        levels["ma20"] = mean(closes[-20:])
    if len(closes) >= 60:
        levels["ma60"] = mean(closes[-60:])
    atr = wilder_atr(prior)
    if atr is not None:
        levels["atr_trailing"] = max(r["high"] for r in prior[-60:]) - ATR_MULTIPLE * atr
    return levels


def _fwd(rows: list[dict], i: int, n: int) -> float | None:
    return (rows[i + n]["close"] / rows[i]["close"] - 1) * 100 if i + n < len(rows) else None


def _summary(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    return {"n": len(values), "mean": round(mean(values), 3) if values else None,
            "median": round(median(values), 3) if values else None}


def false_break_study(days_by_symbol: dict[str, dict[str, dict]]) -> dict:
    events: dict[str, dict[str, list]] = {}
    for symbol, days in days_by_symbol.items():
        rows = _rows(days)
        for i in range(MIN_HISTORY, len(rows)):
            prior, today = rows[:i], rows[i]
            for key, level in _levels(prior).items():
                if prior[-1]["close"] < level or today["low"] >= level:
                    continue
                bucket = events.setdefault(key, {"recovered": [], "confirmed": []})
                if today["close"] >= level:
                    bucket["recovered"].append({"edge": (today["close"] - level) / level * 100,
                                                "fwd5": _fwd(rows, i, 5), "fwd20": _fwd(rows, i, 20)})
                else:
                    bucket["confirmed"].append({"edge": (level - today["close"]) / level * 100,
                                                "fwd5": _fwd(rows, i, 5), "fwd20": _fwd(rows, i, 20)})
    result = {}
    for key, bucket in events.items():
        rec, con = bucket["recovered"], bucket["confirmed"]
        total = len(rec) + len(con)
        result[key] = {
            "events": total,
            "recovered_share_pct": round(len(rec) / total * 100, 1) if total else None,
            "recovered_edge_pct": _summary([e["edge"] for e in rec]),
            "confirmed_edge_pct": _summary([e["edge"] for e in con]),
            "recovered_fwd5_pct": _summary([e["fwd5"] for e in rec]),
            "confirmed_fwd5_pct": _summary([e["fwd5"] for e in con]),
            "recovered_fwd20_pct": _summary([e["fwd20"] for e in rec]),
            "confirmed_fwd20_pct": _summary([e["fwd20"] for e in con]),
        }
    return result


RANGE_TOLERANCE = 0.002


def execution_study(lots: dict[str, list[dict]], days_by_symbol: dict[str, dict[str, dict]]) -> dict:
    """成交價落在當日高低區間外的紀錄另列、不進平均：實測 2026-06/07 有 7 筆低於當日最低價
    2.5–4%，不可能是當日盤中成交（可能是定期定額扣款日與成交日不同，或成本已扣配息），
    與當日均價比較沒有意義。"""
    fills, excluded = [], []
    for symbol, symbol_lots in (lots or {}).items():
        days = days_by_symbol.get(symbol) or {}
        for lot in symbol_lots:
            day = days.get(lot.get("date"))
            shares = lot.get("shares") or 0
            if not day or not shares or not lot.get("cost_per_share"):
                continue
            # 永豐明細成本含手續費（實測 price 為該筆總成本）；扣掉手續費才是成交價。
            price = (lot["cost_per_share"] * shares - (lot.get("fee") or 0)) / shares
            span = day["high"] - day["low"]
            if not (day["low"] * (1 - RANGE_TOLERANCE) <= price <= day["high"] * (1 + RANGE_TOLERANCE)):
                excluded.append({"symbol": symbol, "date": lot["date"], "price": round(price, 4),
                                 "day_low": day["low"], "day_high": day["high"]})
                continue
            fills.append({
                "symbol": symbol, "date": lot["date"], "price": round(price, 4), "vwap": day["vwap"],
                "vs_vwap_pct": round((price / day["vwap"] - 1) * 100, 3),
                "range_position": round((price - day["low"]) / span, 3) if span > 0 else None,
            })
    session = [
        {"first15": (d["vwap_first15"] / d["vwap"] - 1) * 100 if d.get("vwap_first15") else None,
         "last30": (d["vwap_last30"] / d["vwap"] - 1) * 100 if d.get("vwap_last30") else None}
        for days in days_by_symbol.values() for d in days.values()
    ]
    return {
        "fills": sorted(fills, key=lambda f: f["date"], reverse=True),
        "excluded_out_of_range": sorted(excluded, key=lambda f: f["date"], reverse=True),
        "excluded_note": ("成交價不在當日高低區間內，無法當作該日盤中成交比較；"
                          "可能為定期定額扣款（紀錄日≠成交日）或成本已調整配息，尚待確認。"),
        "fills_vs_vwap_pct": _summary([f["vs_vwap_pct"] for f in fills]),
        "fills_above_vwap_share_pct": (round(sum(f["vs_vwap_pct"] > 0 for f in fills) / len(fills) * 100, 1)
                                       if fills else None),
        "fills_range_position": _summary([f["range_position"] for f in fills]),
        "session_first15_vs_day_pct": _summary([s["first15"] for s in session]),
        "session_last30_vs_day_pct": _summary([s["last30"] for s in session]),
    }


def build_report(days_by_symbol: dict, lots: dict | None) -> dict:
    sessions = sum(len(v) for v in days_by_symbol.values())
    return {
        "schema_version": 1,
        "symbols": len(days_by_symbol), "sessions": sessions,
        "false_breaks": false_break_study(days_by_symbol),
        "execution": execution_study(lots or {}, days_by_symbol),
        "method": (
            "觸發價只用前一日為止的資料計算（MA20、MA60、ATR 移動停損＝近 60 日最高 − 3×ATR14）；"
            "只計「前一日收盤在觸發價之上、當日盤中跌破」的新事件。edge：收盤站回時為「盤中賣出相對"
            "等收盤少賺」％；收盤確認跌破時為「等收盤相對盤中賣出多損失」％。假設盤中剛好成交在觸發價。"
        ),
        "caveat": "關注清單樣本少、期間約一年；只描述過去，未經樣本外驗證前不改賣出規則。",
    }
