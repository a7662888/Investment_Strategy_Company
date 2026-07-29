# -*- coding: utf-8 -*-
"""盤後產生每日價值 current-state；覆寫私有資料檔，不新增 ledger 事件。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from company.data.value_fundamentals import load_fundamentals, refresh_pool, save_fundamentals
from company.model.current_state import save_current_state
from company.model.value_daily import build_daily_state
from company.screener.value_rescreen import rescreen_all

ROOT = Path(__file__).resolve().parent


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
    symbols = [row["symbol"] for row in pool.get("stocks", [])]
    results = rescreen_all(symbols, fundamentals=fundamentals)
    state = build_daily_state(results, pool_codes, int(pool.get("n") or len(pool_codes)))
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
        "waiting": [p["symbol"] for p in state["waiting_list"]], "storage": saved,
        "fundamentals": state["fundamentals"],
    }, ensure_ascii=False, indent=2))
    state_ok = saved.get("local_saved") and (saved.get("durable") or not saved.get("remote_error"))
    fundamentals_ok = fundamentals_saved.get("local_saved") and (
        fundamentals_saved.get("durable") or not fundamentals_saved.get("remote_error")
    )
    return 0 if state_ok and fundamentals_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
