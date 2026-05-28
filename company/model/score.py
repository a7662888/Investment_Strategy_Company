# -*- coding: utf-8 -*-
"""
純標準函式庫的上線評分器。Render 的 app.py 可直接:
    from company.model.score import score_series
    ev = score_series(closes, volumes)

回傳(可附加進現有 model 欄位,additive):
  probability_up        校準前 logistic 機率(%)
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

_DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "model_artifacts" / "logit_v1.json"


@lru_cache(maxsize=4)
def _load(artifact_path: str) -> dict:
    return json.loads(Path(artifact_path).read_text(encoding="utf-8"))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _bucket(prob: float, buckets: list[dict]) -> dict | None:
    for bk in buckets:
        if bk["lo"] <= prob < bk["hi"]:
            return bk
    return buckets[-1] if buckets else None


def score_series(
    closes: list[float], volumes: list[float] | None = None,
    artifact_path: str | None = None,
) -> dict | None:
    feats = extract_features(closes, volumes)
    if feats is None:
        return None
    art = _load(str(artifact_path or _DEFAULT_ARTIFACT))
    w, means, stds = art["weights"], art["means"], art["stds"]

    logit = art["bias"]
    contribs = []
    for k in FEATURE_ORDER:
        z = (feats[k] - means[k]) / (stds[k] or 1.0)
        c = w[k] * z
        logit += c
        contribs.append({
            "feature": k, "label": FEATURE_LABELS.get(k, k),
            "value": round(feats[k], 4), "contribution": round(c, 4),
        })
    prob = _sigmoid(logit)
    contribs.sort(key=lambda x: abs(x["contribution"]), reverse=True)

    reasons = []
    for c in contribs[:4]:
        direction = "偏多" if c["contribution"] > 0 else "偏空"
        reasons.append(f"{c['label']}{direction}(貢獻 {c['contribution']:+.2f})")

    bk = _bucket(prob, art.get("calibration_buckets", []))
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
        "probability_up": round(prob * 100.0, 1),
        "calibrated": calibrated,
        "contributions": contribs,
        "reasons": reasons,
        "evidence": art.get("metrics", {}),
        "horizon_days": art["horizon_days"],
        "future_knowledge_used": False,
        "note": "機率經樣本外校準;理由為各技術因子的標準化貢獻;依據為該機率桶歷史命中率與前向報酬。",
    }
