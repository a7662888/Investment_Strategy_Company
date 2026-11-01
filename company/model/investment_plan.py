"""Owner-defined investment plan (private, same storage and auth as positions).

Every number here is set by the owner. The site only checks the owner's own rules
against current holdings and market data; it never proposes the numbers.
"""
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from company.model import durable_document

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH = ROOT / "data" / "private_investment_plan.json"
REMOTE_PATH = os.environ.get("INVESTMENT_PLAN_REMOTE_PATH", "private/investment_plan.json")

RESERVE_MODES = ("monthly", "drawdown")


class PlanConflict(RuntimeError):
    """The client edited an older version than the one stored."""


def _number(value: object, field: str, low: float, high: float, *, optional: bool = True) -> float | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"{field} is required")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{field} must be between {low:g} and {high:g}")
    return number


def normalize_plan(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("plan must be an object")
    reserve_raw = raw.get("reserve") or {}
    if not isinstance(reserve_raw, dict):
        raise ValueError("reserve must be an object")
    mode = reserve_raw.get("mode") or "monthly"
    if mode not in RESERVE_MODES:
        raise ValueError("reserve.mode must be monthly or drawdown")
    tranches = int(_number(reserve_raw.get("tranches"), "reserve.tranches", 1, 36) or 1)
    steps_raw = reserve_raw.get("drawdown_steps_pct") or []
    if not isinstance(steps_raw, list) or len(steps_raw) > 36:
        raise ValueError("reserve.drawdown_steps_pct must be a list")
    steps = sorted(_number(step, "reserve.drawdown_steps_pct", 1, 90, optional=False) for step in steps_raw)
    if mode == "drawdown" and len(steps) != tranches:
        raise ValueError("drawdown mode needs one step per tranche")
    start = reserve_raw.get("start_month")
    if start not in (None, "") and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(start)):
        raise ValueError("reserve.start_month must be YYYY-MM")
    executed = int(_number(reserve_raw.get("executed"), "reserve.executed", 0, 36) or 0)
    if executed > tranches:
        raise ValueError("reserve.executed cannot exceed tranches")
    notes = str(raw.get("notes") or "")
    if len(notes) > 500:
        raise ValueError("notes must be at most 500 characters")
    return {
        "horizon_years": _number(raw.get("horizon_years"), "horizon_years", 0, 60),
        "target_equity_pct": _number(raw.get("target_equity_pct"), "target_equity_pct", 0, 100),
        "cash_amount": _number(raw.get("cash_amount"), "cash_amount", 0, 1e12),
        "rebalance_band_pct": _number(raw.get("rebalance_band_pct"), "rebalance_band_pct", 1, 50),
        "max_single_stock_pct": _number(raw.get("max_single_stock_pct"), "max_single_stock_pct", 1, 100),
        "reserve": {
            "total": _number(reserve_raw.get("total"), "reserve.total", 0, 1e12),
            "tranches": tranches,
            "mode": mode,
            "drawdown_steps_pct": steps if mode == "drawdown" else [],
            "start_month": start or None,
            "executed": executed,
        },
        "notes": notes,
    }


def _document(source: dict | None) -> dict:
    source = source if isinstance(source, dict) else {}
    plan = source.get("plan")
    return {
        "schema_version": 1,
        "version": max(0, int(source.get("version") or 0)),
        "updated_at": source.get("updated_at"),
        "plan": normalize_plan(plan) if isinstance(plan, dict) else None,
    }


def load_plan() -> tuple[dict, dict]:
    doc, storage = durable_document.load_document(LOCAL_PATH, REMOTE_PATH)
    return _document(doc), storage


def save_plan(raw_plan: object, expected_version: int | None = None) -> tuple[dict, dict]:
    plan = normalize_plan(raw_plan)
    current, _ = load_plan()
    if expected_version is not None and int(expected_version) != current["version"]:
        raise PlanConflict(f"version conflict: expected {expected_version}, current {current['version']}")
    doc = {
        "schema_version": 1,
        "version": current["version"] + 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
    }
    result = durable_document.save_document(doc, LOCAL_PATH, REMOTE_PATH,
                                            f"chore(private): investment plan v{doc['version']}")
    return doc, result
