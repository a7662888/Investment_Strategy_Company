# -*- coding: utf-8 -*-
"""
選股回測驗證(Claude lane / audit-evidence)—— 每日模擬版,含風險覆蓋 A/B 測試。

每日模擬(真實回撤,不會被月度取樣低估);只用 ≤T 資料;含換手成本。
"""
from __future__ import annotations

import sys
import math
from pathlib import Path

import numpy as np
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
    foreign_nets, trust_nets = {}, {}
    margin_purchases, short_sales = {}, {}
    revenue_yoys = {}
    
    for sym in UNIVERSE:
        try:
            d = ss.load(sym, START, END)
        except Exception as e:
            print(f"  [略過] {sym}: {e}")
            continue
        c, v = {}, {}
        fn, tn = {}, {}
        mp, ss_val = {}, {}
        ry = {}
        
        # Determine the aligned daily sequences
        for day in d.prices.trading_days:
            bar = d.prices.bar(sym, day)
            if bar is not None:
                c[day] = float(bar["close"])
                v[day] = float(bar["volume"])
                
                # Fetch PIT metrics from view
                view = d.view(day)
                fn[day] = view.foreign_net_daily()
                tn[day] = view.trust_net_daily()
                mp[day] = view.margin_purchase_bal()
                ss_val[day] = view.short_sale_bal()
                ry[day] = view.rev_yoy()
                
        closes[sym] = pd.Series(c)
        vols[sym] = pd.Series(v)
        foreign_nets[sym] = pd.Series(fn)
        trust_nets[sym] = pd.Series(tn)
        margin_purchases[sym] = pd.Series(mp)
        short_sales[sym] = pd.Series(ss_val)
        revenue_yoys[sym] = pd.Series(ry)
        
    cdf = pd.DataFrame(closes).sort_index().ffill()
    vdf = pd.DataFrame(vols).reindex(cdf.index).ffill().fillna(0)
    fndf = pd.DataFrame(foreign_nets).reindex(cdf.index).ffill().fillna(0)
    tndf = pd.DataFrame(trust_nets).reindex(cdf.index).ffill().fillna(0)
    mpdf = pd.DataFrame(margin_purchases).reindex(cdf.index).ffill().fillna(0)
    ssdf = pd.DataFrame(short_sales).reindex(cdf.index).ffill().fillna(0)
    rydf = pd.DataFrame(revenue_yoys).reindex(cdf.index).ffill().fillna(0)
    
    return cdf, vdf, fndf, tndf, mpdf, ssdf, rydf


