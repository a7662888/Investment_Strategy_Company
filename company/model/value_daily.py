# -*- coding: utf-8 -*-
"""每日價值選股與持股加減碼決策（純規則、可測試）。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math

from company.model.value_policy import CYCLICAL
from company.model.fundamental_policy import evaluate_fundamentals, quality_schema
from company.model.reliability import assessment, finite_number


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _finite_json(value):
    """Copy persisted JSON while replacing non-finite numeric leaves with null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    return value


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


def _decide(*, blocked: bool, is_dca_etf: bool, pct, quality_pass, action, fundamental_status: str,
            risk_tier: str, trend: str, chase_risk: bool) -> str:
    """判定階梯（純函式）。正式判定與 B 級並行判定共用同一條，只差 quality_pass／action 的輸入。"""
    if blocked:
        return "資料不足"
    if is_dca_etf:
        return "定期定額"
    if pct is None:
        return "資料不足"
    if quality_pass is False or action == "avoid":
        # 未過品質篩只代表不新增；是否處理持股由 Exit Engine 與「我的持股」另外判斷。
        return "未過品質篩，不新增"
    if fundamental_status == "insufficient":
        return "資料不足"
    if fundamental_status == "pause_new":
        return "基本面待確認，暫緩新增"
    if risk_tier == "高":
        return "高風險反轉觀察"
    if action == "accumulate" and trend == "下跌趨勢":
        return "等待止跌"
    if action == "accumulate" and chase_risk:
        return "高檔，等待拉回"
    if action == "accumulate":
        return "可分批研究"
    if action == "hold":
        return "續列觀察"
    return "等待更便宜"


def turn_price(trend: str, ma20, ma60) -> float | None:
    """脫離「下跌趨勢」所需的收盤價：站回 20 日或 60 日均線中較低的那一條。

    均線每天變動，所以這是「以今天的均線來看」的參考價，不是預測。
    """
    if trend != "下跌趨勢" or ma20 is None or ma60 is None:
        return None
    return round(min(float(ma20), float(ma60)), 2)


_VALUATION_PLAIN = {
    "估值歷史低位（≤P20）": "價格在自己近三年的低檔",
    "分批區（P20–P40）": "價格在自己近三年的偏低區",
    "合理區（P40–P70）": "價格不貴也不便宜",
    "偏貴區（>P70）": "價格在自己近三年的偏貴區",
}


def plain_summary(decision: str, valuation_zone: str, trend: str, turn: float | None,
                  failed: list[str], warnings: list[str], tier_b: str | None = None,
                  relaxed_decision: str | None = None) -> str:
    """每張卡片最上面那一句人話。只重述已算出的判定，不新增任何判斷。"""
    price = _VALUATION_PLAIN.get(valuation_zone, "估值資料不足")
    wait = f"收盤站回 {turn:g} 元附近（月線或季線中較低者）再看" if turn else "等止跌再看"
    if decision == "定期定額":
        return "ETF 走定期定額：每月固定投入，不看價位擇時。"
    if decision == "可分批研究":
        return f"體質過關、{price}、沒有在跌：可以開始研究，分批而不是一次買。"
    if decision == "等待止跌":
        return f"體質過關、{price}，但還在跌：先不買，{wait}。"
    if decision == "高檔，等待拉回":
        return f"體質過關、{price}，但短線漲多又貼近一年高點：等拉回再看。"
    if decision == "續列觀察":
        return f"體質過關，{price}：先放觀察名單。"
    if decision == "等待更便宜":
        return f"體質過關，但{price}：等價格回落再看。"
    if decision == "高風險反轉觀察":
        return "景氣循環股在谷底，可能反轉也可能續弱：風險高，只觀察、不當成一般候選。"
    if decision == "基本面待確認，暫緩新增":
        why = f"（{'；'.join(warnings[:2])}）" if warnings else ""
        return f"近期財報出現警訊{why}：暫時不新買，已持有的另看持股建議。"
    if decision == "未過品質篩，不新增":
        why = f"（{'；'.join(failed[:2])}）" if failed else ""
        text = f"體質有項目沒達標{why}：不新買；已持有的另看持股建議。"
        if tier_b:
            text += f" 只差一點，列入 B 級並行觀察（放寬後判定：{relaxed_decision or '—'}）。"
        return text
    return "財報或估值資料不完整：暫不判定。"


