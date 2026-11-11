"""只增不刪的私有帳務事件；純函式，不自行登入券商或讀寫磁碟。

庫存批次僅是剩餘部位的來源觀察；不是完整成交紀錄。保留這個差別，
避免部分賣出、分割或成本調整被誤認為新的買進金流。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime

REMOTE_PATH = "private/trade_events.json"
SCHEMA_VERSION = 1


class TradeEventsError(ValueError):
    """訊息只含固定代碼，適合供公開工作紀錄引用。"""


def _number(value, *, positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return None
    if not finite or (positive and value <= 0):
        return None
    if integer and value != int(value):
        return None
    return int(value) if integer else value


def _day(value):
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def _identity(account_key, source, row):
    # 不使用會隨查詢範圍改變的 cache id，也不以可變的剩餘股數當唯一鍵。
    dseq, seqno = row.get("dseq"), row.get("seqno")
    if (not isinstance(dseq, str) or not dseq.strip() or len(dseq) > 128
            or (seqno is not None and not isinstance(seqno, str))):
        return None
    payload = [account_key, source, row["date"], row["symbol"], dseq, seqno or ""]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _candidate(account_key, source, row, now):
    day = _day(row.get("date"))
    symbol = row.get("symbol")
    shares = _number(row.get("shares"), positive=True, integer=True)
    price = _number(row.get("price"), positive=True)
    if not day or not isinstance(symbol, str) or not re.fullmatch(r"\d{4,6}[A-Z]?\.TW(?:O)?", symbol):
        return None, "INVALID_EVENT"
    if shares is None or price is None:
        return None, "INVALID_EVENT"
    key = _identity(account_key, source, {**row, "date": day})
    if key is None:
        return None, "STABLE_ID_MISSING"
    is_lot = source == "broker_lots"
    fee, tax = _number(row.get("fee")), _number(row.get("tax"))
    if fee is not None and fee < 0 or tax is not None and tax < 0:
        return None, "INVALID_EVENT"
    return {
        "event_key": key, "date": day, "symbol": symbol,
        "side": "buy" if is_lot else "sell", "shares": shares,
        "price": price, "fee": fee, "tax": tax, "source": source,
        "dseq": row.get("dseq"), "seqno": row.get("seqno") or None,
        "first_seen_at": now, "last_seen_at": now,
        "reconciliation_status": "remaining_inventory_reconciled" if is_lot else "broker_realized_record",
        "quantity_scope": "remaining_inventory" if is_lot else "realized_sale",
        "price_source": "broker_remaining_cost" if is_lot else "broker_trade_price",
        # 官方 StockProfitLoss 未列 direction，不能用 cond=Cash 取代方向驗證。
        # side=sell 是本帳本的現金股票平倉分類，方向另以此欄明示。
        "direction_status": "remaining_inventory" if is_lot else (
            "confirmed_sell" if row.get("direction") == "Sell" else "unconfirmed"),
        "flow_eligible": not is_lot and row.get("direction") == "Sell",
        "fees_complete": False if is_lot else fee is not None and tax is not None,
        "entry_details": copy.deepcopy(row.get("entry_details") or []),
        "detail_status": row.get("detail_status"),
    }, None


def merge_events(previous, lots_doc, realized_doc, account_key, now):
    """合併一次成功的讀取；保留消失事件，僅更新完全相同事件的 last_seen。

    同日、同價、同股數但 dseq 不同的兩筆會保留。來源沒有 dseq，或同次
    讀取重複同一外部識別，無法證實是重複回傳或兩筆成交，列為 issue，
    不以排序序號猜造事件。內容改變時保留首次版本並附 source_changes。
    """
    if not isinstance(account_key, str) or not account_key:
        raise TradeEventsError("ACCOUNT_BINDING_REQUIRED")
    if lots_doc is None and realized_doc is None:
        raise TradeEventsError("EVENT_SOURCES_UNAVAILABLE")
    try:
        datetime.fromisoformat(now.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise TradeEventsError("INVALID_EVENT_TIME") from None
    if previous is not None and (not isinstance(previous, dict)
            or previous.get("schema_version") != SCHEMA_VERSION
            or not isinstance(previous.get("events"), list)):
        raise TradeEventsError("INVALID_EVENT_DOCUMENT")
    if previous and previous.get("account_key") != account_key:
        raise TradeEventsError("ACCOUNT_BINDING_CHANGED")
    for doc in (lots_doc, realized_doc):
        if doc and doc.get("account_key") not in (None, account_key):
            raise TradeEventsError("ACCOUNT_BINDING_CHANGED")
    out = copy.deepcopy(previous) if previous else {
        "schema_version": SCHEMA_VERSION, "account_key": account_key,
        "events": [], "source_changes": [], "coverage_start": None,
        "coverage_note": "僅涵蓋來源實際可取得的紀錄；不是完整帳戶成交歷史。",
    }
    existing = {}
    for event in out["events"]:
        if not isinstance(event, dict) or not event.get("event_key") or event["event_key"] in existing:
            raise TradeEventsError("INVALID_EVENT_DOCUMENT")
        existing[event["event_key"]] = event
    issues, issue_details, candidates = [], [], []

    def issue(code, row=None, *, source=None, symbol=None):
        row = row or {}
        issues.append(code)
        detail = {"code": code, "date": _day(row.get("date")),
                  "symbol": symbol or row.get("symbol"), "source": source}
        if detail not in issue_details:
            issue_details.append(detail)

    if lots_doc is not None:
        for symbol, lots in (lots_doc.get("lots") or {}).items():
            if (lots_doc.get("status") or {}).get(symbol) != "ok":
                issue("LOT_UNRECONCILED", source="broker_lots", symbol=symbol)
                continue
            for lot in lots:
                if lot.get("direction") not in ("", "Buy", None) or lot.get("cond") not in ("", "Cash", None):
                    issue("UNSUPPORTED_EVENT", lot, source="broker_lots", symbol=symbol)
                    continue
                candidate, error = _candidate(account_key, "broker_lots", {
                    **lot, "symbol": symbol, "price": lot.get("cost_per_share"), "tax": None,
                }, now)
                if error:
                    issue(error, lot, source="broker_lots", symbol=symbol)
                else:
                    candidates.append(candidate)
    if realized_doc is not None:
        for trade in realized_doc.get("trades") or []:
            if trade.get("cond") != "Cash":
                issue("UNSUPPORTED_EVENT", trade, source="broker_realized")
                continue
            candidate, error = _candidate(account_key, "broker_realized", trade, now)
            if error:
                issue(error, trade, source="broker_realized")
            else:
                candidates.append(candidate)
    multiplicity = {}
    for event in candidates:
        multiplicity[event["event_key"]] = multiplicity.get(event["event_key"], 0) + 1
    for event in candidates:
        key = event["event_key"]
        if multiplicity[key] > 1:
            issue("AMBIGUOUS_EXTERNAL_ID", event, source=event["source"])
            continue
        prior = existing.get(key)
        if prior is None:
            out["events"].append(event)
            existing[key] = event
            continue
        changed = {k: event[k] for k in event if k not in ("first_seen_at", "last_seen_at")
                   and event[k] != prior.get(k)}
        if changed:
            prior["last_seen_at"] = now
            # 部分賣出/分割/成本調整都可能讓剩餘批次變動，不能新增買賣。
            observation = {"event_key": key, "observed": changed}
            if not any(c.get("event_key") == key and c.get("observed") == changed
                       for c in out.setdefault("source_changes", [])):
                out["source_changes"].append({**observation, "seen_at": now})
            issue("SOURCE_CONTENT_CHANGED", event, source=event["source"])
        else:
            prior["last_seen_at"] = now
    out["events"].sort(key=lambda e: (e["date"], e["event_key"]))
    # coverage 是可查到的下限，不聲稱最早一筆就代表完整涵蓋。
    starts = [out.get("coverage_start")]
    if realized_doc and isinstance(realized_doc.get("range"), list) and realized_doc["range"]:
        starts.append(_day(realized_doc["range"][0]))
    starts.extend(e["date"] for e in out["events"])
    out["coverage_start"] = min((d for d in starts if d), default=None)
    out["last_success_at"] = now
    out["source_status"] = {"lots_available": lots_doc is not None,
                            "realized_available": realized_doc is not None}
    out["data_days"] = len({e["date"] for e in out["events"]})
    out["contains_provisional_prices"] = any(e["source"] == "broker_lots" for e in out["events"])
    out["issues"] = sorted(set(issues))
    out["issue_details"] = sorted(issue_details, key=lambda d: (
        d["code"], d["date"] or "", d["symbol"] or "", d["source"] or ""))
    return out
