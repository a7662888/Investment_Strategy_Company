# -*- coding: utf-8 -*-
"""
模型優化實驗台 —— 讓「訓練的思考過程」可見。

在同一套滾動 walk-forward 下,公平比較不同槓桿:
  A. price_logit            價格技術特徵 + logistic(現行基準)
  B. price+chips+fund_logit 加籌碼(法人買賣超強度)+ 基本面(PER 倒數、營收 YoY)
  C. price+chips+fund_gbm   同特徵但用 gradient boosting(sklearn,離線)

輸出每個設定的池化樣本外 AUC / 準確率 / Brier / 高信心桶上漲率 / 校準是否單調,
寫進 model_artifacts/experiments.md,讓每次優化「有沒有真的進步」一目了然。

用法:python -m company.model.experiments
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from company.data import single_stock as ss
from company.model.features import FEATURE_ORDER, MIN_HISTORY, extract_features, to_vector
from company.model.train import FOLDS, HORIZON, START, END, TRAIN_SYMBOLS

LOG = ROOT / "model_artifacts" / "experiments.md"
EXTRA_ORDER = ["per_inv", "rev_yoy", "chip_intensity_20", "chip_intensity_5"]


def _extra_features(view, vols_slice) -> list[float]:
    per = view.per()
    per_inv = (1.0 / per) if (per and per > 0) else 0.0
    yoy = view.rev_yoy() or 0.0
    v20 = sum(vols_slice[-20:]) or 1.0
    v5 = sum(vols_slice[-5:]) or 1.0
    ci20 = max(-1.0, min(1.0, view.inst_net(20) / v20))
    ci5 = max(-1.0, min(1.0, view.inst_net(5) / v5))
    return [round(per_inv, 6), round(yoy, 6), round(ci20, 6), round(ci5, 6)]


def build_dataset():
    """回傳 dates, X_price, X_extra, y, fwd。"""
    dts, xp, xe, ys, fws = [], [], [], [], []
    for sym in TRAIN_SYMBOLS:
        try:
            data = ss.load(sym, START, END)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        days = data.prices.trading_days
        closes, vols, dates = [], [], []
        for d in days:
            bar = data.prices.bar(sym, d)
            if bar is None:
                continue
            closes.append(float(bar["close"])); vols.append(float(bar["volume"])); dates.append(d)
        n = len(closes)
        added = 0
        for i in range(MIN_HISTORY, n - HORIZON):
            feats = extract_features(closes[: i + 1], vols[: i + 1])
            if feats is None:
                continue
            view = data.view(dates[i])
            dts.append(str(dates[i].date()))
            xp.append(to_vector(feats))
            xe.append(_extra_features(view, vols[: i + 1]))
            ys.append(1 if closes[i + HORIZON] / closes[i] - 1.0 > 0 else 0)
            fws.append(closes[i + HORIZON] / closes[i] - 1.0)
            added += 1
        print(f"  {sym}: +{added}(累計 {len(dts)})")
    return (np.array(dts), np.array(xp, float), np.array(xe, float),
            np.array(ys, float), np.array(fws, float))


def _sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _fit_logit(X, y, l2=1.0, lr=0.3, epochs=4000):
    n, d = X.shape; w = np.zeros(d); b = 0.0
    for _ in range(epochs):
        p = _sigmoid(X @ w + b)
        w -= lr * (X.T @ (p - y) / n + l2 * w / n); b -= lr * float(np.mean(p - y))
    return w, b


def _auc(y, s):
    if len(np.unique(y)) < 2: return 0.5
    order = np.argsort(s); ranks = np.empty_like(order, float); ranks[order] = np.arange(1, len(s) + 1)
    pos = y == 1; npos, nneg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def _walk_forward(dates, X, y, fwd, model_kind):
    """回傳池化 OOS (probs, y, fwd) 與 fold AUC list。"""
    op, oy, ofw, fold_aucs = [], [], [], []
    for fstart, fend in FOLDS:
        tr = dates < fstart; te = (dates >= fstart) & (dates < fend)
        if te.sum() < 100 or tr.sum() < 500:
            continue
        means = X[tr].mean(0); stds = X[tr].std(0); stds[stds == 0] = 1.0
        Xtr = (X[tr] - means) / stds; Xte = (X[te] - means) / stds
        if model_kind == "logit":
            w, b = _fit_logit(Xtr, y[tr]); p = _sigmoid(Xte @ w + b)
        else:  # gbm
            clf = GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
                                             subsample=0.8, random_state=0)
            clf.fit(Xtr, y[tr]); p = clf.predict_proba(Xte)[:, 1]
        op.append(p); oy.append(y[te]); ofw.append(fwd[te]); fold_aucs.append(round(_auc(y[te], p), 4))
    return np.concatenate(op), np.concatenate(oy), np.concatenate(ofw), fold_aucs


def _evaluate(name, p, y, fwd):
    edges = [0.0, 0.45, 0.50, 0.55, 0.60, 1.01]
    buckets, up_rates = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi); c = int(m.sum())
        ur = float(y[m].mean()) if c else None
        buckets.append((lo, hi, c, ur, float(fwd[m].mean()) if c else None))
        if ur is not None and c >= 30:
            up_rates.append(ur)
    monotonic = all(up_rates[i] <= up_rates[i + 1] + 1e-9 for i in range(len(up_rates) - 1)) if len(up_rates) > 1 else False
    high = next((b for b in reversed(buckets) if b[2] >= 30), None)
    return {
        "name": name, "oos_auc": round(_auc(y, p), 4),
        "oos_acc": round(float(((p > 0.5) == y).mean()), 4),
        "brier": round(float(np.mean((p - y) ** 2)), 4),
        "high_bucket": (f"{high[0]:.0%}~{high[1]:.0%}", high[2], round(high[3], 4)) if high else None,
        "monotonic": monotonic, "n_oos": int(len(p)), "buckets": buckets,
    }


def main():
    print("[B] 建特徵資料集(價格 + 籌碼 + 基本面)…")
    dates, Xp, Xe, y, fwd = build_dataset()
    Xpe = np.hstack([Xp, Xe])
    base = float(y.mean())
    print(f"  樣本 {len(y)};基準上漲率 {base:.4f}")

    configs = [
        ("A. price_logit", Xp, "logit"),
        ("B. price+chips+fund_logit", Xpe, "logit"),
        ("C. price+chips+fund_gbm", Xpe, "gbm"),
    ]
    results = []
    for name, X, kind in configs:
        print(f"[實驗] {name} …")
        p, yy, ff, folds = _walk_forward(dates, X, y, fwd, kind)
        ev = _evaluate(name, p, yy, ff); ev["fold_aucs"] = folds
        results.append(ev)
        print(f"   OOS_AUC={ev['oos_auc']} acc={ev['oos_acc']} 高桶={ev['high_bucket']} 單調={ev['monotonic']} folds={folds}")

    best = max(results, key=lambda r: r["oos_auc"])
    lines = ["# 模型優化實驗紀錄", "",
             f"- 資料:{len(set(TRAIN_SYMBOLS))} 檔、樣本 {len(y)}、基準上漲率 {base:.1%}、horizon {HORIZON} 日",
             f"- 驗證:滾動 walk-forward(folds={[f[0][:7] for f in FOLDS]});所有特徵 PIT", "",
             "## 對比(池化樣本外)", "",
             "| 設定 | OOS AUC | fold AUC | 準確率 | Brier | 高信心桶上漲率 | 校準單調 |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        hb = f"{r['high_bucket'][0]} → {r['high_bucket'][2]:.1%}(n={r['high_bucket'][1]})" if r["high_bucket"] else "—"
        lines.append(f"| {r['name']} | {r['oos_auc']} | {r['fold_aucs']} | {r['oos_acc']} | {r['brier']} | {hb} | {'✅' if r['monotonic'] else '✗'} |")
    lines += ["", "## 判讀(誠實)", "",
              f"- 最佳設定(以 OOS AUC):**{best['name']}**(AUC {best['oos_auc']})。",
              f"- 基準上漲率 {base:.1%};AUC 0.5 = 隨機。各設定差距 {min(r['oos_auc'] for r in results):.3f}~{max(r['oos_auc'] for r in results):.3f}。",
              "- 若最佳僅微幅領先基準/logistic,代表**加特徵/換模型的邊際有限**,純技術+籌碼+基本面對 5 日方向預測仍接近天花板;",
              "  價值應放在**校準後機率排序 + 可解釋理由 + 風險控制**,而非追命中率。",
              "- 上線限制:籌碼/基本面特徵需 Codex 在 app.py 補抓 PER/法人;若 B/C 明顯勝出才值得做這層整合。"]
    LOG.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n最佳:{best['name']} (OOS AUC {best['oos_auc']})")
    print(f"實驗紀錄寫入:{LOG}")


if __name__ == "__main__":
    main()
