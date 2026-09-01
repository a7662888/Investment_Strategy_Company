# -*- coding: utf-8 -*-
"""賣出時機引擎（shadow）：把 Exit Engine 的「賣多少」轉成「什麼價位、什麼時候」。

Exit Engine 回答的是部位該降多少（0–100 分 → 比例），但它吃的是日線收盤，
使用者仍不知道「今天盤中要不要動手、跌破哪個價才算數」。本模組補這一段：

1. 以日線 OHLC 算 Wilder ATR(14)，解鎖先前被列為 disabled 的移動停損。
2. 由 Exit Score 決定停損鬆緊（分數越高＝風險越高＝停損越緊，分數低則讓獲利奔跑）。
3. 用即時報價比對各觸發價，但**嚴格區分「盤中觸價」與「收盤確認」**——
   台股假跌破頻繁，盤中穿價只當提醒，收盤跌破才算成立訊號。

本模組為純函式：不連網、不下單、不寫檔，網路取數由呼叫端負責。
"""
from __future__ import annotations

# 盤中觸價與收盤確認的差別，是本引擎最重要的紀律：不做這個區分，
# 使用者會被日內雜訊掃出場，這在台股是實務上最常見的虧損來源之一。
CONFIRM_NOTE = "盤中穿價僅為提醒；台股假跌破頻繁，須收盤跌破才視為訊號成立。"


def atr_multiple(exit_score: float | None) -> float:
    """ATR 倍數：Exit Score 越高代表基本面／估值風險越高，停損收緊；
    分數低時放寬，避免把還在正常波動的贏家洗掉。"""
    if exit_score is None:
        return 3.0
    score = float(exit_score)
    if score >= 60:
        return 2.0
    if score >= 45:
        return 2.5
    if score >= 30:
        return 3.0
    return 3.5


