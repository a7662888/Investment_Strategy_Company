# -*- coding: utf-8 -*-
"""每日價值選股與持股加減碼決策（純規則、可測試）。"""
from __future__ import annotations

from datetime import datetime, timezone

CYCLICAL = {"1301", "2002", "1101", "2603"}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _trend(result: dict) -> tuple[str, float]:
    price, ma20, ma60 = result.get("price"), result.get("ma20"), result.get("ma60")
    if price is None or ma20 is None or ma60 is None:
        return "資料不足", 40.0
    if price >= ma20 >= ma60:
        return "上升趨勢", 100.0
    if price >= ma20:
        return "短線轉穩", 75.0
    if price >= ma60:
        return "整理", 55.0
    return "下跌趨勢", 20.0


def _daily_item(result: dict, eligible_pool: bool) -> dict:
    symbol = result.get("symbol", "")
    code = symbol.split(".")[0]
    pct = result.get("valuation_pct")
    roe = result.get("roe_ttm")
    quality_pass = bool(result.get("quality_pass", result.get("is_etf", False)))
    trend, trend_score = _trend(result)
    risk_tier = "高" if code in CYCLICAL and roe is not None and roe < 0 else "一般"
    if pct is None:
        valuation_zone, valuation_score = "估值資料不足", 0.0
    elif pct <= 20:
        valuation_zone, valuation_score = "深度低估（≤P20）", 100.0 - float(pct)
    elif pct <= 40:
        valuation_zone, valuation_score = "分批區（P20–P40）", 100.0 - float(pct)
    elif pct <= 70:
        valuation_zone, valuation_score = "合理區（P40–P70）", 100.0 - float(pct)
    else:
        valuation_zone, valuation_score = "偏貴區（>P70）", 100.0 - float(pct)
    quality_score = _clamp((float(roe) / 20.0 * 100.0) if roe is not None else 45.0)
    score = 0.45 * valuation_score + 0.35 * quality_score + 0.20 * trend_score
    if risk_tier == "高":
        score -= 30.0
    action = result.get("action")
    if result.get("error") or result.get("data_incomplete") or pct is None:
        decision = "資料不足"
    elif not quality_pass or action == "avoid":
        decision = "排除／賣出檢查"
    elif risk_tier == "高":
        decision = "高風險反轉觀察"
    elif action == "accumulate" and trend == "下跌趨勢":
        decision = "等待止跌"
    elif action == "accumulate":
        decision = "可分批研究"
    elif action == "hold":
        decision = "續列觀察"
    else:
        decision = "等待更便宜"
    return {
        "symbol": symbol, "name": result.get("name"), "as_of": result.get("as_of"),
        "price": round(float(result["price"]), 2) if result.get("price") is not None else None,
        "action": action, "decision": decision, "eligible_pool": bool(eligible_pool),
        "quality_pass": quality_pass, "risk_tier": risk_tier,
        "fundamentals_complete": bool(result.get(
            "fundamentals_complete", not result.get("error") and not result.get("data_incomplete")
        )),
        "roe_ttm": round(float(roe), 2) if roe is not None else None,
        "valuation_basis": result.get("valuation_basis"), "valuation_pct": pct,
        "valuation_zone": valuation_zone, "entry_range": result.get("entry_range"),
        "ma20": result.get("ma20"), "ma60": result.get("ma60"),
        "momentum20": result.get("momentum20"), "trend": trend,
        "rank_score": round(score, 2), "reasons": list(result.get("reasons") or []),
        "failed": list(result.get("failed") or []), "is_etf": bool(result.get("is_etf")),
    }


def build_daily_state(results: list[dict], pool_codes: set[str], pool_total: int) -> dict:
    items = [_daily_item(r, r.get("symbol", "").split(".")[0] in pool_codes) for r in results]
    eligible = [i for i in items if i["eligible_pool"] and not i["is_etf"]]
    picks = sorted((i for i in eligible if i["decision"] == "可分批研究"),
                   key=lambda x: x["rank_score"], reverse=True)[:5]
    waiting = sorted((i for i in eligible if i["decision"] in ("等待止跌", "高風險反轉觀察")),
                     key=lambda x: x["rank_score"], reverse=True)[:5]
    as_of = max((i.get("as_of") or "" for i in items), default="")
    covered = sum(1 for i in eligible if i.get("fundamentals_complete"))
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "as_of": as_of,
        "mode": "daily-current-state", "shadow": True,
        "coverage": {"mother_pool": pool_total, "quality_covered": covered,
                     "not_yet_covered": max(0, pool_total - covered)},
        "top_picks": picks, "waiting_list": waiting, "evaluations": items,
        "method": "母池→季度品質硬篩→近3年估值百分位→20/60日趨勢時機；不自動下單",
    }


