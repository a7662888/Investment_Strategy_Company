# -*- coding: utf-8 -*-
"""
訓練可解釋多分類 XGBoost 三欄式機率模型，並以滾動 walk-forward 做樣本外校準。

流程:
  1. 跨產業多檔真實資料 → 逐日抽取 PIT 特徵（包含均線、動能、籌碼面、融資券與月營收）。
  2. 標籤採用三欄式標記 (Triple-Barrier): 利潤上限 (2xATR), 停損下限 (1.5xATR), 時間到期 (5天)。
  3. 滾動 walk-forward: 每個 OOS fold 只用「該年以前」資料訓練 -> 預測該年(樣本外);
     計算每日 Precision@TopK 與 Spearman Rank IC。
  4. Production 模型用全部資料擬合，並注入決策樹預期值 (Saabas attribution 用)。
  5. 存入 JSON 格式 xgb_v2.json 給 score.py 上線。

用法:python -m company.model.train
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import math
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from company.data import single_stock as ss
from company.model.features import FEATURE_ORDER, MIN_HISTORY, extract_features, to_vector

ARTIFACT = ROOT / "model_artifacts" / "xgb_v2.json"

TRAIN_SYMBOLS = [
    "2330", "2317", "2454", "2308", "2303", "3711",  # 半導體/電子
    "2002", "1301", "1303",                            # 傳產/塑化/鋼
    "2412", "3045",                                    # 電信
    "2881", "2882", "2891",                            # 金融
    "2603", "2609", "2615",                            # 航運
    "2327", "2379", "3034",                            # 被動/IC 設計
]
START, END = "2020-01-01", "2026-05-28"
HORIZON = 5 # max holding period
FOLDS = [("2023-01-01", "2024-01-01"),
         ("2024-01-01", "2025-01-01"),
         ("2025-01-01", "2027-01-01")]


def _series(symbol: str):
    data = ss.load(symbol, START, END)
    days = data.prices.trading_days
    closes, vols, dates = [], [], []
    foreign_nets, trust_nets = [], []
    margin_purchases, short_sales = [], []
    revenue_yoys = []
    
    for d in days:
        bar = data.prices.bar(symbol, d)
        if bar is None:
            continue
        view = data.view(d)
        closes.append(float(bar["close"]))
        vols.append(float(bar["volume"]))
        dates.append(d)
        foreign_nets.append(view.foreign_net_daily())
        trust_nets.append(view.trust_net_daily())
        margin_purchases.append(view.margin_purchase_bal())
        short_sales.append(view.short_sale_bal())
        revenue_yoys.append(view.rev_yoy())
        
    return closes, vols, dates, foreign_nets, trust_nets, margin_purchases, short_sales, revenue_yoys


def build_dataset():
    rows = []
    for sym in TRAIN_SYMBOLS:
        try:
            closes, vols, dates, foreign, trust, margin, short, rev = _series(sym)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        n = len(closes)
        
        # 加載 StockData 用以取得每日 High & Low 價格，並計算 14 日 ATR
        data = ss.load(sym, START, END)
        highs = []
        lows = []
        for d in dates:
            bar = data.prices.bar(sym, d)
            highs.append(float(bar["high"]))
            lows.append(float(bar["low"]))
            
        # 計算 14 日 ATR
        tr = [0.0] * n
        for j in range(n):
            if j == 0:
                tr[j] = highs[j] - lows[j]
            else:
                tr[j] = max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1]))
        
        atr = [0.0] * n
        for j in range(n):
            if j < 14:
                atr[j] = sum(tr[:j+1]) / (j + 1)
            else:
                atr[j] = sum(tr[j-13:j+1]) / 14.0
        
        added = 0
        for i in range(MIN_HISTORY, n - HORIZON):
            feats = extract_features(
                closes[: i + 1],
                vols[: i + 1],
                foreign[: i + 1],
                trust[: i + 1],
                margin[: i + 1],
                short[: i + 1],
                rev[: i + 1]
            )
            if feats is None:
                continue
                
            cur_close = closes[i]
            cur_atr = atr[i]
            if cur_atr <= 0:
                cur_atr = cur_close * 0.02
                
            upper_barrier = cur_close + 2.0 * cur_atr
            lower_barrier = cur_close - 1.5 * cur_atr
            
            # 三欄式標記 (0: DOWN, 1: NO_SIGNAL, 2: UP)
            label = 1 # 預設無信號 (時間到期)
            fwd_ret = closes[i + HORIZON] / cur_close - 1.0
            
            for k in range(1, HORIZON + 1):
                h_val = highs[i + k]
                l_val = lows[i + k]
                
                if h_val >= upper_barrier:
                    label = 2 # 觸及利潤上限 (UP)
                    fwd_ret = upper_barrier / cur_close - 1.0
                    break
                elif l_val <= lower_barrier:
                    label = 0 # 觸及停損下限 (DOWN)
                    fwd_ret = lower_barrier / cur_close - 1.0
                    break
            
            rows.append((dates[i], to_vector(feats), label, fwd_ret))
            added += 1
        print(f"  {sym}: +{added}(累計 {len(rows)})")
    return rows


def fit_xgb(X, y):
    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        max_depth=3,
        n_estimators=100,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42
    )
    model.fit(X, y)
    return model


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


def add_expected_values(node: dict) -> float:
    if "leaf" in node:
        node["expected_value"] = float(node["leaf"])
        return node["expected_value"]
        
    covers = [float(child.get("cover", 1.0)) for child in node["children"]]
    total_cover = sum(covers)
    
    val = 0.0
    for child, cov in zip(node["children"], covers):
        child_val = add_expected_values(child)
        if total_cover > 0:
            val += child_val * cov / total_cover
        else:
            val += child_val / len(node["children"])
            
    node["expected_value"] = val
    return val


def compute_oos_daily_metrics(dates_te, p_up, y_te, fwd_te):
    unique_dates = np.unique(dates_te)
    prec_3_list, prec_5_list = [], []
    ic_list = []
    
    for d in unique_dates:
        mask = dates_te == d
        # 我們需要該日至少有 5 檔股票的預測才能計算 IC 與 TopK
        if mask.sum() < 5:
            continue
        p_d = p_up[mask]
        fwd_d = fwd_te[mask]
        
        # 降序排序
        order = np.argsort(p_d)[::-1]
        
        # Precision@Top3 (未來實際有上漲的比率)
        top3_idx = order[:3]
        prec_3 = (fwd_d[top3_idx] > 0).mean()
        prec_3_list.append(prec_3)
        
        # Precision@Top5
        top5_idx = order[:5]
        prec_5 = (fwd_d[top5_idx] > 0).mean()
        prec_5_list.append(prec_5)
        
        # Spearman Rank IC
        s1 = pd.Series(p_d)
        s2 = pd.Series(fwd_d)
        ic = s1.corr(s2, method="spearman")
        if not np.isnan(ic):
            ic_list.append(ic)
            
    return np.mean(prec_3_list) if prec_3_list else 0.0, np.mean(prec_5_list) if prec_5_list else 0.0, np.mean(ic_list) if ic_list else 0.0


def main():
    print("[B] 載入跨產業多檔真實資料並建特徵 …")
    rows = build_dataset()
    if len(rows) < 1000:
        raise SystemExit("樣本不足")

    dates = np.array([str(r[0].date()) for r in rows])
    X = np.array([r[1] for r in rows], dtype=float)
    y = np.array([r[2] for r in rows], dtype=int)
    fwd = np.array([r[3] for r in rows], dtype=float)
    print(f"  總樣本 {len(X)};跨 {len(set(d[:4] for d in dates))} 個年度")

    # --- 滾動 walk-forward:池化 OOS 預測 ---
    oos_p, oos_y, oos_fwd, oos_pred_class = [], [], [], []
    oos_dates = []
    fold_aucs = []
    
    for fstart, fend in FOLDS:
        tr = dates < fstart
        te = (dates >= fstart) & (dates < fend)
        if te.sum() < 100 or tr.sum() < 500:
            continue
        Xtr_s, Xte_s, _, _ = _standardize(X[tr], X[te])
        model = fit_xgb(Xtr_s, y[tr])
        
        # 預測所有類別的機率
        p_all = model.predict_proba(Xte_s)
        p_up = p_all[:, 2] # Class 2 (UP) 的機率
        pred_cls = np.argmax(p_all, axis=1)
        
        oos_p.append(p_up)
        oos_y.append(y[te])
        oos_fwd.append(fwd[te])
        oos_pred_class.append(pred_cls)
        oos_dates.append(dates[te])
        
        y_te_binary = (y[te] == 2).astype(int)
        a = auc(y_te_binary, p_up)
        fold_aucs.append({"oos": f"{fstart}~{fend}", "n": int(te.sum()), "auc": round(a, 4)})
        print(f"  fold {fstart}~{fend}: n={int(te.sum())} OOS_AUC={a:.4f}")

    oos_p = np.concatenate(oos_p)
    oos_y = np.concatenate(oos_y)
    oos_fwd = np.concatenate(oos_fwd)
    oos_pred_class = np.concatenate(oos_pred_class)
    oos_dates = np.concatenate(oos_dates)

    # 計算 Precision@TopK 與 Spearman Rank IC
    prec3, prec5, mean_ic = compute_oos_daily_metrics(oos_dates, oos_p, oos_y, oos_fwd)
    print(f"\n[樣本外評估指標 (OOS)]")
    print(f"  Spearman Rank IC: {mean_ic:.4f}")
    print(f"  Precision@Top3:   {prec3:.1%}")
    print(f"  Precision@Top5:   {prec5:.1%}")

    # --- production 模型:用全部資料擬合(最即時)---
    Xtr_s, _, means, stds = _standardize(X, X)
    model = fit_xgb(Xtr_s, y)

    # 決策樹預期值注入與 JSON 導出
    temp_fd, temp_path = tempfile.mkstemp(suffix=".json")
    os.close(temp_fd)
    
    try:
        model.get_booster().dump_model(temp_path, dump_format="json", with_stats=True)
        with open(temp_path, "r", encoding="utf-8") as f:
            trees = json.load(f)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # 為每棵樹節點計算expected_value
    for tree in trees:
        add_expected_values(tree)

    oos_y_binary = (oos_y == 2).astype(int)
    metrics = {
        "pooled_oos_auc": round(auc(oos_y_binary, oos_p), 4),
        "pooled_oos_accuracy": round(float((oos_pred_class == oos_y).mean()), 4),
        "pooled_oos_brier": round(float(np.mean((oos_p - oos_y_binary) ** 2)), 4),
        "base_rate_up": round(float(oos_y_binary.mean()), 4),
        "fold_aucs": fold_aucs,
        "precision_top3": round(prec3, 4),
        "precision_top5": round(prec5, 4),
        "spearman_rank_ic": round(mean_ic, 4),
        "n_train_total": int(len(X)),
        "n_oos_total": int(len(oos_p)),
        "n_symbols": len(set(TRAIN_SYMBOLS)),
    }

    # --- 校準表(池化 OOS)---
    base_rate = float(oos_y_binary.mean())
    if base_rate > 0:
        oos_p_scaled = np.clip(oos_p / base_rate * 0.50, 0.01, 0.99)
    else:
        oos_p_scaled = oos_p

    edges = [0.0, 0.45, 0.50, 0.55, 0.60, 1.01]
    buckets = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (oos_p_scaled >= lo) & (oos_p_scaled < hi)
        cnt = int(mask.sum())
        buckets.append({
            "lo": round(lo, 3), "hi": round(hi, 3), "count": cnt,
            "empirical_up_rate": round(float(oos_y_binary[mask].mean()), 4) if cnt else None,
            "avg_fwd_return": round(float(oos_fwd[mask].mean()), 5) if cnt else None,
        })

    artifact = {
        "name": "claude_xgb_calibrated_v2",
        "horizon_days": HORIZON,
        "feature_order": FEATURE_ORDER,
        "trees": trees,
        "means": {k: round(float(m), 6) for k, m in zip(FEATURE_ORDER, means)},
        "stds": {k: round(float(s), 6) for k, s in zip(FEATURE_ORDER, stds)},
        "calibration_buckets": buckets,
        "metrics": metrics,
        "train_symbols": TRAIN_SYMBOLS,
        "train_window": [START, END],
        "validation": "rolling walk-forward,池化 OOS 校準;production 以全資料擬合, XGBoost 3分類與Saabas預期值",
        "future_knowledge_used": False,
        "note": "XGBoost 3分類多因子樹集成模型;特徵只用截止日以前資料;機率經滾動樣本外校準;附帶Saabas歸因expected_value欄位。",
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
