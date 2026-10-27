# -*- coding: utf-8 -*-
"""Private, frozen exit-plan progress; never places or infers an order.

Only an authenticated controller may call mutations. It must derive ``timing``
and ``shares`` on the server, and verify a current confirmed trigger before
recording a completion. A completion records the owner's report, not broker
execution evidence. Fractions always refer to the shares at plan creation.

Persistence is ``private/exit_progress.json`` in the existing private data repo.
Remote failures fail closed; a remote CAS write succeeds before a local cache
is replaced. Without remote configuration local storage is explicitly volatile.
The seven-calendar-day expiry is a review interval, not a validated trading
parameter; configure PLAN_TTL_DAYS (1..30) for a different review interval.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

from company.model import durable_document

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH = ROOT / "data" / "exit_progress.json"
REMOTE_PATH = "private/exit_progress.json"
PLAN_TTL_DAYS = 7
MAX_PLANS = 100
_LOCK = threading.RLock()
_SYMBOL = re.compile(r"[A-Z0-9^.-]{1,20}\Z")
_PLAN_ID = re.compile(r"[a-f0-9]{64}\Z")
WHOLE_SHARE_METHOD = "whole_share_cumulative_floor_v1"


class ProgressConflict(ValueError):
    """The caller must reload the document or explicitly cancel its old plan."""


class ProgressUnavailable(RuntimeError):
    """Progress storage could not be read or saved safely."""


def _now(value=None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if isinstance(current, str):
        current = datetime.fromisoformat(current.replace("Z", "+00:00"))
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return current.astimezone(timezone.utc)


def _number(value, name: str, *, positive=True) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"invalid {name}") from None
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"invalid {name}")
    return number


def _symbol(value) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError("invalid symbol")
    return value


def _version(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    return value


def _steps(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        raise ValueError("plan must contain one to three stages")
    output = []
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict) or type(row.get("stage")) is not int or row["stage"] != index:
            raise ValueError("plan stages must be consecutive")
        fraction = _number(row.get("fraction"), "fraction")
        if fraction > 1:
            raise ValueError("invalid fraction")
        label = row.get("label", "")
        trigger = row.get("trigger", "")
        if not isinstance(label, str) or len(label) > 160 or not isinstance(trigger, str) or len(trigger) > 50:
            raise ValueError("invalid stage label or trigger")
        output.append({"stage": index, "trigger": trigger, "label": label,
                       "price": _number(row.get("price"), "price"),
                       "fraction": round(fraction, 8)})
    if sum(row["fraction"] for row in output) > 1 + 1e-8:
        raise ValueError("plan fractions exceed original position")
    if any(output[i]["price"] >= output[i - 1]["price"] for i in range(1, len(output))):
        raise ValueError("exit trigger prices must decrease")
    return output


def _stage_targets(original_shares, stages, *, whole_shares=False):
    """Freeze quantities; cumulative floor carries each rounding remainder forward."""
    original = _number(original_shares, "original shares")
    if not whole_shares:
        return [round(original * row["fraction"], 6) for row in stages]
    if not original.is_integer():
        raise ValueError("台股計畫的原始股數必須是整數股")
    cumulative, prior, targets = Decimal("0"), 0, []
    for row in stages:
        cumulative += Decimal(str(row["fraction"]))
        total = min(int(original), int((Decimal(str(original)) * cumulative).to_integral_value(rounding=ROUND_FLOOR)))
        quantity = total - prior
        if quantity <= 0:
            raise ValueError("分批目標不足一股，請調整分批計畫後再建立")
        targets.append(quantity)
        prior = total
    return targets


def _document(raw) -> dict:
    if raw is None:
        return {"schema_version": 1, "version": 0, "updated_at": None, "plans": {}}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("invalid progress schema")
    _version(raw.get("version"))
    plans = raw.get("plans")
    if not isinstance(plans, dict) or len(plans) > MAX_PLANS:
        raise ValueError("invalid plans")
    result = copy.deepcopy(raw)
    for symbol, plan in plans.items():
        _symbol(symbol)
        if not isinstance(plan, dict) or not _PLAN_ID.fullmatch(str(plan.get("plan_id", ""))):
            raise ValueError("invalid plan id")
        _number(plan.get("original_shares"), "original shares")
        _steps(plan.get("stages"))
        if not plan.get("created_at") or not plan.get("expires_at"):
            raise ValueError("plan timestamps required")
        if _now(plan["expires_at"]) <= _now(plan["created_at"]):
            raise ValueError("invalid plan expiry")
        date.fromisoformat(plan.get("basis_date", ""))
        if plan.get("status") not in ("active", "completed", "cancelled"):
            raise ValueError("invalid plan status")
        completed = plan.get("completed_stages")
        if not isinstance(completed, list) or len(completed) > len(plan["stages"]):
            raise ValueError("invalid completed stages")
        for idx, stage in enumerate(completed, 1):
            if not isinstance(stage, dict) or stage.get("stage") != idx:
                raise ValueError("completed stages must be consecutive")
            if not stage.get("completed_at"):
                raise ValueError("completion timestamp required")
            _now(stage.get("completed_at"))
        if plan["status"] == "completed" and len(completed) != len(plan["stages"]):
            raise ValueError("completed plan has pending stages")
        rounding = plan.get("share_rounding")
        if rounding not in (None, WHOLE_SHARE_METHOD):
            raise ValueError("unsupported share rounding method")
        if rounding == WHOLE_SHARE_METHOD and not symbol.endswith((".TW", ".TWO")):
            raise ValueError("whole-share method is only valid for Taiwan symbols")
        targets = _stage_targets(plan["original_shares"], plan["stages"], whole_shares=rounding == WHOLE_SHARE_METHOD)
        for row, expected in zip(plan["stages"], targets):
            if row.get("target_shares") != expected or (rounding == WHOLE_SHARE_METHOD and type(row.get("target_shares")) is not int):
                raise ValueError("stage shares do not match frozen fraction")
    return result


def _read():
    config = durable_document._config()
    if config:
        raw, sha, error = durable_document._remote_get(REMOTE_PATH, config)
        if error:
            raise ProgressUnavailable(error)
        storage = {"source": "github", "durable": True}
    else:
        sha = None
        storage = {"source": "local" if LOCAL_PATH.exists() else "none", "durable": False}
        try:
            raw = json.loads(LOCAL_PATH.read_text(encoding="utf-8")) if LOCAL_PATH.exists() else None
        except (ValueError, OSError):
            raise ProgressUnavailable("local_read_failed") from None
    try:
        return _document(raw), storage, config, sha
    except (ValueError, TypeError):
        raise ProgressUnavailable("invalid_progress_document") from None


def load_progress() -> tuple[dict, dict]:
    """Return private state; errors explicitly mark it unavailable, never empty success."""
    with _LOCK:
        try:
            doc, storage, _, _ = _read()
            return doc, storage
        except ProgressUnavailable as exc:
            return _document(None), {"source": "none", "durable": False,
                                     "available": False, "error": str(exc)}


def _cache(doc):
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCAL_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, LOCAL_PATH)


def _save(doc, config, sha):
    if not config:
        _cache(doc)
        return {"source": "local", "local_saved": True, "remote_saved": False, "durable": False}
    token, repo, branch = config
    path = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
    payload = {"message": "chore(exit): update private plan progress",
               "content": base64.b64encode(json.dumps(doc, ensure_ascii=False, allow_nan=False).encode()).decode(),
               "branch": branch}
    if sha:
        payload["sha"] = sha
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{path}",
                                    data=json.dumps(payload).encode(), method="PUT",
                                    headers={"Accept": "application/vnd.github+json",
                                             "Authorization": f"Bearer {token}",
                                             "User-Agent": "investment-exit-progress",
                                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (409, 422):
            raise ProgressConflict("remote version conflict; reload progress") from None
        raise ProgressUnavailable(f"remote_write_http_{exc.code}") from None
    except Exception as exc:
        raise ProgressUnavailable(f"remote_write_{type(exc).__name__}") from None
    result = {"source": "github", "remote_saved": True, "durable": True, "local_saved": False}
    try:
        _cache(doc)
        result["local_saved"] = True
    except OSError:
        result["cache_error"] = "local_cache_write_failed"
    return result


def _commit(doc, expected_version, current, config, sha):
    if _version(expected_version) != current["version"]:
        raise ProgressConflict("version conflict; reload progress")
    doc["version"] = current["version"] + 1
    return doc, _save(doc, config, sha)


def _expired(plan, now):
    return now >= _now(plan["expires_at"])


def _can_act(timing):
    return (isinstance(timing, dict) and timing.get("executable") is True
            and not timing.get("basis_mismatch")
            and timing.get("urgency") not in ("insufficient", "insufficient_data", "stale", "stale_data", "unavailable")
            and not ((timing.get("reliability") or {}).get("executable") is False))


def start_plan(symbol, timing, shares, expected_version, now=None):
    """Freeze a server-generated plan after an explicit owner request."""
    symbol, current_time = _symbol(symbol), _now(now)
    _version(expected_version)
    if not _can_act(timing):
        raise ValueError("timing is not actionable")
    stages = _steps(timing.get("plan"))
    basis_date = timing.get("last_close_date") or timing.get("value_state_as_of")
    date.fromisoformat(basis_date or "")
    original_shares = _number(shares, "shares")
    whole_shares = symbol.endswith((".TW", ".TWO"))
    for stage, quantity in zip(stages, _stage_targets(original_shares, stages, whole_shares=whole_shares)):
        stage["target_shares"] = quantity
    if type(PLAN_TTL_DAYS) is not int or not 1 <= PLAN_TTL_DAYS <= 30:
        raise ValueError("PLAN_TTL_DAYS must be 1..30")
    with _LOCK:
        current, storage, config, sha = _read()
        previous = current["plans"].get(symbol)
        if previous and previous["status"] == "active" and not _expired(previous, current_time):
            if (previous["basis_date"] == basis_date and previous["original_shares"] == original_shares
                    and previous["stages"] == stages):
                return current, {**storage, "unchanged": True}
            raise ProgressConflict("an active plan exists; cancel it before replacing")
        if symbol not in current["plans"] and len(current["plans"]) >= MAX_PLANS:
            raise ValueError("too many plans")
        plan = {"symbol": symbol, "basis_date": basis_date,
                "created_at": current_time.isoformat(),
                "expires_at": (current_time + timedelta(days=PLAN_TTL_DAYS)).isoformat(),
                "original_shares": original_shares, "fraction_basis": "original_plan_shares",
                "stages": stages, "completed_stages": [], "status": "active"}
        if whole_shares:
            plan["share_rounding"] = WHOLE_SHARE_METHOD
        plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True, allow_nan=False).encode()).hexdigest()
        doc = copy.deepcopy(current)
        doc["plans"][symbol] = plan
        doc["updated_at"] = current_time.isoformat()
        return _commit(doc, expected_version, current, config, sha)


def _find(doc, symbol, plan_id):
    if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
        raise ValueError("invalid plan id")
    plan = doc["plans"].get(symbol)
    if not plan or plan["plan_id"] != plan_id:
        raise ProgressConflict("plan changed; reload progress")
    return plan


def complete_stage(symbol, plan_id, stage, expected_version, now=None):
    """Record owner-reported execution; caller verifies trigger and authorization."""
    symbol, current_time = _symbol(symbol), _now(now)
    _version(expected_version)
    if type(stage) is not int or not 1 <= stage <= 3:
        raise ValueError("invalid stage")
    with _LOCK:
        current, storage, config, sha = _read()
        plan = _find(current, symbol, plan_id)
        if stage <= len(plan["completed_stages"]):
            return current, {**storage, "unchanged": True}
        if plan["status"] != "active" or _expired(plan, current_time):
            raise ProgressConflict("plan is not active; create a reviewed plan")
        if stage != len(plan["completed_stages"]) + 1 or stage > len(plan["stages"]):
            raise ValueError("complete stages in order")
        doc = copy.deepcopy(current)
        updated = doc["plans"][symbol]
        updated["completed_stages"].append({"stage": stage, "completed_at": current_time.isoformat(),
                                             "source": "owner_reported"})
        if len(updated["completed_stages"]) == len(updated["stages"]):
            updated["status"] = "completed"
        doc["updated_at"] = current_time.isoformat()
        return _commit(doc, expected_version, current, config, sha)


def cancel_plan(symbol, plan_id, expected_version, now=None):
    symbol, current_time = _symbol(symbol), _now(now)
    _version(expected_version)
    with _LOCK:
        current, storage, config, sha = _read()
        plan = _find(current, symbol, plan_id)
        if plan["status"] == "cancelled":
            return current, {**storage, "unchanged": True}
        doc = copy.deepcopy(current)
        doc["plans"][symbol].update(status="cancelled", cancelled_at=current_time.isoformat())
        doc["updated_at"] = current_time.isoformat()
        return _commit(doc, expected_version, current, config, sha)


def _completion_report(plan, shares, now):
    """A quantity match permits reporting only; it never authorizes a trade."""
    if plan["status"] != "active" or _expired(plan, now):
        return None
    completed = len(plan["completed_stages"])
    if completed >= len(plan["stages"]):
        return None
    actual = _number(shares, "shares", positive=False)
    if actual < 0:
        raise ValueError("shares cannot be negative")
    after = plan["original_shares"] - sum(row["target_shares"] for row in plan["stages"][:completed + 1])
    if abs(actual - after) > max(1e-6, plan["original_shares"] * 1e-8):
        return None
    return {"stage": plan["stages"][completed]["stage"], "plan_id": plan["plan_id"],
            "requires_server_verification": True,
            "note": "庫存股數符合本批完成後數量；本人回報後，伺服器仍須核對最新資料與原計畫觸發條件。"}


def completion_reports(document, positions, now=None):
    """Find post-execution reports, including a fully exited/absent holding."""
    doc, current_time = _document(document), _now(now)
    shares = {}
    for position in positions:
        symbol = _symbol(position.get("symbol"))
        value = _number(position.get("shares"), "shares", positive=False)
        if value < 0:
            raise ValueError("shares cannot be negative")
        shares[symbol] = shares.get(symbol, 0) + value
    reports = {}
    for symbol, plan in doc["plans"].items():
        report = _completion_report(plan, shares.get(symbol, 0), current_time)
        if report:
            reports[symbol] = report
    return reports


def apply_progress(timing, symbol, document, shares=None, now=None):
    """Overlay frozen stages, suppress exhausted/expired plans, and retain evidence."""
    symbol, current_time = _symbol(symbol), _now(now)
    doc = _document(document)
    result = copy.deepcopy(timing)
    plan = doc["plans"].get(symbol)
    result["exit_progress"] = {"status": "none", "version": doc["version"], "fraction_basis": "original_plan_shares"}
    if not plan:
        return result
    completed_count = len(plan["completed_stages"])
    completed_shares = sum(row["target_shares"] for row in plan["stages"][:completed_count])
    target_shares = sum(row["target_shares"] for row in plan["stages"])
    done = round(completed_shares / plan["original_shares"], 8)
    target = round(target_shares / plan["original_shares"], 8)
    progress = copy.deepcopy(plan)
    progress.update(version=doc["version"], completed_fraction=done,
                    remaining_fraction=round(max(0.0, target_shares - completed_shares) / plan["original_shares"], 8),
                    target_fraction=target, completed_shares=completed_shares,
                    remaining_shares=max(0.0, target_shares - completed_shares))
    result["exit_progress"] = progress
    result["fresh_plan_available"] = bool(_can_act(timing) and timing.get("plan"))
    result["plan"] = []
    if _can_act(timing) and timing.get("urgency") == "immediate" and (
            plan["status"] in ("completed", "cancelled") or _expired(plan, current_time)):
        if _expired(plan, current_time) and plan["status"] == "active":
            progress["status"] = "expired"
        result.update(executable=False, urgency="immediate",
                      headline="目前投資論點失效警訊優先；舊計畫已完成、取消或到期，請核對後明確建立新退出計畫")
        return result
    if plan["status"] in ("completed", "cancelled"):
        result.update(executable=False, headline="分批計畫已完成，需重新評估才建立新計畫" if plan["status"] == "completed"
                      else "原出場計畫已取消，需明確建立新計畫")
        return result
    if _expired(plan, current_time):
        progress["status"] = "expired"
        result.update(executable=False, urgency="advisory", headline="出場計畫已到期，請重新評估")
        return result
    if not _can_act(timing):
        progress["status"] = "suspended"
        return result
    if timing.get("urgency") == "immediate" and any(row.get("trigger") != "thesis_broken" for row in plan["stages"]):
        progress["status"] = "needs_review"
        result.update(executable=False, headline="目前投資論點失效警訊優先，原保護計畫暫停；請核對後取消並重建")
        return result
    if shares is not None:
        actual_shares = _number(shares, "shares")
        expected_shares = plan["original_shares"] - completed_shares
        if abs(actual_shares - expected_shares) > max(1e-6, expected_shares * 1e-8):
            report = _completion_report(plan, actual_shares, current_time)
            progress["status"] = "completion_pending" if report else "position_changed"
            if report:
                result["completion_report"] = report
            result.update(executable=False, urgency="advisory", headline="庫存股數符合本批完成後數量，待本人回報確認" if report else "持股股數與計畫剩餘股數不符，請核對後重建計畫")
            return result
    pending = copy.deepcopy(plan["stages"][completed_count:])
    last_close = timing.get("last_close")
    valid_close = (isinstance(last_close, (int, float)) and not isinstance(last_close, bool)
                   and math.isfinite(last_close) and last_close > 0)
    for row in pending:
        row.update(fraction_basis="original_plan_shares", plan_id=plan["plan_id"],
                   confirmed_break=bool(valid_close and last_close < row["price"]),
                   condition="收盤跌破後由本人確認已執行；比例以建計畫時原始股數為準")
    result["plan"] = pending
    result["headline"] = f"沿用已確認計畫：已完成 {completed_count}/{len(plan['stages'])} 批"
    return result
