# -*- coding: utf-8 -*-
"""買進側的當日量價佐證（shadow，不改變選股排序）。

價值引擎已回答「哪些標的夠好、買進區在哪」。它答不出的是「今天這根 K 有沒有
買盤跡象」——尤其「等待止跌」這一類，系統只說價格在均線下方，卻沒有任何
判斷「等到什麼時候為止」的依據。永豐盤後快照提供的當日均價與量比正好補這段。

**分層原則（重要）**：
- **流動性**（買賣價差、成交量）可立即採用：它是客觀的可成交性，不需預測力驗證，
  而系統目前完全沒有這道檢查——買進區再漂亮，價差過大也吃掉報酬。
- **當日量價態勢**（收盤相對均價、量比）僅作**佐證**，不進排序、不改判定。
  單日微結構訊號雜訊高，是否真有預測力必須用既有 outcome 框架累積歷史後驗證；
  在通過驗證前就拿它改決策，等於違反本專案好不容易建立的 freeze-then-verify 紀律。

因此本模組只輸出描述與旗標，`validated=False` 明示尚未驗證。
"""
from __future__ import annotations

# 台股常見的可成交性門檻：價差超過 0.5% 時，一買一賣光滑價就吃掉約 1%。
WIDE_SPREAD_PCT = 0.5
# 成交量過低時，數百張的部位就足以推動價格，買進區形同紙上談兵。
THIN_VOLUME_LOTS = 500


def grade_liquidity(snapshot: dict) -> dict:
    """可成交性檢查。客觀且立即可用，不需要預測力驗證。"""
    spread = snapshot.get("spread_pct")
    volume = snapshot.get("total_volume")
    problems = []
    if spread is not None and float(spread) > WIDE_SPREAD_PCT:
        problems.append(f"買賣價差 {float(spread):.2f}%（>{WIDE_SPREAD_PCT}%，進出成本偏高）")
    if volume is not None and float(volume) < THIN_VOLUME_LOTS:
        problems.append(f"成交量僅 {int(volume)} 張（偏低，不易成交且易被自己推動）")
    return {
        "spread_pct": spread,
        "total_volume": volume,
        "tradable": not problems,
        "warnings": problems,
    }


def grade_day_balance(snapshot: dict) -> dict | None:
    """當日買賣方誰佔上風。僅為佐證，不進排序。"""
    vwap_gap = snapshot.get("close_vs_vwap_pct")
    volume_ratio = snapshot.get("volume_ratio")
    if vwap_gap is None and volume_ratio is None:
        return None

    above_vwap = vwap_gap is not None and float(vwap_gap) >= 0
    ratio = float(volume_ratio) if volume_ratio is not None else None

    if ratio is not None and ratio >= 2.0 and not above_vwap:
        state = "capitulation"
        note = "爆量下殺收在均價之下；可能接近情緒底，但不可接刀，須等隔日止穩"
    elif above_vwap and ratio is not None and ratio >= 1.0:
        state = "stabilizing"
        note = "買方守住當日均價且量能未縮；下跌趨勢中出現的第一個止穩跡象"
    elif ratio is not None and ratio < 0.8:
        state = "drifting"
        note = "無量整理，買盤尚未出現；便宜但沒有人要，通常還有得等"
    elif above_vwap:
        state = "firm"
        note = "收在當日均價之上，但量能普通"
    else:
        state = "soft"
        note = "收在當日均價之下，賣方略佔上風"

    return {
        "state": state, "note": note,
        "close_vs_vwap_pct": vwap_gap, "volume_ratio": volume_ratio,
        "tick_pressure": snapshot.get("tick_pressure"),
    }


def grade_entry(item: dict, snapshot: dict | None) -> dict | None:
    """整合買進側佐證。item 為 current-state 的評估項。"""
    if not snapshot:
        return None
    liquidity = grade_liquidity(snapshot)
    balance = grade_day_balance(snapshot)
    if balance is None and liquidity["tradable"]:
        return None

    decision = item.get("decision")
    # 「等待止跌」是本模組最有價值的著力點：既有規則只說價格在均線下方，
    # 沒有任何「等到什麼時候」的線索。
    hint = None
    if decision == "等待止跌" and balance:
        if balance["state"] == "stabilizing":
            hint = "仍在均線下方，但今日買方守住均價：可列入明日優先觀察，不代表可立即買進"
        elif balance["state"] == "capitulation":
            hint = "今日爆量下殺：等隔日是否止穩再談，切勿接刀"
        elif balance["state"] == "drifting":
            hint = "無量陰跌，買盤未現：維持等待"
    elif decision == "可分批研究" and balance and balance["state"] == "drifting":
        hint = "雖在買進區，但今日量能不足，不急於一次買足"

    return {
        "liquidity": liquidity,
        "day_balance": balance,
        "hint": hint,
        "trade_date": snapshot.get("trade_date"),
        "source": snapshot.get("source"),
        # 明示尚未驗證：流動性可立即採用，量價態勢仍在觀察期。
        "validated": False,
        "disclaimer": (
            "流動性檢查為客觀可成交性，可直接採用；當日量價態勢僅為佐證，"
            "尚未以 outcome 框架驗證預測力，不進入選股排序，也不改變買賣判定。"
        ),
    }
