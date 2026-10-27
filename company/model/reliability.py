"""Shared, deterministic gates for decision data; no wall-clock or network calls."""
from __future__ import annotations

from datetime import date
import math


def finite_number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def assessment(item: dict, expected_as_of: str | None = None) -> dict:
    """Check supplied decision inputs; caller supplies the expected completed day.

    Historical evaluations remain deterministic. A successful check without an
    expected day verifies internal consistency only, not real-time freshness.
    """
    item = item if isinstance(item, dict) else {}
    reasons: list[str] = []
    stale_reasons: list[str] = []
    expected = _date(expected_as_of)
    if expected_as_of is not None and expected is None:
        reasons.append("預期完整交易日格式無效")
    as_of = _date(item.get("as_of"))
    if item.get("as_of") and as_of is None:
        reasons.append("價格資料日期格式無效")
    if expected and not as_of:
        reasons.append("缺少價格資料日期，無法核對完整交易日")
    elif expected and as_of != expected:
        stale_reasons.append(f"價格資料日 {as_of} 未與預期完整交易日 {expected} 一致")

    for key in ("freshness", "reliability", "decision_context"):
        metadata = item.get(key)
        status = metadata.get("status") if isinstance(metadata, dict) else metadata
        if status in ("stale", "stale_data", "outdated"):
            stale_reasons.append("來源已標記資料過期")
        elif status in ("insufficient", "insufficient_data", "incomplete", "unavailable"):
            reasons.append("來源已標記資料不足")
        elif isinstance(metadata, dict) and metadata.get("executable") is False:
            reasons.append("來源資料尚未通過可執行檢查")
        if isinstance(metadata, dict) and (status != "current" or metadata.get("executable") is False):
            details = metadata.get("reasons")
            if isinstance(details, list):
                reasons.extend(value for value in details if isinstance(value, str))
    if item.get("error") or item.get("data_incomplete"):
        reasons.append("來源資料取得失敗或不完整")
    required_prices = [("price", "收盤價")]
    if not item.get("is_etf"):
        required_prices.extend((("ma20", "20 日均線"), ("ma60", "60 日均線")))
    for key, label in required_prices:
        if finite_number(item.get(key), positive=True) is None:
            reasons.append(f"{label}缺值或無效")
    if not item.get("is_etf"):
        valuation = finite_number(item.get("valuation_pct"))
        if valuation is None or not 0 <= valuation <= 100:
            reasons.append("估值位階缺值或無效")
        if not isinstance(item.get("quality_pass"), bool):
            reasons.append("品質硬篩尚未完成")
    provenance = item.get("data_provenance") or {}
    price_date = _date(provenance.get("price_date")) if isinstance(provenance, dict) else None
    if as_of and price_date and as_of != price_date:
        stale_reasons.append("價格來源日期與分析日期不一致")
    status = "stale" if stale_reasons else "insufficient" if reasons else "current"
    return {
        "status": status,
        "executable": status == "current",
        "reasons": list(dict.fromkeys(stale_reasons + reasons)),
        "as_of": as_of,
        "expected_as_of": expected,
        "freshness_verified": expected is not None and as_of == expected and not stale_reasons,
    }
