# -*- coding: utf-8 -*-
"""
市場感知選股 主入口 —— 依「今天大盤」regime 從股池選出潛力股(含理由)。

用法:
    python run_screener.py                       # 預設股池,選到最新交易日
    python run_screener.py --top 8 --end 2026-05-27
    python run_screener.py --symbols 2330,2317,2454

輸出 reports/screener_<date>.md
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
from company.screener.market_screener import screen

# 跨產業候選股池(已快取者秒出;新標的會即時抓)
DEFAULT_UNIVERSE = [
    "2330", "2317", "2454", "2308", "2303", "3711", "2002", "1301", "1303", "2412",
    "3045", "2881", "2882", "2891", "2603", "2609", "2615", "2327", "2379", "3034",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="逗號分隔股票代號")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    universe = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_UNIVERSE
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    print(f"[B] 載入 {len(universe)} 檔候選股池 …")
    datasets = {}
    for sym in universe:
        try:
            datasets[sym] = ss.load(sym, "2020-01-01", str(end.date()))
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
    if not datasets:
        raise SystemExit("無可用資料")

    # 對齊到實際最新交易日
    any_data = next(iter(datasets.values()))
    days = any_data.prices.trading_days
    as_of = days[days <= end][-1]

    print(f"[Agent] 讀今日大盤 → 選潛力股(截至 {as_of.date()})…")
    result = screen(datasets, as_of, top_n=args.top)
    ctx = result["context"]

    lines = [f"# 今日選股 — {ctx['as_of']}", ""]
    lines.append(f"## 大盤狀態(Agent 判讀)")
    lines.append(f"- regime:**{ctx['regime_label']}**（{ctx['regime']}）")
    lines.append(f"- 市場廣度(站上 20 日均線比例):{ctx['breadth_above_ma20']:.0%}")
    lines.append(f"- 指數 20 日動能:{ctx['index_momentum_20']:+.1%}")
    lines.append(f"- 操作基調:{ctx['stance']}")
    lines.append(f"- 篩選門檻:模型機率 ≥ {result['policy']['min_prob']}%、最多選 {result['policy']['max_picks']} 檔")
    lines.append("")
    lines.append(f"## 潛力股(掃 {result['candidates_scored']} 檔 → {result['qualified']} 檔過門檻 → 選 {len(result['picks'])} 檔)")
    if result["note"]:
        lines.append(f"\n> {result['note']}")
    lines.append("")
    for i, p in enumerate(result["picks"], 1):
        lines.append(f"### {i}. {p['name']}（{p['symbol']}）｜收 {p['close']}｜偏多機率 {p['probability_up']:.0f}%")
        lines.append(f"- 篩選分數 {p['screen_score']}｜20日動能 {p['momentum_20']:+.1%}｜波動 {p['volatility_20']:.1%}")
        lines.append(f"- 理由:{p.get('why_selected','')}")
        lines.append("")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / f"screener_{ctx['as_of']}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    # 終端摘要
    print(f"\n大盤:{ctx['regime_label']} | 廣度 {ctx['breadth_above_ma20']:.0%} | 指數20日動能 {ctx['index_momentum_20']:+.1%}")
    print(f"基調:{ctx['stance']}")
    if result["note"]:
        print(result["note"])
    print(f"\n選出 {len(result['picks'])} 檔潛力股:")
    for i, p in enumerate(result["picks"], 1):
        print(f"  {i}. {p['name']}({p['symbol']}) 收{p['close']} 偏多{p['probability_up']:.0f}% 分數{p['screen_score']}")
        print(f"     {p.get('why_selected','')}")
    print(f"\n報告:{out}")


if __name__ == "__main__":
    main()
