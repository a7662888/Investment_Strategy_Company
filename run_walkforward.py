# -*- coding: utf-8 -*-
"""
選股驗證閘門 主入口 — walk-forward 樣本外回測,各策略 vs 等權買進持有。

用法:
    python run_walkforward.py                         # 預設股池, K=5, 月再平衡
    python run_walkforward.py --k 5 --rebal 21 --end 2026-05-28
    python run_walkforward.py --symbols 2330,2317,...

輸出 reports/walkforward_selection_<date>.md,並在終端印出對打表與結論。
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
from company.validation.walkforward_selection import walk_forward

DEFAULT_UNIVERSE = [
    "2330", "2317", "2454", "2308", "2303", "3711", "2002", "1301", "1303", "2412",
    "3045", "2881", "2882", "2891", "2603", "2609", "2615", "2327", "2379", "3034",
]


def _fmt(m):
    def pct(x): return f"{x*100:+.1f}%" if x is not None else "—"
    flag = ""
    if m.get("beats_bh_sharpe") is True:
        flag = " ✅勝"
    elif m.get("beats_bh_sharpe") is False:
        flag = " ❌"
    return (f"{pct(m['total_return']):>9} | {pct(m['cagr']):>8} | {pct(m['max_drawdown']):>8} | "
            f"{m['sharpe']:>5.2f} | 換手{m['avg_turnover']*100:>4.0f}%{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--rebal", type=int, default=21)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    universe = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_UNIVERSE
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    print(f"[WalkForward] 載入 {len(universe)} 檔 …")
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

    res = walk_forward(datasets, as_of, k=args.k, rebal=args.rebal)

    hdr = f"{'策略':<28} | {'總報酬':>9} | {'年化':>8} | {'最大回撤':>8} | {'Sharpe':>5} | 換手/勝負"
    lines = [
        f"# 選股驗證閘門 — walk-forward 樣本外回測 ({res['as_of']})",
        "",
        f"- 期間:{res['period']}｜{res['n_days']} 交易日｜股池 {res['n_symbols']} 檔｜選 {res['k']} 檔｜每 {res['rebal_days']} 日再平衡｜來回成本 {res['round_trip_cost']*100:.1f}%",
        "",
        "> ⚠️ 誠實邊界:股池為大型 survivor,**絕對報酬被高估**。有效推論為**相對**:策略 vs 等權買進持有(同池抽樣,survivor 偏誤大致中和)。「✅勝」= Sharpe 贏過等權買進持有。",
        "",
        "```",
        hdr,
        "-" * len(hdr),
    ]
    # benchmark 先印
    order = sorted(res["results"].items(), key=lambda kv: (not kv[1].get("is_benchmark", False), -kv[1]["sharpe"]))
    for name, m in order:
        lines.append(f"{name:<28} | {_fmt(m)}")
    lines.append("```")
    lines.append("")

    # 結論
    bh = res["results"]["等權買進持有(benchmark)"]
    winners = [n for n, m in res["results"].items()
               if m.get("beats_bh_sharpe") is True]
    lines.append("## 結論")
    lines.append(f"- 等權買進持有 benchmark:Sharpe {bh['sharpe']:.2f}、總報酬 {bh['total_return']*100:+.1f}%、MDD {bh['max_drawdown']*100:+.1f}%")
    if winners:
        lines.append(f"- **通過驗證閘門(Sharpe 贏過 benchmark)**:{', '.join(winners)}")
    else:
        lines.append("- **沒有任何選股策略在扣成本後贏過『等權買進持有』的 Sharpe** → 選股無超額價值,價值應放在風控/分散,而非『選贏家』。")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / f"walkforward_selection_{res['as_of']}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines[5:]))   # 終端印表+結論
    print(f"\n報告:{out}")


if __name__ == "__main__":
    main()
