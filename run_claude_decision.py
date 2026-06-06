# -*- coding: utf-8 -*-
"""
Claude 明日決策模型 主入口 — 風險優先 · 分散再平衡 · 控回撤。

用法:
    python run_claude_decision.py
    python run_claude_decision.py --risk BLACK --end 2026-05-28
    python run_claude_decision.py --satellite 0.3 --k 5

輸出 reports/claude_decision_<date>.md,並在終端印出明日決策。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.data import single_stock as ss
from company.strategies.claude_core import claude_decision

DEFAULT_UNIVERSE = [
    "2330", "2317", "2454", "2308", "2303", "3711", "2002", "1301", "1303", "2412",
    "3045", "2881", "2882", "2891", "2603", "2609", "2615", "2327", "2379", "3034",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--risk", default=None, help="覆蓋風險燈號 GREEN/YELLOW/RED/BLACK")
    ap.add_argument("--satellite", type=float, default=0.30)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    universe = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_UNIVERSE
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    print(f"[Claude] 載入 {len(universe)} 檔 …")
    datasets = {}
    for sym in universe:
        try:
            datasets[sym] = ss.load(sym, "2020-01-01", str(end.date()))
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
    if not datasets:
        raise SystemExit("無可用資料")

    any_data = next(iter(datasets.values()))
    days = any_data.prices.trading_days
    as_of = days[days <= end][-1]

    d = claude_decision(datasets, as_of, risk_level=args.risk,
                        satellite_frac=args.satellite, k_satellite=args.k)

    lines = [
        f"# {d['model']} — 明日決策 ({d['as_of']})",
        "",
        f"- **風險狀態**:{d['risk_state']}｜**總曝險**:{d['exposure']*100:.0f}%｜**現金**:{d['cash_pct']*100:.0f}%",
        f"- 參數:Base/Satellite = {int((1-d['params']['satellite_frac'])*100)}/{int(d['params']['satellite_frac']*100)}、選 {d['params']['k_satellite']} 檔衛星、產業上限 {d['params']['sector_cap']}、{d['params']['rebalance']} 再平衡",
        "",
        "## 決策理由",
    ]
    for r in d["rationale"]:
        lines.append(f"- {r}")
    lines += ["", "## 衛星傾斜名單(波動調整動能)", ""]
    for p in d["satellite_picks"]:
        lines.append(f"- {p['name']}（{p['symbol']}，{p['sector']}）")
    lines += ["", "## 目標配置(權重已含曝險縮放)", "", "| 標的 | 產業 | 權重 | 衛星 |", "|---|---|---|---|"]
    for h in d["holdings"]:
        lines.append(f"| {h['name']}（{h['symbol']}） | {h['sector']} | {h['weight']*100:.1f}% | {'★' if h['satellite'] else ''} |")
    lines.append(f"| **現金** | — | **{d['cash_pct']*100:.1f}%** | |")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / f"claude_decision_{d['as_of']}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines))
    print(f"\n報告:{out}")


if __name__ == "__main__":
    main()
