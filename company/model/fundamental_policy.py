"""Shared, non-executing fundamental checks for new buys and existing holdings."""
from __future__ import annotations

import math


def quality_schema(item: dict) -> str:
    """Recover non-industrial schemas for persisted pre-versioned evaluations."""
    if item.get("quality_schema"):
        return str(item["quality_schema"])
    code = str(item.get("symbol") or "").split(".")[0]
    if code in {"2881", "2882", "2891", "2886", "2884"}:
        return "finhold"
    if code in {"5871", "9941"}:
        return "leasing"
    return "generic"


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def evaluate_fundamentals(item: dict) -> dict:
    """Classify evidence, without converting a warning into an automatic sale.

    The 0.8 cash-conversion warning and two-quarter margin warnings already exist
    in the product. Their combination pauses new exposure; thresholds are not
    presented as a validated return forecast.
    """
    if item.get("is_etf"):
        return {"status": "clear", "allow_new": True, "reasons": [],
                "policy_version": "fundamental_guard_v1", "applicable": False}
    trend = item.get("fundamental_trend") or {}
    reasons: list[str] = []
    missing: list[str] = []
    quality = item.get("quality_pass")
    if quality is False or item.get("action") == "avoid":
        return {"status": "thesis_review", "allow_new": False,
                "reasons": ["品質硬篩未通過，須核對投資論點；不自動賣出"],
                "policy_version": "fundamental_guard_v1", "applicable": True}
    if quality is not True:
        missing.append("品質硬篩尚無完整結果")

    # Financial holding/leasing schemas intentionally do not use industrial
    # margin/operating-cash-conversion cutoffs. Do not turn that N/A into failure.
    industrial = quality_schema(item) not in ("finhold", "leasing")
    cash_quality = _finite(trend.get("earnings_quality_ttm"))
    margin_decline = False
    if industrial:
        for field, label in (("gross_margin_decline_2q", "毛利率"),
                             ("operating_margin_decline_2q", "營益率")):
            value = trend.get(field)
            if not isinstance(value, bool):
                missing.append(f"{label}連續季度趨勢不足")
            elif value:
                margin_decline = True
                reasons.append(f"{label}連兩季下降")
        if cash_quality is None:
            missing.append("近四季現金轉換資料不足或淨利分母不適用")
        elif cash_quality < 0.8:
            reasons.append("近四季營業現金流／淨利低於既有警示線 0.8")
    streak = _finite(trend.get("monthly_revenue_negative_streak"))
    if streak is not None and streak >= 3:
        reasons.append("月營收年減已持續至少三個月")
    if missing:
        status, allow_new = "insufficient", False
        reasons.extend(missing)
    elif industrial and cash_quality is not None and cash_quality < 0.8 and margin_decline:
        status, allow_new = "pause_new", False
    elif reasons:
        status, allow_new = "observe", True
    else:
        status, allow_new = "clear", True
    return {"status": status, "allow_new": allow_new, "reasons": reasons,
            "policy_version": "fundamental_guard_v1", "applicable": True}
