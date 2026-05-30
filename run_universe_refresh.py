# -*- coding: utf-8 -*-
"""
每月離線重算 60 檔選股名單(依據:近 60 交易日平均成交額 + 跨產業分散)。
產出 model_artifacts/active_universe.json,需 commit 才能在 Render 持久生效。

用法:
    python run_universe_refresh.py                 # 以今天為 as_of
    python run_universe_refresh.py 2026-05-28      # 指定 as_of(回溯)
    python run_universe_refresh.py 2026-05-28 50   # 指定 as_of 與檔數
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.data.universe import select_universe, save_active_universe


def main():
    as_of = sys.argv[1] if len(sys.argv) > 1 else None
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    print(f"[Universe] 重算名單 n={n} as_of={as_of or '今天'} …(依近60日平均成交額,各產業上限分散)")
    data = select_universe(n=n, as_of=as_of)
    save_active_universe(data)
    print(f"  評估 {data['candidates_evaluated']} 檔 → 選出 {data['n']} 檔(as_of {data['as_of']})")
    by_sector = {}
    for s in data["stocks"]:
        by_sector[s["sector"]] = by_sector.get(s["sector"], 0) + 1
    print("  產業分布:", ", ".join(f"{k}×{v}" for k, v in sorted(by_sector.items(), key=lambda x: -x[1])))
    print("  前 10(依成交額):")
    for s in data["stocks"][:10]:
        print(f"    {s['rank']:>2}. {s['symbol']} {s['name']} ({s['sector']}) 日均成交額 {s['avg_turnover']:,.0f}")
    print(f"\n已寫入 model_artifacts/active_universe.json — 請 commit 以在 Render 持久生效。")


if __name__ == "__main__":
    main()
