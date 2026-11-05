# -*- coding: utf-8 -*-
from datetime import date
from unittest.mock import patch

from company.data import chip_sources as src
from company.model import chip_flow as cf

PAYLOAD = {"status": "ok", "fields": ["證券代號", "證券簡稱", "除息交易日", "收益分配基準日", "收益分配發放日",
                                      "收益分配金額 (每1受益權益單位)", "收益分配標準 (102年度起啟用)", "公告年度"],
           "data": [["00901", "永豐智能車", "115年11月24日", "115年11月30日", "115年12月18日", None, "說明", "115"],
                    ["0056", "元大高股息", "115年10月22日", "115年10月28日", "115年11月18日", "1.72", "說明", "115"],
                    ["0056", "元大高股息", "115年07月22日", "115年07月28日", "115年08月18日", "1.00", "說明", "115"]]}


def rows():
    return src.parse_etf_distributions(PAYLOAD)


def test_parser_reads_official_dates_and_pending_amount():
    first = rows()[0]
    assert first == {"code": "00901", "name": "永豐智能車", "ex_date": "2026-11-24", "record_date": "2026-11-30",
                     "pay_date": "2026-12-18", "cash_dividend": None}
    assert src.parse_etf_distributions({"status": "error"}) == []


def test_fetch_requests_a_range_around_today():
    with patch.object(src, "get", return_value=b'{"status": "ok", "fields": [], "data": []}') as get:
        try:
            src.fetch_etf_distributions(date(2026, 11, 5))
        except StopIteration:
            pass
    assert "startDate=20251001" in get.call_args[0][0] and "endDate=20270504" in get.call_args[0][0]


def test_merge_keeps_notice_rows_adds_dates_and_appends_missing_etfs():
    notice = [{"code": "00901", "name": "永豐", "ex_date": "2026-11-24", "kind": "息", "cash_dividend": 0.5, "market": "上市"},
              {"code": "2330", "name": "台積電", "ex_date": "2026-12-01", "kind": "息", "cash_dividend": 6.0, "market": "上市"}]
    merged = {(r["code"], r["ex_date"]): r for r in cf.merge_etf_distributions(notice, rows())}
    assert merged[("00901", "2026-11-24")]["pay_date"] == "2026-12-18"
    assert merged[("00901", "2026-11-24")]["cash_dividend"] == 0.5
    assert merged[("2330", "2026-12-01")]["pay_date"] is None
    assert merged[("0056", "2026-10-22")]["cash_dividend"] == 1.72


def test_calendar_keeps_ex_dividend_rows_until_payment():
    merged = cf.merge_etf_distributions([], rows())
    calendar = cf.dividend_calendar(merged, "2026-11-05", lambda day: True, set())
    keys = {(r["code"], r["ex_date"]) for r in calendar}
    assert ("0056", "2026-10-22") in keys and ("00901", "2026-11-24") in keys
    assert ("0056", "2026-07-22") not in keys
    after = cf.dividend_calendar(merged, "2026-11-19", lambda day: True, set())
    assert ("0056", "2026-10-22") not in {(r["code"], r["ex_date"]) for r in after}


def test_latest_uses_most_recent_paid_amount_only():
    latest = cf.latest_etf_distributions(rows(), "2026-11-05")
    assert latest["0056"]["ex_date"] == "2026-10-22" and "00901" not in latest
