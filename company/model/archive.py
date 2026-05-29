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
