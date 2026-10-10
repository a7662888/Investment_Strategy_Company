# -*- coding: utf-8 -*-
"""盤後：關注清單的永豐 1 分 K → 每日彙整 → 買賣時機研究。

關注清單＝永豐持股＋手動持股＋今日候選／觀察＋ETF 子軌（上限 MAX_SYMBOLS 檔），
只抓這些標的以節省流量。首次對每檔回補近一年（kbars 每次最多 30 天），之後每天
只補最近 7 天。以 api.usage() 量測本次實際流量。

清單含個人持股，故結果只寫私有資料庫；程式碼 repo 為 public，log 只印筆數與流量。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from company.data.kbar_daily import FIELDS, aggregate_days, decode_days, encode_days, parse_kbars
from company.model.durable_document import load_document, save_document
from company.model.timing_research import build_report

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
PRIVATE_DIR = ROOT / "data" / "daily_audit" / "broker_positions"   # 已被 .gitignore 排除
HISTORY = (PRIVATE_DIR / "kbar_daily.json", "private/kbar_daily.json")
REPORT = (PRIVATE_DIR / "timing_research.json", "private/timing_research.json")
MAX_SYMBOLS = 30
BACKFILL_DAYS = 365
KEEP_DAYS = 400
WINDOW_DAYS = 30          # 官方：kbars 查詢區間不得超過 30 天
QUERY_PAUSE = 0.25        # 行情查詢 10 秒 50 次上限 → 每秒最多 4 次


def watchlist() -> list[str]:
    from company.model.current_state import load_current_state

    symbols: list[str] = []
    broker, _ = load_document(PRIVATE_DIR / "snapshot.json", "private/broker_positions.json")
    snap = (broker or {}).get("last_successful_snapshot") or {}
    symbols += [p["symbol"] for p in snap.get("positions") or []]
    manual, _ = load_document(ROOT / "data" / "private_positions.json", "private/positions.json")
    symbols += [p["symbol"] for p in (manual or {}).get("positions") or []]
    state, _ = load_current_state()
    for bucket in ("top_picks", "waiting_list", "etf_candidates"):
        symbols += [i["symbol"] for i in (state or {}).get(bucket) or [] if i.get("symbol")]
    return list(dict.fromkeys(s for s in symbols if s))[:MAX_SYMBOLS]


def _contract(api, code: str):
    lookup = getattr(getattr(api, "contracts", None), "get", None)
    contract = lookup(code) if callable(lookup) else None
    return contract or api.Contracts.Stocks.get(code)


def fetch_symbol(api, symbol: str, start: date, end: date) -> tuple[dict, int]:
    contract = _contract(api, symbol.split(".")[0])
    if contract is None:
        return {}, 0
    days, queries, cursor = {}, 0, start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=WINDOW_DAYS - 1))
        kbars = api.kbars(contract=contract, start=cursor.isoformat(), end=stop.isoformat(), timeout=30000)
        queries += 1
        days.update(aggregate_days(parse_kbars(kbars)))
        cursor = stop + timedelta(days=1)
        time.sleep(QUERY_PAUSE)
    return days, queries


def main() -> int:
    from company.data.shioaji_source import connect_best_effort
    from company.model.daily_jobs import session_unsettled
    from run_broker_positions import _quiet_sdk

    today = datetime.now(TAIPEI).date()
    symbols = watchlist()
    history, _ = load_document(*HISTORY)
    history = history or {"schema_version": 1, "codes": {}}
    codes = {s: decode_days(text) for s, text in (history.get("codes") or {}).items()}
    stats = {"symbols": len(symbols), "backfilled": 0, "queries": 0, "new_sessions": 0, "errors": 0}
    usage_before = usage_after = None
    with _quiet_sdk():
        api, simulation = connect_best_effort()
        try:
            try:
                usage_before = api.usage()
            except Exception:  # noqa: BLE001
                usage_before = None
            for symbol in symbols:
                known = codes.setdefault(symbol, {})
                backfill = len(known) < 200
                start = today - timedelta(days=BACKFILL_DAYS if backfill else 7)
                try:
                    days, queries = fetch_symbol(api, symbol, start, today)
                except Exception:  # noqa: BLE001 - 單檔失敗不影響其他檔
                    stats["errors"] += 1
                    continue
                if session_unsettled():
                    days.pop(today.isoformat(), None)   # 今日盤中尚未收盤：不存半天的資料
                stats["queries"] += queries
                stats["backfilled"] += int(backfill)
                stats["new_sessions"] += len(set(days) - set(known))
                known.update(days)
                cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
                codes[symbol] = {d: v for d, v in sorted(known.items()) if d >= cutoff}
            try:
                usage_after = api.usage()
            except Exception:  # noqa: BLE001
                usage_after = None
        finally:
            try:
                api.logout()
            except Exception:  # noqa: BLE001
                pass
    # 只保留目前關注清單的標的，退出清單的就不再佔空間。
    kept = {s: codes[s] for s in symbols if codes.get(s)}
    history = {"schema_version": 1, "format": "csv:date," + ",".join(FIELDS),
               "codes": {s: encode_days(d) for s, d in kept.items()},
               "updated_at": datetime.now(timezone.utc).isoformat()}
    stored = save_document(history, *HISTORY, f"chore(private): kbar daily aggregates {today}")

    lots, _ = load_document(PRIVATE_DIR / "lots.json", "private/broker_lots.json")
    report = build_report(kept, (lots or {}).get("lots"))
    report["generated_at"] = history["updated_at"]
    saved = save_document(report, *REPORT, f"chore(private): timing research {today}")

    mb = lambda u, k: round(getattr(u, k) / 1024 / 1024, 2) if u is not None and getattr(u, k, None) is not None else None
    print(json.dumps({
        **stats, "sessions_stored": sum(len(v) for v in kept.values()),
        "history_kb": round(len(json.dumps(history)) / 1024),
        "traffic_mb": (round(mb(usage_after, "bytes") - mb(usage_before, "bytes"), 2)
                       if usage_before is not None and usage_after is not None else None),
        "daily_limit_mb": mb(usage_after, "limit_bytes"), "remaining_mb": mb(usage_after, "remaining_bytes"),
        "fills_analyzed": len(report["execution"]["fills"]),
        "false_break_events": {k: v["events"] for k, v in report["false_breaks"].items()},
        "simulation": bool(simulation),
        "durable": {"history": stored.get("durable"), "report": saved.get("durable")},
    }))
    return 0 if stored.get("local_saved") else 1


if __name__ == "__main__":
    sys.exit(main())
