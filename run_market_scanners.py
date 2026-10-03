"""One post-close broker scanner batch, independent of the value engine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from company.data.shioaji_source import connect_best_effort, snapshot_timestamp
from company.model.current_state import load_current_state
from company.model.durable_document import save_document
from company.model.market_research import number

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))


def collect_scanners(api, types, trade_date):
    groups, errors = {}, {}
    for label, kind, descending in (
        ("amount", "AmountRank", True), ("volume", "VolumeRank", True),
        ("gainers", "ChangePercentRank", True), ("losers", "ChangePercentRank", False),
    ):
        try:
            data = api.scanners(scanner_type=getattr(types, kind), date=trade_date,
                                ascending=descending, count=20, timeout=10000)
            rows = []
            for row in data:
                get = lambda key: getattr(row, key, None)
                if str(get("date")) != trade_date:
                    continue
                timestamp = snapshot_timestamp(get("ts"), expected_date=trade_date)
                if timestamp["timestamp_status"] != "valid":
                    continue
                code = str(get("code") or "")
                if not code.isalnum() or len(code) > 10:
                    continue
                rows.append({"code": code, "name": get("name"), "date": trade_date,
                             **timestamp,
                             **{key: number(get(key)) for key in (
                                 "close", "change_price", "rank_value", "total_volume",
                                 "total_amount", "volume_ratio", "average_price")}})
            groups[label] = rows
        except Exception as exc:
            errors[label] = type(exc).__name__
    return {"schema_version": 1, "trade_date": trade_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Shioaji scanners", "scope": "broker-market-ranking",
            "rankings": groups, "errors": errors, "shadow": True}


def main():
    from run_broker_positions import _quiet_sdk
    from company.data.market_calendar import is_trading_day
    from company.model.daily_history import latest_completed_market_date

    now = datetime.now(TAIPEI)
    state, _ = load_current_state()
    if is_trading_day(now) and (9, 0) <= (now.hour, now.minute) < (14, 0):
        print(json.dumps({"skipped": "market session; use postclose batch only"}))
        return 0
    day = (state or {}).get("as_of")
    if day != latest_completed_market_date(now):
        print(json.dumps({"skipped": "current-state not completed today", "as_of": day}))
        return 1
    with _quiet_sdk():
        import shioaji as sj
        api, simulation = connect_best_effort()
        try:
            if simulation:
                doc = None
            else:
                doc = collect_scanners(api, sj.constant.ScannerType, day)
        finally:
            api.logout()
    if doc is None:
        print(json.dumps({"error": "production scanner unavailable"}))
        return 1
    doc["simulation"] = simulation
    if not any(doc["rankings"].values()):
        print(json.dumps({"error": "no valid scanner rows", "errors": doc["errors"]}))
        return 1
    storage = save_document(doc, ROOT / "data/market_scanners.json", "market/scanners/latest.json",
                            f"chore(market): postclose scanners {day}")
    print(json.dumps({"trade_date": day, "counts": {k: len(v) for k, v in doc["rankings"].items()},
                      "errors": doc["errors"], "storage": storage}))
    return 0 if storage.get("durable") and not doc["errors"] else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        print(json.dumps({"error": "SCANNER_BATCH_FAILED"}))
        code = 1
    raise SystemExit(code)