def _daily_item(result: dict, eligible_pool: bool) -> dict:
    # Classify only normalized numbers, before comparisons or formatting. This
    # creates a copy: reading a cached/historical artifact never rewrites it.
    result = _finite_json(result)
    for key in ("price", "valuation_pct", "roe_ttm", "ma20", "ma60", "momentum20",
                "distance_from_high_252", "high_252", "price_pct_252", "ma240",
                "ma240_deviation_pct", "pe", "pb", "valuation_observations"):
        if key in result:
            result[key] = finite_number(result[key])
    if not result.get("is_etf") and result.get("roe_ttm") is None:
        result["data_incomplete"] = result.get("data_incomplete") or "roe_ttm_unavailable"
    if not isinstance(result.get("quality_pass"), bool):
        result["quality_pass"] = None
    if not isinstance(result.get("fundamental_trend"), dict):
        result["fundamental_trend"] = {}
    symbol = str(result.get("symbol") or "")
    code = symbol.split(".")[0]
    pct = result.get("valuation_pct")
    roe = result.get("roe_ttm")
    quality_pass = result.get("quality_pass")
    fundamental = evaluate_fundamentals(result)
    reliability = assessment(result)
    trend, trend_score = _trend(result)
    risk_tier = "高" if code in CYCLICAL else "一般"
    momentum20 = result.get("momentum20")
    distance_from_high_252 = result.get("distance_from_high_252")
    chase_risk = bool(
        momentum20 is not None and float(momentum20) >= 0.10
        and distance_from_high_252 is not None and float(distance_from_high_252) >= -0.02
    )
    if pct is None:
        valuation_zone, valuation_score = "估值資料不足", 0.0
    elif pct <= 20:
        valuation_zone, valuation_score = "估值歷史低位（≤P20）", 100.0 - float(pct)
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
    if chase_risk:
        score -= 20.0
    action = result.get("action")
    is_dca_etf = bool(result.get("is_etf") and result.get("dca"))
    if is_dca_etf:
        # ETF 子軌改為定期定額：沒有估值百分位，位置只用年線乖離描述。
        valuation_zone = result.get("ma240_band") or "年線資料不足"
    blocked = bool(result.get("error") or result.get("data_incomplete") or not reliability["executable"])
    ladder = dict(blocked=blocked, is_dca_etf=is_dca_etf, pct=pct, risk_tier=risk_tier,
                  trend=trend, chase_risk=chase_risk)
    decision = _decide(quality_pass=quality_pass, action=action, fundamental_status=fundamental["status"], **ladder)
    # B 級並行判定：同一條階梯，只把「差一項且合乎放寬原則」視為通過。不影響正式 decision。
    tier = result.get("quality_tier") if result.get("quality_tier") in ("A", "B", "C") else None
    relaxed_decision = None
    if tier == "B":
        relaxed_guard = evaluate_fundamentals(
            {**result, "quality_pass": True, "action": result.get("relaxed_action")})
        relaxed_decision = _decide(quality_pass=True, action=result.get("relaxed_action"),
                                   fundamental_status=relaxed_guard.get("status", "insufficient"), **ladder)
    turn = turn_price(trend, result.get("ma20"), result.get("ma60"))
    summary = plain_summary(decision, valuation_zone, trend, turn, list(result.get("failed") or []),
                            list(fundamental.get("reasons") or []), result.get("tier_b_basis") if tier == "B" else None,
                            relaxed_decision)
    return {
        "symbol": symbol, "name": result.get("name"), "as_of": result.get("as_of"),
        "price": round(float(result["price"]), 2) if result.get("price") is not None else None,
        "action": "watch" if (not reliability["executable"] or not fundamental["allow_new"]) and action == "accumulate" else action,
        "source_action": result.get("source_action", action),
        "decision": decision, "eligible_pool": bool(eligible_pool),
        "plain_summary": summary, "turn_price": turn,
        "quality_tier": tier, "tier_b_basis": result.get("tier_b_basis") if tier == "B" else None,
        "relaxed_decision": relaxed_decision,
        # 網站 API 會把已存的評估再跑一次本函式；少了這個輸入，重算後的 B 級判定會全部變成「等待更便宜」。
        "relaxed_action": result.get("relaxed_action") if tier == "B" else None,
        "quality_pass": quality_pass, "risk_tier": risk_tier,
        "quality_schema": quality_schema(result),
        "quality_missing": list(result.get("quality_missing") or []),
        "fundamental_assessment": fundamental, "reliability": reliability,
        "fundamentals_complete": fundamental["status"] != "insufficient" and bool(result.get(
            "fundamentals_complete", not result.get("error") and not result.get("data_incomplete")
        )),
        "roe_ttm": round(float(roe), 2) if roe is not None else None,
        "valuation_basis": result.get("valuation_basis"), "valuation_pct": pct,
        "pe": result.get("pe"), "pb": result.get("pb"),
        "valuation_as_of": result.get("valuation_as_of"),
        "valuation_history_start": result.get("valuation_history_start"),
        "valuation_observations": result.get("valuation_observations"),
        "valuation_source": result.get("valuation_source"),
        "valuation_zone": valuation_zone, "entry_range": result.get("entry_range"),
        "ma20": result.get("ma20"), "ma60": result.get("ma60"),
        "momentum20": momentum20, "trend": trend,
        "high_252": result.get("high_252"),
        "distance_from_high_252": distance_from_high_252,
        "price_pct_252": result.get("price_pct_252"), "chase_risk": chase_risk,
        "rank_score": round(score, 2), "reasons": list(result.get("reasons") or []),
        "failed": list(result.get("failed") or []), "is_etf": bool(result.get("is_etf")),
        "dca": is_dca_etf, "ma240": result.get("ma240"),
        "ma240_deviation_pct": result.get("ma240_deviation_pct"), "ma240_band": result.get("ma240_band"),
        "fundamental_trend": result.get("fundamental_trend") or {},
        # 逐項來源時間戳需一併帶到前端；_daily_item 是白名單式輸出，未列即遺失。
        "data_provenance": result.get("data_provenance") or {},
    }