def _vol(closes, w):
    if len(closes) < w + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))]
    avg = sum(rets) / len(rets)
    return (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5


def select_codex(cands, top_n):
    s = []
    for sym, c in cands.items():
        ev = score_series(
            c["closes"], c["volumes"],
            symbol=sym,
            dates=c["dates"],
            foreign_net_buy=c["foreign_net_buy"],
            trust_net_buy=c["trust_net_buy"],
            margin_purchase=c["margin_purchase"],
            short_sale=c["short_sale"],
            revenue_yoy=c["revenue_yoy"]
        )
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


def _cands_upto(cdf, vdf, fndf, tndf, mpdf, ssdf, rydf, date):
    out = {}
    for sym in cdf.columns:
        cs = cdf[sym].loc[:date].dropna().tolist()
        vs = vdf[sym].loc[:date].dropna().tolist()
        fn = fndf[sym].loc[:date].dropna().tolist()
        tn = tndf[sym].loc[:date].dropna().tolist()
        mp = mpdf[sym].loc[:date].dropna().tolist()
        ss_val = ssdf[sym].loc[:date].dropna().tolist()
        ry = rydf[sym].loc[:date].dropna().tolist()
        dates_list = [d.strftime("%Y-%m-%d") for d in cdf.index[cdf.index <= date]]
        
        if len(cs) >= 130:
            out[sym] = {
                "closes": cs, "volumes": vs,
                "foreign_net_buy": fn, "trust_net_buy": tn,
                "margin_purchase": mp, "short_sale": ss_val,
                "revenue_yoy": ry, "dates": dates_list[-len(cs):]
            }
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
    print("[B] 載入 20 檔快取資料與籌碼面指標 …")
    cdf, vdf, fndf, tndf, mpdf, ssdf, rydf = _load_panel()
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

    codex_p3_list, codex_p5_list, codex_ic_list = [], [], []

    for d in sim_days:
        if d in rebal_days:
            cands = _cands_upto(cdf, vdf, fndf, tndf, mpdf, ssdf, rydf, d)
            cres = claude_screen(cands, top_n=TOP_N, names=NAME_MAP)
            cpicks = [p["symbol"] for p in cres["picks"]]
            ctx = cres["context"]
            set_picks(st["Claude v2(風險感知)"], cpicks, ctx["target_exposure"], ctx["trail_stop"])
            set_picks(st["Claude v1(選股無停損)"], cpicks, 1.0, None)
            
            # Predict for all candidates using Codex model to compute IC and TopK
            codex_picks = select_codex(cands, TOP_N)
            set_picks(st["Codex 風格"], codex_picks, 1.0, None)
            set_picks(st["Antigravity 風格"], select_antigravity(cands, TOP_N), 1.0, None)
            set_picks(st["固定5檔買進持有"], FIXED5, 1.0, None)
            set_picks(st["全宇宙買進持有"], list(cdf.columns), 1.0, None)

            # 計算樣本外預測指標 (Spearman IC & Precision@TopK)
            sorted_rebal = sorted(list(rebal_days))
            idx_rebal = sorted_rebal.index(d)
            if idx_rebal < len(sorted_rebal) - 1:
                next_rebal = sorted_rebal[idx_rebal + 1]
                codex_probs = []
                codex_fwds = []
                
                for sym, c in cands.items():
                    ev = score_series(
                        c["closes"], c["volumes"],
                        symbol=sym,
                        dates=c["dates"],
                        foreign_net_buy=c["foreign_net_buy"],
                        trust_net_buy=c["trust_net_buy"],
                        margin_purchase=c["margin_purchase"],
                        short_sale=c["short_sale"],
                        revenue_yoy=c["revenue_yoy"]
                    )
                    if ev and sym in cdf.columns:
                        codex_probs.append(ev["probability_up"])
                        ret_val = float(cdf.loc[next_rebal, sym] / cdf.loc[d, sym] - 1.0)
                        codex_fwds.append(ret_val)
                        
                if len(codex_probs) >= 5:
                    probs_arr = np.array(codex_probs)
                    fwds_arr = np.array(codex_fwds)
                    order = np.argsort(probs_arr)[::-1]
                    
                    p3 = float((fwds_arr[order[:3]] > 0).mean())
                    codex_p3_list.append(p3)
                    
                    p5 = float((fwds_arr[order[:5]] > 0).mean())
                    codex_p5_list.append(p5)
                    
                    s1 = pd.Series(probs_arr)
                    s2 = pd.Series(fwds_arr)
                    ic = s1.corr(s2, method="spearman")
                    if not np.isnan(ic):
                        codex_ic_list.append(ic)

        idx_dates.append(d)
        for n, s in st.items():
            picks = s["picks"]
            basket_r = float(dret.loc[d, picks].mean()) if picks else 0.0
            # 移動停損
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

    mean_p3 = np.mean(codex_p3_list) if codex_p3_list else 0.0
    mean_p5 = np.mean(codex_p5_list) if codex_p5_list else 0.0
    mean_ic = np.mean(codex_ic_list) if codex_ic_list else 0.0

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
    
    lines += ["", "## 樣本外模型預測指標 (OOS Metrics)", "",
              f"- **Spearman Rank IC**: {mean_ic:.4f} (預測得分與未來21日再平衡區間報酬之相關係數)",
              f"- **Precision@Top3**: {mean_p3:.1%} (預測前 3 名在未來再平衡區間實際上漲的概率)",
              f"- **Precision@Top5**: {mean_p5:.1%} (預測前 5 名在未來再平衡區間實際上漲的概率)",
              "", "## A/B:風險覆蓋有沒有用(v1 同選股、無停損 vs v2 曝險縮放+停損)", "",
              f"- 最大回撤:v1 {v1['mdd']:.1%} → **v2 {v2['mdd']:.1%}**(回撤{'下降' if v2['mdd'] > v1['mdd'] else '未下降'})。",
              f"- Sharpe:v1 {v1['sharpe']:.2f} → v2 {v2['sharpe']:.2f};總報酬 v1 {v1['total_return']:+.0%} → v2 {v2['total_return']:+.0%}。",
              "", "## 誠實判讀", "",
              "- 若 v2 回撤明顯小於 v1 且 Sharpe 不差 → 風險感知**有效**,Claude Agent 的差異化成立。",
              "- 若選股仍贏不過『全宇宙買進持有』的 Sharpe → 選股的價值在風控與可解釋,而非超額報酬。",
              "- 股池 survivor + 多頭使絕對報酬高估;重點看**相對**(回撤、Sharpe)而非總報酬數字。"]
              
    out = ROOT / "reports" / "selection_backtest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== 每日模擬結果 ===")
    print(f"{'策略':<26}{'總報酬':>9}{'年化':>8}{'回撤':>8}{'Sharpe':>8}{'在場':>6}")
    for n in names:
        m = rows[n]
        print(f"{n:<26}{m['total_return']:>+8.0%}{m['cagr']:>+8.1%}{m['mdd']:>8.1%}{m['sharpe']:>8.2f}{m['time_in_market']:>6.0%}")
    print(f"\n[A/B] Claude 回撤 v1 {v1['mdd']:.1%} → v2 {v2['mdd']:.1%};Sharpe {v1['sharpe']:.2f} → {v2['sharpe']:.2f}")
    print(f"\n[OOS Metrics] Rank IC: {mean_ic:.4f} | Prec@Top3: {mean_p3:.1%} | Prec@Top5: {mean_p5:.1%}")
    print(f"報告:{out}")


if __name__ == "__main__":
    run()
