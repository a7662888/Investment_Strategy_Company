# -*- coding: utf-8 -*-
"""
選股回測驗證(Claude lane / audit-evidence)。

公平比較不同「選股風格」在歷史上的表現,回答:依大盤選股到底有沒有比固定清單/買進持有好?
  * Claude Agent     —— regime 風險感知(空頭/高波動轉守、可現金),用校準模型
  * Codex 風格(代理) —— 純用校準模型機率排序、永遠滿倉
  * Antigravity 風格(代理)—— VCP 波動收縮 + 動能 + 逼近高點,永遠滿倉
  * 固定 5 檔買進持有 —— 2327/2330/2317/2454/2308 等權
  * 全宇宙買進持有   —— 20 檔等權(市場基準)

設計:月度(21 交易日)再平衡;只用 ≤T 資料;含台股近似交易成本(換手 × 0.6%)。
注意:Codex/Antigravity 風格為「**風格代理**」,非其正式程式碼(各在自己 repo);用來對照「風險感知 vs 永遠滿倉」。

用法:python -m company.screener.backtest_selection
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from company.data import single_stock as ss
from company.model.score import score_series
from company.screener.agent_screen import claude_screen
from company.screener.market_screener import NAME_MAP

UNIVERSE = [
    "2330", "2317", "2454", "2308", "2303", "3711", "2002", "1301", "1303", "2412",
    "3045", "2881", "2882", "2891", "2603", "2609", "2615", "2327", "2379", "3034",
]
FIXED5 = ["2327", "2330", "2317", "2454", "2308"]
TOP_N = 5
REBAL = 21          # 交易日
WARMUP = 150        # 暖身(>130 才有特徵)
COST_RATE = 0.006   # 換手往返近似成本
START, END = "2020-01-01", "2026-05-28"


def _load_panel():
    closes, vols = {}, {}
    for sym in UNIVERSE:
        try:
            d = ss.load(sym, START, END)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        days = d.prices.trading_days
        c, v = {}, {}
        for day in days:
            bar = d.prices.bar(sym, day)
            if bar is not None:
                c[day] = float(bar["close"]); v[day] = float(bar["volume"])
        closes[sym] = pd.Series(c); vols[sym] = pd.Series(v)
    cdf = pd.DataFrame(closes).sort_index().ffill()
    vdf = pd.DataFrame(vols).reindex(cdf.index).ffill().fillna(0)
    return cdf, vdf


def _vol(closes: list[float], w: int) -> float:
    if len(closes) < w + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))]
    avg = sum(rets) / len(rets)
    return (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5


# --- 各風格的選股函式:回傳挑選的 symbol list(空 = 現金)---

def select_codex(cands: dict, top_n: int) -> list[str]:
    scored = []
    for sym, c in cands.items():
        ev = score_series(c["closes"], c["volumes"])
        if ev:
            scored.append((ev["probability_up"], sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:top_n]]


def select_antigravity(cands: dict, top_n: int) -> list[str]:
    scored = []
    for sym, c in cands.items():
        closes = c["closes"]
        if len(closes) < 70:
            continue
        mom60 = closes[-1] / closes[-61] - 1
        v10, v60 = _vol(closes, 10), _vol(closes, 60)
        vcp = 1.0 if (v60 > 0 and v10 < v60) else 0.0
        near_high = closes[-1] / max(closes[-20:]) - 1  # 逼近 20 日高點(<=0)
        score = mom60 * 2.0 + vcp * 0.3 + near_high
        scored.append((score, sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:top_n]]


def select_claude(cands: dict, top_n: int) -> list[str]:
    res = claude_screen(cands, top_n=top_n, names=NAME_MAP)
    return [p["symbol"] for p in res["picks"]]


def _cands_upto(cdf, vdf, date) -> dict:
    out = {}
    for sym in cdf.columns:
        cs = cdf[sym].loc[:date].dropna().tolist()
        vs = vdf[sym].loc[:date].dropna().tolist()
        if len(cs) >= 130:
            out[sym] = {"closes": cs, "volumes": vs}
    return out


def _metrics(equity: pd.Series, periods_per_year: float) -> dict:
    rets = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = max(len(equity) / periods_per_year, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    roll = equity.cummax()
    mdd = float((equity / roll - 1).min())
    sharpe = float(rets.mean() / rets.std() * (periods_per_year ** 0.5)) if rets.std() else 0.0
    return {"total_return": total, "cagr": float(cagr), "mdd": mdd, "sharpe": sharpe}


def run():
    print("[B] 載入 20 檔快取資料 …")
    cdf, vdf = _load_panel()
    dates = cdf.index
    rebal_idx = list(range(WARMUP, len(dates) - 1, REBAL))
    rebal_dates = [dates[i] for i in rebal_idx]
    print(f"  再平衡點 {len(rebal_dates)} 個({rebal_dates[0].date()} ~ {rebal_dates[-1].date()})")

    strategies = {
        "Claude Agent(風險感知)": select_claude,
        "Codex 風格(機率排序滿倉)": select_codex,
        "Antigravity 風格(VCP突破滿倉)": select_antigravity,
    }
    # 動態策略
    equities, in_market = {}, {}
    prev_picks = {k: set() for k in strategies}
    for name in strategies:
        equities[name] = [1.0]; in_market[name] = 0

    # 買進持有基準
    fixed_eq = [1.0]; uni_eq = [1.0]

    for j in range(len(rebal_dates) - 1):
        t0, t1 = rebal_dates[j], rebal_dates[j + 1]
        cands = _cands_upto(cdf, vdf, t0)
        # 各動態策略
        for name, fn in strategies.items():
            picks = fn(cands, TOP_N)
            if picks:
                ret = sum(cdf[s].loc[t1] / cdf[s].loc[t0] - 1 for s in picks) / len(picks)
                in_market[name] += 1
            else:
                ret = 0.0
            new = set(picks) - prev_picks[name]
            turnover = len(new) / max(len(picks), 1)
            ret -= COST_RATE * turnover
            prev_picks[name] = set(picks)
            equities[name].append(equities[name][-1] * (1 + ret))
        # 固定 5 檔等權
        fr = sum(cdf[s].loc[t1] / cdf[s].loc[t0] - 1 for s in FIXED5) / len(FIXED5)
        fixed_eq.append(fixed_eq[-1] * (1 + fr))
        # 全宇宙等權
        ur = sum(cdf[s].loc[t1] / cdf[s].loc[t0] - 1 for s in cdf.columns) / len(cdf.columns)
        uni_eq.append(uni_eq[-1] * (1 + ur))

    idx = pd.DatetimeIndex(rebal_dates[: len(fixed_eq)])
    ppy = 252 / REBAL
    rows = {}
    for name in strategies:
        eq = pd.Series(equities[name], index=idx)
        m = _metrics(eq, ppy)
        m["time_in_market"] = in_market[name] / (len(rebal_dates) - 1)
        rows[name] = m
    rows["固定5檔買進持有"] = {**_metrics(pd.Series(fixed_eq, index=idx), ppy), "time_in_market": 1.0}
    rows["全宇宙20檔買進持有"] = {**_metrics(pd.Series(uni_eq, index=idx), ppy), "time_in_market": 1.0}

    # 報告
    lines = ["# 選股回測驗證 — 三風格 vs 固定/買進持有", "",
             f"- 期間:{rebal_dates[0].date()} ~ {rebal_dates[-1].date()};月度再平衡;含換手成本 {COST_RATE:.1%}",
             f"- 股池 {len(cdf.columns)} 檔;每次選 {TOP_N} 檔;只用 ≤T 資料(無未來函數)。",
             "- ⚠️ Codex/Antigravity 為**風格代理**(非其正式程式碼),用來對照『永遠滿倉 vs Claude 風險感知』。", "",
             "| 策略 | 總報酬 | 年化 | 最大回撤 | Sharpe | 在場時間 |",
             "|---|---|---|---|---|---|"]
    order = list(strategies) + ["固定5檔買進持有", "全宇宙20檔買進持有"]
    for name in order:
        m = rows[name]
        lines.append(f"| {name} | {m['total_return']:+.1%} | {m['cagr']:+.1%} | "
                     f"{m['mdd']:.1%} | {m['sharpe']:.2f} | {m['time_in_market']:.0%} |")
    best_ret = max(order, key=lambda n: rows[n]["total_return"])
    best_dd = max(order, key=lambda n: rows[n]["mdd"])  # mdd 為負,最大=最小回撤
    best_sharpe = max(order, key=lambda n: rows[n]["sharpe"])
    lines += ["", "## 判讀(誠實)", "",
              f"- 報酬最高:**{best_ret}**({rows[best_ret]['total_return']:+.1%})。",
              f"- 回撤最小:**{best_dd}**({rows[best_dd]['mdd']:.1%})。",
              f"- 風險調整後最佳(Sharpe):**{best_sharpe}**({rows[best_sharpe]['sharpe']:.2f})。",
              "- Claude Agent 的賣點是**風險感知(回撤較小、空頭轉守)**,不是衝最高報酬;",
              "  若它在 Sharpe/回撤勝出、但總報酬輸給滿倉/買進持有,屬預期(犧牲部分多頭報酬換抗跌)。",
              "- 若**買進持有就贏過所有選股**,代表此股池/期間選股難加值,應如實告知使用者、不過度行銷選股功能。"]
    out = ROOT / "reports" / "selection_backtest.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== 回測結果 ===")
    print(f"{'策略':<28}{'總報酬':>9}{'年化':>8}{'回撤':>8}{'Sharpe':>8}{'在場':>6}")
    for name in order:
        m = rows[name]
        print(f"{name:<28}{m['total_return']:>+8.1%}{m['cagr']:>+8.1%}{m['mdd']:>8.1%}{m['sharpe']:>8.2f}{m['time_in_market']:>6.0%}")
    print(f"\n報酬最高:{best_ret} | 回撤最小:{best_dd} | Sharpe 最佳:{best_sharpe}")
    print(f"報告:{out}")


if __name__ == "__main__":
    run()
