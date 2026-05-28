# -*- coding: utf-8 -*-
"""
能力③ — 單筆交易貢獻度分析(Trade Attribution)。

D 不只看總績效,還要拆解『錢從哪賺、從哪賠』:
  * 各標的累計實現損益(找出抬轎股 vs 拖油瓶)
  * 最佳 / 最差交易
  * 平均賺、平均賠、賺賠比、期望值(每筆)
  * 平均持有天數(由『持平→再持平』的完整回合計算)
讓 D 與 Claude 能針對『少數虧損集中在哪』下精準的優化建議。
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd


def analyze(trades: list) -> dict:
    sells = [t for t in trades if t.side == "sell"]
    if not sells:
        return {"empty": True}

    by_symbol = defaultdict(float)
    for t in sells:
        by_symbol[t.symbol] += t.pnl

    wins = [t.pnl for t in sells if t.pnl > 0]
    losses = [t.pnl for t in sells if t.pnl <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss else float("inf")
    expectancy = sum(t.pnl for t in sells) / len(sells)

    # 平均持有天數:逐標的追蹤,部位由 0 →建倉日;回到 0 →記一回合
    hold_days = []
    pos_by_sym = defaultdict(float)
    open_date = {}
    for t in sorted(trades, key=lambda x: x.date):
        before = pos_by_sym[t.symbol]
        if t.side == "buy":
            if before <= 1e-9:
                open_date[t.symbol] = t.date
            pos_by_sym[t.symbol] = before + t.shares
        else:
            pos_by_sym[t.symbol] = max(0.0, before - t.shares)
            if pos_by_sym[t.symbol] <= 1e-9 and t.symbol in open_date:
                hold_days.append((t.date - open_date[t.symbol]).days)
    avg_hold = sum(hold_days) / len(hold_days) if hold_days else 0.0

    best = max(sells, key=lambda t: t.pnl)
    worst = min(sells, key=lambda t: t.pnl)

    return {
        "empty": False,
        "by_symbol": dict(by_symbol),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss_ratio,
        "expectancy": expectancy,
        "avg_hold_days": avg_hold,
        "best": (best.symbol, best.pnl, best.date),
        "worst": (worst.symbol, worst.pnl, worst.date),
        "n_round_trips": len(hold_days),
    }


def to_markdown(name: str, a: dict, top: int = 5) -> str:
    if a.get("empty"):
        return f"### {name} 交易貢獻度\n- (無已實現交易,略)"
    lines = [f"### {name} 交易貢獻度", ""]
    lines.append(
        f"- 平均賺 {a['avg_win']:,.0f} / 平均賠 {a['avg_loss']:,.0f} / "
        f"賺賠比 {a['win_loss_ratio']:.2f} / 每筆期望值 {a['expectancy']:,.0f}"
    )
    lines.append(f"- 平均持有天數:{a['avg_hold_days']:.0f} 天({a['n_round_trips']} 個完整回合)")
    lines.append(f"- 最佳交易:{a['best'][0]} 賺 {a['best'][1]:,.0f}(平倉 {a['best'][2].date()})")
    lines.append(f"- 最差交易:{a['worst'][0]} 賠 {a['worst'][1]:,.0f}(平倉 {a['worst'][2].date()})")

    ranked = sorted(a["by_symbol"].items(), key=lambda kv: kv[1], reverse=True)
    lines.append("")
    lines.append(f"**貢獻前 {top} 名 / 拖累後 {top} 名(累計實現損益):**")
    lines.append("")
    lines.append("| 抬轎股 | 損益 | | 拖油瓶 | 損益 |")
    lines.append("|---|---|---|---|---|")
    winners = ranked[:top]
    losers = ranked[-top:][::-1]
    for i in range(max(len(winners), len(losers))):
        wc = f"{winners[i][0]} | {winners[i][1]:,.0f}" if i < len(winners) else " | "
        lc = f"{losers[i][0]} | {losers[i][1]:,.0f}" if i < len(losers) else " | "
        lines.append(f"| {wc} | | {lc} |")
    return "\n".join(lines)
