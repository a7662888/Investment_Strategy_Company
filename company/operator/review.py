# -*- coding: utf-8 -*-
"""
D 定期復盤(月/季)+ 學習回饋 —— 「強化操盤手」的核心。

從操盤日誌與交易紀錄,用確定性規則偵測操盤錯誤,產出復盤報告:
  * 月損益、在場時間、勝率
  * 追高進場 / 殺低出場 / whipsaw 來回巴 / 錯過大波段
  * 最佳最差月份
最後附「給 Claude(D)的萃取教訓 hook」:由 LLM 把錯誤模式轉成具體規則調整建議,
但任何調整都須在 walk-forward 樣本外重新驗證(防線③),不可在歷史上反覆挑參數。
"""
from __future__ import annotations

import pandas as pd

from ..audit import attribution as ATTR
from ..audit import metrics as M
from .journal import JournalResult


def _monthly_returns(equity: pd.Series) -> pd.Series:
    m = equity.resample("ME").last()
    return m.pct_change().dropna()


def _detect_mistakes(journal: pd.DataFrame) -> dict:
    close = journal["close"]
    roll_max = close.rolling(60, min_periods=20).max()
    roll_min = close.rolling(60, min_periods=20).min()

    chase_high, sell_low = [], []
    for t, row in journal.iterrows():
        if row["action"] in ("進場", "加碼") and not pd.isna(roll_max.loc[t]):
            if close.loc[t] >= 0.95 * roll_max.loc[t]:
                chase_high.append((t, close.loc[t]))
        if row["action"] == "出場" and not pd.isna(roll_min.loc[t]):
            if close.loc[t] <= 1.05 * roll_min.loc[t]:
                sell_low.append((t, close.loc[t]))

    # 錯過大波段:該月個股漲 >15% 但平均曝險 <0.3
    missed = []
    for period, g in journal.groupby(journal.index.to_period("M")):
        stock_ret = g["close"].iloc[-1] / g["close"].iloc[0] - 1
        avg_exp = g["exposure"].mean()
        if stock_ret > 0.15 and avg_exp < 0.3:
            missed.append((str(period), stock_ret, avg_exp))

    return {"chase_high": chase_high, "sell_low": sell_low, "missed": missed}


def _whipsaws(trades: list) -> list:
    """來回巴:持有 <10 天且虧損的完整回合。"""
    from collections import defaultdict
    pos, opened = defaultdict(float), {}
    wp = []
    for t in sorted(trades, key=lambda x: x.date):
        if t.side == "buy":
            if pos[t.symbol] <= 1e-9:
                opened[t.symbol] = t.date
            pos[t.symbol] += t.shares
        else:
            pos[t.symbol] = max(0.0, pos[t.symbol] - t.shares)
            if pos[t.symbol] <= 1e-9 and t.symbol in opened:
                held = (t.date - opened[t.symbol]).days
                if held < 10 and t.pnl < 0:
                    wp.append((t.date, held, t.pnl))
    return wp


def review_report(jr: JournalResult, symbol: str, freq: str = "ME") -> str:
    journal, result = jr.journal, jr.result
    m = M.compute(result)
    mistakes = _detect_mistakes(journal)
    wp = _whipsaws(result.trades)
    attr = ATTR.analyze(result.trades)
    time_in_market = (journal["exposure"] > 0).mean()
    mret = _monthly_returns(result.equity)

    lines = [f"# {jr.name} 操盤復盤報告 — {symbol}", ""]
    lines.append("## 一、總體表現")
    lines.append(
        f"- 總報酬 {m['total_return']:+.1%} | CAGR {m['cagr']:+.1%} | Sharpe {m['sharpe']:.2f} | "
        f"MDD {m['max_drawdown']:.1%} | Calmar {m['calmar']:.2f}"
    )
    lines.append(
        f"- 在場時間 {time_in_market:.0%} | 交易回合 {attr.get('n_round_trips', 0)} | "
        f"勝率 {m['win_rate']:.0%} | 成本拖累 {m['cost_drag']:.1%}"
    )
    if result.breaker_trips:
        lines.append(f"- 🛑 熔斷觸發 {result.breaker_trips} 次、持現金 {result.breaker_halted_days} 天")

    lines.append("")
    lines.append("## 二、操盤錯誤偵測(學習重點)")
    ch, sl, ms = mistakes["chase_high"], mistakes["sell_low"], mistakes["missed"]
    lines.append(f"- **追高進場**:{len(ch)} 次(進場時已接近 60 日高點)")
    if ch:
        lines.append("  - 例:" + "、".join(f"{d.date()}@{p:.0f}" for d, p in ch[:5]))
    lines.append(f"- **殺低出場**:{len(sl)} 次(出場時已接近 60 日低點)")
    if sl:
        lines.append("  - 例:" + "、".join(f"{d.date()}@{p:.0f}" for d, p in sl[:5]))
    lines.append(f"- **whipsaw 來回巴**:{len(wp)} 次(持有<10 天且虧損)")
    if wp:
        lines.append("  - 例:" + "、".join(f"{d.date()}(持{h}天賠{p:,.0f})" for d, h, p in wp[:5]))
    lines.append(f"- **錯過大波段**:{len(ms)} 個月(個股月漲>15% 但平均曝險<30%)")
    if ms:
        lines.append("  - 例:" + "、".join(f"{p}(漲{r:.0%}/曝險{e:.0%})" for p, r, e in ms[:5]))

    lines.append("")
    lines.append("## 三、月報酬(近 12 個月)")
    lines.append("")
    lines.append("| 月份 | 報酬 |")
    lines.append("|---|---|")
    for d, r in mret.tail(12).items():
        lines.append(f"| {d.strftime('%Y-%m')} | {r:+.1%} |")
    if len(mret):
        best, worst = mret.idxmax(), mret.idxmin()
        lines.append("")
        lines.append(f"- 最佳月:{best.strftime('%Y-%m')} {mret.max():+.1%} / "
                     f"最差月:{worst.strftime('%Y-%m')} {mret.min():+.1%}")

    lines.append("")
    lines.append("## 四、給 Claude(D)的萃取教訓 hook")
    lines.append("")
    lines.append(
        "> 請根據上方『操盤錯誤偵測』的數量與案例,診斷此操盤手的系統性弱點"
        "(例:追高多→進場訊號太鈍、錯過大波段多→出場太早/部位太小、whipsaw 多→濾網不足),"
        "提出**具體**的規則/參數調整(如均線參數、停損幅度、進場濾網)。"
        "**鐵律(防線③)**:任何調整只能在訓練期套用,須以 walk-forward 樣本外重新驗證不退步,"
        "嚴禁在這段歷史上反覆調到好看為止。"
    )
    return "\n".join(lines)
