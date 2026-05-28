# -*- coding: utf-8 -*-
"""
單股逐日操盤主入口 —— 兩派操盤手在同一檔(預設 2327 國巨)上逐日對決 + D 定期復盤。

用法:
    python run_operator.py                 # 用 config(預設 2327,2020-01~2026-05)
    python run_operator.py --symbol 2330   # 換股

輸出 reports/:
    operator_<sym>_<persona>_journal.csv   逐日操盤日誌
    operator_<sym>_<persona>_review.md     D 定期復盤(含錯誤偵測與學習 hook)
    operator_<sym>_summary.md              兩派對比
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.audit import metrics as M
from company.data import single_stock as ss
from company.operator.journal import JournalEngine
from company.operator.review import review_report
from company.operator.trend import TrendOperator
from company.operator.value_chip import ValueChipOperator
from company.sandbox.circuit_breaker import CircuitBreaker
from company.sandbox.costs import TaiwanCostModel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(ROOT / "config" / "settings.yaml", encoding="utf-8"))
    sym = args.symbol or cfg["operator"]["symbol"]
    start = pd.Timestamp(cfg["operator"]["start"])
    end = pd.Timestamp(cfg["operator"]["end"])
    capital = cfg["capital"]["initial"]
    costs = TaiwanCostModel(**cfg["costs"])
    cb = cfg.get("circuit_breaker", {})
    breaker = CircuitBreaker(
        enabled=cb.get("enabled", True),
        halt_drawdown=cb.get("halt_drawdown", 0.20),
        cooldown_days=cb.get("cooldown_days", 20),
    )

    print(f"[B] 載入 {sym} 真實資料(快取)…")
    data = ss.load(sym, str(start.date()), str(end.date()))
    print(f"[A] 沙盒就緒,逐日操盤 {start.date()} ~ {end.date()}({len(data.prices.trading_days)} 個交易日)")

    engine = JournalEngine(data, costs, capital, circuit_breaker=breaker)
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)

    personas = {
        "trend": TrendOperator(),
        "value": ValueChipOperator(),
    }
    results = {}
    for key, op in personas.items():
        print(f"[操盤手] {op.name} 逐日決策中 …")
        jr = engine.run(op, start, end)
        jr.journal.to_csv(reports / f"operator_{sym}_{key}_journal.csv", encoding="utf-8-sig")
        (reports / f"operator_{sym}_{key}_review.md").write_text(
            review_report(jr, sym), encoding="utf-8"
        )
        results[key] = jr

    # 買進持有基準(buy & hold)
    bh_first = float(data.prices.bar(sym, data.prices.trading_days[
        data.prices.trading_days.searchsorted(start)]).get("open"))
    bh_last = float(data.view(end).close())
    bh_ret = bh_last / bh_first - 1

    # 對比摘要
    lines = [f"# {sym} 兩派操盤手對比 — {start.date()} ~ {end.date()}", ""]
    lines.append(f"買進持有(Buy&Hold)基準報酬:**{bh_ret:+.1%}**")
    lines.append("")
    named = {jr.name: M.compute(jr.result) for jr in results.values()}
    lines.append(M.to_table(named))
    (reports / f"operator_{sym}_summary.md").write_text("\n".join(lines), encoding="utf-8")

    # 終端摘要
    print("\n" + M.to_table(named))
    print(f"\n買進持有基準:{bh_ret:+.1%}")
    for key, jr in results.items():
        mm = M.compute(jr.result)
        print(f"  {jr.name}:報酬 {mm['total_return']:+.1%} | Sharpe {mm['sharpe']:.2f} | "
              f"MDD {mm['max_drawdown']:.1%} | 熔斷 {jr.result.breaker_trips} 次")
    print(f"\n報告已輸出至 {reports}/operator_{sym}_*")


if __name__ == "__main__":
    main()
