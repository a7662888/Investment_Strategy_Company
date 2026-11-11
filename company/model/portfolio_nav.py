"""持股部位每日歷史（不含券商現金）；未知金流保持未知。"""
from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime, timedelta, timezone

from company.data import broker_positions as broker
from company.model import private_history_store as store
from company.model.ledger import _adj_close
from company.model.trade_events import merge_events

TAIPEI = timezone(timedelta(hours=8))
NAV_REMOTE = "private/portfolio_nav.json"
EVENTS_REMOTE = "private/trade_events.json"
STATUS_REMOTE = "private/portfolio_history_status.json"
SCOPE = "券商持股部位（不含券商現金及手動持股）"


class NavError(ValueError):
    pass


def _num(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and (n > 0 if positive else n >= 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _path(kind):
    return broker.LOCAL_PATH.parent / {"nav": "portfolio_nav.json", "events": "trade_events.json",
                                      "status": "portfolio_history_status.json"}[kind]


def _validate(doc, kind):
    if doc is None:
        return
    key = "rows" if kind == "nav" else "events"
    if (not isinstance(doc, dict) or doc.get("schema_version") != 1
            or not isinstance(doc.get(key), list) or not doc.get("account_key")):
        raise NavError("INVALID_HISTORY_DOCUMENT")
    if kind == "nav":
        days = []
        for row in doc["rows"]:
            if not isinstance(row, dict):
                raise NavError("INVALID_HISTORY_DOCUMENT")
            day = row.get("date")
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError
            except (ValueError, TypeError):
                raise NavError("INVALID_HISTORY_DOCUMENT") from None
            if (_num(row.get("market_value")) is None or _num(row.get("total_cost")) is None
                    or not isinstance(row.get("positions"), list)):
                raise NavError("INVALID_HISTORY_DOCUMENT")
            days.append(day)
        if days != sorted(set(days)):
            raise NavError("INVALID_HISTORY_DOCUMENT")


def load_public(kind):
    """名稱 public 指 API serializer；路由必須先完成裝置授權。"""
    if kind not in ("nav", "events"):
        raise NavError("INVALID_HISTORY_KIND")
    doc, storage = store.load(_path(kind), NAV_REMOTE if kind == "nav" else EVENTS_REMOTE)
    _validate(doc, kind)
    doc = copy.deepcopy(doc or {})
    doc.pop("account_key", None)
    doc.setdefault("rows" if kind == "nav" else "events", [])
    doc.setdefault("coverage_start", None)
    doc.setdefault("last_success_at", None)
    doc.setdefault("data_days", 0)
    doc["contains_provisional"] = bool(doc.get("contains_provisional", doc.get("contains_provisional_prices", False)))
    doc["scope"] = SCOPE
    doc["storage"] = storage
    status, status_storage = store.load(_path("status"), STATUS_REMOTE)
    if status:
        # 固定白名單；不讓 exception、帳戶或 SDK 物件進入回應。
        doc["last_attempt"] = {k: status.get(k) for k in ("at", "ok", "error_code")}
        doc["status_storage"] = status_storage
    doc["note"] = "事後紀錄，不是預測，也不代表選股能力已被驗證。成交毛額口徑；不推估未知費稅。"
    return doc


def _shares(row):
    return {p["symbol"]: p["shares"] for p in row.get("positions", [])}


def make_snapshot(snapshot, lots, realized, events, histories, actions, previous, now):
    """只對當日實際庫存估值；絕不把今日庫存搬往任何過去日期。"""
    if snapshot.get("source") != "shioaji" or snapshot.get("simulation") is not False or snapshot.get("unit") != "Share":
        raise NavError("INVALID_INVENTORY")
    stamp = datetime.fromisoformat(snapshot["as_of"].replace("Z", "+00:00"))
    local = now.astimezone(TAIPEI)
    day = local.date().isoformat()
    if (stamp.tzinfo is None or stamp.astimezone(TAIPEI).date().isoformat() != day
            or stamp > now or stamp.astimezone(TAIPEI).hour < 14 or local.hour < 14 or local.weekday() >= 5):
        raise NavError("INVENTORY_NOT_POSTCLOSE_TODAY")
    key = snapshot.get("account_key")
    if not key or any(d is None or d.get("account_key") != key for d in (lots, realized)):
        raise NavError("ACCOUNT_OR_LEDGER_UNAVAILABLE")
    for doc in (lots, realized):
        try:
            source_time = datetime.fromisoformat(doc["as_of"].replace("Z", "+00:00"))
            if (source_time.tzinfo is None or source_time > now or source_time.astimezone(TAIPEI).hour < 14
                    or source_time.astimezone(TAIPEI).date().isoformat() != day):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise NavError("STALE_LEDGER_SOURCE") from None
    positions = []
    seen = set()
    for p in snapshot.get("positions", []):
        symbol = p.get("symbol")
        shares, cost = _num(p.get("shares"), positive=True), _num(p.get("cost"), positive=True)
        if (not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{4,6}[A-Z]?\.(TW|TWO)", symbol)
                or symbol in seen or shares is None or shares != int(shares) or cost is None):
            raise NavError("INVALID_INVENTORY")
        seen.add(symbol)
        candidates = [r for r in histories.get(symbol, []) if r.get("date", "") <= day]
        quote = max(candidates, key=lambda r: r["date"]) if candidates else {}
        price = _num(quote.get("close"), positive=True)
        if price is None:
            raise NavError("PRICE_UNAVAILABLE")
        positions.append({"symbol": symbol, "shares": int(shares), "cost_per_share": cost,
                          "cost": shares * cost, "cost_source": "broker_average_cost",
                          "price": price, "market_value": shares * price,
                          "price_date": quote["date"], "price_source": "Decision Ledger / Yahoo history",
                          "provisional": quote["date"] != day})
    statuses = lots.get("status") or {}
    reconciled = all(statuses.get(p["symbol"]) == "ok" for p in positions)
    reconciled = reconciled and set(lots.get("lots") or {}) == seen and all(v == "ok" for v in statuses.values())
    for p in positions:
        ls = (lots.get("lots") or {}).get(p["symbol"], [])
        if any(_num(l.get("shares"), positive=True) is None or _num(l.get("cost_per_share"), positive=True) is None for l in ls):
            reconciled = False
            continue
        qty = sum(l["shares"] for l in ls)
        total = sum(l["shares"] * l["cost_per_share"] for l in ls)
        if qty != p["shares"] or abs(total / qty / p["cost_per_share"] - 1) > 0.03:
            reconciled = False
    provisional = any(p["provisional"] for p in positions)
    row = {"date": day, "market_value": sum(p["market_value"] for p in positions),
           "total_cost": sum(p["cost"] for p in positions), "holding_count": len(positions),
           "positions": positions, "provisional": provisional,
           "inventory_reconciled": reconciled, "anchor": reconciled and not provisional,
           "source": "broker_postclose", "captured_at": now.isoformat(),
           "buy_amount": None, "sell_amount": None, "dividend_amount": None,
           "flow_complete": False, "corporate_actions": copy.deepcopy(actions),
           "flow_basis": "成交毛額；不含未知費稅；除息日以前一交易日股數認列",
           "daily_return": None, "cumulative_return": None, "benchmark_return": None,
           "cashflow_benchmark_value": None, "cashflow_benchmark_return": None,
           "return_status": "baseline"}
    if not math.isfinite(row["market_value"]) or not math.isfinite(row["total_cost"]):
        raise NavError("INVALID_INVENTORY")
    if previous is None:
        return row
    previous_shares, current_shares = _shares(previous), _shares(row)
    split_ratios = {}
    for split in actions.get("splits", []):
        if split.get("date") == day:
            ratio = _num(split.get("ratio"), positive=True)
            if ratio is None or split["symbol"] in split_ratios:
                raise NavError("INVALID_CORPORATE_ACTION")
            split_ratios[split["symbol"]] = ratio
    expected = {s: q * split_ratios.get(s, 1) for s, q in previous_shares.items()}
    buys, sells, complete = 0.0, 0.0, actions.get("complete") is True
    # 缺失一個交易日就不能將跨日成交冒充當日金流。
    between = [r["date"] for r in histories.get("0050.TW", []) if previous["date"] < r["date"] <= day]
    complete = complete and sorted(set(between)) == [day]
    for event in events.get("events", []):
        if event.get("date") != day:
            continue
        if event.get("flow_eligible") is not True:
            complete = False
            continue
        q, p = _num(event.get("shares"), positive=True), _num(event.get("price"), positive=True)
        if q is None or p is None or event.get("side") not in ("buy", "sell"):
            complete = False
            continue
        symbol = event["symbol"]
        sign = 1 if event["side"] == "buy" else -1
        expected[symbol] = expected.get(symbol, 0) + sign * q
        if sign > 0:
            buys += q * p
        else:
            sells += q * p
    if "issue_details" in events:
        complete = complete and not any(
            issue.get("date") in (None, day) and not (
                issue.get("code") == "SOURCE_CONTENT_CHANGED" and issue.get("source") == "broker_lots")
            for issue in events["issue_details"])
    else:
        complete = complete and not events.get("issues")
    complete = complete and all(abs(expected.get(s, 0) - current_shares.get(s, 0)) < 1e-6
                                for s in expected.keys() | current_shares.keys())
    dividend = 0.0
    dividend_symbols = set()
    for action in actions.get("dividends", []):
        if action.get("date") == day:
            amount = _num(action.get("amount_per_share"))
            symbol = action.get("symbol")
            if amount is None or symbol in dividend_symbols or symbol in split_ratios:
                complete = False
            else:
                dividend += previous_shares.get(symbol, 0) * amount
            dividend_symbols.add(symbol)
    if complete:
        row.update(buy_amount=buys, sell_amount=sells, dividend_amount=dividend, flow_complete=True)
    else:
        row["return_status"] = "cashflow_incomplete"
    return row


def calculate_returns(rows, benchmark_rows):
    """逐段對齊三線；缺金流、缺日、零部位或暫定價都切斷累乘。"""
    output = copy.deepcopy(rows)
    benchmark = {r["date"]: r for r in benchmark_rows}
    active, factor, benchmark_factor = False, 1.0, 1.0
    cf_value = cf_in = cf_out = 0.0
    start = None
    for i, row in enumerate(output):
        row.update(daily_return=None, cumulative_return=None, benchmark_return=None,
                   cashflow_benchmark_value=None, cashflow_benchmark_return=None)
        valid = row.get("anchor") is True
        prev = output[i - 1] if i else None
        if not valid or (prev and (not row.get("flow_complete") or not prev.get("anchor"))):
            active = False
            row["return_status"] = "provisional" if row.get("provisional") else "cashflow_incomplete"
            continue
        if not active or prev is None or prev["market_value"] == 0:
            active = row["market_value"] > 0
            start = row["date"]
            factor = benchmark_factor = 1.0
            cf_value = cf_in = row["market_value"]
            cf_out = 0.0
            benchmark_valid = row.get("benchmark_input", {}).get("valid") if "benchmark_input" in row else bool(_num(_adj_close(benchmark.get(row["date"], {})), positive=True))
            if not benchmark_valid:
                benchmark_factor = cf_value = None
            row.update(return_status="baseline" if i == 0 else "reset_base", return_start=start,
                       cumulative_return=0.0 if active else None,
                       benchmark_return=0.0 if active and benchmark_valid else None,
                       cashflow_benchmark_value=cf_value if active and benchmark_valid else None,
                       cashflow_benchmark_return=0.0 if active and benchmark_valid else None)
            continue
        daily = (row["market_value"] + row["dividend_amount"] + row["sell_amount"] - row["buy_amount"]) / prev["market_value"] - 1
        if not math.isfinite(daily) or daily < -1:
            active = False
            row["return_status"] = "invalid_return"
            continue
        factor *= 1 + daily
        row.update(daily_return=daily, cumulative_return=factor - 1, return_status="ok", return_start=start)
        if "benchmark_input" in row:
            rate = _num(row["benchmark_input"].get("factor"), positive=True)
        else:
            b0 = _num(_adj_close(benchmark.get(prev["date"], {})), positive=True)
            b1 = _num(_adj_close(benchmark.get(row["date"], {})), positive=True)
            rate = b1 / b0 if b0 is not None and b1 is not None else None
        if rate is None or benchmark_factor is None:
            benchmark_factor = None
            cf_value = None
            continue
        benchmark_factor *= rate
        row["benchmark_return"] = benchmark_factor - 1
        if cf_value is not None:
            next_value = cf_value * rate + row["buy_amount"] - row["sell_amount"]
            cf_in += row["buy_amount"]
            cf_out += row["sell_amount"]
            if next_value < 0:
                cf_value = None  # 不借款，不默默引入槓桿或額外現金。
            else:
                cf_value = next_value
                row["cashflow_benchmark_value"] = cf_value
                row["cashflow_benchmark_return"] = (cf_value + cf_out) / cf_in - 1 if cf_in else None
    return output


def merge_nav(previous, row, account_key, benchmark_rows, now):
    _validate(previous, "nav")
    if previous and previous["account_key"] != account_key:
        raise NavError("ACCOUNT_BINDING_CHANGED")
    out = copy.deepcopy(previous or {"schema_version": 1, "account_key": account_key, "rows": []})
    # 第一筆當日成功資料凍結。失敗重跑和新來源都不能覆寫它。
    if any(r["date"] == row["date"] for r in out["rows"]):
        return out
    if out["rows"] and row["date"] < out["rows"][-1]["date"]:
        raise NavError("HISTORICAL_INVENTORY_FORBIDDEN")
    row = copy.deepcopy(row)
    by_date = {r["date"]: r for r in benchmark_rows}
    b1 = _num(_adj_close(by_date.get(row["date"], {})), positive=True)
    b0 = _num(_adj_close(by_date.get(out["rows"][-1]["date"], {})), positive=True) if out["rows"] else None
    # 每日凍結「同次抓取中前後兩日還原價的比例」，避免之後回溯調整或取數失敗改寫舊線。
    row["benchmark_input"] = {"date": row["date"], "valid": b1 is not None,
                              "adj_close": b1, "previous_adj_close": b0,
                              "factor": b1 / b0 if b1 is not None and b0 is not None else None,
                              "source": "Decision Ledger / ledger._adj_close"}
    out["rows"] = calculate_returns([*out["rows"], row], benchmark_rows)
    out.update(coverage_start=out["rows"][0]["date"], last_success_at=now,
               data_days=len(out["rows"]), contains_provisional=any(r["provisional"] for r in out["rows"]),
               scope=SCOPE, benchmark={"symbol": "0050.TW", "source": "Decision Ledger / app.fetch_history / ledger._adj_close",
               "total_return_supported": any(r.get("benchmark_return") is not None for r in out["rows"]),
               "coverage_complete": all(r.get("benchmark_input", {}).get("valid") is True and
                   (i == 0 or r["benchmark_input"].get("factor") is not None) for i, r in enumerate(out["rows"])),
               "reason": "只用同日含息還原價；僅顯示已確認的日期與區段，缺值斷線、不退回原始價格。",
               "cashflow_basis": "起始部位市值視為期初投入；同日收盤交易、配息再投入、允許零碎單位、不借款、不計未知費稅；百分比為含領回金額的投入資本損益率，與時間加權報酬不同。"})
    return out


def record_refresh(snapshot, lots, realized, fetch_history, fetch_actions, *, now=None):
    """既有券商登入後的私有保存；不會建立任何 SDK client 或新登入。"""
    now = now or datetime.now(timezone.utc)
    report = {}
    try:
        if snapshot is None:
            raise NavError("INVENTORY_UNAVAILABLE")
        key = snapshot["account_key"]
        events, event_storage = store.update(_path("events"), EVENTS_REMOTE,
            lambda old: merge_events(old, lots, realized, key, now.isoformat()))
        report["events"] = {"count": len(events["events"]), "durable": event_storage["durable"]}
        prior, _ = store.load(_path("nav"), NAV_REMOTE)
        _validate(prior, "nav")
        day = now.astimezone(TAIPEI).date().isoformat()
        if prior and prior["account_key"] != key:
            raise NavError("ACCOUNT_BINDING_CHANGED")
        # 同日重跑完全不重估當日快照。
        if prior and any(r["date"] == day for r in prior["rows"]):
            report["nav"] = {"count": len(prior["rows"]), "unchanged": True}
        else:
            start = prior["coverage_start"] if prior and prior["rows"] else day
            start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
            end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            symbols = {p["symbol"] for p in snapshot["positions"]}
            if prior and prior["rows"]:
                symbols.update(_shares(prior["rows"][-1]))
            histories = {s: fetch_history(s, start, end) for s in sorted(symbols | {"0050.TW"})}
            # 基準實際有當日交易列才承認當天是交易日。
            if not any(r.get("date") == day for r in histories["0050.TW"]):
                raise NavError("MARKET_SESSION_UNCONFIRMED")
            actions = fetch_actions(sorted(symbols), day)
            row = make_snapshot(snapshot, lots, realized, events, histories, actions,
                                prior["rows"][-1] if prior and prior["rows"] else None, now)
            if row["inventory_reconciled"] is not True:
                raise NavError("INVENTORY_UNRECONCILED")
            def transform(old):
                # 兩個 writer 的庫存對帳都基於同一個前日錨點，否則重試整次工作。
                if (old or {}).get("rows", []) != (prior or {}).get("rows", []):
                    if any(r["date"] == day for r in (old or {}).get("rows", [])):
                        return old
                    raise NavError("HISTORY_BASE_CHANGED")
                return merge_nav(old, row, key, histories["0050.TW"], now.isoformat())
            nav, nav_storage = store.update(_path("nav"), NAV_REMOTE, transform)
            report["nav"] = {"count": len(nav["rows"]), "durable": nav_storage["durable"]}
        status = {"at": now.isoformat(), "ok": True, "error_code": None}
    except Exception as exc:
        allowed = {"INVENTORY_UNAVAILABLE", "ACCOUNT_BINDING_CHANGED", "HISTORY_READ_FAILED", "HISTORY_WRITE_FAILED",
                   "HISTORY_CONFLICT", "INVENTORY_NOT_POSTCLOSE_TODAY", "ACCOUNT_OR_LEDGER_UNAVAILABLE", "INVALID_INVENTORY",
                   "PRICE_UNAVAILABLE", "MARKET_SESSION_UNCONFIRMED", "INVALID_HISTORY_DOCUMENT", "HISTORY_BASE_CHANGED", "STALE_LEDGER_SOURCE", "INVENTORY_UNRECONCILED"}
        code = str(exc) if str(exc) in allowed else "PORTFOLIO_HISTORY_FAILED"
        status = {"at": now.isoformat(), "ok": False, "error_code": code}
        report["error_code"] = code
    try:
        _, storage = store.update(_path("status"), STATUS_REMOTE,
                                  lambda old: old if old and old.get("at", "") > status["at"] else status)
        report["status"] = {"ok": status["ok"], "durable": storage["durable"]}
    except Exception:
        report["status"] = {"ok": False, "error_code": "HISTORY_STATUS_WRITE_FAILED"}
    return report
