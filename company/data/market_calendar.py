# -*- coding: utf-8 -*-
"""台股交易日曆（休市日表）。

來源為證交所 holidaySchedule OpenAPI。該表**同時包含休市日與交易日標記**，
不可照單全收：例如「農曆春節後開始交易日」「農曆春節前最後交易日」都是
正常交易日，若當成休市會讓系統整天不做事。判別規則：名稱含「交易日」
且不含「無交易」者為交易日標記；其餘（放假、補假、市場無交易）才是休市。

失效時一律 fail-open（視為交易日）：誤判休市會讓當日完全不更新，
比多跑一次昂貴得多。
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / "data" / "market_holidays.json"
SOURCE_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
REFRESH_AFTER_SECONDS = 7 * 24 * 3600
TAIPEI = timezone(timedelta(hours=8))

# 證交所憑證缺 Subject Key Identifier，Python 3.13 的嚴格驗證會拒絕；
# 與 app.py 既有做法一致：只關掉 X509_STRICT，仍要求驗證與主機名比對。
_SSL_CONTEXT = ssl.create_default_context()
if hasattr(ssl, "VERIFY_X509_STRICT"):
    _SSL_CONTEXT.verify_flags &= ~ssl.VERIFY_X509_STRICT

_MEMO: dict | None = None


def _roc_to_iso(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) != 7:
        return None
    try:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])).isoformat()
    except ValueError:
        return None


def is_trading_marker(name: str) -> bool:
    """該列是否為「交易日」標記而非休市日。"""
    return "交易日" in name and "無交易" not in name


def parse_holidays(rows: list[dict]) -> list[dict]:
    holidays: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        iso = _roc_to_iso(row.get("Date"))
        name = str(row.get("Name") or "")
        if not iso or is_trading_marker(name):
            continue
        holidays.append({"date": iso, "name": name})
    holidays.sort(key=lambda item: item["date"])
    return holidays


def fetch_holidays(timeout: float = 30.0) -> list[dict]:
    request = urllib.request.Request(
        SOURCE_URL, headers={"User-Agent": "investment-market-calendar/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return parse_holidays(json.loads(response.read().decode("utf-8")))


def _load_cache() -> dict | None:
    global _MEMO
    if _MEMO is not None:
        return _MEMO
    if not CACHE_PATH.exists():
        return None
    try:
        _MEMO = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 壞掉的快取不得讓整個排程停擺
        return None
    return _MEMO


def _save_cache(holidays: list[dict]) -> dict:
    global _MEMO
    document = {
        "schema_version": 1,
        "source": SOURCE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at_epoch": time.time(),
        "count": len(holidays),
        "holidays": holidays,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    _MEMO = document
    return document


def refresh(force: bool = False) -> dict:
    """必要時更新休市日表；抓取失敗時保留既有快取。"""
    cache = _load_cache()
    fresh_enough = (
        cache is not None
        and (time.time() - float(cache.get("fetched_at_epoch") or 0)) < REFRESH_AFTER_SECONDS
    )
    if cache is not None and fresh_enough and not force:
        return cache
    try:
        return _save_cache(fetch_holidays())
    except Exception:  # noqa: BLE001
        # 抓不到就沿用舊表；完全沒有表時由 is_holiday fail-open。
        return cache or {"holidays": [], "count": 0, "stale": True}


def holiday_set() -> set[str]:
    document = refresh()
    return {item["date"] for item in document.get("holidays") or [] if item.get("date")}


def is_holiday(day: date | datetime | str) -> bool:
    if isinstance(day, datetime):
        day = day.astimezone(TAIPEI).date()
    key = day if isinstance(day, str) else day.isoformat()
    return key in holiday_set()


def is_trading_day(moment: datetime) -> bool:
    """週一～週五且非休市日。表不可用時 fail-open，只擋週末。"""
    local = moment.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return False
    return not is_holiday(local.date())


def holiday_name(day: date | str) -> str | None:
    key = day if isinstance(day, str) else day.isoformat()
    for item in refresh().get("holidays") or []:
        if item.get("date") == key:
            return item.get("name")
    return None
