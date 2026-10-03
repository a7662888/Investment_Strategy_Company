"""Private daily market analyses, separate from immutable signals and outcomes.

The first complete analysis for a market date is retained. Reruns repair the
metadata index without rewriting that day's decisions. Capture time is explicit:
this is daily operational history, not a point-in-time backtest dataset.
"""
from __future__ import annotations

import base64
import json
import math
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from company.data.market_calendar import is_trading_day
from company.model import durable_document as store

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DIR = ROOT / "data" / "daily_history"
INDEX_LOCAL = ROOT / "data" / "daily_history_index.json"
REMOTE_DIR = "value/daily"
INDEX_REMOTE = "value/daily/index.json"
INDEX_LIMIT = 366
TAIPEI = timezone(timedelta(hours=8))
_LOCK = threading.Lock()
_COVERAGE_FIELDS = (
    "mother_pool", "quality_covered", "not_yet_covered", "price_current",
    "price_total", "price_stale", "oldest_price_as_of",
)
_ITEM_FIELDS = (
    "symbol", "name", "as_of", "price", "action", "decision", "eligible_pool",
    "quality_pass", "risk_tier", "fundamentals_complete", "roe_ttm",
    "valuation_basis", "valuation_pct", "valuation_zone", "entry_range",
    "ma20", "ma60", "momentum20", "trend", "high_252",
    "distance_from_high_252", "price_pct_252", "chase_risk", "rank_score", "is_etf",
)
_PROVENANCE_FIELDS = ("price_date", "price_source", "financials_period", "revenue_month")


class HistoryValidationError(ValueError):
    """A payload-free validation code suitable for pipeline diagnostics."""


