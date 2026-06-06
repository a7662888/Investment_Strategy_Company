# -*- coding: utf-8 -*-
"""
P2-1 模型有效性監控 — 滾動樣本外 (rolling OOS) AUC。

目的(對齊三方共識 HANDOFF §7 P2-1):
  「模型有效性」必須由**模型自身的預測 vs 實際結果**判定,**與大盤燈號脫鉤**。
  現況訓練期 pooled OOS AUC≈0.55、IC≈0.0055 → 幾乎無 edge;此監控用「最近一段時間
  的真實預測命中」算滾動 AUC,低於門檻就自動標記「暫停採用」,給選股/前端一個誠實訊號。

設計原則(可信):
  - PIT:每個評估日 d 的「預測機率」只用 ≤ d 的資料(沿用 score_series 的 PIT 切片)。
  - 實現標籤只用於『評估』:label = 1 if close[d+H] > close[d] else 0(這是回測評估,
    預測本身沒偷看未來;防偷看由 tests/test_no_lookahead.py 守門 score/feature 路徑)。
  - 純後端(Claude lane,company/);不碰 app.py 前端。前端/health 接線交 Codex,UI 交 Antigravity。

對外主要函式:
  rolling_oos_auc(datasets, as_of, window_days, horizon, ...) -> dict
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..data.single_stock import StockData
from .features import MIN_HISTORY
from .score import score_series

# 預設門檻:AUC < 0.52 視為「與丟銅板無異」→ 暫停採用模型機率作為排序依據。
DEFAULT_THRESHOLD = 0.52


def _prob_up_at(data: StockData, as_of: pd.Timestamp) -> Optional[float]:
    """以 ≤ as_of 的資料算 score_series 的 probability_up(%)。沿用 market_screener._score_one 的特徵串建構。"""
    h = data.prices.history(data.symbol, as_of)
    if len(h) < MIN_HISTORY:
        return None
    closes = h["close"].astype(float).tolist()
    vols = h["volume"].astype(float).tolist()
    days = h.index

    fn = data._foreign_net.reindex(days).fillna(0.0).tolist()
    tn = data._trust_net.reindex(days).fillna(0.0).tolist()
    if len(data._margin) > 0 and "MarginPurchaseTodayBalance" in data._margin.columns:
        mp = data._margin["MarginPurchaseTodayBalance"].reindex(days).ffill().fillna(0.0).tolist()
        ss = data._margin["ShortSaleTodayBalance"].reindex(days).ffill().fillna(0.0).tolist()
    else:
        mp = [0.0] * len(days)
        ss = [0.0] * len(days)
    ry = data._rev_yoy.reindex(days).ffill().fillna(0.0).tolist()
    dates = [d.strftime("%Y-%m-%d") for d in days]

    # foreign_net_buy 已給 → score_series 不會再走 FinMind 自動對齊(離線、快)。
    ev = score_series(
        closes, vols, symbol=data.symbol, dates=dates,
        foreign_net_buy=fn, trust_net_buy=tn, margin_purchase=mp,
        short_sale=ss, revenue_yoy=ry,
    )
    if ev is None:
        return None
    return float(ev["probability_up"])


def _auc(scores: list[float], labels: list[int]) -> Optional[float]:
    """Mann-Whitney U 排名法,含平手平均秩處理。"""
    n = len(scores)
    pos = sum(labels)
    neg = n - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(n), key=lambda i: scores[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and scores[order[j]] == scores[order[i]]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # 1-based 平均秩
        for k in range(i, j):
            ranks[order[k]] = avg_rank
        i = j
    sum_pos = sum(ranks[idx] for idx in range(n) if labels[idx] == 1)
    return (sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def rolling_oos_auc(
    datasets: dict[str, StockData],
    as_of: pd.Timestamp,
    window_days: int = 120,
    horizon: int = 5,
    step: int = 1,
    threshold: float = DEFAULT_THRESHOLD,
    min_pairs: int = 50,
) -> dict:
    """
    對 datasets 內各股,在 [as_of-window, as_of-horizon] 的每個交易日做一次預測,
    與 horizon 日後的實現方向比對,彙總成單一滾動 OOS AUC。

    回傳:
      auc, n_pairs, up_rate, window_days, horizon, threshold, status, by_symbol_n,
      verdict(人話結論)。status ∈ {"正常","暫停採用","資料不足"}。
    """
    probs: list[float] = []
    labels: list[int] = []
    by_symbol_n: dict[str, int] = {}

    for sym, data in datasets.items():
        td = data.prices.trading_days
        td = td[td <= as_of]
        if len(td) < MIN_HISTORY + horizon + 1:
            continue
        end_i = len(td) - horizon - 1            # 最後一個能算實現標籤的位置
        start_i = max(MIN_HISTORY, end_i - window_days)
        cnt = 0
        for i in range(start_i, end_i + 1, step):
            d = td[i]
            prob = _prob_up_at(data, d)
            if prob is None:
                continue
            c0 = float(data.prices.history(sym, d)["close"].iloc[-1])
            c1 = float(data.prices.history(sym, td[i + horizon])["close"].iloc[-1])
            if c0 <= 0:
                continue
            probs.append(prob / 100.0)
            labels.append(1 if c1 > c0 else 0)
            cnt += 1
        if cnt:
            by_symbol_n[sym] = cnt

    n = len(probs)
    if n < min_pairs:
        return {
            "as_of": str(pd.Timestamp(as_of).date()), "auc": None, "n_pairs": n,
            "up_rate": None, "window_days": window_days, "horizon": horizon,
            "threshold": threshold, "status": "資料不足", "by_symbol_n": by_symbol_n,
            "verdict": f"樣本不足({n} < {min_pairs}),無法判定模型有效性。",
        }

    auc = _auc(probs, labels)
    up_rate = sum(labels) / n
    if auc is None:
        status = "資料不足"
        verdict = "標籤全為單一類別,無法計算 AUC。"
    elif auc < threshold:
        status = "暫停採用"
        verdict = (f"滾動 OOS AUC {auc:.3f} < 門檻 {threshold} → 模型近期幾乎無預測力(≈丟銅板),"
                   f"建議**暫停**把模型機率當選股排序依據,改採純規則+風控。")
    else:
        status = "正常"
        verdict = f"滾動 OOS AUC {auc:.3f} ≥ 門檻 {threshold} → 模型近期具基本鑑別力,可續用(仍建議搭配風控)。"

    return {
        "as_of": str(pd.Timestamp(as_of).date()), "auc": round(auc, 4) if auc is not None else None,
        "n_pairs": n, "up_rate": round(up_rate, 4), "window_days": window_days,
        "horizon": horizon, "threshold": threshold, "status": status,
        "by_symbol_n": by_symbol_n, "verdict": verdict, "future_knowledge_used": False,
    }
