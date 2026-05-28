# -*- coding: utf-8 -*-
"""
訓練可解釋 logistic 隔日偏多機率模型,並以時間外樣本校準。

流程:
  1. 多檔真實資料(FinMind,company.data.single_stock)→ 逐日抽 PIT 特徵。
  2. 標籤 = 未來 H 個交易日報酬 > 0(嚴格用未來只當「答案」,特徵只用 ≤T)。
  3. 依日期切「訓練期 / 樣本外期」;標準化用訓練期參數。
  4. numpy 擬合 logistic(GD + L2)。
  5. 在樣本外算校準表(機率桶→實際上漲率、前向報酬)與指標(AUC/Brier/Acc)。
  6. 存純 JSON artifact 給 score.py(純 stdlib)上線用。

用法:python -m company.model.train
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from company.data import single_stock as ss
from company.model.features import FEATURE_ORDER, MIN_HISTORY, extract_features, to_vector

ARTIFACT = ROOT / "model_artifacts" / "logit_v1.json"
TRAIN_SYMBOLS = ["2327", "2330", "2317", "2454", "2308", "2603"]
START, END = "2020-01-01", "2026-05-28"
HORIZON = 5            # 前向交易日數
OOS_SPLIT = "2024-01-01"  # 此日(含)之後為樣本外


def _series(symbol: str):
    data = ss.load(symbol, START, END)
    days = data.prices.trading_days
    closes, vols, dates = [], [], []
    for d in days:
        bar = data.prices.bar(symbol, d)
        if bar is None:
            continue
        closes.append(float(bar["close"]))
        vols.append(float(bar["volume"]))
        dates.append(d)
    return closes, vols, dates


def build_dataset():
    rows = []  # (date, x_vector, label)
    for sym in TRAIN_SYMBOLS:
        try:
            closes, vols, dates = _series(sym)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        n = len(closes)
        for i in range(MIN_HISTORY, n - HORIZON):
            feats = extract_features(closes[: i + 1], vols[: i + 1])
            if feats is None:
                continue
            fwd = closes[i + HORIZON] / closes[i] - 1.0
            rows.append((dates[i], to_vector(feats), 1 if fwd > 0 else 0, fwd))
        print(f"  {sym}: 累計樣本 {len(rows)}")
    return rows


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, l2=1.0, lr=0.3, epochs=4000):
    n, d = X.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        p = sigmoid(X @ w + b)
        grad_w = X.T @ (p - y) / n + l2 * w / n
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def auc(y_true, scores):
    order = np.argsort(scores)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    pos = y_true == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return 0.5
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main():
    print("[B] 載入多檔真實資料並建特徵資料集 …")
    rows = build_dataset()
    if len(rows) < 500:
        raise SystemExit("樣本不足,請確認資料")

    dates = np.array([str(r[0].date()) for r in rows])
    X = np.array([r[1] for r in rows], dtype=float)
    y = np.array([r[2] for r in rows], dtype=float)
    fwd = np.array([r[3] for r in rows], dtype=float)

    is_oos = dates >= OOS_SPLIT
    Xtr, ytr = X[~is_oos], y[~is_oos]
    Xte, yte, fwd_te = X[is_oos], y[is_oos], fwd[is_oos]
    print(f"  訓練期樣本 {len(Xtr)} / 樣本外 {len(Xte)}")

    means = Xtr.mean(axis=0)
    stds = Xtr.std(axis=0)
    stds[stds == 0] = 1.0
    Xtr_s = (Xtr - means) / stds
    Xte_s = (Xte - means) / stds

    w, b = fit_logistic(Xtr_s, ytr)

    p_te = sigmoid(Xte_s @ w + b)
    p_tr = sigmoid(Xtr_s @ w + b)
    metrics = {
        "train_accuracy": round(float(((p_tr > 0.5) == ytr).mean()), 4),
        "oos_accuracy": round(float(((p_te > 0.5) == yte).mean()), 4),
        "oos_auc": round(auc(yte, p_te), 4),
        "oos_brier": round(float(np.mean((p_te - yte) ** 2)), 4),
        "base_rate_up": round(float(y.mean()), 4),
    }

    # 樣本外校準表:依預測機率分 5 桶,算實際上漲率與平均前向報酬
    buckets = []
    edges = [0.0, 0.45, 0.50, 0.55, 0.60, 1.01]
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p_te >= lo) & (p_te < hi)
        cnt = int(mask.sum())
        if cnt == 0:
            buckets.append({"lo": lo, "hi": hi, "count": 0,
                            "empirical_up_rate": None, "avg_fwd_return": None})
            continue
        buckets.append({
            "lo": round(lo, 3), "hi": round(hi, 3), "count": cnt,
            "empirical_up_rate": round(float(yte[mask].mean()), 4),
            "avg_fwd_return": round(float(fwd_te[mask].mean()), 5),
        })

    artifact = {
        "name": "claude_logit_calibrated_v1",
        "horizon_days": HORIZON,
        "feature_order": FEATURE_ORDER,
        "weights": {k: round(float(wi), 5) for k, wi in zip(FEATURE_ORDER, w)},
        "bias": round(float(b), 5),
        "means": {k: round(float(m), 6) for k, m in zip(FEATURE_ORDER, means)},
        "stds": {k: round(float(s), 6) for k, s in zip(FEATURE_ORDER, stds)},
        "calibration_buckets": buckets,
        "metrics": metrics,
        "train_symbols": TRAIN_SYMBOLS,
        "train_window": [START, OOS_SPLIT],
        "oos_window": [OOS_SPLIT, END],
        "future_knowledge_used": False,
        "note": "特徵只用截止日以前資料;機率經樣本外校準;每桶附歷史上漲率與前向報酬作依據。",
    }
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[D] 指標:{metrics}")
    print("[校準表(樣本外)]")
    for bk in buckets:
        print(f"  機率 {bk['lo']}~{bk['hi']}: n={bk['count']} "
              f"實際上漲率={bk['empirical_up_rate']} 平均{HORIZON}日報酬={bk['avg_fwd_return']}")
    print(f"\nartifact 已寫入:{ARTIFACT}")


if __name__ == "__main__":
    main()
