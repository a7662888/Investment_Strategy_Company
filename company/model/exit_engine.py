# -*- coding: utf-8 -*-
"""持倉專屬 Exit Engine（shadow）。

分數只使用目前可追溯的收盤、估值、季度/月營收與持倉資料。分析師預估
修正與 ATR trailing stop 尚無可靠資料時不計分，避免把缺值冒充證據。
"""
from __future__ import annotations


def _cap(value: float, maximum: float) -> float:
    return round(max(0.0, min(maximum, value)), 1)


def _valuation_score(item: dict) -> tuple[float, list[str], bool]:
    pct = item.get("valuation_pct")
    if pct is None:
        return 0.0, ["估值位階缺值，不計分"], False
    pct = float(pct)
    if pct >= 95:
        return 30.0, [f"估值位於自身歷史第 {pct:.1f} 百分位（極高）"], True
    if pct >= 90:
        return 24.0, [f"估值位於自身歷史第 {pct:.1f} 百分位（很高）"], True
    if pct >= 80:
        return 16.0, [f"估值位於自身歷史第 {pct:.1f} 百分位（偏高）"], True
    if pct >= 70:
        return 8.0, [f"估值進入自身歷史偏高區（P{pct:.1f}）"], True
    return 0.0, [f"估值尚未進入高估區（P{pct:.1f}）"], True


def _fundamental_score(item: dict) -> tuple[float, list[str], int, int]:
    trend = item.get("fundamental_trend") or {}
    reasons: list[str] = []
    score = 0.0
    deterioration_categories: set[str] = set()

    observed_fields = [
        item.get("quality_pass") is not None,
        trend.get("monthly_revenue_yoy_latest") is not None,
        trend.get("monthly_revenue_negative_streak") is not None,
        trend.get("quarterly_revenue_yoy") is not None,
        trend.get("quarterly_eps_yoy") is not None,
        trend.get("gross_margin_decline_2q") is not None,
        trend.get("operating_margin_decline_2q") is not None,
    ]
    coverage_points = round(30 * sum(observed_fields) / len(observed_fields))

    if item.get("quality_pass") is False:
        score += 8
        reasons.append("目前品質硬篩未通過")

    latest_monthly = trend.get("monthly_revenue_yoy_latest")
    if latest_monthly is not None:
        if float(latest_monthly) <= -10:
            score += 8
            deterioration_categories.add("monthly_revenue")
            reasons.append(f"最新月營收年減 {abs(float(latest_monthly)):.1f}%")
        elif float(latest_monthly) < 0:
            score += 4
            deterioration_categories.add("monthly_revenue")
            reasons.append(f"最新月營收年增率轉負（{float(latest_monthly):.1f}%）")
    negative_months = trend.get("monthly_revenue_negative_streak")
    if negative_months is not None and int(negative_months) >= 3:
        negative_months = int(negative_months)
        score += 4
        deterioration_categories.add("monthly_revenue")
        reasons.append(f"月營收年增率連續 {negative_months} 個月為負")

    revenue_yoy = trend.get("quarterly_revenue_yoy")
    if revenue_yoy is not None and float(revenue_yoy) < 0:
        score += 5 if float(revenue_yoy) <= -10 else 3
        deterioration_categories.add("quarterly_revenue")
        reasons.append(f"季度營收年增率為 {float(revenue_yoy):.1f}%")
    eps_yoy = trend.get("quarterly_eps_yoy")
    if eps_yoy is not None and float(eps_yoy) < 0:
        score += 5 if float(eps_yoy) <= -10 else 3
        deterioration_categories.add("quarterly_eps")
        reasons.append(f"季度 EPS 年增率為 {float(eps_yoy):.1f}%")
    if trend.get("gross_margin_decline_2q"):
        score += 4
        deterioration_categories.add("margins")
        reasons.append("毛利率連續兩季下降")
    if trend.get("operating_margin_decline_2q"):
        score += 4
        deterioration_categories.add("margins")
        reasons.append("營益率連續兩季下降")

    if not reasons:
        reasons.append("現有季度與月營收資料未出現明確惡化組合")
    return _cap(score, 30), reasons, coverage_points, len(deterioration_categories)


def _momentum_score(item: dict) -> tuple[float, list[str], int]:
    price, ma20, ma60 = item.get("price"), item.get("ma20"), item.get("ma60")
    distance = item.get("distance_from_high_252")
    available = price is not None and ma20 is not None and ma60 is not None
    if not available:
        return 0.0, ["20／60 日趨勢資料不足，不計分"], 0
    price, ma20, ma60 = float(price), float(ma20), float(ma60)
    score = 0.0
    reasons: list[str] = []
    if price < ma20:
        score += 4
        reasons.append("收盤跌破 20 日均線")
    if price < ma60:
        score += 7
        reasons.append("收盤跌破 60 日均線")
    if ma20 < ma60:
        score += 5
        reasons.append("20 日均線低於 60 日均線")
    if distance is not None:
        drawdown = float(distance)
        if drawdown <= -0.20:
            score += 4
            reasons.append("距近一年高點回撤超過 20%")
        elif drawdown <= -0.15:
            score += 3
            reasons.append("距近一年高點回撤超過 15%")
        elif drawdown <= -0.10:
            score += 2
            reasons.append("距近一年高點回撤超過 10%")
    if not reasons:
        reasons.append("20／60 日趨勢仍未破壞")
    return _cap(score, 20), reasons, 20 if distance is not None else 16