def wilder_atr(rows: list[dict], period: int = 14) -> float | None:
    """Wilder 平滑 ATR。rows 需為日期遞增且含 high/low/close。"""
    usable = [r for r in rows if r.get("high") is not None
              and r.get("low") is not None and r.get("close") is not None]
    if len(usable) < period + 1:
        return None
    true_ranges = []
    for idx in range(1, len(usable)):
        high = float(usable[idx]["high"])
        low = float(usable[idx]["low"])
        prev_close = float(usable[idx - 1]["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = (atr * (period - 1) + true_range) / period
    return round(atr, 4)


def _anchor_high(rows: list[dict], lookback: int = 60) -> float | None:
    """移動停損的錨點：近 lookback 個交易日的最高價（Chandelier 式）。

    持股資料只有 symbol/shares/cost，沒有進場日，故不能用「進場後最高價」，
    改用固定回看窗；這點必須對使用者透明，不可假裝是個人化的進場後高點。
    """
    highs = [float(r["high"]) for r in rows[-lookback:] if r.get("high") is not None]
    return round(max(highs), 4) if highs else None


def _state(level_price: float, last_close: float | None,
           live_price: float | None) -> tuple[str, str]:
    """回傳 (state, 中文說明)。收盤確認優先於盤中觸價。"""
    if last_close is not None and last_close < level_price:
        return "confirmed_break", "已收盤跌破（訊號成立）"
    if live_price is not None and live_price < level_price:
        return "intraday_break", "盤中已跌破，等收盤確認"
    reference = live_price if live_price is not None else last_close
    if reference is not None and level_price > 0:
        distance = reference / level_price - 1.0
        if distance <= 0.02:
            return "approaching", f"距觸發價僅 {distance * 100:.1f}%"
    return "safe", "未接近觸發價"


def build_levels(item: dict, exit_result: dict, rows: list[dict],
                 cost: float | None, gain: float | None) -> list[dict]:
    """組出所有觸發價位。ETF 不掛趨勢型停損，只留配置與保本提示。"""
    is_etf = bool(item.get("is_etf"))
    score = (exit_result or {}).get("score")
    levels: list[dict] = []

    atr = wilder_atr(rows)
    anchor = _anchor_high(rows)
    if atr is not None and anchor is not None and not is_etf:
        multiple = atr_multiple(score)
        levels.append({
            "key": "atr_trailing",
            "label": f"ATR 移動停損（{multiple}×ATR14）",
            "price": round(anchor - multiple * atr, 2),
            "basis": f"近 60 日最高 {anchor:.2f} − {multiple}×ATR({atr:.2f})",
            "kind": "trailing_stop",
        })

    ma20, ma60 = item.get("ma20"), item.get("ma60")
    if ma20 is not None and not is_etf:
        levels.append({"key": "ma20", "label": "跌破 20 日均線",
                       "price": round(float(ma20), 2), "basis": "短期趨勢轉弱",
                       "kind": "trend_break"})
    if ma60 is not None and not is_etf:
        levels.append({"key": "ma60", "label": "跌破 60 日均線",
                       "price": round(float(ma60), 2), "basis": "中期趨勢轉弱",
                       "kind": "trend_break"})

    high_252 = item.get("high_252")
    if high_252 is not None and not is_etf:
        levels.append({"key": "drawdown_15", "label": "自近一年高點回撤 15%",
                       "price": round(float(high_252) * 0.85, 2),
                       "basis": f"近一年高點 {float(high_252):.2f}",
                       "kind": "drawdown"})

    # 保本線只在「目前仍有獲利」時才有意義：避免把已浮盈的部位放回虧損。
    if cost and cost > 0 and gain is not None and gain > 0:
        levels.append({"key": "breakeven", "label": "保本線（成本價）",
                       "price": round(float(cost), 2),
                       "basis": "不讓已實現的帳面獲利倒賠回成本以下",
                       "kind": "breakeven"})
    return levels


# 兩個階段若價位太近，一根長黑就會同時觸發，「分批」形同虛設而變成一天出清。
# 實測 2330 的 20MA/60MA/ATR 停損曾同時落在 2366–2391（相差不到 1%）。
MIN_STAGE_GAP = 0.02

# Exit Engine 判定續抱時，價格訊號最多只能保護一半部位：
# 系統既有原則是「不以價格訊號單獨清倉」（見 value_daily.portfolio_actions），
# 若讓保護性停損累計到 100%，等於用技術訊號推翻基本面結論。
PROTECTIVE_CAP = 0.50

# 超過現價這個跌幅的價位不列入分批計畫：那已不是「時機」，只是填充。
MAX_STAGE_DEPTH = 0.25


def _allocate(total: float, weights: list[float]) -> list[float]:
    """依權重切分並讓末批吸收餘數，確保四捨五入後合計精確等於 total。"""
    fractions = [round(total * weight, 2) for weight in weights[:-1]]
    fractions.append(round(total - sum(fractions), 2))
    return fractions


def _spread_stages(levels: list[dict]) -> list[dict]:
    """由高而低挑出彼此至少相隔 MIN_STAGE_GAP 的價位，確保分批真的分得開。"""
    kept: list[dict] = []
    for level in levels:
        if not kept or level["price"] <= kept[-1]["price"] * (1 - MIN_STAGE_GAP):
            kept.append(level)
    return kept


def build_plan(exit_result: dict, levels: list[dict], is_etf: bool,
               reference_price: float | None = None) -> list[dict]:
    """把觸發價依「先觸發者在前」排序，並配上每階段減碼比例。"""
    if is_etf:
        return []
    actionable = [lv for lv in levels
                  if lv["kind"] in ("trailing_stop", "trend_break", "drawdown", "breakeven")]
    if reference_price:
        # 離現價過遠的價位不構成「時機」：成本 900、現價 2440 時，
        # 把保本線排成第三批只是填充——真跌到那裡，前兩批早已出清。
        floor_price = float(reference_price) * (1 - MAX_STAGE_DEPTH)
        actionable = [lv for lv in actionable if lv["price"] >= floor_price]
    actionable.sort(key=lambda lv: lv["price"], reverse=True)
    stages = _spread_stages(actionable)[:3]
    if not stages:
        return []

    target = float((exit_result or {}).get("suggested_fraction") or 0.0)
    if target > 0:
        # Exit Engine 已判定要減碼：觸發價決定「何時執行這些既定比例」。
        weights = [0.5, 0.3, 0.2][:len(stages)]
        total_weight = sum(weights)
        fractions = _allocate(target, [weight / total_weight for weight in weights])
        mode = "執行 Exit Engine 既定減碼比例"
    else:
        # Exit Engine 說續抱：觸發價退化為「獲利保護停損」，且合計不超過半數部位。
        fractions = _allocate(PROTECTIVE_CAP, [1.0 / len(stages)] * len(stages))
        mode = f"獲利保護停損（Exit Engine 尚未要求減碼，合計上限 {PROTECTIVE_CAP:.0%}）"

    return [{
        "stage": idx + 1, "trigger": level["key"], "label": level["label"],
        "price": level["price"], "fraction": fractions[idx],
        "mode": mode,
        "condition": "收盤跌破後執行，不在盤中追殺",
    } for idx, level in enumerate(stages)]


def compute_sell_timing(item: dict, exit_result: dict, rows: list[dict],
                        live_quote: dict | None, cost: float | None,
                        gain: float | None, market_open: bool) -> dict:
    """回傳可稽核的賣出時機建議。不下單、不自動執行。"""
    is_etf = bool(item.get("is_etf"))
    # Yahoo 會回 54.849998474121094 這類浮點雜訊；台股報價本身只有兩位小數，
    # 不先收斂會讓觸發價比較與畫面同時出現假精度。
    last_close = (round(float(rows[-1]["close"]), 2)
                  if rows and rows[-1].get("close") is not None else None)
    last_close_date = rows[-1].get("date") if rows else None

    live_price = None
    price_basis = "無可用報價"
    quote_time = None
    if live_quote and live_quote.get("regularMarketPrice") is not None:
        live_price = round(float(live_quote["regularMarketPrice"]), 2)
        quote_time = live_quote.get("regularMarketTime")
        price_basis = "盤中即時成交價" if market_open else "最後成交價（非盤中）"
    elif last_close is not None:
        price_basis = f"日線收盤（{last_close_date}）"

    levels = build_levels(item, exit_result, rows, cost, gain)
    for level in levels:
        state, note = _state(level["price"], last_close, live_price)
        level["state"], level["state_note"] = state, note
        reference = live_price if live_price is not None else last_close
        level["distance_pct"] = (
            round((reference / level["price"] - 1.0) * 100, 2)
            if reference is not None and level["price"] > 0 else None
        )

    reference_price = live_price if live_price is not None else last_close
    plan = build_plan(exit_result, levels, is_etf, reference_price)
    confirmed = [lv for lv in levels if lv["state"] == "confirmed_break"]
    intraday = [lv for lv in levels if lv["state"] == "intraday_break"]
    approaching = [lv for lv in levels if lv["state"] == "approaching"]

    if (exit_result or {}).get("thesis_broken"):
        urgency = "immediate"
        headline = "投資論點已失效：不等技術訊號，直接檢查全數出場"
    elif is_etf:
        urgency = "advisory"
        headline = "ETF 不因均線或單日波動賣出；僅在估值過高或配置失衡時再平衡"
    elif confirmed:
        stage = plan[0]["stage"] if plan else 1
        urgency = "act"
        headline = f"已收盤跌破「{confirmed[0]['label']}」：可執行第 {stage} 階段減碼"
    elif intraday:
        urgency = "watch_close"
        headline = f"盤中已跌破「{intraday[0]['label']}」，但尚未收盤確認——先不動手"
    elif approaching:
        urgency = "prepare"
        headline = f"距「{approaching[0]['label']}」不到 2%，先設好提醒與掛單價"
    else:
        urgency = "hold"
        headline = "所有觸發價均未觸及，續抱"

    return {
        "schema_version": 1,
        "urgency": urgency,
        "headline": headline,
        "price_basis": price_basis,
        "live_price": live_price,
        "quote_time": quote_time,
        "last_close": last_close,
        "last_close_date": last_close_date,
        "market_open": bool(market_open),
        "atr14": wilder_atr(rows),
        "atr_multiple": None if is_etf else atr_multiple((exit_result or {}).get("score")),
        "levels": levels,
        "plan": plan,
        "confirm_rule": CONFIRM_NOTE,
        "anchor_note": "移動停損錨點採近 60 交易日最高價；系統未保存個人進場日，故非「進場後最高點」。",
        "shadow": True,
        "disclaimer": "研究提示，不自動下單；執行前須人工確認公告、流動性與稅費。",
    }
