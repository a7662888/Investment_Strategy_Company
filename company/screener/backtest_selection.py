# -*- coding: utf-8 -*-
"""
選股回測驗證(Claude lane / audit-evidence)—— 每日模擬版,含風險覆蓋 A/B 測試。

回答兩件事:
  1. 依大盤選股到底有沒有比固定清單/買進持有好?
  2. Claude Agent 的「風險感知」(regime 調整曝險 + 移動停損)能不能真的把回撤壓下來?

策略:
  * Claude v1(選股,滿倉無停損)      —— 與 v2 同樣選股,但不做風險覆蓋(對照組)
  * Claude v2(風險感知:曝險縮放+停損) —— 同樣選股 + regime 調整總曝險 + 組合移動停損
  * Codex 風格(代理,機率排序滿倉)
  * Antigravity 風格(代理,VCP突破滿倉)
  * 固定 5 檔買進持有 / 全宇宙 20 檔買進持有(市場基準)

每日模擬(真實回撤,不會被月度取樣低估);只用 ≤T 資料;含換手成本。
⚠️ Codex/Antigravity 為風格代理;股池為手挑大型股(survivor),數字偏高估,非實盤預期。

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
REBAL = 21
WARMUP = 150
COST_RATE = 0.006
START, END = "2020-01-01", "2026-05-28"


def _load_panel():
    closes, vols = {}, {}
    for sym in UNIVERSE:
        try:
            d = ss.load(sym, START, END)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        c, v = {}, {}
        for day in d.prices.trading_days:
            bar = d.prices.bar(sym, day)
            if bar is not None:
                c[day] = float(bar["close"]); v[day] = float(bar["volume"])
        closes[sym] = pd.Series(c); vols[sym] = pd.Series(v)
    cdf = pd.DataFrame(closes).sort_index().ffill()
    vdf = pd.DataFrame(vols).reindex(cdf.index).ffill().fillna(0)
    return cdf, vdf


def _vol(closes, w):
    if len(closes) < w + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))]
    avg = sum(rets) / len(rets)
    return (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5


def select_codex(cands, top_n):
    s = []
    for sym, c in cands.items():
        ev = score_series(c["closes"], c["volumes"])
        if ev:
            s.append((ev["probability_up"], sym))
    s.sort(reverse=True)
    return [x for _, x in s[:top_n]]


def select_antigravity(cands, top_n):
    s = []
    for sym, c in cands.items():
        closes = c["closes"]
        if len(closes) < 70:
            continue
        mom60 = closes[-1] / closes[-61] - 1
        v10, v60 = _vol(closes, 10), _vol(closes, 60)
        vcp = 1.0 if (v60 > 0 and v10 < v60) else 0.0
        near_high = closes[-1] / max(closes[-20:]) - 1
        s.append((mom60 * 2.0 + vcp * 0.3 + near_high, sym))
    s.sort(reverse=True)
    return [x for _, x in s[:top_n]]


def _cands_upto(cdf, vdf, date):
    out = {}
    for sym in cdf.columns:
        cs = cdf[sym].loc[:date].dropna().tolist()
        vs = vdf[sym].loc[:date].dropna().tolist()
        if len(cs) >= 130:
            out[sym] = {"closes": cs, "volumes": vs}
    return out


def _metrics(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = max(len(equity) / 252.0, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    mdd = float((equity / equity.cummax() - 1).min())
    sharpe = float(rets.mean() / rets.std() * (252 ** 0.5)) if rets.std() else 0.0
    return {"total_return": total, "cagr": float(cagr), "mdd": mdd, "sharpe": sharpe}


def _new_state():
    return {"picks": [], "exposure": 1.0, "stop": None, "stopped": False,
            "bfac": 1.0, "bpeak": 1.0, "equity": 1.0, "curve": [], "in_mkt": 0}


def run():
    print("[B] 載入 20 檔快取資料 …")
    cdf, vdf = _load_panel()
    dret = cdf.pct_change().fillna(0.0)
    dates = list(cdf.index)
    rebal_days = {dates[i] for i in range(WARMUP, len(dates) - 1, REBAL)}
    sim_days = dates[WARMUP:]

    names = ["Claude v2(風險感知)", "Claude v1(選股無停損)", "Codex 風格", "Antigravity 風格",
             "固定5檔買進持有", "全宇宙買進持有"]
    st = {n: _new_state() for n in names}
    idx_dates = []

    def set_picks(state, picks, exposure, stop):
        old = set(state["picks"])
        turnover = len(set(picks) - old) / max(len(picks), 1)
        state["equity"] *= (1 - COST_RATE * turnover)  # 換手成本
        state["picks"] = picks
        state["exposure"] = exposure
        state["stop"] = stop
        state["stopped"] = False
        state["bfac"] = 1.0
        state["bpeak"] = 1.0

    for d in sim_days:
        if d in rebal_days:
            cands = _cands_upto(cdf, vdf, d)
            cres = claude_screen(cands, top_n=TOP_N, names=NAME_MAP)
            cpicks = [p["symbol"] for p in cres["picks"]]
            ctx = cres["context"]
            set_picks(st["Claude v2(風險感知)"], cpicks, ctx["target_exposure"], ctx["trail_stop"])
            set_picks(st["Claude v1(選股無停損)"], cpicks, 1.0, None)
            set_picks(st["Codex 風格"], select_codex(cands, TOP_N), 1.0, None)
            set_picks(st["Antigravity 風格"], select_antigravity(cands, TOP_N), 1.0, None)
            set_picks(st["固定5檔買進持有"], FIXED5, 1.0, None)
            set_picks(st["全宇宙買進持有"], list(cdf.columns), 1.0, None)

        idx_dates.append(d)
        for n, s in st.items():
            picks = s["picks"]
            basket_r = float(dret.loc[d, picks].mean()) if picks else 0.0
            # 移動停損:以「投資籃子」回撤觸發(與曝險無關),觸發後該期轉現金
            s["bfac"] *= (1 + basket_r)
            s["bpeak"] = max(s["bpeak"], s["bfac"])
            if s["stop"] is not None and not s["stopped"] and s["bfac"] / s["bpeak"] - 1 <= -s["stop"]:
                s["stopped"] = True
            port_r = 0.0 if (s["stopped"] or not picks) else s["exposure"] * basket_r
            s["equity"] *= (1 + port_r)
            s["curve"].append(s["equity"])
            if picks and not s["stopped"]:
                s["in_mkt"] += 1

    index = pd.DatetimeIndex(idx_dates)
    rows = {}
    for n, s in st.items():
        m = _metrics(pd.Series(s["curve"], index=index))
        m["time_in_market"] = s["in_mkt"] / len(sim_days)
        rows[n] = m

    lines = ["# 選股回測驗證(每日模擬)— 含 Claude 風險覆蓋 A/B", "",
             f"- 期間:{sim_days[0].date()} ~ {sim_days[-1].date()};**每日**模擬(真實回撤);月度選股;換手成本 {COST_RATE:.1%}",
             f"- 股池 {len(cdf.columns)} 檔、每次選 {TOP_N} 檔;只用 ≤T 資料。",
             "- ⚠️ Codex/Antigravity 為風格代理;股池為手挑大型 survivor + 大多頭期,數字高估,非實盤預期。", "",
             "| 策略 | 總報酬 | 年化 | 最大回撤 | Sharpe | 在場時間 |",
             "|---|---|---|---|---|---|"]
    for n in names:
        m = rows[n]
        lines.append(f"| {n} | {m['total_return']:+.0%} | {m['cagr']:+.1%} | {m['mdd']:.1%} | "
                     f"{m['sharpe']:.2f} | {m['time_in_market']:.0%} |")
    v1, v2 = rows["Claude v1(選股無停損)"], rows["Claude v2(風險感知)"]
    lines += ["", "## A/B:風險覆蓋有沒有用(v1 同選股、無停損 vs v2 曝險縮放+停損)", "",
              f"- 最大回撤:v1 {v1['mdd']:.1%} → **v2 {v2['mdd']:.1%}**(回撤{'下降' if v2['mdd'] > v1['mdd'] else '未下降'})。",
              f"- Sharpe:v1 {v1['sharpe']:.2f} → v2 {v2['sharpe']:.2f};總報酬 v1 {v1['total_return']:+.0%} → v2 {v2['total_return']:+.0%}。",
              "", "## 誠實判讀", "",
              "- 若 v2 回撤明顯小於 v1 且 Sharpe 不差 → 風險感知**有效**,Claude Agent 的差異化成立。",
              "- 若選股仍贏不過『全宇宙買進持有』的 Sharpe → 選股的價值在風控與可解釋,而非超額報酬。",
              "- 股池 survivor + 多頭使絕對報酬高估;重點看**相對**(回撤、Sharpe)而非總報酬數字。"]
    out = ROOT / "reports" / "selection_backtest.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== 每日模擬結果 ===")
    print(f"{'策略':<26}{'總報酬':>9}{'年化':>8}{'回撤':>8}{'Sharpe':>8}{'在場':>6}")
    for n in names:
        m = rows[n]
        print(f"{n:<26}{m['total_return']:>+8.0%}{m['cagr']:>+8.1%}{m['mdd']:>8.1%}{m['sharpe']:>8.2f}{m['time_in_market']:>6.0%}")
    print(f"\n[A/B] Claude 回撤 v1 {v1['mdd']:.1%} → v2 {v2['mdd']:.1%};Sharpe {v1['sharpe']:.2f} → {v2['sharpe']:.2f}")
    print(f"報告:{out}")


if __name__ == "__main__":
    run()
