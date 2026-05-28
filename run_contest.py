# -*- coding: utf-8 -*-
"""
公司日常運作主入口 —— 紅藍對抗 + 審計 + PM 配置 + 樣本外驗證,端到端跑完。

用法:
    python run_contest.py                      # 用 config/settings.yaml(預設合成資料)
    python run_contest.py --source finmind     # 改用 FinMind 真實資料(需 FINMIND_TOKEN)

輸出:
    reports/audit_test.md          —— 測試期紅藍對抗審計報告
    reports/audit_walkforward.md   —— 樣本外(walk-forward)誠實績效
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

# 確保中文在 Windows 終端機正確輸出(遵守繁中 UTF-8 規則)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.allocator import regime_pm
from company.audit import metrics as M
from company.audit.auditor import audit_report
from company.data import finmind_adapter, synthetic
from company.sandbox.circuit_breaker import CircuitBreaker
from company.sandbox.costs import TaiwanCostModel
from company.sandbox.engine import BacktestEngine
from company.strategies.momentum_red import MomentumParams, MomentumRed
from company.strategies.value_blue import ValueBlue, ValueParams
from company.validation import cost_stress
from company.validation.walk_forward import run_walk_forward


def load_config() -> dict:
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dataset(cfg: dict, source: str):
    if source == "finmind":
        d = cfg["data"]
        print("[B] 由 FinMind 撈取真實台股資料 …")
        return finmind_adapter.load(d["finmind_symbols"], d["start"], d["end"])
    print("[B] 產生合成資料(離線,內建多頭噴出 + 急跌 regime)…")
    return synthetic.generate(n_symbols=30, start=cfg["data"]["start"], days=720)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synthetic", "finmind"], default=None)
    args = ap.parse_args()

    cfg = load_config()
    source = args.source or cfg["data"]["source"]
    capital = cfg["capital"]["initial"]
    costs = TaiwanCostModel(**{
        "fee_rate": cfg["costs"]["fee_rate"],
        "fee_discount": cfg["costs"]["fee_discount"],
        "min_fee": cfg["costs"]["min_fee"],
        "tax_rate": cfg["costs"]["tax_rate"],
        "slippage_bps": cfg["costs"]["slippage_bps"],
    })

    cb_cfg = cfg.get("circuit_breaker", {})
    breaker = CircuitBreaker(
        enabled=cb_cfg.get("enabled", True),
        halt_drawdown=cb_cfg.get("halt_drawdown", 0.20),
        cooldown_days=cb_cfg.get("cooldown_days", 20),
    )

    dataset = build_dataset(cfg, source)
    engine = BacktestEngine(dataset, costs, capital, circuit_breaker=breaker)

    days = dataset.prices.trading_days
    test_start = pd.Timestamp(cfg["periods"]["validation_end"]) + pd.Timedelta(days=1)
    test_end = days[-1]
    full_start = days[0]

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)

    # === 1) 測試期紅藍對抗(防線① PIT、④ 成本) ===
    print(f"[A] 載入沙盒,測試期 {test_start.date()} ~ {test_end.date()}")
    print("[C-1 vs C-2] 同段資料、同組成本,公平對決 …")
    c1 = engine.run(ValueBlue(ValueParams()), test_start, test_end)
    c2 = engine.run(MomentumRed(MomentumParams()), test_start, test_end)

    # === 2) E:regime 配置綜合基金(防線⑤ + 能力①) ===
    print("[E] 市場 regime 分類 + 配置 C-1 / C-2 …")
    idx = regime_pm.market_index(dataset, test_start, test_end)
    weights = regime_pm.regime_weights(idx)
    blended = regime_pm.blend(c1, c2, weights, capital)
    regime_dist = weights["regime"].value_counts(normalize=True)

    # === 3) D:硬指標 + 審計(防線②、能力②③) ===
    print("[D] 計算硬指標、產生審計報告 …")
    report = audit_report([c1, c2, blended], f"測試期 {test_start.date()}~{test_end.date()}")
    report += "\n\n## 六、市場 regime 分布(能力①)\n\n"
    report += "\n".join(f"- {k}:{v:.0%}" for k, v in regime_dist.items())
    (reports_dir / "audit_test.md").write_text(report, encoding="utf-8")

    # === 3b) 能力④:交易成本加倍壓力測試 ===
    print("[防線④+] 交易成本壓力測試(0.5x~3x)…")
    mults = cfg.get("cost_stress", {}).get("multipliers", [0.5, 1.0, 2.0, 3.0])
    cs_lines = ["# 交易成本壓力測試(能力④)", ""]
    for label, builder in (
        ("C-1 藍軍·價值流", lambda: ValueBlue(ValueParams())),
        ("C-2 紅軍·動能流", lambda: MomentumRed(MomentumParams())),
    ):
        stress = cost_stress.run_cost_stress(
            dataset, costs, builder, test_start, test_end, capital, mults,
            circuit_breaker=breaker,
        )
        cs_lines.append(cost_stress.to_markdown(label, stress))
        cs_lines.append("")
    (reports_dir / "cost_stress.md").write_text("\n".join(cs_lines), encoding="utf-8")

    # === 4) 防線③:walk-forward 樣本外驗證(以動能流為例) ===
    print("[防線③] walk-forward 樣本外驗證 …")
    grid = [
        MomentumParams(top_n=n, lookback=lb, trail_stop=ts).__dict__
        for n in (5, 8) for lb in (40, 60) for ts in (0.12, 0.18)
    ]
    stitched, segs = run_walk_forward(
        dataset, costs,
        build_strategy=lambda p: MomentumRed(MomentumParams(**p)),
        param_grid=grid,
        start=full_start, end=test_end,
        initial_capital=capital,
        train_days=cfg["walk_forward"]["train_days"],
        test_days=cfg["walk_forward"]["test_days"],
        circuit_breaker=breaker,
    )
    wf_m = M.compute(stitched)
    avg_is = sum(s.in_sample_sharpe for s in segs) / len(segs)
    avg_oos = sum(s.out_sample_sharpe for s in segs) / len(segs)

    wf_lines = [
        "# Walk-Forward 樣本外驗證(防線③)", "",
        f"- 區段數:{len(segs)}",
        f"- 平均樣本內 Sharpe:{avg_is:.2f}",
        f"- 平均樣本外 Sharpe:{avg_oos:.2f}",
        f"- 樣本外串接:總報酬 {wf_m['total_return']:.1%}、Sharpe {wf_m['sharpe']:.2f}、MDD {wf_m['max_drawdown']:.1%}",
        "",
        "## 過擬合判讀",
        "- 樣本內遠優於樣本外 → 警告:策略在背歷史答案,不可信。",
        "- 兩者接近 → 策略具一定穩健性。",
        "",
        "## 各區段最佳參數",
    ]
    for s in segs:
        wf_lines.append(
            f"- {s.test_start.date()}~{s.test_end.date()}:IS Sharpe {s.in_sample_sharpe:.2f} / "
            f"OOS Sharpe {s.out_sample_sharpe:.2f} / 參數 {s.best_params}"
        )
    (reports_dir / "audit_walkforward.md").write_text("\n".join(wf_lines), encoding="utf-8")

    # === 終端摘要 ===
    named = {c1.name: M.compute(c1), c2.name: M.compute(c2), blended.name: M.compute(blended)}
    print("\n" + M.to_table(named))
    print(f"\n[能力①] regime 分布:" + "、".join(f"{k} {v:.0%}" for k, v in regime_dist.items()))
    print(f"[能力②] 熔斷:C-1 觸發 {c1.breaker_trips} 次 / C-2 觸發 {c2.breaker_trips} 次")
    print(f"[防線③] 平均樣本內 Sharpe {avg_is:.2f} vs 樣本外 {avg_oos:.2f}")
    print(
        f"\n報告已輸出:\n  - {reports_dir / 'audit_test.md'}"
        f"\n  - {reports_dir / 'audit_walkforward.md'}"
        f"\n  - {reports_dir / 'cost_stress.md'}"
    )


if __name__ == "__main__":
    main()
