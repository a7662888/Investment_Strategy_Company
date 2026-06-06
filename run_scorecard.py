# -*- coding: utf-8 -*-
"""
三方策略成效計分 主入口 — 定期比較投資策略成效。

用法:
    python run_scorecard.py

讀 model_artifacts/strategy_archive.json,輸出 reports/strategy_scorecard.md(+終端表)。
比的是風險調整後:累積報酬 / 年化 / Sharpe / 最大回撤 / 勝率。
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.model.scorecard import score_agents


def _p(x, pct=True):
    if x is None:
        return "—"
    return f"{x*100:+.1f}%" if pct else f"{x:.2f}"


def main():
    res = score_agents(str(ROOT / "model_artifacts" / "strategy_archive.json"))
    n = res["n_records"]

    hdr = f"{'策略':<16} | {'累積':>8} | {'年化':>8} | {'Sharpe':>6} | {'最大回撤':>8} | {'勝率':>6} | 天數"
    lines = [
        f"# 三方策略成效計分 — {res['span']}",
        "",
        f"- 樣本:{n} 個交易日的每日績效（archive 持續累積中）",
        "- 排序依 **Sharpe**（風險調整後）；累積報酬僅供參考（survivor/多頭易高估）。",
    ]
    if n < 20:
        lines.append(f"- ⚠️ 樣本僅 {n} 天，Sharpe/MDD 統計意義有限，待累積（建議 ≥ 1~3 個月再下定論）。")
    lines += ["", "```", hdr, "-" * len(hdr)]

    agents = res["agents"]
    ordered = sorted(agents.items(),
                     key=lambda kv: (kv[1]["sharpe"] is None, -(kv[1]["sharpe"] or -999)))
    for name, m in ordered:
        lines.append(
            f"{name:<16} | {_p(m['cum_return']):>8} | {_p(m['cagr']):>8} | "
            f"{_p(m['sharpe'], pct=False):>6} | {_p(m['max_drawdown']):>8} | "
            f"{_p(m['win_rate']):>6} | {m['n_days']}"
        )
    lines.append("```")
    lines += ["", "## 判讀原則", "",
              "- **比 Sharpe 與 MDD，不比誰報酬高**：報酬高常伴隨高回撤，6/6 那種日子就是回撤在咬人。",
              "- Claude 模型賭「扣成本 Sharpe + 控回撤」較佳；Codex/Antigravity 賭動能/突破報酬。",
              "- 樣本足夠後，勝者應同時具備『較高 Sharpe + 較淺 MDD』，而非單純累積報酬第一。"]

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    out = reports / "strategy_scorecard.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n報告:{out}")


if __name__ == "__main__":
    main()
