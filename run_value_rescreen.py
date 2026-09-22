# -*- coding: utf-8 -*-
"""每週價值重篩（TASK-018 entry point）。

流程：規則層重篩 → 與現行凍結卡比對 → 只在「判定改變」時凍新卡（revision_of）。
4-agent 質性分析：有 LLM key 則自動生成，無 key 則沿用既有論述並標記「待深度分析」，
確保排程不因缺 key 而失敗。

用法：
    python run_value_rescreen.py            # 重篩並在必要時凍新卡
    python run_value_rescreen.py --dry-run  # 只報告不寫帳本
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from company.model import ledger  # noqa: E402
from company.data.value_fundamentals import load_fundamentals  # noqa: E402
from company.screener.value_rescreen import rescreen_all  # noqa: E402


def _current_fundamentals() -> dict:
    """與 run_daily_value_state 完全相同的基本面來源：靜態 seed 疊上已刷新的季度資料。"""
    seed_path = ROOT / "data" / "value_fundamentals.json"
    fundamentals = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
    full_doc, _ = load_fundamentals()
    fundamentals.update(full_doc.get("stocks") or {})
    return fundamentals

VALUE_AGENTS = ("claude-value", "claude-etf-subtrack")
MV = "tw_value_method v2.2 / weekly-rescreen"


def _rank(e: dict) -> tuple[str, str]:
    """排序鍵：先看資料截止日，同日再看寫入時間。
    只比 data_cutoff 會在『同日重凍多版』時取到舊版（曾誤取未還原價算的 ETF 區間）。"""
    return (e.get("data_cutoff") or "", e.get("recorded_at") or "")


def current_cards() -> dict[str, dict]:
    events, _ = ledger.load_events(prefer_remote=True, use_cache=False)
    newest: dict[str, dict] = {}
    for e in events:
        if e.get("event_type") == "signal" and e.get("agent_id") in VALUE_AGENTS:
            sym = e.get("symbol")
            if sym not in newest or _rank(e) > _rank(newest[sym]):
                newest[sym] = e
    return newest


def qualitative(result: dict, old: dict | None) -> list[dict]:
    """4-agent 質性層。有 LLM key 走模型；否則沿用舊論述 + 標記待深度分析（不中斷排程）。"""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    carried = [e for e in ((old or {}).get("evidence") or [])
               if isinstance(e, dict) and not any(t in str(e.get("claim", ""))
                                                  for t in ("位階", "百分位", "revision_of", "本卡取代"))]
    if not key:
        carried.append({"claim": "本次為規則層自動重篩（估值位階＋品質硬篩）；商業模式／護城河／風險之質性分析"
                                 "沿用前次人工論述，尚待下次深度分析更新",
                        "source": "weekly rescreen (no LLM key)", "data_quality": "medium"})
        return carried
    try:
        from company.model.gemini_analyst import analyze_value_thesis  # type: ignore
        extra = analyze_value_thesis(result)
        if extra:
            carried.extend(extra)
    except Exception as exc:
        carried.append({"claim": f"質性分析暫時無法生成（{type(exc).__name__}），本卡僅含規則層判定",
                        "source": "weekly rescreen", "data_quality": "medium"})
    return carried


def candidate_symbols() -> list[str]:
    """今日被價值引擎選出的標的（可分批研究＋觀察名單）。

    帳本原本只重篩既有卡片，新標的永遠進不來——實測今日 7 檔候選中有 4 檔
    （中興電、祥碩、AES-KY、英業達）從未被記錄。帳本的目的是驗證這套方法，
    只記錄舊卡而不記錄現在的選股，驗證的就是一個已不代表系統判斷的樣本。
    """
    from company.model.current_state import load_current_state

    state, _ = load_current_state()
    if not state:
        return []
    return [item["symbol"] for bucket in ("top_picks", "waiting_list")
            for item in (state.get(bucket) or []) if item.get("symbol")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cards = current_cards()
    if not cards:
        print("找不到現行價值卡，先執行初始凍結。", file=sys.stderr)
        return 1
    fresh = [s for s in candidate_symbols() if s not in cards]
    symbols = sorted(set(cards) | set(fresh))
    print(f"重篩 {len(symbols)} 檔（既有卡 {len(cards)}、今日新候選 {len(fresh)}）："
          f"{', '.join(s.replace('.TW','') for s in symbols)}")
    if fresh:
        print(f"首次凍結對象：{', '.join(s.replace('.TW','') for s in fresh)}")
    print()

    # 必須與 run_daily_value_state 餵同一份基本面，否則同一個引擎會對同一檔給出
    # 兩種判定：帳本走靜態 seed（實測僅 9 季），每日狀態走已刷新的季度資料（12 季）。
    # 實測分叉案例：台灣大 3045 今日狀態 quality_pass=False／排除，帳本卻仍 accumulate。
    # 帳本的意義是「凍結同一條決策鏈當時的判斷」，資料源分叉會讓它記錄一個
    # 現行引擎根本不會做出的決定，整個 shadow 驗證因此失去意義。
    results = rescreen_all(symbols, fundamentals=_current_fundamentals())
    changed, unchanged, errors = [], [], []
    signals = []

    skipped = []
    for r in results:
        sym = r["symbol"]
        old = cards.get(sym)
        if r.get("error"):
            errors.append(f"{sym}: {r['error']}")
            continue
        # 資料不全時「維持現狀」而非改判定——否則一次 API 限流就會把 accumulate 誤降為 watch
        if r.get("data_incomplete"):
            skipped.append(f"{sym}({r['data_incomplete']})")
            continue
        old_action = (old or {}).get("action", "")
        new_action = r.get("action", "")
        old_er = (old or {}).get("entry_range")
        new_er = r.get("entry_range")
        price = r.get("price")
        # 只有「action 改變」才凍新卡。若僅價格漂移但判定未變，前端已用即時價標示
        # 「是否仍在買進區間」，不需為此每週各凍一張——否則帳本每年多近千筆，
        # 會重蹈 2026-07-09 超過 1MB 被覆蓋的事故。
        left_range = (isinstance(old_er, list) and len(old_er) == 2 and price is not None
                      and not (float(old_er[0]) <= price <= float(old_er[1])))
        if new_action == old_action:
            note = f"{sym} {new_action}" + (f"（價格已離開原區間，前端即時標示）" if left_range else "")
            unchanged.append(note)
            continue

        is_new = old is None
        if is_new:
            why = [f"首次凍結：{new_action}"]
        else:
            why = [f"判定 {old_action or '—'} → {new_action}"]
            if left_range:
                why.append(f"現價 {price} 已離開原買進區間 {old_er}")
        changed.append(("＋新卡 " if is_new else "") + f"{sym} {r.get('name','')}：{'；'.join(why)}")

        ev = qualitative(r, old)
        for reason in r.get("reasons", []):
            ev.append({"claim": reason, "source": "weekly rescreen (rule layer)", "data_quality": "high"})
        # 新卡沒有前身，不可寫「本卡取代 (舊卡)」——那會在帳本留下不存在的血緣關係。
        ev.append({
            "claim": (f"首次納入帳本：今日經價值引擎選為候選（{new_action}）"
                      if is_new else
                      f"本卡取代 {(old or {}).get('signal_id','(舊卡)')}：{'；'.join(why)}"),
            "source": "TASK-018 每週重篩", "data_quality": "high",
        })

        signals.append({
            "agent_id": (old or {}).get("agent_id") or ("claude-etf-subtrack" if r.get("is_etf") else "claude-value"),
            "model_version": MV, "symbol": sym, "name": r.get("name"),
            "data_cutoff": r["as_of"], "action": new_action, "horizon": "120D",
            "reference_price": price, "entry_range": new_er,
            "stop_loss": None, "target": (old or {}).get("target"),
            "invalidation": (old or {}).get("invalidation") or "品質硬篩條件轉負 或 估值回到自身歷史高位",
            "grade": (old or {}).get("grade"), "evidence": ev,
            "market_risk": (old or {}).get("market_risk") or "",
            "data_quality": {"valuation": "high", "fundamentals": "medium",
                             "note": "規則層每週重篩；基本面沿用最近季報快照"},
        })

    print(f"■ 判定改變 {len(changed)} 檔")
    for c in changed:
        print("   ", c)
    print(f"■ 維持不變 {len(unchanged)} 檔：{', '.join(unchanged) if unchanged else '（無）'}")
    if skipped:
        print(f"■ 資料不全跳過 {len(skipped)} 檔（維持原判定，不誤改）：{', '.join(skipped)}")
    if errors:
        print(f"■ 取數失敗 {len(errors)} 檔：{'; '.join(errors)}")

    if not signals:
        print("\n無須更新帳本（所有判定維持不變）。")
        return 0
    if args.dry_run:
        print(f"\n[dry-run] 將凍結 {len(signals)} 張新卡，未寫入。")
        return 0
    res = ledger.freeze_signals(signals)
    print("\nfreeze:", json.dumps(res, ensure_ascii=False))
    return 0 if res.get("durable") or res.get("local_saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
