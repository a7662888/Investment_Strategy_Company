# -*- coding: utf-8 -*-
"""
選股「驗證閘門」— walk-forward 樣本外回測,扣成本,對打「等權買進持有」。

為什麼要這支(對齊 Claude 審計立場):
  P2-1 已證明模型機率近期 OOS AUC≈0.47(無 edge)。在改線上選股前,任何新排序規則
  都必須先通過唯一硬標準:**樣本外、扣成本後,贏過「等權買進持有」的 Sharpe**。
  否則只是換一個好看的故事。

誠實邊界(務必與讀者明說):
  - 股池為手上可取得的大型 survivor(20 檔),**絕對報酬被 survivor/多頭高估**,不是實盤預期。
  - 但本回測的**有效推論是「相對」**:同一股池下,策略 vs 等權買進持有的超額/Sharpe 差。
    benchmark 與策略抽自同一池,survivor 偏誤對「相對比較」大致中和 → 排序優劣可信。
  - PIT:每個再平衡日 T 的排序只用 ≤T 資料;報酬用 T→次再平衡的實現價(回測評估,非偷看)。

對外:
  walk_forward(datasets, strategies, ...) -> {strategy_name: metrics, ...}
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from ..model.monitor import _prob_up_at

ANN = 252
DEFAULT_ROUND_TRIP = 0.006   # 台股來回成本估計 ~0.6%(手續費×2 + 證交稅)

# 20 檔大型股池的產業對照(供分散約束策略)
SECTOR = {
    "2330": "半導體", "2454": "半導體", "2303": "半導體", "3711": "半導體",
    "2379": "IC設計", "3034": "IC設計",
    "2317": "電子代工", "2308": "電源", "2002": "鋼鐵",
    "1301": "塑化", "1303": "塑化",
    "2412": "電信", "3045": "電信",
    "2881": "金融", "2882": "金融", "2891": "金融",
    "2603": "航運", "2609": "航運", "2615": "航運",
    "2327": "被動元件",
}


# ---------- 價格面板 ----------
def build_close_matrix(datasets: dict, as_of: pd.Timestamp) -> pd.DataFrame:
    """dates × symbols 收盤矩陣(≤as_of),前向填補(只用過去值,無未來洩漏),去除前段未齊資料。"""
    cols = {}
    for sym, data in datasets.items():
        h = data.prices.history(sym, as_of)
        if len(h):
            cols[sym] = h["close"].astype(float)
    df = pd.DataFrame(cols).sort_index().ffill()
    return df.dropna(how="any")  # 從所有股都有資料起算(20 檔大型股,2020 起齊全)


# ---------- PIT 特徵(排序用) ----------
def _feat_at(closes: pd.Series, t_idx: int, lookback: int = 60):
    """回傳 (mom, vol, vol_adj_mom);closes 為單股收盤序列,t_idx 為當前位置。只用 ≤t。"""
    if t_idx < lookback:
        return None
    window = closes.iloc[t_idx - lookback: t_idx + 1]
    c0, c1 = float(window.iloc[0]), float(window.iloc[-1])
    if c0 <= 0:
        return None
    mom = c1 / c0 - 1.0
    rets = window.pct_change().dropna()
    vol = float(rets.std()) if len(rets) > 1 else 0.0
    vol_adj = mom / (vol + 1e-6)
    return mom, vol, vol_adj


# ---------- 選股策略(回傳 top-K 代號清單) ----------
def _topk(scores: dict, k: int, sector_cap: Optional[int] = None) -> list:
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if sector_cap is None:
        return [s for s, _ in ranked[:k]]
    picks, used = [], {}
    for s, _ in ranked:
        sec = SECTOR.get(s, s)
        if used.get(sec, 0) >= sector_cap:
            continue
        picks.append(s)
        used[sec] = used.get(sec, 0) + 1
        if len(picks) >= k:
            break
    return picks


def make_strategies(k: int = 5):
    """回傳 {名稱: select_fn(close_matrix, t_idx, datasets) -> list[picks]}。"""
    def feats(C, t):
        out = {}
        for sym in C.columns:
            f = _feat_at(C[sym], t)
            if f is not None:
                out[sym] = f
        return out

    def s_mom60(C, t, ds):
        return _topk({s: f[0] for s, f in feats(C, t).items()}, k)

    def s_voladj(C, t, ds):
        return _topk({s: f[2] for s, f in feats(C, t).items()}, k)

    def s_voladj_sector(C, t, ds):
        return _topk({s: f[2] for s, f in feats(C, t).items()}, k, sector_cap=2)

    def s_lowvol(C, t, ds):
        return _topk({s: -f[1] for s, f in feats(C, t).items()}, k)

    def s_model(C, t, ds):
        date = C.index[t]
        scores = {}
        for sym in C.columns:
            p = _prob_up_at(ds[sym], date)
            if p is not None:
                scores[sym] = p
        return _topk(scores, k) if scores else list(C.columns[:k])

    return {
        "模型機率(現況)": s_model,
        "原始60日動能": s_mom60,
        "波動調整動能(Anti②)": s_voladj,
        "波動調整動能+產業分散(Anti②+④)": s_voladj_sector,
        "低波動(對照)": s_lowvol,
    }


# ---------- 權益曲線 ----------
def _equity_curve(C: pd.DataFrame, select_fn: Callable, rebal: int,
                  warmup: int, round_trip: float, buy_hold: bool = False):
    """逐日權益。每 rebal 個交易日依 select_fn 重排(buy_hold=只在起點配置一次)。"""
    dates = C.index
    n = len(dates)
    start = warmup
    weights = {}          # sym -> 目標權重
    shares = {}           # sym -> 股數
    equity = 1.0
    curve = []
    turnover_log = []
    initialized = False

    for t in range(start, n):
        px = C.iloc[t]
        # 先以昨日股數結算今日權益
        if initialized:
            equity = sum(shares[s] * px[s] for s in shares)

        is_rebal = (not initialized) or (not buy_hold and (t - start) % rebal == 0)
        if is_rebal:
            picks = select_fn(C, t, _equity_curve.datasets)
            if not picks:
                picks = list(C.columns[:1])
            w_new = {s: 1.0 / len(picks) for s in picks}
            # 計算單邊換手(與目前漂移後權重比)
            cur_w = {}
            if initialized and equity > 0:
                for s in shares:
                    cur_w[s] = shares[s] * px[s] / equity
            syms = set(w_new) | set(cur_w)
            oneway = sum(abs(w_new.get(s, 0.0) - cur_w.get(s, 0.0)) for s in syms) / 2.0
            equity *= (1.0 - oneway * round_trip)
            turnover_log.append(oneway)
            # 重新配置股數
            shares = {s: equity * w_new[s] / px[s] for s in w_new}
            weights = w_new
            initialized = True

        curve.append(equity)

    eq = pd.Series(curve, index=dates[start:])
    return eq, turnover_log


def _metrics(eq: pd.Series) -> dict:
    rets = eq.pct_change().dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = len(eq) / ANN
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    mdd = float((eq / eq.cummax() - 1.0).min())
    sharpe = float(rets.mean() / rets.std() * (ANN ** 0.5)) if rets.std() > 0 else 0.0
    return {"total_return": total, "cagr": cagr, "max_drawdown": mdd, "sharpe": sharpe}


def walk_forward(datasets: dict, as_of: pd.Timestamp, k: int = 5,
                 rebal: int = 21, warmup: int = 65, round_trip: float = DEFAULT_ROUND_TRIP) -> dict:
    """跑所有策略 + 兩個 benchmark,回傳含『是否贏過等權買進持有』的結論。"""
    C = build_close_matrix(datasets, as_of)
    _equity_curve.datasets = datasets  # 給 select_fn 取用(模型策略需要)

    results = {}

    # Benchmark: 等權買進持有(起點配置一次,之後不動)
    eq_bh, _ = _equity_curve(C, lambda C, t, ds: list(C.columns), rebal, warmup, round_trip, buy_hold=True)
    results["等權買進持有(benchmark)"] = {**_metrics(eq_bh), "avg_turnover": 0.0, "is_benchmark": True}

    # Benchmark: 等權定期再平衡(顯示換手成本拖累)
    eq_ewr, tov = _equity_curve(C, lambda C, t, ds: list(C.columns), rebal, warmup, round_trip)
    results["等權定期再平衡"] = {**_metrics(eq_ewr), "avg_turnover": (sum(tov) / len(tov) if tov else 0.0)}

    # 各選股策略
    for name, fn in make_strategies(k).items():
        eq, tov = _equity_curve(C, fn, rebal, warmup, round_trip)
        m = _metrics(eq)
        results[name] = {**m, "avg_turnover": (sum(tov) / len(tov) if tov else 0.0)}

    # 結論:相對等權買進持有
    bh = results["等權買進持有(benchmark)"]
    for name, m in results.items():
        if m.get("is_benchmark"):
            m["beats_bh_sharpe"] = None
            m["beats_bh_return"] = None
            continue
        m["excess_return"] = m["total_return"] - bh["total_return"]
        m["sharpe_diff"] = m["sharpe"] - bh["sharpe"]
        m["beats_bh_sharpe"] = m["sharpe"] > bh["sharpe"]
        m["beats_bh_return"] = m["total_return"] > bh["total_return"]

    return {
        "as_of": str(pd.Timestamp(as_of).date()),
        "period": f"{C.index[warmup].date()} ~ {C.index[-1].date()}",
        "n_days": len(C) - warmup, "k": k, "rebal_days": rebal,
        "round_trip_cost": round_trip, "n_symbols": len(C.columns),
        "results": results,
    }
