# -*- coding: utf-8 -*-
"""
可投資母池更新（規則式，預設 200 檔）。正式決策直接掃描母池，不再使用週選30作決策來源。

用法:
    python run_universe_refresh.py pool              # 月更母池（唯一正式模式）
    python run_universe_refresh.py pool 2026-05-28   # 指定 as_of
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
        print(f"    {rk:>3}. {s['symbol']} {s['name']} ({s['sector']}) 日均成交額 {s['avg_turnover']:,.0f}{comp}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pool"
    as_of = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"[Universe] mode={mode} as_of={as_of or '今天'}")

    if mode != "pool":
        raise SystemExit("週選30與三方競賽已退役；唯一支援模式為 pool")
    res = u.refresh_all(as_of=as_of)
    _summary(res["pool"], "母池(月)")
    pool = res["pool"]
    print(f"\n  候選來源 {pool.get('candidate_source')}｜符合範圍 {pool.get('eligible_universe')} 檔｜"
          f"逐檔評估 {pool.get('candidates_evaluated')} 檔｜單日成交額替代 {pool.get('turnover_single_day_fallback')} 檔")
    print(f"  新增 {len(pool.get('added') or [])} 檔、剔除 {len(pool.get('removed') or [])} 檔：{pool.get('removed')}")
    if pool["n"] < 150 and pool.get("candidate_source") == "official":
        raise SystemExit(f"母池只有 {pool['n']} 檔，低於安全下限 150，拒絕覆寫（多半是逐檔資料來源故障）")

    u.save_pool(res["pool"])
    # 同時寫入私有資料庫：repo 檔案要靠部署才生效，而母池 commit 不觸發部署。
    durable = u.save_pool_durable(res["pool"])
    print("持久化:", durable)
    print("\n已寫入 model_artifacts/active_pool.json 與私有資料庫；網站不必等部署即可讀到新母池。")


if __name__ == "__main__":
    main()
