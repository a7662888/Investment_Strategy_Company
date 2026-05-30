# -*- coding: utf-8 -*-
"""
股池更新(三層漏斗)。產出需 commit 才能在 Render 持久生效。

用法:
    python run_universe_refresh.py both              # 月更母池100 + 週選30(預設,單次抓取)
    python run_universe_refresh.py monthly           # 只重算母池100(+順帶週選30)
    python run_universe_refresh.py weekly            # 只重算週選30(讀現有母池100,較省)
    python run_universe_refresh.py both 2026-05-28   # 指定 as_of
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.data import universe as u


def _summary(doc, title):
    by_sector = {}
    for s in doc["stocks"]:
        by_sector[s["sector"]] = by_sector.get(s["sector"], 0) + 1
    print(f"\n[{title}] {doc['n']} 檔 · {doc['basis']}")
    print("  產業分布:", ", ".join(f"{k}×{v}" for k, v in sorted(by_sector.items(), key=lambda x: -x[1])))
    print("  前 10:")
    for s in doc["stocks"][:10]:
        rk = s.get("rank", s.get("pool_rank"))
        comp = f" 複合{s['composite']}" if "composite" in s else ""
        print(f"    {rk:>2}. {s['symbol']} {s['name']} ({s['sector']}) 日均成交額 {s['avg_turnover']:,.0f} 動能{s['mom60']:+.1%}{comp}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    as_of = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"[Universe] mode={mode} as_of={as_of or '今天'}")

    if mode == "weekly":
        weekly = u.refresh_weekly_from_pool(as_of=as_of)
        if weekly is None:
            print("  母池不存在,請先跑 monthly/both")
            return
        u.save_active_universe(weekly)
        _summary(weekly, "週選30")
    else:  # both / monthly
        res = u.refresh_all(as_of=as_of)
        u.save_pool(res["pool"])
        u.save_active_universe(res["weekly"])
        _summary(res["pool"], "母池100(月)")
        _summary(res["weekly"], "週選30(供每日推薦)")

    print("\n已寫入 model_artifacts/active_pool.json 與 active_universe.json — 請 commit 以在 Render 持久生效。")


if __name__ == "__main__":
    main()
