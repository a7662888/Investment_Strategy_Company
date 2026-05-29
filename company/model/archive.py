# -*- coding: utf-8 -*-
"""
策略存檔與績效歸檔系統 (Strategy Archive System).
紀錄人工區間訓練參數與自動每日績效回顧。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_PATH = ROOT / "model_artifacts" / "strategy_archive.json"


def load_archive() -> dict:
    if not ARCHIVE_PATH.exists():
        # Initialize default structure
        return {
            "manual_training_history": [],
            "daily_performance_history": []
        }
    try:
        return json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "manual_training_history": [],
            "daily_performance_history": []
        }


def save_archive(data: dict) -> None:
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_manual_training(ticker: str, start_date: str, end_date: str, lr: float, l2: float, epochs: int, accuracy: float, weights: dict) -> None:
    archive = load_archive()
    record = {
        "timestamp": datetime.now().isoformat(),
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date,
        "learning_rate": lr,
        "l2_penalty": l2,
        "epochs": epochs,
        "accuracy": accuracy,
        "weights": weights
    }
    archive["manual_training_history"].append(record)
    save_archive(archive)


def append_daily_performance(pick_date: str, eval_date: str, agents_performance: list[dict]) -> None:
    archive = load_archive()
    
    # Avoid duplicate records for the same pick_date/eval_date
    for record in archive["daily_performance_history"]:
        if record["pick_date"] == pick_date and record["eval_date"] == eval_date:
            return  # Skip duplicate
            
    record = {
        "timestamp": datetime.now().isoformat(),
        "pick_date": pick_date,
        "eval_date": eval_date,
        "agents": agents_performance
    }
    archive["daily_performance_history"].append(record)
    save_archive(archive)


# ---------------------------------------------------------------------------
# 更新建議層(Claude lane 補充):把存檔轉成「可更新依據」,對應「作為更新使用」需求。
# 只『建議』不自動改線上權重,確保更新可審;Render ephemeral 故 durable 更新需 commit。
# ---------------------------------------------------------------------------

def summarize(window: int = 20) -> dict:
    """彙總近 window 日各家平均實現報酬與勝率,及訓練累積次數。"""
    archive = load_archive()
    daily = archive.get("daily_performance_history", [])[-window:]
    agg: dict[str, dict] = {}
    for rec in daily:
        for a in rec.get("agents", []):
            name = a.get("agent")
            r = a.get("avg_return")
            if name is None or r is None:
                continue
            d = agg.setdefault(name, {"rets": [], "wins": 0})
            d["rets"].append(r)
            if r > 0:
                d["wins"] += 1
    agents = []
    for name, d in agg.items():
        n = len(d["rets"])
        agents.append({
            "agent": name, "days": n,
            "avg_daily_return": round(sum(d["rets"]) / n, 5) if n else None,
            "win_rate": round(d["wins"] / n, 3) if n else None,
        })
    agents.sort(key=lambda x: (x["avg_daily_return"] if x["avg_daily_return"] is not None else -9), reverse=True)
    return {
        "n_manual_training": len(archive.get("manual_training_history", [])),
        "n_daily_records": len(archive.get("daily_performance_history", [])),
        "window": window,
        "agent_summary": agents,
    }


def propose_update() -> dict:
    """依累積存檔提出『更新建議』(供審閱,不自動套用)。"""
    s = summarize()
    proposals = []
    agents = s["agent_summary"]
    if agents and agents[0]["days"] >= 5:
        best, worst = agents[0], agents[-1]
        proposals.append(
            f"近 {best['days']} 日實現報酬最佳:{best['agent']}(日均 {best['avg_daily_return']:+.2%}、"
            f"勝率 {best['win_rate']:.0%})→ 建議『明日決策中心』預設多取其前 N 檔。"
        )
        if worst["agent"] != best["agent"] and (worst["avg_daily_return"] or 0) < 0:
            proposals.append(
                f"{worst['agent']} 近期日均 {worst['avg_daily_return']:+.2%} 為負 → 建議降權或檢視其門檻。"
            )
    archive = load_archive()
    runs = archive.get("manual_training_history", [])
    if runs:
        accs = [r.get("accuracy") for r in runs if r.get("accuracy") is not None]
        if accs:
            proposals.append(
                f"已累積 {len(runs)} 次人工區間訓練(近一次擬合準確率 {accs[-1]:.1f}%);"
                "下一輪離線重訓可採近期最佳權重為起點並 commit 為新 artifact。"
            )
    if not proposals:
        proposals.append("資料尚不足(需累積數日績效或數次訓練)才能提出可靠更新建議。")
    return {"summary": s, "proposals": proposals,
            "note": "建議僅供審閱;線上權重更新需離線重訓並 commit artifact(Render ephemeral)。"}
