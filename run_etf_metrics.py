# -*- coding: utf-8 -*-
"""每日盤後：ETF 規模／資金流／折溢價／流動性 → 研究候選與停扣檢查。

在 GitHub Actions（email-daily）執行；有 Shioaji 金鑰時以一次 snapshots
取同日成交值與買賣價（319 檔 < 單次 500 檔上限），否則退回交易所 OpenAPI。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import etf_research
from company.data.etf_market import build_daily, fetch_sources
from company.model.durable_document import load_document, save_document
from company.model.etf_screen import screen, update_history

ROOT = Path(__file__).resolve().parent
HISTORY = (ROOT / "data" / "etf" / "metrics_history.json", "etf/metrics_history.json")
LATEST = (ROOT / "data" / "etf" / "screen_latest.json", "etf/screen_latest.json")


def _shioaji_snapshots(codes: list[str]) -> tuple[dict, str | None]:
    if not (os.environ.get("SHIOAJI_API_KEY") and os.environ.get("SHIOAJI_SECRET_KEY")):
        return {}, "no_credentials"
    try:
        from company.data.shioaji_source import connect_best_effort, fetch_snapshots
        from run_broker_positions import _quiet_sdk

        with _quiet_sdk():
            api, _ = connect_best_effort()
            try:
                snaps, _missing = fetch_snapshots(api, [f"{c}.TW" for c in codes])
            finally:
                try:
                    api.logout()
                except Exception:  # noqa: BLE001
                    pass
        return {s.split(".")[0]: row for s, row in snaps.items()}, None
    except Exception as exc:  # noqa: BLE001 - 永豐失敗時仍可用交易所資料
        return {}, type(exc).__name__


def _subpool() -> list[str]:
    from company.model.current_state import load_current_state

    state, _ = load_current_state()
    return [str(i.get("symbol", "")).split(".")[0] for i in (state or {}).get("etf_candidates") or []]


def main() -> int:
    from company.model.daily_jobs import session_unsettled
    if session_unsettled():
        # 盤中成交值與折溢價都未定稿，寫進歷史會污染 20 日均量與停扣檢查。
        print(json.dumps({"skipped": "session not settled; ETF metrics run after 14:00"}))
        return 0
    catalog = etf_research.catalog()
    rows = catalog["rows"]
    codes = [r["code"] for r in rows]
    mis, twse, tpex, errors = fetch_sources(set(codes))
    dates = Counter(v["date"] for v in mis.values() if v.get("date"))
    if not dates:
        print(f"MIS 淨值資料不可用：{errors}", file=sys.stderr)
        return 1
    trade_date = dates.most_common(1)[0][0]
    snapshots, snap_error = _shioaji_snapshots(codes)
    if snap_error:
        errors["shioaji"] = snap_error
    daily = build_daily(codes, trade_date, mis, twse, tpex, snapshots)

    holders = {r["code"]: (r["ranking_as_of"], int(r["holders"]))
               for r in rows if r.get("holders") and r.get("ranking_as_of")}
    lagged: dict[str, dict[str, float]] = {}
    for source in (twse, tpex):
        for code in codes:
            row = source.get(code) or {}
            if row.get("date") and row["date"] != trade_date and row.get("amount"):
                lagged.setdefault(row["date"], {})[code] = round(row["amount"] / 1e8, 4)
    history_doc, _ = load_document(*HISTORY)
    history = update_history(history_doc, trade_date, daily, holders, lagged)
    previous, _ = load_document(*LATEST)
    result = screen(rows, daily, history, trade_date, _subpool(), previous)
    result["source_errors"] = errors
    result["sources"] = {
        "nav": "證交所 MIS ETF 單位變動及淨值揭露（發行人提供，證交所轉載）",
        "trading": "永豐 Shioaji 快照（同日）優先，其次證交所／櫃買 OpenAPI（須同日）",
        "holders": "集保結算所每週受益人數",
    }

    storage = {
        "history": save_document(history, *HISTORY, f"chore(etf): metrics history {trade_date}"),
        "dated": save_document(result, ROOT / "data" / "etf" / "screen" / f"{trade_date}.json",
                               f"etf/screen/{trade_date}.json", f"chore(etf): screen {trade_date}"),
        "latest": save_document(result, *LATEST, f"chore(etf): latest screen {trade_date}"),
    }
    print(json.dumps({
        "trade_date": trade_date, "candidates": len(result["candidates"]),
        "new_entries": result["new_entries"], "exits": result["exits"],
        "stop_checks": {c["code"]: c["flags"] for c in result["stop_checks"]},
        "coverage": result["coverage"], "errors": errors,
        "durable": {k: v.get("durable") for k, v in storage.items()},
    }, ensure_ascii=False, indent=2))
    return 0 if storage["latest"].get("local_saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