def _date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("market date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("market date must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("market date must be YYYY-MM-DD")
    return value


def latest_completed_market_date(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("capture time must have a timezone")
    local = now.astimezone(TAIPEI)
    day = local.date()
    if local.hour < 14:
        day -= timedelta(days=1)
    for _ in range(370):
        if is_trading_day(datetime.combine(day, datetime.min.time(), TAIPEI)):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError("no completed trading day in calendar")


def _item(item: dict) -> dict:
    result = {key: item[key] for key in _ITEM_FIELDS if key in item}
    result["data_provenance"] = {
        key: item["data_provenance"][key] for key in _PROVENANCE_FIELDS
        if key in (item.get("data_provenance") or {})
    }
    return result


def _public_day(document: dict) -> dict:
    fields = (
        "schema_version", "date", "as_of", "analysis_date_taipei", "generated_at",
        "captured_at", "mode", "status", "shadow", "market_expected_as_of",
        "market_data_complete", "method",
    )
    result = {key: document[key] for key in fields if key in document}
    result["coverage"] = {key: document["coverage"][key] for key in _COVERAGE_FIELDS
                          if key in (document.get("coverage") or {})}
    for bucket in ("evaluations", "top_picks", "waiting_list", "etf_candidates"):
        result[bucket] = [_item(item) for item in document.get(bucket, [])]
    return result


def _validate_saved_day(document: dict, day: str) -> None:
    if (document.get("date") != day or document.get("as_of") != day
            or document.get("status") != "complete"
            or document.get("market_data_complete") is not True
            or document.get("market_expected_as_of") != day):
        raise ValueError("history document is not a complete market day")
    items = document.get("evaluations") or []
    symbols = [item.get("symbol") for item in items]
    coverage = document.get("coverage") or {}
    if (not items or len(set(symbols)) != len(items)
            or coverage.get("price_current") != len(items)
            or coverage.get("price_total") != len(items) or coverage.get("price_stale") != 0):
        raise ValueError("history document has incomplete coverage")
    for item in items:
        price = item.get("price")
        if (item.get("as_of") != day or isinstance(price, bool)
                or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0
                or not item.get("decision") or not item.get("action")
                or item.get("data_provenance", {}).get("price_date", day) != day):
            raise ValueError("history document has incomplete evaluations")


def _metadata(document: dict) -> dict:
    return {"date": document["date"], "as_of": document["as_of"],
            "coverage": document["coverage"],
            "picks": [item["symbol"] for item in document.get("top_picks", [])]}


def _public_index(document: dict) -> dict:
    entries = []
    for entry in document.get("dates", []):
        day = _date(entry.get("date"))
        if entry.get("as_of") != day:
            raise ValueError("history index date mismatch")
        entries.append({"date": day, "as_of": day,
                        "coverage": {key: entry["coverage"][key] for key in _COVERAGE_FIELDS
                                     if key in (entry.get("coverage") or {})},
                        "picks": list(entry.get("picks") or [])})
    by_date = {entry["date"]: entry for entry in entries}
    dates = [by_date[day] for day in sorted(by_date, reverse=True)[:INDEX_LIMIT]]
    return {"schema_version": 1, "latest": dates[0]["date"] if dates else None, "dates": dates}


def load_history_index() -> tuple[dict | None, dict]:
    document, storage = store.load_document(INDEX_LOCAL, INDEX_REMOTE)
    return (_public_index(document) if document is not None else None), storage


def load_history_day(day: str) -> tuple[dict | None, dict]:
    day = _date(day)
    document, storage = store.load_document(LOCAL_DIR / f"{day}.json", f"{REMOTE_DIR}/{day}.json")
    if document is not None:
        _validate_saved_day(document, day)
        document = _public_day(document)
    return document, storage


def load_daily_history(date: str | None = None) -> tuple[dict | None, dict]:
    return load_history_index() if date is None else load_history_day(date)


def _complete_document(state: dict, now: datetime, expected_symbols: list[str], *,
                       allow_existing_analysis: bool = False) -> dict:
    day = _date(state.get("as_of"))
    if day != latest_completed_market_date(now):
        raise HistoryValidationError("market_date_not_latest_completed")
    analysis_date = _date(state.get("analysis_date_taipei"))
    capture_date = now.astimezone(TAIPEI).date().isoformat()
    if analysis_date > capture_date or (not allow_existing_analysis and analysis_date != capture_date):
        raise HistoryValidationError("analysis_date_mismatch")
    try:
        generated = datetime.fromisoformat(str(state.get("generated_at") or "").replace("Z", "+00:00"))
    except ValueError:
        raise HistoryValidationError("invalid_generation_time") from None
    if generated.tzinfo is None or generated > now:
        raise HistoryValidationError("invalid_generation_time")
    if generated.astimezone(TAIPEI).date().isoformat() != state["analysis_date_taipei"]:
        raise HistoryValidationError("generation_date_mismatch")
    if generated.astimezone(TAIPEI) < datetime.fromisoformat(day + "T14:00:00+08:00"):
        raise HistoryValidationError("analysis_before_completed_close")
    if state.get("market_data_complete") is not True or state.get("market_expected_as_of") != day:
        raise HistoryValidationError("unconfirmed_market_close")
    items = state.get("evaluations") or []
    symbols = [item.get("symbol") for item in items]
    if not expected_symbols or len(symbols) != len(set(symbols)) or set(symbols) != set(expected_symbols):
        raise HistoryValidationError("incomplete_symbol_coverage")
    for item in items:
        price = item.get("price")
        if (item.get("as_of") != day or isinstance(price, bool)
                or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0):
            raise HistoryValidationError("incomplete_price_coverage")
        if item.get("data_provenance", {}).get("price_date", day) != day:
            raise HistoryValidationError("price_provenance_mismatch")
        if not item.get("decision") or not item.get("action"):
            raise HistoryValidationError("missing_daily_decision")
    coverage = state.get("coverage") or {}
    if (coverage.get("price_current") != len(items) or coverage.get("price_total") != len(items)
            or coverage.get("price_stale") != 0):
        raise HistoryValidationError("inconsistent_price_coverage")
    document = _public_day(state)
    document.update(schema_version=1, date=day, captured_at=now.isoformat(),
                    mode="daily-analysis-history", status="complete")
    # Buckets must reference the same evaluated decisions, not independent payloads.
    by_symbol = {item["symbol"]: item for item in document["evaluations"]}
    for bucket in ("top_picks", "waiting_list", "etf_candidates"):
        document[bucket] = [by_symbol[item["symbol"]] for item in state.get(bucket, [])]
    return document


def _read(local: Path, remote: str, config) -> tuple[dict | None, str | None]:
    if config:
        document, sha, error = store._remote_get(remote, config)
        if error:
            raise RuntimeError("history_remote_read_failed")
        return document, sha
    document, storage = store.load_document(local, remote, prefer_remote=False)
    if storage.get("error"):
        raise RuntimeError("history_local_read_failed")
    return document, None


def _write(document: dict, local: Path, remote: str, config, sha: str | None) -> bool:
    if not config:
        result = store.save_document(document, local, remote, "chore(value): daily history")
        if not result.get("local_saved"):
            raise RuntimeError("history_local_write_failed")
        return True
    token, repo, branch = config
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in remote.split("/"))
    body = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    payload = {"message": "chore(value): daily history", "branch": branch,
               "content": base64.b64encode(body.encode("utf-8")).decode("ascii")}
    if sha:
        payload["sha"] = sha
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{encoded}",
        data=json.dumps(payload).encode("utf-8"), method="PUT",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "investment-daily-history"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (409, 422):
            return False
        raise RuntimeError(f"history_remote_write_http_{exc.code}") from None
    except Exception:
        raise RuntimeError("history_remote_write_failed") from None
    return True


def record_daily_history(state: dict, expected_symbols: list[str], *, now: datetime | None = None,
                         require_durable: bool | None = None,
                         allow_existing_analysis: bool = False) -> dict:
    """Write once per complete market day; errors never masquerade as success.

Conditional creates preserve the first day's analysis. CAS index writes merge
concurrent dates and retry; a crash after the day write is repaired on rerun.
    No ledger function is called and no portfolio fields are copied. Explicit
    bootstrap may reuse the latest completed day's already-generated analysis;
    it never recalculates fundamentals or backdates the actual capture time.
"""
    now = now or datetime.now(timezone.utc)
    config = store._config()
    if require_durable is None:
        require_durable = config is not None or os.environ.get("GITHUB_ACTIONS") == "true"
    try:
        document = _complete_document(state, now, expected_symbols,
                                      allow_existing_analysis=allow_existing_analysis)
        if require_durable and not config:
            raise RuntimeError("history_durable_storage_not_configured")
        day = document["date"]
        local, remote = LOCAL_DIR / f"{day}.json", f"{REMOTE_DIR}/{day}.json"
        with _LOCK:
            existed = False
            for _ in range(3):
                previous, _ = _read(local, remote, config)
                if previous is not None:
                    _validate_saved_day(previous, day)
                    document = _public_day(previous)
                    existed = True
                    break
                if _write(document, local, remote, config, None):
                    break
            else:
                raise RuntimeError("history_day_conflict")
            for _ in range(3):
                previous, sha = _read(INDEX_LOCAL, INDEX_REMOTE, config)
                index = _public_index(previous or {})
                dates = {entry["date"]: entry for entry in index["dates"]}
                dates[day] = _metadata(document)
                updated = _public_index({"dates": list(dates.values())})
                if updated == index or _write(updated, INDEX_LOCAL, INDEX_REMOTE, config, sha):
                    return {"ok": True, "status": "already_exists" if existed else "saved",
                            "date": day, "durable": config is not None, "indexed": True}
            raise RuntimeError("history_index_conflict")
    except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
        # Never include payloads, credentials or provider response bodies in output.
        error = str(exc) if isinstance(exc, (RuntimeError, HistoryValidationError)) else type(exc).__name__
        return {"ok": False, "status": "error", "durable": False, "error": error}
