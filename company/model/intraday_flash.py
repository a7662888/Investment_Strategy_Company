# -*- coding: utf-8 -*-
"""盤中研究快訊（provisional）。

把盤後定稿的 current-state 疊上盤中即時報價，回答「今天盤中這幾檔落在買進區的
哪個位置」。刻意**不新增第二套判斷**：品質硬篩、估值位階與買進區全部沿用既有
value engine 的輸出，本模組只做「即時價 × 既有買進區」的位置換算與排序。

紀律：
- 盤中價未定案，本文件一律標記 provisional，**不寫入 Decision Ledger**、
  不取代盤後 current-state。帳本只收盤後凍結卡。
- 買進區來自 entry_range（格式為 [下緣, 上緣] 的 list，不是 dict）。
"""
from __future__ import annotations

from datetime import datetime, timezone

MAX_CANDIDATES = 12

# 這些判定代表「規則層認為值得研究」，才有資格進盤中快訊；
# 排除／賣出檢查與資料不足者不列入，避免把不該買的東西端上來。
RESEARCHABLE = {"可分批研究", "等待止跌", "高檔，等待拉回"}


def _entry_bounds(item: dict) -> tuple[float | None, float | None]:
    entry = item.get("entry_range")
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        try:
            low, high = float(entry[0]), float(entry[1])
        except (TypeError, ValueError):
            return None, None
        return (low, high) if low <= high else (high, low)
    return None, None


def select_candidates(state: dict) -> list[dict]:
    """挑出盤中值得看的標的：規則層已認可、非 ETF、且有買進區可比對。"""
    evaluations = state.get("evaluations") or []
    as_of = state.get("as_of")
    picked: list[dict] = []
    for item in evaluations:
        if item.get("is_etf") or item.get("error") or item.get("data_incomplete"):
            continue
        if item.get("as_of") and as_of and item["as_of"] != as_of:
            continue
        if not item.get("quality_pass") or item.get("action") == "avoid":
            continue
        if item.get("decision") not in RESEARCHABLE:
            continue
        low, _ = _entry_bounds(item)
        if low is None:
            continue
        picked.append(item)
    picked.sort(key=lambda i: float(i.get("rank_score") or 0), reverse=True)
    return picked[:MAX_CANDIDATES]


def _position(live: float | None, low: float, high: float) -> tuple[str, float | None]:
    """即時價相對買進區的位置。回傳 (狀態, 距上緣百分比)。"""
    if live is None:
        return "無即時報價", None
    gap = (live / high - 1.0) * 100 if high else None
    if live < low:
        return "低於買進區（更便宜）", gap
    if live <= high:
        return "落在買進區內", gap
    return "高於買進區，不追價", gap


def build_flash(state: dict, quotes: dict, now: datetime | None = None,
                market_open: bool = True) -> dict:
    """產生盤中快訊文件。quotes 為 {symbol: quote} 的即時報價。"""
    from company.model.daily_jobs import taipei_now

    moment = now or taipei_now()
    items = []
    for item in select_candidates(state):
        symbol = item.get("symbol")
        low, high = _entry_bounds(item)
        quote = quotes.get(symbol) or {}
        live = quote.get("regularMarketPrice")
        live = round(float(live), 2) if live is not None else None
        status, gap = _position(live, low, high)
        trend = item.get("fundamental_trend") or {}
        items.append({
            "symbol": symbol,
            "name": item.get("name"),
            "decision": item.get("decision"),
            "action": item.get("action"),
            "live_price": live,
            "quote_source": quote.get("source"),
            "close_price": item.get("price"),
            "entry_low": round(low, 2),
            "entry_high": round(high, 2),
            "chase_limit": round(high, 2),
            "position": status,
            "gap_to_chase_pct": round(gap, 2) if gap is not None else None,
            "valuation_pct": item.get("valuation_pct"),
            "valuation_zone": item.get("valuation_zone"),
            "roe_ttm": item.get("roe_ttm"),
            "trend": item.get("trend"),
            "monthly_revenue_yoy": trend.get("monthly_revenue_yoy_latest"),
            "rank_score": item.get("rank_score"),
            "reasons": list(item.get("reasons") or [])[:2],
        })

    # 落在買進區內的排前面：盤中最有行動意義的是「現在就在區間裡」。
    order = {"落在買進區內": 0, "低於買進區（更便宜）": 1, "高於買進區，不追價": 2, "無即時報價": 3}
    items.sort(key=lambda i: (order.get(i["position"], 9), -float(i.get("rank_score") or 0)))

    priced = sum(1 for i in items if i["live_price"] is not None)
    return {
        "schema_version": 1,
        "date": moment.date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_taipei": moment.isoformat(),
        "market_open": bool(market_open),
        "provisional": True,
        "basis": {
            "state_as_of": state.get("as_of"),
            "state_analysis_date": state.get("analysis_date_taipei"),
            "candidates": len(items),
            "quoted": priced,
        },
        "items": items,
        "method": (
            "盤中快訊 v1：品質硬篩、估值位階與買進區沿用盤後 value engine 輸出，"
            "本層只做「即時價 × 既有買進區」的位置換算與排序，不新增判斷來源。"
        ),
        "disclaimer": (
            "盤中價未定案，本區為研究用 provisional 資訊；不寫入 Decision Ledger，"
            "也不取代盤後 current-state。不自動下單。"
        ),
    }