def _accounting_score(item: dict) -> tuple[float, list[str], int]:
    trend = item.get("fundamental_trend") or {}
    quality = trend.get("earnings_quality_ttm")
    debt_change = trend.get("debt_ratio_change_yoy")
    if quality is None and debt_change is None:
        return 0.0, ["現金轉換／負債趨勢資料不足，不計分"], 0
    score = 0.0
    reasons: list[str] = []
    if quality is not None:
        quality = float(quality)
        if quality < 0:
            score += 10
            reasons.append(f"近四季 OCF／淨利為負（{quality:.2f}）")
        elif quality < 0.6:
            score += 7
            reasons.append(f"近四季 OCF／淨利偏低（{quality:.2f}）")
        elif quality < 0.8:
            score += 4
            reasons.append(f"近四季 OCF／淨利低於 0.8（{quality:.2f}）")
    if debt_change is not None and float(debt_change) >= 10:
        score += 3
        reasons.append(f"負債比一年增加 {float(debt_change):.1f} 個百分點")
    if not reasons:
        reasons.append("現金轉換與負債趨勢未見明顯警訊")
    return _cap(score, 10), reasons, (5 if quality is not None else 0) + (5 if debt_change is not None else 0)


def _position_score(item: dict, weight: float | None) -> tuple[float, list[str], int]:
    if weight is None:
        return 0.0, ["未取得完整輸入持股權重，不計分"], 0
    pct = float(weight) * 100
    if item.get("is_etf"):
        if pct > 60:
            return 10.0, [f"此 ETF 占已輸入投資組合 {pct:.1f}%"], 10
        if pct > 50:
            return 6.0, [f"此 ETF 占已輸入投資組合 {pct:.1f}%"], 10
        if pct > 40:
            return 3.0, [f"此 ETF 占已輸入投資組合 {pct:.1f}%"], 10
    else:
        if pct > 25:
            return 10.0, [f"單一個股占已輸入投資組合 {pct:.1f}%（高集中）"], 10
        if pct > 20:
            return 6.0, [f"單一個股占已輸入投資組合 {pct:.1f}%"], 10
        if pct > 15:
            return 3.0, [f"單一個股占已輸入投資組合 {pct:.1f}%"], 10
    return 0.0, [f"持倉權重 {pct:.1f}% 未觸發集中度門檻"], 10


def score_exit(item: dict, gain: float | None, weight: float | None) -> dict:
    """Return a transparent, non-executing exit recommendation."""
    valuation, valuation_reasons, valuation_ok = _valuation_score(item)
    fundamental, fundamental_reasons, fundamental_coverage, deterioration_flags = _fundamental_score(item)
    momentum, momentum_reasons, momentum_coverage = _momentum_score(item)
    accounting, accounting_reasons, accounting_coverage = _accounting_score(item)
    position, position_reasons, position_coverage = _position_score(item, weight)
    components = {
        "valuation": valuation,
        "fundamentals": fundamental,
        "momentum": momentum,
        "accounting_quality": accounting,
        "position_risk": position,
    }
    maxima = {"valuation": 30, "fundamentals": 30, "momentum": 20,
              "accounting_quality": 10, "position_risk": 10}
    coverage_by_component = {
        "valuation": 30 if valuation_ok else 0,
        "fundamentals": fundamental_coverage,
        "momentum": momentum_coverage,
        "accounting_quality": accounting_coverage,
        "position_risk": position_coverage,
    }
    coverage = sum(coverage_by_component.values())
    score = round(sum(components.values()), 1)
    thesis_broken = bool(
        item.get("quality_pass") is False and deterioration_flags >= 2 and coverage >= 70
    )

    profitable = gain is not None and gain > 0
    if thesis_broken:
        status, label, fraction = "exit", "投資論點失效", 1.0
    elif score >= 75:
        status, label, fraction = "take_profit", ("獲利了結 50–75%" if profitable else "大幅降低風險部位"), 0.60
    elif score >= 60:
        status, label, fraction = "trim_30_50", ("分批獲利了結 30–50%" if profitable else "分批減碼 30–50%"), 0.40
    elif score >= 45:
        status, label, fraction = "trim_20_25", ("分批獲利了結 20–25%" if profitable else "分批減碼 20–25%"), 0.25
    elif score >= 30:
        status, label, fraction = "watch_profit", ("觀察獲利保護" if profitable else "風險觀察"), 0.0
    else:
        status, label, fraction = "hold", "續抱，讓獲利奔跑", 0.0

    if coverage < 70 and status not in ("hold", "watch_profit"):
        status, label, fraction = "watch_profit", "資料覆蓋不足，先觀察不執行", 0.0

    return {
        "schema_version": 1,
        "score": score,
        "status": status,
        "label": label,
        "suggested_fraction": fraction,
        "components": components,
        "component_maxima": maxima,
        "coverage": coverage,
        "coverage_by_component": coverage_by_component,
        "thesis_broken": thesis_broken,
        "reasons": {
            "valuation": valuation_reasons,
            "fundamentals": fundamental_reasons,
            "momentum": momentum_reasons,
            "accounting_quality": accounting_reasons,
            "position_risk": position_reasons,
        },
        "missing": [key for key, points in coverage_by_component.items() if points < maxima[key]],
        "disabled_inputs": ["analyst_eps_revision", "atr_trailing_stop", "tax_optimization"],
        "shadow": True,
        "disclaimer": "規則仍在前瞻驗證期；不自動下單，須人工核對公告、稅費與投資論點。",
    }
