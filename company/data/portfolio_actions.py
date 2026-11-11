"""公開台股除息／分割事件；只取指定日期，不由股數差猜測金流。"""
from __future__ import annotations

import math
import re
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
SOURCE = "Yahoo Finance chart events（除息日口徑）"


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("INVALID_ACTION_NUMBER")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("INVALID_ACTION_NUMBER")
    return number


def _day(epoch):
    return datetime.fromtimestamp(_positive(epoch), timezone.utc).astimezone(TAIPEI).date().isoformat()


def collect_actions(symbols, day, fetch_json):
    """fetch_json(url) 由呼叫端注入；任何來源失敗或單位不明均不宣稱完整。"""
    result = {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
    try:
        target = date.fromisoformat(day)
        if target.isoformat() != day or target.weekday() >= 5:
            return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
        symbols = sorted(set(symbols))
        if any(not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{4,6}[A-Z]?\.(TW|TWO)", symbol)
               for symbol in symbols):
            return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
        start = datetime.combine(target - timedelta(days=7), time(), TAIPEI)
        end = datetime.combine(target + timedelta(days=1), time(), TAIPEI)
        params = urllib.parse.urlencode({"period1": int(start.timestamp()), "period2": int(end.timestamp()),
                                         "interval": "1d", "events": "div,splits"})
        for symbol in symbols:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}"
            payload = fetch_json(url)
            chart = payload.get("chart") or {}
            rows = chart.get("result")
            if chart.get("error") or not isinstance(rows, list) or len(rows) != 1:
                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
            row = rows[0]
            meta = row.get("meta") or {}
            # 僅處理台幣台股；不把外幣配息或不同時區的事件套入部位。
            if meta.get("currency") != "TWD" or meta.get("exchangeTimezoneName") != "Asia/Taipei":
                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
            if meta.get("symbol") != symbol:
                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
            timestamps = row.get("timestamp")
            if not isinstance(timestamps, list) or day not in {_day(value) for value in timestamps}:
                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
            events = row.get("events") or {}
            if not isinstance(events, dict):
                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
            for key, collection in (("dividends", result["dividends"]), ("splits", result["splits"])):
                block = events.get(key, {})
                if not isinstance(block, dict):
                    return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
                for event in block.values():
                    event_day = _day(event["date"])
                    if event_day > day:
                        return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
                    if key == "dividends":
                        fields = {"amount_per_share": _positive(event["amount"])}
                    else:
                        ratio = _positive(event["numerator"]) / _positive(event["denominator"])
                        if not math.isfinite(ratio) or ratio <= 0:
                            return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
                        if event.get("splitRatio"):
                            parts = event["splitRatio"].split(":")
                            if len(parts) != 2 or not math.isclose(
                                    ratio, _positive(parts[0]) / _positive(parts[1]), rel_tol=1e-9):
                                return {"complete": False, "dividends": [], "splits": [], "source": SOURCE}
                        fields = {"ratio": ratio}
                    if event_day == day:
                        collection.append({"symbol": symbol, "date": day, **fields, "source": SOURCE})
        result["complete"] = True
    except (TypeError, ValueError, OverflowError, OSError, KeyError, AttributeError):
        pass
    except Exception:
        # 網路／解析例外不得印來源網址（含持股代號）或例外內容。
        pass
    if not result["complete"]:
        result["dividends"] = []
        result["splits"] = []
    return result
