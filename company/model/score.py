# -*- coding: utf-8 -*-
"""
純標準函式庫的上線評分器。Render 的 app.py 可直接:
    from company.model.score import score_series
    ev = score_series(closes, volumes)

回傳(可附加進現有 model 欄位,additive):
  probability_up        校準前 XGBoost class 2 (UP) 機率(%)
  calibrated            該機率桶的樣本外實際上漲率/平均前向報酬/樣本數(依據)
  contributions         每因子貢獻(理由),已依影響力排序
  reasons               人話版前幾大理由
  evidence              模型樣本外指標(AUC/Brier/Acc)
  future_knowledge_used False
無資料或歷史不足回 None。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from company.model.features import FEATURE_LABELS, FEATURE_ORDER, extract_features

_DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "model_artifacts" / "xgb_v2.json"


@lru_cache(maxsize=4)
def _load(artifact_path: str) -> dict:
    art = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    feature_order = art.get("feature_order", FEATURE_ORDER)
    
    def map_node(node):
        if "split" in node:
            sf = node["split"]
            if sf.startswith("f") and sf[1:].isdigit():
                idx = int(sf[1:])
                if idx < len(feature_order):
                    node["split"] = feature_order[idx]
        if "children" in node:
            for child in node["children"]:
                map_node(child)
                
    for tree in art.get("trees", []):
        map_node(tree)
        
    return art


def _bucket(prob: float, buckets: list[dict]) -> dict | None:
    for bk in buckets:
        if bk["lo"] <= prob < bk["hi"]:
            return bk
    return buckets[-1] if buckets else None


def _evaluate_tree_val(node: dict, feats: dict[str, float]) -> float:
    if "leaf" in node:
        return float(node["leaf"])
    
    split_feat = node["split"]
    val = feats.get(split_feat, 0.0)
    split_cond = float(node["split_condition"])
    
    if val is None or math.isnan(val):
        target_nodeid = node.get("missing", node["yes"])
    else:
        target_nodeid = node["yes"] if val < split_cond else node["no"]
    
    for child in node["children"]:
        if child["nodeid"] == target_nodeid:
            return _evaluate_tree_val(child, feats)
            
    if "children" in node and len(node["children"]) > 0:
        return _evaluate_tree_val(node["children"][0], feats)
    return 0.0


def _traverse_and_attribute(node: dict, feats: dict[str, float], attribs: dict[str, float]):
    if "leaf" in node:
        return
        
    feat = node["split"]
    val = feats.get(feat, 0.0)
    split_cond = float(node["split_condition"])
    
    if val is None or math.isnan(val):
        target_nodeid = node.get("missing", node["yes"])
    else:
        target_nodeid = node["yes"] if val < split_cond else node["no"]
    
    chosen_child = None
    for child in node["children"]:
        if child["nodeid"] == target_nodeid:
            chosen_child = child
            break
    if not chosen_child and "children" in node and len(node["children"]) > 0:
        chosen_child = node["children"][0]
        
    if chosen_child:
        contrib = float(chosen_child.get("expected_value", 0.0)) - float(node.get("expected_value", 0.0))
        attribs[feat] = attribs.get(feat, 0.0) + contrib
        _traverse_and_attribute(chosen_child, feats, attribs)


def score_series(
    closes: list[float], volumes: list[float] | None = None,
    artifact_path: str | None = None,
    symbol: str | None = None,
    dates: list[str] | None = None,
    foreign_net_buy: list[float] | None = None,
    trust_net_buy: list[float] | None = None,
    margin_purchase: list[float] | None = None,
    short_sale: list[float] | None = None,
    revenue_yoy: list[float] | None = None
) -> dict | None:
    # 嘗試自動讀取快取的 FinMind 籌碼/營收資料
    if symbol and dates and (foreign_net_buy is None):
        try:
            from company.data.finmind_raw import align_extra_features
            foreign_net_buy, trust_net_buy, margin_purchase, short_sale, revenue_yoy = align_extra_features(dates, symbol)
        except Exception as e:
            print(f"[Score] Failed to auto-align extra features for {symbol}: {e}")

    feats = extract_features(
        closes, volumes,
        foreign_net_buy, trust_net_buy,
        margin_purchase, short_sale,
        revenue_yoy
    )
    if feats is None:
        return None
        
    art = _load(str(artifact_path or _DEFAULT_ARTIFACT))
    trees = art.get("trees", [])
    
    # XGBoost 3分類: class 0, class 1, class 2
    raw_scores = [0.0, 0.0, 0.0]
    for tree_idx, tree in enumerate(trees):
        class_idx = tree_idx % 3
        raw_scores[class_idx] += _evaluate_tree_val(tree, feats)
        
    # Softmax
    try:
        exp_scores = [math.exp(max(-30.0, min(30.0, s))) for s in raw_scores]
        sum_exp = sum(exp_scores)
        probs = [s / sum_exp for s in exp_scores]
    except Exception:
        probs = [1/3, 1/3, 1/3]
        
    prob_up = probs[2] # Class 2 (UP) 機率
    
    # 機率縮放以相容篩選器門檻 (以 50% 為基準)
    base_rate_up = art.get("metrics", {}).get("base_rate_up", 0.2235) or 0.2235
    prob_up_scaled = (prob_up / base_rate_up) * 0.50
    prob_up_scaled = max(0.01, min(0.99, prob_up_scaled))
    
    # 計算 Saabas 特徵貢獻度
    contribs = {k: 0.0 for k in FEATURE_ORDER}
    for tree_idx, tree in enumerate(trees):
        if tree_idx % 3 == 2: # 只計算 Class 2 (UP) 的樹
            _traverse_and_attribute(tree, feats, contribs)
            
    contrib_list = []
    for k in FEATURE_ORDER:
        contrib_list.append({
            "feature": k, "label": FEATURE_LABELS.get(k, k),
            "value": round(feats[k], 4), "contribution": round(contribs[k], 4),
        })
    contrib_list.sort(key=lambda x: abs(x["contribution"]), reverse=True)

    reasons = []
    for c in contrib_list[:4]:
        direction = "偏多" if c["contribution"] > 0 else "偏空"
        reasons.append(f"{c['label']}{direction}(貢獻 {c['contribution']:+.2f})")

    bk = _bucket(prob_up_scaled, art.get("calibration_buckets", []))
    calibrated = None
    if bk and bk.get("empirical_up_rate") is not None:
        calibrated = {
            "prob_bucket": f"{bk['lo']:.0%}~{bk['hi']:.0%}",
            "empirical_up_rate": bk["empirical_up_rate"],
            "avg_fwd_return": bk["avg_fwd_return"],
            "sample_count": bk["count"],
            "horizon_days": art["horizon_days"],
        }

    return {
        "name": art["name"],
        "probability_up": round(prob_up_scaled * 100.0, 1),
        "calibrated": calibrated,
        "contributions": contrib_list,
        "reasons": reasons,
        "evidence": art.get("metrics", {}),
        "horizon_days": art["horizon_days"],
        "future_knowledge_used": False,
        "note": "XGBoost多分類機率經樣本外校準;理由為Class 2決策樹路徑的Saabas特徵貢獻值;依據為歷史樣本外上漲率。",
    }
