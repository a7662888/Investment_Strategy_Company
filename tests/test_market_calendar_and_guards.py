# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data import market_calendar as calendar
from company.model import positions as positions_model

TAIPEI = timezone(timedelta(hours=8))

# 證交所 holidaySchedule 的真實樣本：同一份表混雜休市日與「交易日」標記。
SAMPLE = [
    {"Name": "中華民國開國紀念日", "Date": "1150101", "Description": "依規定放假1日。"},
    {"Name": "國曆新年開始交易日", "Date": "1150102", "Description": "國曆新年開始交易。"},
    {"Name": "農曆春節前最後交易日", "Date": "1150211", "Description": "農曆春節前最後交易。"},
    {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Description": ""},
    {"Name": "農曆春節後開始交易日", "Date": "1150223", "Description": "農曆春節後開始交易。"},
    {"Name": "中秋節", "Date": "1150925", "Description": "依規定放假1日。"},
    {"Name": "壞資料", "Date": "abc", "Description": ""},
]


class TestHolidayParsing(unittest.TestCase):
    def test_trading_day_markers_are_not_holidays(self) -> None:
        """把「開始交易日」當成休市，會讓系統在真正的交易日整天不做事。"""
        dates = [item["date"] for item in calendar.parse_holidays(SAMPLE)]
        self.assertIn("2026-01-01", dates)
        self.assertIn("2026-09-25", dates)
        self.assertNotIn("2026-01-02", dates)
        self.assertNotIn("2026-02-11", dates)
        self.assertNotIn("2026-02-23", dates)

    def test_market_closed_for_settlement_is_a_holiday(self) -> None:
        # 名稱含「交易」但其實是「無交易」，不可誤判成交易日。
        self.assertIn("2026-02-12", [i["date"] for i in calendar.parse_holidays(SAMPLE)])

    def test_unparsable_dates_are_dropped_not_crashed(self) -> None:
        # 7 筆樣本 − 3 個交易日標記 − 1 筆壞日期 = 3 個休市日
        self.assertEqual([i["date"] for i in calendar.parse_holidays(SAMPLE)],
                         ["2026-01-01", "2026-02-12", "2026-09-25"])

    def test_marker_rule(self) -> None:
        self.assertTrue(calendar.is_trading_marker("農曆春節後開始交易日"))
        self.assertFalse(calendar.is_trading_marker("市場無交易，僅辦理結算交割作業"))
        self.assertFalse(calendar.is_trading_marker("中秋節"))


class TestTradingDayGate(unittest.TestCase):
    def setUp(self) -> None:
        self.holidays = {"holidays": calendar.parse_holidays(SAMPLE)}

    def _at(self, text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=TAIPEI)

    def test_holiday_weekday_is_not_a_trading_day(self) -> None:
        from company.model import daily_jobs as jobs
        with patch.object(calendar, "refresh", return_value=self.holidays):
            moment = self._at("2026-09-25T10:30")  # 中秋節（週五）
            self.assertFalse(jobs.is_trading_weekday(moment))
            self.assertFalse(jobs.postclose_due({}, self._at("2026-09-25T14:30"))[0])
            self.assertIn("中秋節", jobs.non_trading_reason(moment))

    def test_ordinary_weekday_still_trades(self) -> None:
        from company.model import daily_jobs as jobs
        with patch.object(calendar, "refresh", return_value=self.holidays):
            self.assertTrue(jobs.is_trading_weekday(self._at("2026-09-11T10:30")))

    def test_calendar_failure_fails_open(self) -> None:
        """抓不到休市表時寧可多跑一次，也不要整天不更新。"""
        from company.model import daily_jobs as jobs
        with patch.object(calendar, "refresh", side_effect=RuntimeError("offline")):
            self.assertTrue(jobs.is_trading_weekday(self._at("2026-09-25T10:30")))


class TestSyncTokenSurvivesRotation(unittest.TestCase):
    def setUp(self) -> None:
        positions_model._SYNC_SECRET_CACHE.update(at=0.0, value=None)

    def tearDown(self) -> None:
        positions_model._SYNC_SECRET_CACHE.update(at=0.0, value=None)

    def test_persisted_secret_is_used_over_the_derived_one(self) -> None:
        with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-A"}, clear=False):
            with patch.dict("os.environ", {"POSITIONS_SYNC_TOKEN": ""}, clear=False):
                with patch.object(positions_model.durable_document, "load_document",
                                  return_value=({"token": "persisted-key"}, {})):
                    self.assertEqual(positions_model.expected_sync_token(), "persisted-key")

    def test_rotating_the_data_token_does_not_change_the_sync_key(self) -> None:
        stored = ({"token": "persisted-key"}, {})
        with patch.object(positions_model.durable_document, "load_document", return_value=stored):
            with patch.dict("os.environ", {"POSITIONS_SYNC_TOKEN": ""}, clear=False):
                with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-A"}, clear=False):
                    before = positions_model.expected_sync_token()
                positions_model._SYNC_SECRET_CACHE.update(at=0.0, value=None)
                with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-B-rotated"}, clear=False):
                    after = positions_model.expected_sync_token()
        self.assertEqual(before, after)

    def test_derived_key_changes_when_the_token_rotates(self) -> None:
        """對照組：說明為什麼非固化不可。"""
        with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-A"}, clear=False):
            first = positions_model._derived_sync_token()
        with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-B-rotated"}, clear=False):
            second = positions_model._derived_sync_token()
        self.assertNotEqual(first, second)

    def test_seeding_reuses_the_current_key_so_browsers_keep_working(self) -> None:
        saved = {}
        with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "token-A"}, clear=False):
            expected_seed = positions_model._derived_sync_token()
            with patch.object(positions_model.durable_document, "load_document", return_value=(None, {})):
                with patch.object(positions_model.durable_document, "save_document",
                                  side_effect=lambda doc, *a, **k: saved.update(doc) or {"durable": True}):
                    token, meta = positions_model.ensure_sync_token()
        self.assertTrue(meta["created"])
        self.assertEqual(token, expected_seed)
        self.assertEqual(saved["token"], expected_seed)

    def test_explicit_env_still_wins(self) -> None:
        with patch.dict("os.environ", {"POSITIONS_SYNC_TOKEN": "explicit"}, clear=False):
            self.assertEqual(positions_model.expected_sync_token(), "explicit")


class TestDailyEmailOnce(unittest.TestCase):
    def test_same_day_is_reported_as_already_sent(self) -> None:
        import run_daily_email as mailer
        with patch.object(mailer, "load_document", create=True):
            pass
        with patch("company.model.durable_document.load_document",
                   return_value=({"last_sent_date": "2026-09-11"}, {})):
            self.assertIsNotNone(mailer.already_sent_today("2026-09-11"))
            self.assertIsNone(mailer.already_sent_today("2026-09-12"))

    def test_no_log_means_not_sent(self) -> None:
        import run_daily_email as mailer
        with patch("company.model.durable_document.load_document", return_value=(None, {})):
            self.assertIsNone(mailer.already_sent_today("2026-09-11"))


if __name__ == "__main__":
    unittest.main()
