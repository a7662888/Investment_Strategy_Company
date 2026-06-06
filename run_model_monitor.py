# -*- coding: utf-8 -*-
"""
P2-1 模型有效性監控 主入口 — 算「滾動樣本外 AUC」判模型該不該續用於選股排序。

用法:
    python run_model_monitor.py                      # 預設股池、window=120、horizon=5
    python run_model_monitor.py --window 90 --horizon 5 --step 2
    python run_model_monitor.py --symbols 2330,2317,2454 --end 2026-06-05

輸出 reports/model_monitor_<date>.md,並在終端印出結論。
與大盤 regime 完全脫鉤:只看模型自身預測 vs 實現。
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
from company.model.monitor import rolling_oos_auc

# 與 run_screener 一致的跨產業候選(已快取者秒出)
DEFAULT_UNIVERSE = [
    "2330", "2317", "2454", "2308", "2303", "3711", "2002", "1301", "1303", "2412",
    "3045", "2881", "2882", "2891", "2603", "2609", "2615", "2327", "2379", "3034",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="逗號分隔股票代號")
    ap.add_argument("--window", type=int, default=120, help="滾動評估的交易日數")
    ap.add_argument("--horizon", type=int, default=5, help="前向報酬天數(對齊模型 horizon)")
    ap.add_argument("--step", type=int, default=1, help="評估抽樣間隔(2 = 每兩日取一,較快)")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    universe = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_UNIVERSE
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    print(f"[Monitor] 載入 {len(universe)} 檔 … (window={args.window}, horizon={args.horizon}, step={args.step})")
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

    res = rolling_oos_auc(datasets, as_of, window_days=args.window,
                          horizon=args.horizon, step=args.step)

    auc_str = f"{res['auc']:.4f}" if res["auc"] is not None else "N/A"
    lines = [
        f"# 模型有效性監控 (滾動 OOS AUC) — {res['as_of']}",
        "",
        f"- **滾動 OOS AUC**:{auc_str}（門檻 {res['threshold']}）",
        f"- **狀態**:{res['status']}",
        f"- 樣本對數(prob, 實現方向):{res['n_pairs']}",
        f"- 實際上漲比率(base rate):{res['up_rate']}",
        f"- 視窗:{res['window_days']} 交易日｜前向:{res['horizon']} 日｜抽樣 step:{args.step}",
        "",
        f"> {res['verdict']}",
        "",
        "## 各股樣本數",
        "",
    ]
    for sym, n in sorted(res["by_symbol_n"].items(), key=lambda x: -x[1]):
        lines.append(f"- {sym}: {n}")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / f"model_monitor_{res['as_of']}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 56)
    print(f"滾動 OOS AUC = {auc_str}  (門檻 {res['threshold']})  狀態:{res['status']}")
    print(f"樣本 {res['n_pairs']} 對｜實際上漲率 {res['up_rate']}")
    print(res["verdict"])
    print(f"報告:{out}")
    print("=" * 56)


if __name__ == "__main__":
    main()
