# -*- coding: utf-8 -*-
"""永豐帳務明細（唯讀）：持股逐筆買進紀錄與已實現損益。

與 broker_positions 相同的紀律：只讀、不下單；結果只寫私有資料庫；
公開的 Actions log 只能印筆數與錯誤代碼（程式碼 repo 為 public）。

官方文件未說明 list_position_detail 的 quantity 單位（股或張）與 price 是每股
成本還是總成本（範例同一檔一處 30.0、一處 30000）。因此逐檔與 list_positions
（Unit.Share）對帳：股數合計與平均成本都核對得上才採用，否則標記 unreconciled，
不以猜測值餵給賣出時機。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
LOTS_REMOTE = "private/broker_lots.json"
REALIZED_REMOTE = "private/realized_pnl.json"
REALIZED_DAYS = 365
COST_TOLERANCE = 0.03


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _num(value) -> float | None:
    try:
        number = float(getattr(value, "value", value))
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _day(value) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10] if len(text) >= 10 else None


def reconcile_lots(position_shares: float, position_cost: float, details: list) -> dict:
    """把明細換算成「股、每股成本」，並與庫存對帳。回傳 {ok, lots, reason, units}。"""
    raw = [(_day(_get(d, "date")), _num(_get(d, "quantity")), _num(_get(d, "price")),
            _num(_get(d, "fee")), _num(_get(d, "ex_dividends"))) for d in details]
    raw = [r for r in raw if r[0] and r[1] and r[1] > 0 and r[2] and r[2] > 0]
    if not raw or not position_shares or not position_cost:
        return {"ok": False, "lots": [], "reason": "no_detail"}
    total = sum(r[1] for r in raw)
    if abs(total - position_shares) < 0.5:
        factor = 1
    elif abs(total * 1000 - position_shares) < 0.5:
        factor = 1000
    else:
        return {"ok": False, "lots": [], "reason": "quantity_mismatch"}
    lots = []
    for day, qty, price, fee, dividends in raw:
        shares = qty * factor
        # price 可能是每股成本或該筆總成本：取與庫存平均成本同數量級的解讀。
        candidates = [("per_share", price), ("total", price / shares)]
        basis, per_share = min(candidates, key=lambda c: abs(c[1] / position_cost - 1))
        lots.append({"date": day, "shares": int(round(shares)), "cost_per_share": round(per_share, 4),
                     "fee": fee, "ex_dividends": dividends, "price_basis": basis})
    weighted = sum(l["shares"] * l["cost_per_share"] for l in lots) / sum(l["shares"] for l in lots)
    if abs(weighted / position_cost - 1) > COST_TOLERANCE:
        return {"ok": False, "lots": [], "reason": "cost_mismatch"}
    lots.sort(key=lambda l: l["date"])
    return {"ok": True, "lots": lots, "reason": None, "units": {"quantity_factor": factor}}


def fetch_lots(api, account, share_unit, symbol_of) -> dict:
    """每檔持股的逐筆買進紀錄。symbol_of(code) 由呼叫端提供（沿用庫存的合約解析）。"""
    rows = api.list_positions(account=account, unit=share_unit, timeout=5000)
    out, status = {}, {}
    for row in rows or []:
        code = _get(row, "code")
        try:
            symbol = symbol_of(code)
        except Exception:  # noqa: BLE001
            continue
        time.sleep(0.3)   # 帳務查詢 5 秒 25 次上限；持股多時也不會觸頂
        try:
            details = api.list_position_detail(account=account, detail_id=_get(row, "id"), timeout=5000)
        except Exception:  # noqa: BLE001
            status[symbol] = "detail_query_failed"
            continue
        result = reconcile_lots(_num(_get(row, "quantity")) or 0, _num(_get(row, "price")) or 0,
                                list(details or []))
        status[symbol] = "ok" if result["ok"] else result["reason"]
        if result["ok"]:
            out[symbol] = result["lots"]
    return {"lots": out, "status": status}


def fetch_realized(api, account, share_unit, symbol_of, today: date | None = None) -> dict:
    """近一年已實現損益（逐筆＋依代號彙總）。"""
    end = today or datetime.now(TAIPEI).date()
    begin = end - timedelta(days=REALIZED_DAYS)
    trades = []
    for row in api.list_profit_loss(account=account, begin_date=begin.isoformat(),
                                    end_date=end.isoformat(), unit=share_unit, timeout=10000) or []:
        try:
            symbol = symbol_of(_get(row, "code"))
        except Exception:  # noqa: BLE001
            continue
        trades.append({"date": _day(_get(row, "date")), "symbol": symbol,
                       "shares": _num(_get(row, "quantity")), "price": _num(_get(row, "price")),
                       "pnl": _num(_get(row, "pnl")), "pr_ratio": _num(_get(row, "pr_ratio")),
                       "cond": str(getattr(_get(row, "cond"), "value", _get(row, "cond")) or "")})
    summary, total = [], None
    try:
        result = api.list_profit_loss_summary(account=account, begin_date=begin.isoformat(),
                                              end_date=end.isoformat(), timeout=10000)
        for row in _get(result, "profitloss_summary") or []:
            try:
                symbol = symbol_of(_get(row, "code"))
            except Exception:  # noqa: BLE001
                continue
            summary.append({"symbol": symbol, **{k: _num(_get(row, k)) for k in (
                "quantity", "entry_price", "cover_price", "pnl", "pr_ratio", "buy_cost", "sell_cost")}})
        raw_total = _get(result, "total")
        if raw_total is not None:
            total = {k: _num(_get(raw_total, k)) for k in (
                "entry_amount", "cover_amount", "quantity", "buy_cost", "sell_cost", "pnl", "pr_ratio")}
    except Exception:  # noqa: BLE001 - 彙總失敗不影響逐筆
        summary, total = [], None
    trades.sort(key=lambda t: t["date"] or "", reverse=True)
    return {"range": [begin.isoformat(), end.isoformat()], "trades": trades,
            "summary": summary, "total": total,
            "note": "summary.quantity 的單位官方文件未註明（無 unit 參數），僅供參考；逐筆 shares 為股。"}
