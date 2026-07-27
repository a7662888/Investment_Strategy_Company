# -*- coding: utf-8 -*-
"""重凍 6 張 ETF 卡（TASK-016）。

修正兩層問題：
 1. 原卡 reference_price 錯誤（0050 寫 234、006208 寫 135.5…）→ entry_range 失效、
    outcome 算出 -53%/+85% 垃圾報酬。
 2. 百分位必須用「還原權值價 adj_close」：高股息 ETF 每季除息會使 raw close 跳空
    下跌，用 raw 算百分位會系統性高估現價位階（6 檔全部 93-97 百分位的假象）。
    entry_range 再依 raw/adj 比例換算回實際可交易價位。

規則可重現：近一年 adj_close 百分位 → 便宜區 P20–P40；
 ≤P40 accumulate／≤P70 hold／>P70 watch（對自己偏貴）。
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as A
from company.model import ledger

ETFS = ["0050.TW", "006208.TW", "0056.TW", "00878.TW", "00919.TW", "00713.TW"]
MV = "tw_value_method v2.1 附錄B / claude-etf-subtrack v3(adj)"

def at_pct(sorted_vals, p):
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[idx]

def main():
    events, _ = ledger.load_events(prefer_remote=True, use_cache=False)
    old = {}
    for e in events:
        if e.get("event_type") == "signal" and e.get("agent_id") == "claude-etf-subtrack":
            prev = old.get(e["symbol"])
            if not prev or (e.get("data_cutoff") or "") > (prev.get("data_cutoff") or ""):
                old[e["symbol"]] = e

    signals = []
    for sym in ETFS:
        rows = A.fetch_history(sym, "2025-07-01", "2026-07-13")
        pairs = [(float(r["close"]), float(r["adj_close"])) for r in rows
                 if r.get("close") and r.get("adj_close")]
        last = rows[-1]
        cur_raw, cur_adj = float(last["close"]), float(last["adj_close"])
        adjs = sorted(a for _, a in pairs)
        p_now = round(sum(1 for a in adjs if a <= cur_adj) / len(adjs) * 100, 1)
        ratio = cur_raw / cur_adj if cur_adj else 1.0          # 還原價 → 實際交易價
        lo = round(at_pct(adjs, 20) * ratio, 2)
        hi = round(at_pct(adjs, 40) * ratio, 2)
        if p_now <= 40:
            action, grade = "accumulate", "cheap-vs-own-history"
        elif p_now <= 70:
            action, grade = "hold", "fair-vs-own-history"
        else:
            action, grade = "watch", "rich-vs-own-history"

        o = old.get(sym, {})
        ev = [x for x in (o.get("evidence") or []) if isinstance(x, dict)
              and "尚待確認" not in str(x.get("claim", ""))
              and "revision_of" not in str(x.get("claim", ""))
              and "百分位" not in str(x.get("claim", ""))]
        ev.append({"claim": f"還原權值價位階：現價 {cur_raw} 位於近一年第 {p_now} 百分位"
                            f"（{'對自己偏貴' if p_now > 70 else '合理' if p_now > 40 else '相對便宜'}）；"
                            f"便宜區(P20–P40)換算現價基準約 {lo}–{hi}",
                   "source": "Yahoo 日線近一年 adj_close（每季除息已還原，可重現）",
                   "data_quality": "high"})
        ev.append({"claim": f"本卡取代 {o.get('signal_id','(舊卡)')}：原參考價 {o.get('reference_price')} 有誤"
                            f"（0050 為分割前價等），致買進區間失效、報酬率失真（曾算出 -53%/+85%）",
                   "source": "TASK-016 修正", "data_quality": "high"})

        signals.append({
            "agent_id": "claude-etf-subtrack", "model_version": MV,
            "symbol": sym, "name": o.get("name") or sym,
            "data_cutoff": last["date"], "action": action, "horizon": "120D",
            "reference_price": cur_raw, "entry_range": [lo, hi],
            "stop_loss": None, "target": None,
            "invalidation": o.get("invalidation") or "配息政策重大改變 或 追蹤指數編製規則變更",
            "grade": grade, "evidence": ev,
            "market_risk": o.get("market_risk") or "",
            "data_quality": {"price": "high", "valuation": "high",
                             "note": "ETF 子軌：還原權值價位階＋配息品質，不套個股 4-agent"},
        })
        print(f"{sym:<10} {action:<11} ref={cur_raw:<8} adj百分位={p_now:<6} 便宜區=[{lo}, {hi}]")

    print("freeze:", json.dumps(ledger.freeze_signals(signals), ensure_ascii=False))

if __name__ == "__main__":
    main()
