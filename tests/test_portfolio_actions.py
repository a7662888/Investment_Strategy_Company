"""公開事件來源契約測試；全部使用合成 payload，無網路連線。"""
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from company.data.portfolio_actions import collect_actions

DAY = "2026-11-11"
STAMP = int(datetime(2026, 11, 11, 1, tzinfo=timezone.utc).timestamp())
SYMBOL = "9999.TW"


def fixture(events=None):
    return {"chart": {"error": None, "result": [{"meta": {
        "symbol": SYMBOL, "currency": "TWD", "exchangeTimezoneName": "Asia/Taipei"},
        "timestamp": [STAMP], "events": events or {}}]}}


def test_collect_actions_uses_explicit_symbols_and_events_query():
    seen = []

    def fetch(url):
        seen.append(url)
        return fixture()

    result = collect_actions([SYMBOL, SYMBOL], DAY, fetch)
    assert result["complete"] is True
    assert result["dividends"] == result["splits"] == []
    assert len(seen) == 1
    assert urlsplit(seen[0]).path.endswith("/" + SYMBOL)
    query = parse_qs(urlsplit(seen[0]).query)
    assert query["events"] == ["div,splits"]
    assert int(query["period2"][0]) - int(query["period1"][0]) == 8 * 86400


def test_collect_actions_validates_dividend_and_split_on_ex_date():
    data = fixture({"dividends": {"a": {"date": STAMP, "amount": 2.5},
                                     "old": {"date": STAMP - 86400, "amount": 1}},
                    "splits": {"b": {"date": STAMP, "numerator": 4,
                                       "denominator": 1, "splitRatio": "4:1"}}})
    result = collect_actions([SYMBOL], DAY, lambda _: data)
    assert result["complete"] is True
    assert len(result["dividends"]) == 1
    assert result["dividends"][0]["amount_per_share"] == 2.5
    assert result["dividends"][0]["date"] == DAY
    assert result["splits"][0]["ratio"] == 4


def test_collect_actions_missing_day_or_unknown_units_are_incomplete():
    variants = []
    data = fixture()
    data["chart"]["result"][0]["timestamp"] = [STAMP - 86400]
    variants.append(data)
    for field, value in (("currency", "USD"), ("exchangeTimezoneName", "UTC"), ("symbol", "8888.TW")):
        data = fixture()
        data["chart"]["result"][0]["meta"][field] = value
        variants.append(data)
    variants.extend(({"chart": {"error": {"code": "Not Found"}}}, {"chart": {"result": []}}))
    for data in variants:
        assert collect_actions([SYMBOL], DAY, lambda _: data)["complete"] is False


def test_collect_actions_rejects_nonfinite_negative_or_future_events():
    events = [
        {"dividends": {"a": {"date": STAMP, "amount": value}}}
        for value in (float("nan"), float("inf"), -1, 0, True)
    ]
    events.extend([
        {"dividends": {"a": {"date": STAMP + 86400, "amount": 1}}},
        {"splits": {"a": {"date": STAMP, "numerator": 4, "denominator": 0}}},
        {"splits": {"a": {"date": STAMP, "numerator": 4, "denominator": 1, "splitRatio": "2:1"}}},
        {"dividends": []},
    ])
    for event in events:
        result = collect_actions([SYMBOL], DAY, lambda _: fixture(event))
        assert result["complete"] is False
        assert result["dividends"] == result["splits"] == []


def test_collect_actions_partial_failure_never_retains_partial_actions():
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            data = fixture({"dividends": {"a": {"date": STAMP, "amount": 1}}})
            data["chart"]["result"][0]["meta"]["symbol"] = "8888.TW"
            return data
        raise RuntimeError("synthetic-fetch-error")

    result = collect_actions(["8888.TW", SYMBOL], DAY, fetch)
    assert result["complete"] is False
    assert result["dividends"] == result["splits"] == []


def test_collect_actions_rejects_invalid_inputs_and_empty_scope_needs_no_fetch():
    def forbidden(_):
        raise AssertionError("unexpected fetch")

    assert collect_actions([], DAY, forbidden)["complete"] is True
    for symbols, day in ((["../../secret"], DAY), ([SYMBOL], "2026-11-14"), ([SYMBOL], "bad-day")):
        assert collect_actions(symbols, day, forbidden)["complete"] is False
