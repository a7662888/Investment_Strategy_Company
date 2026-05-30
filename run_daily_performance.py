# -*- coding: utf-8 -*-
"""
每日(收盤後)計算三家 Agent 前一交易日選股的實現報酬,並累積進
model_artifacts/strategy_archive.json(由 GitHub Action 自動 commit,持久留存)。

daily_performance() 內部已呼叫 archive.append_daily_performance,故本腳本只需呼叫一次。
用法:
    python run_daily_performance.py            # 以今天為 end
    python run_daily_performance.py 2026-05-30 # 指定 end
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app  # 匯入即定義 daily_performance(有 __main__ guard,不會啟動伺服器)
from company.model.archive import summarize


def main():
    end = sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()
    print(f"[DailyPerf] 計算三家昨日績效 end={end} …")
    res = app.daily_performance(end)
    if res.get("error"):
        print(f"  錯誤:{res['error']}")
        return
    print(f"  選股日 {res['pick_date']} → 評估 {res['eval_date']}")
    for a in res.get("agents", []):
        ar = a.get("avg_return")
        print(f"    {a['agent']}: 平均報酬 {ar:+.2%}" if ar is not None else f"    {a['agent']}: 無資料")
    s = summarize()
    print(f"  archive 累積:每日紀錄 {s['n_daily_records']} 筆、訓練 {s['n_manual_training']} 次")
    print("已寫入 model_artifacts/strategy_archive.json — GitHub Action 會自動 commit。")


if __name__ == "__main__":
    main()
