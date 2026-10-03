"""Read-only SinoPac inventory, isolated from the manual portfolio.

Only production, one unambiguously selected signed stock account, Unit.Share,
Cash/Buy rows are accepted. An unsupported row rejects the entire fetch.
Public status is a whitelist; neither exceptions nor account identifiers are
ever serialized into it. Reconciliation is deliberately left to the caller.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from company.model import durable_document

ROOT = Path(__file__).resolve().parents[2]
# Use an already ignored private directory; never create an unignored export.
LOCAL_PATH = ROOT / "data" / "daily_audit" / "broker_positions" / "snapshot.json"
REMOTE_PATH = "private/broker_positions.json"
MAX_ROWS = 1000
ERROR_CODES = frozenset({
    "ACCOUNT_SELECTION_REQUIRED", "ACCOUNT_NOT_FOUND", "ACCOUNT_AMBIGUOUS",
    "ACCOUNT_NOT_SIGNED", "ACCOUNT_SELECTION_CHANGED", "PRODUCTION_REQUIRED",
    "SHARE_UNIT_REQUIRED", "INVALID_RESPONSE", "INVALID_POSITION",
    "UNSUPPORTED_POSITION", "UNKNOWN_CONTRACT", "UNSUPPORTED_EXCHANGE",
    "INVALID_SNAPSHOT_DOCUMENT", "BROKER_UNAUTHORIZED", "BROKER_FORBIDDEN",
    "BROKER_ACCOUNT_NOT_ACCEPTABLE", "BROKER_PRODUCTION_PERMISSION",
    "BROKER_TIMEOUT", "BROKER_QUERY_FAILED", "SDK_UNAVAILABLE",
    "CREDENTIALS_REQUIRED", "PRIVATE_STORAGE_NOT_CONFIGURED",
    "STORAGE_READ_FAILED", "STORAGE_WRITE_FAILED", "STORAGE_CONFLICT",
    "LOCAL_CACHE_WRITE_FAILED", "LIVE_FETCH_NOT_REQUESTED",
})


class BrokerPositionsError(ValueError):
    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "BROKER_QUERY_FAILED"
        super().__init__(self.code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _enum(value):
    return getattr(value, "value", value)


def _positive(value, *, integer=False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BrokerPositionsError("INVALID_POSITION")
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or number <= 0
                or not math.isfinite(float(number))
                or (integer and number != number.to_integral_value())):
            raise BrokerPositionsError("INVALID_POSITION")
        return number
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise BrokerPositionsError("INVALID_POSITION") from None


def _account_key(broker_id: str, account_id: str) -> str:
    if (not isinstance(broker_id, str) or not broker_id.strip()
            or not isinstance(account_id, str) or not account_id.strip()):
        raise BrokerPositionsError("ACCOUNT_SELECTION_REQUIRED")
    # Private-only binding prevents accidentally displaying a different account's cache.
    raw = json.dumps([broker_id.strip(), account_id.strip()]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _symbol(api, code) -> str:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4,6}[A-Z]?", code):
        raise BrokerPositionsError("INVALID_POSITION")
    contracts = getattr(api, "contracts", None)
    if contracts is None:
        contracts = getattr(api, "Contracts", None)
    stocks = getattr(contracts, "Stocks", None)
    contract = stocks.get(code) if stocks is not None else None
    if contract is None or _value(contract, "code") != code:
        raise BrokerPositionsError("UNKNOWN_CONTRACT")
    suffix = {"TSE": ".TW", "OTC": ".TWO"}.get(_enum(_value(contract, "exchange")))
    if suffix is None:
        raise BrokerPositionsError("UNSUPPORTED_EXCHANGE")
    return code + suffix


def select_stock_account(api, *, broker_id="", account_id=""):
    """Never use the SDK default: exact IDs, or exactly one signed stock account."""
    broker_id = broker_id.strip() if isinstance(broker_id, str) else ""
    account_id = account_id.strip() if isinstance(account_id, str) else ""
    if bool(broker_id) != bool(account_id):
        raise BrokerPositionsError("ACCOUNT_SELECTION_REQUIRED")
    accounts = api.list_accounts()
    if not isinstance(accounts, (list, tuple)):
        raise BrokerPositionsError("INVALID_RESPONSE")
    stocks = [a for a in accounts if _enum(_value(a, "account_type")) == "S"]
    if broker_id:
        matches = [a for a in stocks if _value(a, "broker_id") == broker_id
                   and _value(a, "account_id") == account_id]
    else:
        matches = [a for a in stocks if _value(a, "signed") is True]
        if stocks and not matches:
            raise BrokerPositionsError("ACCOUNT_NOT_SIGNED")
    if not matches:
        raise BrokerPositionsError("ACCOUNT_NOT_FOUND")
    if len(matches) != 1:
        raise BrokerPositionsError("ACCOUNT_AMBIGUOUS")
    account = matches[0]
    if _value(account, "signed") is not True:
        raise BrokerPositionsError("ACCOUNT_NOT_SIGNED")
    _account_key(_value(account, "broker_id"), _value(account, "account_id"))
    return account


def fetch_broker_positions(api, *, broker_id="", account_id="", share_unit) -> dict:
    """Use only list_accounts/list_positions and the contract catalog; never orders."""
    if getattr(api, "simulation", None) is not False:
        raise BrokerPositionsError("PRODUCTION_REQUIRED")
    if _enum(share_unit) != "Share":
        raise BrokerPositionsError("SHARE_UNIT_REQUIRED")
    account = select_stock_account(api, broker_id=broker_id, account_id=account_id)
    key = _account_key(_value(account, "broker_id"), _value(account, "account_id"))
    rows = api.list_positions(account=account, unit=share_unit, timeout=5000)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise BrokerPositionsError("INVALID_RESPONSE")
    totals = {}
    for row in rows:
        if (_enum(_value(row, "cond")) != "Cash"
                or _enum(_value(row, "direction")) != "Buy"):
            raise BrokerPositionsError("UNSUPPORTED_POSITION")
        symbol = _symbol(api, _value(row, "code"))
        quantity = _positive(_value(row, "quantity"), integer=True)
        price = _positive(_value(row, "price"))
        shares, cost = totals.get(symbol, (Decimal(0), Decimal(0)))
        totals[symbol] = (shares + quantity, cost + quantity * price)
    positions = [{"symbol": symbol, "shares": int(shares), "cost": float(cost / shares)}
                 for symbol, (shares, cost) in sorted(totals.items())]
    if any(not math.isfinite(p["cost"]) for p in positions):
        raise BrokerPositionsError("INVALID_POSITION")
    return {"source": "shioaji", "simulation": False, "unit": "Share",
            "account_key": key, "as_of": _now(), "positions": positions}


def error_code(exc: Exception) -> str:
    """Classify locally; raw SDK exceptions may contain PII or credentials."""
    if isinstance(exc, BrokerPositionsError):
        return exc.code
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in (401, "401"):
        return "BROKER_UNAUTHORIZED"
    if code in (403, "403"):
        return "BROKER_FORBIDDEN"
    if code in (406, "406"):
        return "BROKER_ACCOUNT_NOT_ACCEPTABLE"
    if isinstance(exc, TimeoutError):
        return "BROKER_TIMEOUT"
    text = str(exc).lower()
    if "production permission" in text:
        return "BROKER_PRODUCTION_PERMISSION"
    if "account not acceptable" in text:
        return "BROKER_ACCOUNT_NOT_ACCEPTABLE"
    if "doesn't have permission" in text or "unauthorized" in text:
        return "BROKER_UNAUTHORIZED"
    return "BROKER_QUERY_FAILED"


def _timestamp(value):
    if not isinstance(value, str):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT") from None
    return value


def _snapshot(raw):
    if (not isinstance(raw, dict) or raw.get("source") != "shioaji"
            or raw.get("simulation") is not False or raw.get("unit") != "Share"):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    if not re.fullmatch(r"[a-f0-9]{64}", str(raw.get("account_key", ""))):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    rows = raw.get("positions")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    positions, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"[0-9]{4,6}[A-Z]?\.(TW|TWO)", str(row.get("symbol", ""))):
            raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
        symbol = row["symbol"]
        if symbol in seen:
            raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
        seen.add(symbol)
        positions.append({"symbol": symbol, "shares": int(_positive(row.get("shares"), integer=True)),
                          "cost": float(_positive(row.get("cost")))})
    return {"source": "shioaji", "simulation": False, "unit": "Share",
            "account_key": raw["account_key"], "as_of": _timestamp(raw.get("as_of")),
            "positions": sorted(positions, key=lambda p: p["symbol"])}


def _document(raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    attempt = raw.get("last_attempt")
    if not isinstance(attempt, dict) or not isinstance(attempt.get("ok"), bool):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    code = attempt.get("error_code")
    if ((attempt["ok"] and code is not None)
            or (not attempt["ok"] and code not in ERROR_CODES)):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    snapshot = raw.get("last_successful_snapshot")
    if attempt["ok"] and snapshot is None:
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    return {"schema_version": 1,
            "last_attempt": {"at": _timestamp(attempt.get("at")), "ok": attempt["ok"], "error_code": code},
            "last_successful_snapshot": _snapshot(snapshot) if snapshot is not None else None}


def _status(doc, storage, code=None):
    snapshot = doc.get("last_successful_snapshot") if doc else None
    attempt = doc.get("last_attempt") if doc else None
    return {"source": "shioaji", "simulation": False, "unit": "Share",
            "has_snapshot": snapshot is not None,
            "last_success_at": snapshot["as_of"] if snapshot else None,
            "position_count": len(snapshot["positions"]) if snapshot else 0,
            "last_attempt_at": attempt["at"] if attempt else None,
            "last_attempt_ok": attempt["ok"] if attempt else None,
            "error_code": code or (attempt["error_code"] if attempt else None),
            "stale": bool(code or not attempt or not attempt["ok"] or not storage.get("durable")),
            "storage": {"source": storage.get("source") if storage.get("source") in ("github", "local") else "none",
                        "durable": storage.get("durable") is True}}


def load_broker_positions(*, prefer_remote=True, broker_id=None, account_id=None):
    """Return (private document, storage), compatible with the manual store loader.

    Only a successful latest attempt read from durable storage has status=ok.
    A failed attempt retains prior positions but cannot be automatically adopted.
    Selectors reject another account's cache. Public callers use the accessor below.
    """
    raw, storage = durable_document.load_document(LOCAL_PATH, REMOTE_PATH, prefer_remote=prefer_remote)
    code = "STORAGE_READ_FAILED" if storage.get("remote_error") or storage.get("error") else None
    doc = None
    snapshot = None
    try:
        doc = _document(raw) if raw is not None else None
        snapshot = doc.get("last_successful_snapshot") if doc else None
        if broker_id is not None or account_id is not None:
            key = _account_key(broker_id, account_id)
            if snapshot and snapshot["account_key"] != key:
                snapshot = None
                code = "ACCOUNT_SELECTION_CHANGED"
    except BrokerPositionsError:
        doc, snapshot, code = None, None, "INVALID_SNAPSHOT_DOCUMENT"
    public = _status(doc, storage, code)
    if snapshot:
        snapshot = {k: v for k, v in snapshot.items() if k != "account_key"}
    state = "ok" if snapshot is not None and not public["stale"] else "error" if public["error_code"] else "stale" if snapshot is not None else "unavailable"
    result = {"schema_version": 1, "source": "shioaji", "simulation": False, "unit": "Share",
              "status": state, "as_of": snapshot["as_of"] if snapshot else None,
              "fetched_at": snapshot["as_of"] if snapshot else None,
              "updated_at": public["last_attempt_at"],
              "positions": snapshot["positions"] if snapshot else [],
              "error_code": public["error_code"], "last_attempt": doc["last_attempt"] if doc else None,
              "last_successful_snapshot": snapshot,
              "last_successful_status": {"status": "ok", "as_of": snapshot["as_of"],
                                         "position_count": len(snapshot["positions"])} if snapshot else None,
              "public_status": {**public, "status": state}}
    # Storage retains safe attempt metadata for existing endpoint integrations.
    return result, {**public, **public["storage"]}


def broker_positions_status(**kwargs) -> dict:
    """Safe for a public health/status endpoint; contains no portfolio or identifiers."""
    return load_broker_positions(**kwargs)[0]["public_status"]


def _atomic_cache(doc):
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=LOCAL_PATH.parent, delete=False) as handle:
            name = handle.name
            json.dump(doc, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, LOCAL_PATH)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def record_broker_attempt(*, snapshot=None, failure_code=None) -> dict:
    """Persist one envelope with CAS; failures cannot clear a prior successful snapshot.

    Commit remote first, cache only after success. A CAS conflict is never retried
    with stale content. Remote read errors must not be interpreted as empty state.
    """
    if (snapshot is None) == (failure_code is None) or (failure_code is not None and failure_code not in ERROR_CODES):
        raise BrokerPositionsError("INVALID_SNAPSHOT_DOCUMENT")
    snapshot = _snapshot(snapshot) if snapshot is not None else None
    config = durable_document._config()
    sha = None
    if config:
        raw, sha, read_error = durable_document._remote_get(REMOTE_PATH, config)
        if read_error:
            return {"saved": False, "durable": False, "error_code": "STORAGE_READ_FAILED"}
    else:
        raw, storage = durable_document.load_document(LOCAL_PATH, REMOTE_PATH, prefer_remote=False)
        if storage.get("error"):
            return {"saved": False, "durable": False, "error_code": "STORAGE_READ_FAILED"}
    try:
        old = _document(raw) if raw is not None else None
    except BrokerPositionsError:
        return {"saved": False, "durable": False, "error_code": "INVALID_SNAPSHOT_DOCUMENT"}
    doc = {"schema_version": 1,
           "last_attempt": {"at": _now(), "ok": snapshot is not None, "error_code": failure_code},
           "last_successful_snapshot": snapshot if snapshot is not None else (old.get("last_successful_snapshot") if old else None)}
    if config:
        token, repo, branch = config
        encoded = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
        payload = {"message": "chore(broker): update private inventory status", "branch": branch,
                   "content": base64.b64encode(json.dumps(doc, allow_nan=False).encode()).decode("ascii")}
        if sha:
            payload["sha"] = sha
        request = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{encoded}",
            data=json.dumps(payload).encode(), method="PUT", headers={
                "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                "Content-Type": "application/json", "User-Agent": "investment-broker-positions"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            return {"saved": False, "durable": False, "error_code": "STORAGE_CONFLICT" if exc.code in (409, 422) else "STORAGE_WRITE_FAILED"}
        except Exception:
            return {"saved": False, "durable": False, "error_code": "STORAGE_WRITE_FAILED"}
    try:
        _atomic_cache(doc)
    except OSError:
        return {"saved": bool(config), "durable": bool(config), "error_code": "LOCAL_CACHE_WRITE_FAILED"}
    return {"saved": True, "durable": bool(config), "error_code": None}