def portfolio_actions(state: dict, positions: list[dict]) -> list[dict]:
    by_symbol = {i.get("symbol"): i for i in state.get("evaluations", [])}
    market_values = {}
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        item = by_symbol.get(symbol)
        price = item.get("price") if item else None
        market_values[symbol] = float(pos.get("shares") or 0) * float(price or 0)
    total_value = sum(market_values.values())
    output = []
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        item = by_symbol.get(symbol)
        shares = float(pos.get("shares") or 0)
        cost = float(pos.get("cost") or 0)
        price = item.get("price") if item else None
        gain = (price / cost - 1.0) if price is not None and cost > 0 else None
        weight = market_values.get(symbol, 0.0) / total_value if total_value > 0 else None
        reasons = []
        if not item:
            action = "資料不足，人工檢查"
            reasons.append("此標的尚未納入每日品質覆蓋池")
        elif not item.get("quality_pass") or item.get("action") == "avoid":
            action = "賣出／減碼檢查"
            reasons.append("品質硬篩未通過或價值引擎判定 avoid；先核對失效條件，不自動賣出")
        elif item.get("is_etf"):
            if weight is not None and weight > 0.40:
                action = "配置過高，減碼再平衡檢查"
                reasons.append(f"此 ETF 約占目前輸入持股 {weight * 100:.1f}%，超過單一標的 40% 風控線")
            elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 40 and item.get("trend") != "下跌趨勢":
                action = "可小額分批追加"
                reasons.append("ETF 位階偏低且趨勢未惡化；仍須遵守資產配置上限")
            elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 40:
                action = "暫停追加，等待止跌"
                reasons.append("ETF 位階偏低但仍在下跌，不因便宜標籤一次加滿")
            else:
                action = "續抱領息，停止追加"
                reasons.append("ETF 不因單日或均線訊號單獨賣出；待估值回落或配置失衡時再調整")
        elif item.get("risk_tier") == "高":
            action = "暫停追加，檢查反轉假設"
            reasons.append("景氣股仍在虧損谷底，便宜可能是 value trap")
        elif "減碼" in str(item.get("decision")):
            action = "分批減碼檢查"
        elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 40:
            if item.get("trend") == "下跌趨勢":
                action = "暫停追加，等待止跌"
                reasons.append("估值便宜但仍在 20/60 日均線下方，先避免接落刀")
            elif gain is not None and gain >= 0.25:
                action = "續抱，不追價追加"
                reasons.append("已有明顯帳面獲利，避免因歷史低估標籤集中加碼")
            else:
                action = "可小額分批追加"
                reasons.append("品質通過、估值位於自身歷史低位且價格趨勢未惡化")
        elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 70:
            action = "續抱，暫不追加"
            reasons.append("估值合理但安全邊際不足")
        else:
            if item and item.get("trend") == "下跌趨勢" and gain is not None and gain >= 0.15:
                action = "停止追加，分批減碼檢查"
                reasons.append("估值偏高、趨勢轉弱且仍有獲利，優先保護部分成果")
            else:
                action = "停止追加，續抱觀察"
                reasons.append("估值偏高但尚無品質失效證據，不以價格訊號單獨清倉")
        output.append({
            "symbol": symbol, "name": item.get("name") if item else None,
            "shares": shares, "cost": cost, "price": price,
            "unrealized_gain": round(gain, 6) if gain is not None else None,
            "portfolio_weight": round(weight, 6) if weight is not None else None,
            "action": action, "reasons": reasons,
            "value_state": item, "as_of": state.get("as_of"),
            "disclaimer": "研究提示，不自動下單；賣出前須人工確認論點失效與稅費。",
        })
    return output
