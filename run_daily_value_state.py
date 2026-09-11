# -*- coding: utf-8 -*-
"""盤後產生每日價值 current-state；覆寫私有資料檔，不新增 ledger 事件。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from company.data.value_fundamentals import load_fundamentals, refresh_pool, save_fundamentals
from company.model.current_state import load_current_state, save_current_state
from company.model.value_daily import build_daily_state
from company.screener.value_rescreen import latest_official_close_date, rescreen_all

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")


def main() -> int:
    seed = json.loads((ROOT / "data" / "value_fundamentals.json").read_text(encoding="utf-8"))
    pool = json.loads((ROOT / "model_artifacts" / "active_pool.json").read_text(encoding="utf-8"))
    pool_codes = {s["symbol"].split(".")[0] for s in pool.get("stocks", [])}
    full_doc, load_meta = load_fundamentals()
    batch_size = int(os.environ.get("VALUE_FUNDAMENTALS_BATCH", "5"))
    full_doc, refresh_meta = refresh_pool(pool, full_doc, batch_size=batch_size)
    fundamentals_saved = save_fundamentals(full_doc)
    fundamentals = dict(seed)
    fundamentals.update(full_doc.get("stocks") or {})
    # 母池100維持個股可投資池；ETF另設正式子池，但仍使用同一套價值規則與同一份 current-state。
    etf_symbols = [
        f"{code}.TW" for code, item in seed.items()
        if isinstance(item, dict) and item.get("is_etf")
    ]
    symbols = list(dict.fromkeys([row["symbol"] for row in pool.get("stocks", [])] + etf_symbols))
    results = rescreen_all(symbols, fundamentals=fundamentals)
    state = build_daily_state(results, pool_codes, int(pool.get("n") or len(pool_codes)))
    official_as_of = latest_official_close_date(symbols)
    state["market_expected_as_of"] = official_as_of
    state["market_data_complete"] = not official_as_of or state.get("as_of") == official_as_of
    # 原本只要資料未追平官方就 raise，整份拒存。但拒存的後果是網站繼續顯示**更舊**
    # 的資料——嚴格來說更糟，實測 2026-09-10、09-11 連續兩天因此完全停止更新。
    # 真正該防的是「用較舊的狀態覆蓋較新的狀態」，所以只在回頭時拒絕；
    # 落後官方則照存並留下 market_data_complete=False 供前端顯示落後狀態。
    previous, _ = load_current_state()
    previous_as_of = (previous or {}).get("as_of") or ""
    current_as_of = state.get("as_of") or ""
    if previous_as_of and current_as_of and current_as_of < previous_as_of:
        raise RuntimeError(
            f"refusing to regress market state: saved={previous_as_of}, new={current_as_of}"
        )
    if not state["market_data_complete"]:
        print(f"[warn] 市場資料尚未追平官方：official={official_as_of}, state={current_as_of}；"
              "仍儲存，並以 market_data_complete=False 標記。")
    state["analysis_date_taipei"] = datetime.now(TAIPEI).date().isoformat()
    state["etf_subpool"] = {"count": len(etf_symbols), "symbols": etf_symbols}
    state["fundamentals"] = {
        "coverage": full_doc.get("coverage"),
        "load": load_meta,
        "refresh": refresh_meta,
        "storage": fundamentals_saved,
    }
    saved = save_current_state(state)
    print(json.dumps({
        "as_of": state["as_of"], "coverage": state["coverage"],
        "top_picks": [p["symbol"] for p in state["top_picks"]],
        "waiting": [p["symbol"] for p in state["waiting_list"]],
        "etf_candidates": [p["symbol"] for p in state["etf_candidates"]], "storage": saved,
        "fundamentals": state["fundamentals"],
    }, ensure_ascii=False, indent=2))
    state_ok = saved.get("local_saved") and (saved.get("durable") or not saved.get("remote_error"))
    fundamentals_ok = fundamentals_saved.get("local_saved") and (
        fundamentals_saved.get("durable") or not fundamentals_saved.get("remote_error")
    )
    return 0 if state_ok and fundamentals_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
