# -*- coding: utf-8 -*-
"""Append-only decision signals and forward outcome events."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = Path(os.environ.get("DECISION_LEDGER_PATH", ROOT / "data" / "decision_ledger.jsonl"))
SCHEMA_VERSION = 1
HORIZONS = ("1D", "5D", "20D", "60D", "120D")
REMOTE_PATH = "data/decision_ledger.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_id(prefix: str, payload: dict) -> str:
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _parse_jsonl(content: str) -> list[dict]:
    events = []
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not event.get("event_id"):
            raise ValueError(f"ledger line {number} lacks event_id")
        events.append(event)
    return events


def _serialize_jsonl(events: list[dict]) -> str:
    return "".join(_canonical(event) + "\n" for event in events)


def _remote_config() -> tuple[str, str, str] | None:
    token = os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT")
    repo = os.environ.get("GITHUB_DATA_REPO")
    branch = os.environ.get("GITHUB_DATA_BRANCH", "main")
    if not token or not repo or "/" not in repo:
        return None
    return token, repo, branch


def _remote_request(method: str, token: str, repo: str, branch: str, payload: dict | None = None):
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}"
    if method == "GET":
        url += "?" + urllib.parse.urlencode({"ref": branch})
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "investment-strategy-company-ledger",
    }
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_remote() -> tuple[list[dict] | None, str | None, str | None]:
    config = _remote_config()
    if config is None:
        return None, None, "remote_not_configured"
    token, repo, branch = config
    try:
        data = _remote_request("GET", token, repo, branch)
        raw = base64.b64decode(str(data.get("content", "")).replace("\n", "")).decode("utf-8")
        return _parse_jsonl(raw), data.get("sha"), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return [], None, None
        return None, None, f"github_http_{exc.code}"
    except Exception as exc:
        return None, None, f"github_read:{type(exc).__name__}"


def _write_remote(events: list[dict], sha: str | None) -> tuple[bool, str | None]:
    config = _remote_config()
    if config is None:
        return False, "remote_not_configured"
    token, repo, branch = config
    payload = {
        "message": f"chore(ledger): append decision events {_utc_now()}",
        "content": base64.b64encode(_serialize_jsonl(events).encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    try:
        _remote_request("PUT", token, repo, branch, payload)
        return True, None
    except urllib.error.HTTPError as exc:
        return False, f"github_http_{exc.code}"
    except Exception as exc:
        return False, f"github_write:{type(exc).__name__}"


def load_events(prefer_remote: bool = True) -> tuple[list[dict], dict]:
    """Load remote events when configured, otherwise the local ephemeral snapshot."""
    remote_events = None
    remote_error = None
    if prefer_remote:
        remote_events, _, remote_error = _fetch_remote()
    if remote_events is not None:
        events = remote_events
        source = "github"
        try:
            LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            LEDGER_PATH.write_text(_serialize_jsonl(events), encoding="utf-8")
        except OSError:
            pass
    else:
        source = "local"
        try:
            events = _parse_jsonl(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else []
        except Exception:
            events = []
    return events, {
        "source": source,
        "remote_configured": _remote_config() is not None,
        "durable": source == "github",
        "error": remote_error,
        "event_count": len(events),
    }


def append_events(new_events: list[dict]) -> dict:
    """Idempotently append events and report whether durable storage succeeded."""
    remote_events, sha, remote_error = _fetch_remote()
    if remote_events is None:
        try:
            current = _parse_jsonl(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else []
        except Exception:
            current = []
    else:
        current = remote_events

    known = {event.get("event_id") for event in current}
    added = [event for event in new_events if event.get("event_id") and event["event_id"] not in known]
    merged = current + added
    local_ok = True
    local_error = None
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(_serialize_jsonl(merged), encoding="utf-8")
    except OSError as exc:
        local_ok = False
        local_error = f"local_write:{type(exc).__name__}"

    remote_ok = False
    write_error = remote_error
    if _remote_config() is not None and added:
        remote_ok, write_error = _write_remote(merged, sha)
        if not remote_ok and write_error == "github_http_409":
            latest, latest_sha, _ = _fetch_remote()
            if latest is not None:
                latest_ids = {event.get("event_id") for event in latest}
                merged = latest + [event for event in added if event["event_id"] not in latest_ids]
                remote_ok, write_error = _write_remote(merged, latest_sha)
    elif _remote_config() is not None:
        remote_ok = True
        write_error = None

    return {
        "added": len(added),
        "total": len(merged),
        "local_saved": local_ok,
        "remote_configured": _remote_config() is not None,
        "remote_saved": remote_ok,
        "durable": remote_ok,
        "status": "durable" if remote_ok else "degraded",
        "error": write_error or local_error,
    }


def build_signal_event(signal: dict) -> dict:
    core = {
        "agent_id": str(signal.get("agent_id") or signal.get("provider") or "unknown"),
        "model_version": str(signal.get("model_version") or "unknown"),
        "symbol": str(signal.get("symbol") or "").upper(),
        "data_cutoff": str(signal.get("data_cutoff") or ""),
        "action": str(signal.get("action") or "watch"),
        "horizon": str(signal.get("horizon") or "1D"),
    }
    if not core["symbol"] or not core["data_cutoff"]:
        raise ValueError("symbol and data_cutoff are required")
    signal_id = _event_id("SIG", core)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "signal",
        "event_id": signal_id,
        "signal_id": signal_id,
        "recorded_at": _utc_now(),
        **core,
        "name": signal.get("name"),
        "reference_price": signal.get("reference_price", signal.get("close")),
        "entry_range": signal.get("entry_range", signal.get("buy_range")),
        "stop_loss": signal.get("stop_loss"),
        "target": signal.get("target", signal.get("take_profit")),
        "invalidation": signal.get("invalidation"),
        "grade": signal.get("grade"),
        "score": signal.get("score"),
        "evidence": list(signal.get("evidence") or signal.get("reasons") or []),
        "market_risk": signal.get("market_risk"),
        "data_quality": signal.get("data_quality") or {},
    }


def freeze_signals(signals: list[dict]) -> dict:
    events = []
    errors = []
    for index, signal in enumerate(signals):
        try:
            events.append(build_signal_event(signal))
        except Exception as exc:
            errors.append({"index": index, "error": str(exc)})
    result = append_events(events)
    result["invalid"] = errors
    return result


def materialize(events: list[dict]) -> list[dict]:
    signals = {}
    for event in events:
        if event.get("event_type") == "signal":
            signals[event["signal_id"]] = {**event, "outcomes": {}}
        elif event.get("event_type") == "outcome" and event.get("signal_id") in signals:
            signals[event["signal_id"]]["outcomes"][event["horizon"]] = event
    return sorted(signals.values(), key=lambda item: item.get("recorded_at", ""), reverse=True)


def ledger_summary(limit: int = 100) -> dict:
    events, storage = load_events()
    signals = materialize(events)
    return {"storage": storage, "signals": signals[: max(0, limit)], "signal_count": len(signals)}


def _outcome_event(signal: dict, horizon: str, rows: list[dict], benchmark_rows: list[dict], cost_rate: float) -> dict | None:
    n_days = int(horizon[:-1])
    cutoff = signal["data_cutoff"]
    after = [row for row in rows if row["date"] > cutoff]
    if len(after) < n_days:
        return None
    reference = float(signal.get("reference_price") or 0)
    if reference <= 0:
        return None
    period = after[:n_days]
    end_row = period[-1]
    gross = float(end_row["close"]) / reference - 1.0
    benchmark_before = [row for row in benchmark_rows if row["date"] <= cutoff]
    benchmark_end = [row for row in benchmark_rows if row["date"] <= end_row["date"]]
    benchmark_return = None
    if benchmark_before and benchmark_end:
        b0 = float(benchmark_before[-1]["close"])
        b1 = float(benchmark_end[-1]["close"])
        if b0 > 0:
            benchmark_return = b1 / b0 - 1.0
    payload = {"signal_id": signal["signal_id"], "horizon": horizon, "evaluation_date": end_row["date"]}
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "outcome",
        "event_id": _event_id("OUT", payload),
        "recorded_at": _utc_now(),
        **payload,
        "reference_price": reference,
        "evaluation_price": float(end_row["close"]),
        "gross_return": round(gross, 8),
        "transaction_cost_rate": cost_rate,
        "net_return": round(gross - cost_rate, 8),
        "mfe": round(max(float(row["high"]) for row in period) / reference - 1.0, 8),
        "mae": round(min(float(row["low"]) for row in period) / reference - 1.0, 8),
        "benchmark_symbol": "0050.TW",
        "benchmark_return": None if benchmark_return is None else round(benchmark_return, 8),
        "excess_return": None if benchmark_return is None else round(gross - benchmark_return, 8),
    }


def update_outcomes(as_of: str, fetch_history: Callable[[str, str, str], list[dict]]) -> dict:
    events, storage = load_events()
    signals = materialize(events)
    existing = {event.get("event_id") for event in events}
    pending = []
    if not signals:
        return {"added": 0, "storage": storage}
    start = min(signal["data_cutoff"] for signal in signals)
    end = (datetime.fromisoformat(as_of) + timedelta(days=1)).date().isoformat()
    try:
        benchmark_rows = sorted(fetch_history("0050.TW", start, end), key=lambda row: row["date"])
    except Exception:
        benchmark_rows = []
    cost_rate = max(0.0, float(os.environ.get("LEDGER_TRANSACTION_COST_RATE", "0")))
    cache = {}
    for signal in signals:
        symbol = signal["symbol"]
        if symbol not in cache:
            try:
                cache[symbol] = sorted(fetch_history(symbol, signal["data_cutoff"], end), key=lambda row: row["date"])
            except Exception:
                cache[symbol] = []
        for horizon in HORIZONS:
            event = _outcome_event(signal, horizon, cache[symbol], benchmark_rows, cost_rate)
            if event and event["event_id"] not in existing:
                pending.append(event)
                existing.add(event["event_id"])
    result = append_events(pending)
    result["storage_before"] = storage
    return result

