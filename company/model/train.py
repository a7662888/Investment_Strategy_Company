# -*- coding: utf-8 -*-
"""
訓練可解釋 logistic 隔日偏多機率模型,並以**滾動 walk-forward** 做樣本外校準。

流程:
  1. 跨產業多檔真實資料(FinMind,company.data.single_stock)→ 逐日抽 PIT 特徵。
  2. 標籤 = 未來 H 個交易日報酬 > 0(特徵只用 ≤T)。
  3. 滾動 walk-forward:每個 OOS fold 只用「該年以前」資料訓練 → 預測該年(真正樣本外);
     池化所有 fold 的 OOS 預測,建校準表(機率桶→實際上漲率/前向報酬)。
  4. production 模型用全部資料擬合(最即時),feature pipeline 與 fold 相同。
  5. 存純 JSON artifact 給 score.py(純 stdlib)上線用。schema 與舊版相容。

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
# 跨產業流動性較佳的標的(失敗的會自動略過)
TRAIN_SYMBOLS = [
    "2330", "2317", "2454", "2308", "2303", "3711",  # 半導體/電子
    "2002", "1301", "1303",                            # 傳產/塑化/鋼
    "2412", "3045",                                    # 電信
    "2881", "2882", "2891",                            # 金融
    "2603", "2609", "2615",                            # 航運
    "2327", "2379", "3034",                            # 被動/IC 設計
]
START, END = "2020-01-01", "2026-05-28"
HORIZON = 5
# 滾動 OOS folds:每段只用該段開始日以前資料訓練
FOLDS = [("2023-01-01", "2024-01-01"),
         ("2024-01-01", "2025-01-01"),
         ("2025-01-01", "2027-01-01")]


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
    rows = []
    for sym in TRAIN_SYMBOLS:
        try:
            closes, vols, dates = _series(sym)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        n = len(closes)
        added = 0
        for i in range(MIN_HISTORY, n - HORIZON):
            feats = extract_features(closes[: i + 1], vols[: i + 1])
            if feats is None:
                continue
            fwd = closes[i + HORIZON] / closes[i] - 1.0
            rows.append((dates[i], to_vector(feats), 1 if fwd > 0 else 0, fwd))
            added += 1
        print(f"  {sym}: +{added}(累計 {len(rows)})")
    return rows


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, l2=1.0, lr=0.3, epochs=4000):
    n, d = X.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        p = sigmoid(X @ w + b)
        w -= lr * (X.T @ (p - y) / n + l2 * w / n)
        b -= lr * float(np.mean(p - y))
    return w, b


def auc(y_true, scores):
    if len(np.unique(y_true)) < 2:
        return 0.5
    order = np.argsort(scores)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    pos = y_true == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _standardize(Xtr, Xte):
    means = Xtr.mean(axis=0)
    stds = Xtr.std(axis=0)
    stds[stds == 0] = 1.0
    return (Xtr - means) / stds, (Xte - means) / stds, means, stds


def main():
    print("[B] 載入跨產業多檔真實資料並建特徵 …")
    rows = build_dataset()
    if len(rows) < 1000:
        raise SystemExit("樣本不足")

    dates = np.array([str(r[0].date()) for r in rows])
    X = np.array([r[1] for r in rows], dtype=float)
    y = np.array([r[2] for r in rows], dtype=float)
    fwd = np.array([r[3] for r in rows], dtype=float)
    print(f"  總樣本 {len(X)};跨 {len(set(d[:4] for d in dates))} 個年度")

    # --- 滾動 walk-forward:池化 OOS 預測 ---
    oos_p, oos_y, oos_fwd = [], [], []
    fold_aucs = []
    for fstart, fend in FOLDS:
        tr = dates < fstart
        te = (dates >= fstart) & (dates < fend)
        if te.sum() < 100 or tr.sum() < 500:
            continue
        Xtr_s, Xte_s, _, _ = _standardize(X[tr], X[te])
        w, b = fit_logistic(Xtr_s, y[tr])
        p = sigmoid(Xte_s @ w + b)
        oos_p.append(p); oos_y.append(y[te]); oos_fwd.append(fwd[te])
        a = auc(y[te], p)
        fold_aucs.append({"oos": f"{fstart}~{fend}", "n": int(te.sum()), "auc": round(a, 4)})
        print(f"  fold {fstart}~{fend}: n={int(te.sum())} OOS_AUC={a:.4f}")

    oos_p = np.concatenate(oos_p); oos_y = np.concatenate(oos_y); oos_fwd = np.concatenate(oos_fwd)

    # --- production 模型:用全部資料擬合(最即時)---
    Xtr_s, _, means, stds = _standardize(X, X)
    w, b = fit_logistic(Xtr_s, y)

    metrics = {
        "pooled_oos_auc": round(auc(oos_y, oos_p), 4),
        "pooled_oos_accuracy": round(float(((oos_p > 0.5) == oos_y).mean()), 4),
        "pooled_oos_brier": round(float(np.mean((oos_p - oos_y) ** 2)), 4),
        "base_rate_up": round(float(y.mean()), 4),
        "fold_aucs": fold_aucs,
        "n_train_total": int(len(X)),
        "n_oos_total": int(len(oos_p)),
        "n_symbols": len(set(TRAIN_SYMBOLS)),
    }

    # --- 校準表(池化 OOS)---
    edges = [0.0, 0.45, 0.50, 0.55, 0.60, 1.01]
    buckets = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (oos_p >= lo) & (oos_p < hi)
        cnt = int(mask.sum())
        buckets.append({
            "lo": round(lo, 3), "hi": round(hi, 3), "count": cnt,
            "empirical_up_rate": round(float(oos_y[mask].mean()), 4) if cnt else None,
            "avg_fwd_return": round(float(oos_fwd[mask].mean()), 5) if cnt else None,
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
        "train_window": [START, END],
        "validation": "rolling walk-forward,池化 OOS 校準;production 以全資料擬合",
        "future_knowledge_used": False,
        "note": "特徵只用截止日以前資料;機率經滾動樣本外校準;每桶附歷史上漲率與前向報酬作依據。",
    }
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[D] 指標:{ {k: v for k, v in metrics.items() if k != 'fold_aucs'} }")
    print("[校準表(池化 OOS)]")
    for bk in buckets:
        print(f"  機率 {bk['lo']}~{bk['hi']}: n={bk['count']} "
              f"上漲率={bk['empirical_up_rate']} 平均{HORIZON}日報酬={bk['avg_fwd_return']}")
    print(f"\nartifact 已寫入:{ARTIFACT}")


if __name__ == "__main__":
    main()