def _consensus_as_of(items: list[dict]) -> str:
    """多數標的共有的價格日期。

    不可用 max()：Yahoo 常有少數標的先出現新一日 bar，而官方收盤尚未發布。
    取最大值會造成三個連鎖故障（2026-09-10 實際發生，整週狀態停擺）：
      1. 整份狀態標成只有 5/106 檔擁有的日期，對外宣稱的資料日不實；
      2. current_eligible 以 as_of 相符為條件，於是選股只從那 5 檔挑，
         top_picks 被悄悄掏空（實測從 5 檔掉到 1 檔）；
      3. state.as_of 比官方收盤日新，觸發 run_daily_value_state 的
         stale-state 守門而整份拒存 —— 資料因此完全不再更新。
    規則：取「數量至少達最大群一半」的最新日期。這樣少數搶跑的標的無法定義
    整份狀態的日期（5/106 會被排除），但當新日期已有相當覆蓋時仍會如實前進，
    不會把真正的新交易日誤判成尚未到來。
    """
    dates = [item.get("as_of") for item in items if item.get("as_of")]
    if not dates:
        return ""
    counts = Counter(dates)
    threshold = max(counts.values()) / 2
    return max(date for date, count in counts.items() if count >= threshold)


def build_daily_state(results: list[dict], pool_codes: set[str], pool_total: int) -> dict:
    items = [_daily_item(r, r.get("symbol", "").split(".")[0] in pool_codes) for r in results]
    eligible = [i for i in items if i["eligible_pool"] and not i["is_etf"]]
    as_of = _consensus_as_of(items)
    current_eligible = [i for i in eligible if i.get("as_of") == as_of]
    picks = sorted((i for i in current_eligible if i["decision"] == "可分批研究"),
                   key=lambda x: x["rank_score"], reverse=True)[:5]
    waiting = sorted((i for i in current_eligible if i["decision"] in (
        "等待止跌", "高風險反轉觀察", "高檔，等待拉回", "基本面待確認，暫緩新增"
    )),
                     key=lambda x: x["rank_score"], reverse=True)[:5]
    etf_candidates = sorted(
        (i for i in items if i["is_etf"] and i.get("as_of") == as_of),
        key=lambda x: x["rank_score"], reverse=True,
    )[:5]
    tier_b_items = [i for i in current_eligible if i.get("quality_tier") == "B"]
    tier_b_picks = sorted((i for i in tier_b_items if i.get("relaxed_decision") == "可分批研究"),
                          key=lambda x: x["rank_score"], reverse=True)[:10]
    covered = sum(1 for i in eligible if i.get("fundamentals_complete"))
    priced = [i for i in items if i.get("as_of")]
    current_prices = sum(i.get("as_of") == as_of for i in priced)
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "as_of": as_of,
        "mode": "daily-current-state", "shadow": True,
        "coverage": {"mother_pool": pool_total, "quality_covered": covered,
                     "not_yet_covered": max(0, pool_total - covered),
                     "price_current": current_prices, "price_total": len(priced),
                     "price_stale": len(priced) - current_prices,
                     "oldest_price_as_of": min((i.get("as_of") for i in priced), default=None)},
        "top_picks": picks, "waiting_list": waiting, "etf_candidates": etf_candidates,
        "tier_b": {
            "policy_version": "tier_b_v1（v2.5 shadow，並行觀察）",
            "note": "正式門檻不變。B 級＝品質篩只差一項，且合乎下列原則；不進正式候選與 Decision Ledger，"
                    "每日判定另存供日後與正式名單比較。放寬門檻不代表報酬會提高。",
            "rules": [
                "單一項目與門檻的差距在一成內",
                "金融保險業 ROE 達 9%（一般門檻 12%）",
                "資本密集產業負債比不超過 75%（一般門檻 60%）",
                "近四季現金轉換未達標，但近八季累計達標",
            ],
            "count": len(tier_b_items),
            "by_decision": dict(Counter(i.get("relaxed_decision") for i in tier_b_items)),
            "candidates": tier_b_picks,
        },
        "evaluations": items,
        "method": (
            "tw_value_method v2.4：母池→四季現金轉換／品質硬篩→基本面警訊閘門→近3年估值百分位"
            "→20/60日趨勢＋高檔追價閘門；持倉另用100分Exit Engine，缺值降級且不自動下單。"
            "ETF 子軌 etf_dca v1：定期定額，年線乖離僅供參考。"
            "母池 pool_rules_v2（規則式）；B 級 tier_b_v1 為並行觀察，不改正式判定"
        ),
    }


