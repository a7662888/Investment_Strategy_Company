# -*- coding: utf-8 -*-
"""Append-only decision signals and forward outcome events."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
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
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        os.environ[parts[0].strip()] = parts[1].strip()
        except Exception:
            pass
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
        content = str(data.get("content") or "")
        # >1MB 檔案 contents API 回傳 content=""/encoding="none"（但 sha 有效）——
        # 若誤當空帳本會導致後續寫入整檔覆蓋（2026-07-09 事故根因）。改走 blob API（上限 100MB）。
        if (not content.strip()) and data.get("sha") and int(data.get("size") or 0) > 0:
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "investment-strategy-company-ledger",
            }
            blob_url = f"https://api.github.com/repos/{repo}/git/blobs/{data['sha']}"
            request = urllib.request.Request(blob_url, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                blob = json.loads(response.read().decode("utf-8"))
            content = str(blob.get("content") or "")
            if not content.strip():
                return None, None, "blob_read_empty"
        raw = base64.b64decode(content.replace("\n", "")).decode("utf-8")
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


_READ_CACHE: dict = {"at": 0.0, "events": None, "storage": None, "prefer_remote": None}
_READ_TTL = float(os.environ.get("LEDGER_READ_TTL", "45"))


def _invalidate_read_cache() -> None:
    _READ_CACHE.update(events=None, storage=None, prefer_remote=None)


def load_events(prefer_remote: bool = True, use_cache: bool = True) -> tuple[list[dict], dict]:
    """Load remote events when configured, otherwise the local ephemeral snapshot."""
    if (
        use_cache
        and _READ_CACHE["events"] is not None
        and _READ_CACHE["prefer_remote"] == prefer_remote
        and (time.monotonic() - _READ_CACHE["at"]) < _READ_TTL
    ):
        return list(_READ_CACHE["events"]), dict(_READ_CACHE["storage"])
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
    storage = {
        "source": source,
        "remote_configured": _remote_config() is not None,
        "durable": source == "github",
        "error": remote_error,
        "event_count": len(events),
    }
    _READ_CACHE.update(
        at=time.monotonic(),
        events=list(events),
        storage=dict(storage),
        prefer_remote=prefer_remote,
    )
    return events, storage


def append_events(new_events: list[dict]) -> dict:
    """Idempotently append events and report whether durable storage succeeded."""
    _invalidate_read_cache()
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
        if remote_events is not None:
            # Remote read succeeded and nothing new to write: already consistent → durable.
            remote_ok = True
            write_error = None
        else:
            # Remote configured but unreachable this cycle: cannot claim durability.
            remote_ok = False
            write_error = remote_error or "github_read_failed"

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


def _adj_close(row: dict) -> float | None:
    """Adjusted (dividend/split) close. Returns None when no adjusted value is present,
    so callers fall back to the frozen raw reference rather than silently using raw close."""
    try:
        value = row.get("adj_close")
        return float(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def _adj_factor(row: dict) -> float:
    """adj/raw ratio, used to put unadjusted Yahoo high/low on the adjusted basis."""
    try:
        raw = float(row.get("close"))
        adj = float(row.get("adj_close") or row.get("close"))
        return adj / raw if raw else 1.0
    except (TypeError, ValueError):
        return 1.0


def _outcome_event(signal: dict, horizon: str, rows: list[dict], benchmark_rows: list[dict], cost_rate: float) -> dict | None:
    n_days = int(horizon[:-1])
    cutoff = signal["data_cutoff"]
    after = [row for row in rows if row["date"] > cutoff]
    if len(after) < n_days:
        return None
    raw_reference = float(signal.get("reference_price") or 0)
    if raw_reference <= 0:
        return None
    period = after[:n_days]
    end_row = period[-1]

    # Prefer a total-return basis: same adjusted series for reference and evaluation.
    ref_rows = [row for row in rows if row["date"] <= cutoff]
    adj_ref = _adj_close(ref_rows[-1]) if ref_rows else None
    adj_end = _adj_close(end_row)
    if adj_ref and adj_ref > 0 and adj_end:
        return_basis = "adjusted"
        reference_basis = adj_ref
        end_value = adj_end
        highs = [_adj_factor(row) * float(row["high"]) for row in period]
        lows = [_adj_factor(row) * float(row["low"]) for row in period]
    else:
        return_basis = "raw"
        reference_basis = raw_reference
        end_value = float(end_row["close"])
        highs = [float(row["high"]) for row in period]
        lows = [float(row["low"]) for row in period]

    gross = end_value / reference_basis - 1.0

    benchmark_before = [row for row in benchmark_rows if row["date"] <= cutoff]
    benchmark_end = [row for row in benchmark_rows if row["date"] <= end_row["date"]]
    benchmark_return = None
    benchmark_return_basis = None
    if benchmark_before and benchmark_end:
        b0 = _adj_close(benchmark_before[-1])
        b1 = _adj_close(benchmark_end[-1])
        if b0 and b0 > 0 and b1:
            benchmark_return = b1 / b0 - 1.0
            benchmark_return_basis = "adjusted"
        else:
            try:
                b0_raw = float(benchmark_before[-1]["close"])
                b1_raw = float(benchmark_end[-1]["close"])
                if b0_raw > 0:
                    benchmark_return = b1_raw / b0_raw - 1.0
                    benchmark_return_basis = "raw"
            except (KeyError, TypeError, ValueError):
                pass
    payload = {"signal_id": signal["signal_id"], "horizon": horizon, "evaluation_date": end_row["date"]}
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "outcome",
        "event_id": _event_id("OUT", payload),
        "recorded_at": _utc_now(),
        **payload,
        "return_basis": return_basis,
        "reference_price": raw_reference,
        "evaluation_price": float(end_row["close"]),
        "gross_return": round(gross, 8),
        "transaction_cost_rate": cost_rate,
        "net_return": round(gross - cost_rate, 8),
        "mfe": round(max(highs) / reference_basis - 1.0, 8),
        "mae": round(min(lows) / reference_basis - 1.0, 8),
        "benchmark_symbol": "0050.TW",
        "benchmark_return_basis": benchmark_return_basis,
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
