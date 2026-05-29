# -*- coding: utf-8 -*-
"""
純標準函式庫的 PIT 技術特徵抽取。

輸入只給「截止日以前」的收盤(與選用成交量)序列,杜絕未來函數。
特徵設計可解釋:每個都是常見技術意義,方便回推「理由」。
train.py 與 score.py 共用本檔,確保訓練/上線特徵一致。
"""
from __future__ import annotations

import math

# 特徵順序固定(artifact 權重對齊用)
FEATURE_ORDER = [
    "ma_ratio_20",     # 收盤相對 20 日均線
    "ma_ratio_60",     # 收盤相對 60 日均線
    "ma20_over_ma60",  # 短均 vs 中均(趨勢結構)
    "ma60_over_ma120", # 中均 vs 長均
    "mom_20",          # 20 日動能
    "mom_60",          # 60 日動能
    "rsi14_z",         # RSI 置中:(rsi-50)/50
    "macd_hist_norm",  # MACD 柱狀體 / 收盤
    "vol_20",          # 20 日已實現波動
    "dist_from_high",  # 相對 120 日高點的位置(<=0)
    "vol_surge",       # 量能放大:近 5 日均量 / 60 日均量 - 1
    "foreign_net_buy_ratio", # 外資近 20 日累計淨買超比率
    "trust_net_buy_ratio",   # 投信近 20 日累計淨買超比率
    "margin_balance_chg",    # 融資餘額 5 日變動比率
    "revenue_yoy",           # 月營收年增率
]

MIN_HISTORY = 130  # 需要足夠長以算 120 日均線與長動能

FEATURE_LABELS = {
    "ma_ratio_20": "站上/跌破 20 日均線",
    "ma_ratio_60": "站上/跌破 60 日均線",
    "ma20_over_ma60": "短中均線多空結構",
    "ma60_over_ma120": "中長均線多空結構",
    "mom_20": "20 日動能",
    "mom_60": "60 日動能",
    "rsi14_z": "RSI(14) 強弱",
    "macd_hist_norm": "MACD 柱狀體動能",
    "vol_20": "近期波動風險",
    "dist_from_high": "距 120 日高點位置",
    "vol_surge": "量能放大程度",
    "foreign_net_buy_ratio": "外資近20日淨買超",
    "trust_net_buy_ratio": "投信近20日淨買超",
    "margin_balance_chg": "融資餘額5日變動",
    "revenue_yoy": "月營收年增率 (YoY)",
}


def _ma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append((v - out[-1]) * k + out[-1])
    return out


def _macd_hist(closes: list[float], fast=12, slow=26, signal=9) -> float:
    if len(closes) < slow + signal:
        return 0.0
    macd_line = [f - s for f, s in zip(_ema(closes, fast), _ema(closes, slow))]
    sig = _ema(macd_line, signal)
    return macd_line[-1] - sig[-1]


def extract_features(
    closes: list[float],
    volumes: list[float] | None = None,
    foreign_net_buy: list[float] | None = None,
    trust_net_buy: list[float] | None = None,
    margin_purchase: list[float] | None = None,
    short_sale: list[float] | None = None,
    revenue_yoy: list[float] | None = None,
) -> dict[str, float] | None:
    """從 ≤T 的序列算特徵;歷史不足回 None。"""
    if len(closes) < MIN_HISTORY:
        return None
    last = closes[-1]
    if last <= 0:
        return None

    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    ma120 = _ma(closes, 120)

    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    avg = sum(rets) / len(rets)
    vol20 = math.sqrt(sum((r - avg) ** 2 for r in rets) / len(rets))

    high120 = max(closes[-120:])

    if volumes and len(volumes) >= 60:
        v5 = sum(volumes[-5:]) / 5
        v60 = sum(volumes[-60:]) / 60
        vol_surge = (v5 / v60 - 1.0) if v60 > 0 else 0.0
    else:
        vol_surge = 0.0

    # 4 New Features with Safe Fallbacks
    # 1. Foreign Net Buy Ratio (20-day cumulative net / 20-day volume)
    foreign_net_buy_ratio = 0.0
    if foreign_net_buy and volumes and len(foreign_net_buy) >= 20 and len(volumes) >= 20:
        v_sum = sum(volumes[-20:])
        if v_sum > 0:
            foreign_net_buy_ratio = sum(foreign_net_buy[-20:]) / v_sum

    # 2. Trust Net Buy Ratio (20-day cumulative net / 20-day volume)
    trust_net_buy_ratio = 0.0
    if trust_net_buy and volumes and len(trust_net_buy) >= 20 and len(volumes) >= 20:
        v_sum = sum(volumes[-20:])
        if v_sum > 0:
            trust_net_buy_ratio = sum(trust_net_buy[-20:]) / v_sum

    # 3. Margin Balance Change (5-day change / 20-day average Volume in board lots)
    margin_balance_chg = 0.0
    if margin_purchase and volumes and len(margin_purchase) >= 6 and len(volumes) >= 20:
        m_diff = margin_purchase[-1] - margin_purchase[-6]
        avg_vol = sum(volumes[-20:]) / 20.0
        if avg_vol > 0:
            margin_balance_chg = m_diff / (avg_vol / 1000.0)

    # 4. Revenue YoY
    rev_yoy_val = 0.0
    if revenue_yoy and len(revenue_yoy) > 0:
        for val in reversed(revenue_yoy):
            if val is not None and not math.isnan(val):
                rev_yoy_val = float(val)
                break

    return {
        "ma_ratio_20": last / ma20 - 1 if ma20 else 0.0,
        "ma_ratio_60": last / ma60 - 1 if ma60 else 0.0,
        "ma20_over_ma60": (ma20 / ma60 - 1) if (ma20 and ma60) else 0.0,
        "ma60_over_ma120": (ma60 / ma120 - 1) if (ma60 and ma120) else 0.0,
        "mom_20": last / closes[-21] - 1 if len(closes) > 21 else 0.0,
        "mom_60": last / closes[-61] - 1 if len(closes) > 61 else 0.0,
        "rsi14_z": (_rsi(closes, 14) - 50.0) / 50.0,
        "macd_hist_norm": _macd_hist(closes) / last,
        "vol_20": vol20,
        "dist_from_high": last / high120 - 1 if high120 else 0.0,
        "vol_surge": max(-1.0, min(3.0, vol_surge)),
        "foreign_net_buy_ratio": foreign_net_buy_ratio,
        "trust_net_buy_ratio": trust_net_buy_ratio,
        "margin_balance_chg": margin_balance_chg,
        "revenue_yoy": rev_yoy_val,
    }


def to_vector(feats: dict[str, float]) -> list[float]:
    return [feats[k] for k in FEATURE_ORDER]