def portfolio_actions(state: dict, positions: list[dict], *, expected_as_of: str | None = None) -> list[dict]:
    from company.model.exit_engine import score_exit

    by_symbol = {i.get("symbol"): i for i in state.get("evaluations", [])}
    market_values = {}
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        item = by_symbol.get(symbol)
        price = item.get("price") if item else None
        market_values[symbol] = float(pos.get("shares") or 0) * float(price or 0)
    total_value = sum(market_values.values())
    priced_position_count = sum(value > 0 for value in market_values.values())
    output = []
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        item = by_symbol.get(symbol)
        shares = float(pos.get("shares") or 0)
        cost = float(pos.get("cost") or 0)
        price = item.get("price") if item else None
        gain = (price / cost - 1.0) if price is not None and cost > 0 else None
        weight = market_values.get(symbol, 0.0) / total_value if total_value > 0 else None
        fundamental = evaluate_fundamentals(item or {})
        reliability = assessment(item or {}, expected_as_of=expected_as_of)
        reasons = []
        if not item:
            action = "資料不足，人工檢查"
            reasons.append("此標的尚未納入每日品質覆蓋池")
        elif not reliability["executable"]:
            action = "資料過期，暫停建議" if reliability["status"] == "stale" else "資料不足，人工檢查"
            reasons.extend(reliability["reasons"])
        elif item.get("is_etf"):
            # ETF 必須排在個股品質硬篩之前：ETF 沒有 ROE／毛利率／營益率，
            # 其 quality_pass 恆為空值，若先過硬篩會被一律誤判成「賣出／減碼檢查」。
            if weight is not None and weight > 0.40:
                action = "配置過高，減碼再平衡檢查"
                reasons.append(f"此 ETF 約占目前輸入持股 {weight * 100:.1f}%，超過單一標的 40% 風控線")
            elif item.get("dca"):
                action = "定期定額續扣"
                reasons.append("ETF 採定期定額，不依價位擇時加碼或停扣；只在配置失衡時再平衡")
                if item.get("ma240_deviation_pct") is not None:
                    reasons.append(f"年線乖離 {float(item['ma240_deviation_pct']):+.1f}%（{item.get('ma240_band')}），僅供參考")
            elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 40 and item.get("trend") != "下跌趨勢":
                action = "可小額分批追加"
                reasons.append("ETF 位階偏低且趨勢未惡化；仍須遵守資產配置上限")
            elif item.get("valuation_pct") is not None and item["valuation_pct"] <= 40:
                action = "暫停追加，等待止跌"
                reasons.append("ETF 位階偏低但仍在下跌，不因便宜標籤一次加滿")
            else:
                action = "續抱領息，停止追加"
                reasons.append("ETF 不因單日或均線訊號單獨賣出；待估值回落或配置失衡時再調整")
        elif item.get("quality_pass") is False or item.get("action") == "avoid":
            action = "賣出／減碼檢查"
            reasons.append("品質硬篩未通過或價值引擎判定 avoid；先核對失效條件，不自動賣出")
        elif fundamental["status"] == "insufficient":
            action = "基本面資料不足，暫停追加"
            reasons.extend(fundamental["reasons"])
        elif fundamental["status"] == "pause_new":
            action = "基本面待確認，暫停追加"
            reasons.extend(fundamental["reasons"])
        elif item.get("risk_tier") == "高":
            action = "暫停追加，檢查反轉假設"
            reasons.append("景氣循環股須用 PBR 與獲利週期判斷；低 PER 可能只是高峰盈餘造成的假便宜")
        elif item.get("chase_risk"):
            action = "續抱，不追價追加"
            reasons.append("股價貼近近一年高點且 20 日漲幅偏大，等待拉回或整理後再評估")
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
        # 只輸入一檔時顯示的 100% 不足以證明「完整投資組合高度集中」。
        # Exit Score 至少要有兩檔可定價持股才採計 position-risk；原 ETF 配置提示維持相容。
        exit_weight = weight if priced_position_count >= 2 else None
        exit_engine = score_exit(item or {}, gain, exit_weight, expected_as_of=expected_as_of)
        base_action = action
        exit_status = exit_engine["status"]
        if not reliability["executable"] or exit_status in ("insufficient_data", "stale_data"):
            action = "資料過期，暫停建議" if (reliability["status"] == "stale" or exit_status == "stale_data") else "資料不足，人工檢查"
            reasons = list(dict.fromkeys([*reasons, *reliability["reasons"]]))
        elif exit_status in ("trim_20_25", "trim_30_50", "take_profit", "exit"):
            action = exit_engine["label"]
            reasons = [
                f"Exit Score {exit_engine['score']:.1f}/100（資料覆蓋 {exit_engine['coverage']}/100）",
                *reasons,
            ]
        elif (exit_status == "watch_profit" and gain is not None and gain > 0
              and "賣出" not in base_action and "減碼" not in base_action
              and fundamental["allow_new"]):
            action = "獲利續抱，密切觀察"
            reasons.insert(0, f"Exit Score {exit_engine['score']:.1f}/100，尚未達減碼門檻")
        elif (item and not item.get("is_etf") and priced_position_count >= 2
              and weight is not None and weight > 0.25):
            action = "配置過高，減碼再平衡檢查"
            reasons.insert(0, f"單一個股約占已輸入持股 {weight * 100:.1f}%，先檢查集中風險")
        output.append({
            "symbol": symbol, "name": item.get("name") if item else None,
            "shares": shares, "cost": cost, "price": price,
            "unrealized_gain": round(gain, 6) if gain is not None else None,
            "portfolio_weight": round(weight, 6) if weight is not None else None,
            "action": action, "base_action": base_action, "reasons": reasons,
            "exit_engine": exit_engine,
            "fundamental_assessment": fundamental, "reliability": reliability,
            "value_state": item, "as_of": state.get("as_of"),
            "disclaimer": "研究提示，不自動下單；賣出前須人工確認論點失效與稅費。",
        })
    return output
