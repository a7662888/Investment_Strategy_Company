# -*- coding: utf-8 -*-
"""盤後產生每日價值 current-state；覆寫私有資料檔，不新增 ledger 事件。"""
from __future__ import annotations

import json
from pathlib import Path

from company.model.current_state import save_current_state
from company.model.value_daily import build_daily_state
from company.screener.value_rescreen import rescreen_all

ROOT = Path(__file__).resolve().parent


def main() -> int:
    fundamentals = json.loads((ROOT / "data" / "value_fundamentals.json").read_text(encoding="utf-8"))
    pool = json.loads((ROOT / "model_artifacts" / "active_pool.json").read_text(encoding="utf-8"))
    pool_codes = {s["symbol"].split(".")[0] for s in pool.get("stocks", [])}
    # 所有已有基本面者都重評，母池外標的僅供既有持股查詢，不進每日新選名單。
    symbols = [f"{code}.TW" for code in fundamentals]
    results = rescreen_all(symbols)
    state = build_daily_state(results, pool_codes, int(pool.get("n") or len(pool_codes)))
    saved = save_current_state(state)
    print(json.dumps({
        "as_of": state["as_of"], "coverage": state["coverage"],
        "top_picks": [p["symbol"] for p in state["top_picks"]],
        "waiting": [p["symbol"] for p in state["waiting_list"]], "storage": saved,
    }, ensure_ascii=False, indent=2))
    return 0 if saved.get("local_saved") and (saved.get("durable") or not saved.get("remote_error")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
