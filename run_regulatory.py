# -*- coding: utf-8 -*-
"""盤後一次：永豐 Shioaji 處置股（punish）與注意股（notice）清單 → 私有資料庫。

只讀公開的監理資訊，不碰帳務。網站用來在選股、快訊、持股與排行上標示風險；
不改變任何買賣判定。官方文件範例的 updated_at 約在 17–18 時，故 email-daily
（14 時後）讀到的可能是前一交易日公告；處置期間通常跨多日，仍具提示價值。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from company.model.durable_document import save_document

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
LOCAL = ROOT / "data" / "regulatory.json"
REMOTE = "market/regulatory.json"
PUNISH_FIELDS = ("code", "start_date", "end_date", "interval", "unit_limit", "total_limit",
                 "description", "announced_date", "updated_at")
NOTICE_FIELDS = ("code", "reason", "close", "announced_date", "updated_at")


def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def rows_of(obj, fields: tuple[str, ...]) -> list[dict]:
    """把 SDK 物件轉成列。文件描述為「欄位皆為清單」（欄式），也容忍列式。"""
    if obj is None:
        return []
    raw = obj.dict() if hasattr(obj, "dict") else obj
    if isinstance(raw, list):
        return [{f: _plain((r.dict() if hasattr(r, "dict") else r).get(f)) for f in fields} for r in raw]
    if isinstance(raw, dict):
        columns = {f: list(raw.get(f) or []) for f in fields}
        length = max((len(v) for v in columns.values()), default=0)
        return [{f: _plain(columns[f][i] if i < len(columns[f]) else None) for f in fields}
                for i in range(length)]
    return []


def main() -> int:
    from company.data.shioaji_source import connect_best_effort
    from run_broker_positions import _quiet_sdk

    errors = {}
    punish, notice = [], []
    with _quiet_sdk():
        api, simulation = connect_best_effort()
        try:
            try:
                punish = rows_of(api.punish(timeout=10000), PUNISH_FIELDS)
            except Exception as exc:  # noqa: BLE001
                errors["punish"] = type(exc).__name__
            try:
                notice = rows_of(api.notice(timeout=10000), NOTICE_FIELDS)
            except Exception as exc:  # noqa: BLE001
                errors["notice"] = type(exc).__name__
        finally:
            try:
                api.logout()
            except Exception:  # noqa: BLE001
                pass
    punish = [r for r in punish if r.get("code")]
    notice = [r for r in notice if r.get("code")]
    if errors and not (punish or notice):
        print(json.dumps({"error": "regulatory lists unavailable", "errors": errors}))
        return 1
    doc = {
        "schema_version": 1,
        "as_of": datetime.now(TAIPEI).isoformat(),
        "source": "Shioaji punish/notice",
        "simulation": bool(simulation),
        "punish": punish, "notice": notice, "errors": errors,
    }
    storage = save_document(doc, LOCAL, REMOTE, f"chore(market): regulatory lists {doc['as_of'][:10]}")
    print(json.dumps({"punish": len(punish), "notice": len(notice), "errors": errors,
                      "durable": storage.get("durable")}))
    return 0 if storage.get("local_saved") else 1


if __name__ == "__main__":
    sys.exit(main())
