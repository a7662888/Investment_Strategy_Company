# -*- coding: utf-8 -*-
"""群益、野村 PCF 解析（欄位依 2026-10-14 官方頁面實際回應）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_research as e


def capital(weight=7.68, date2="2026-10-14"):
    return {"code": 200, "data": {
        "pcf": {"date1": "2026-10-15", "date2": date2, "nav": 50041797782.0, "totUnit": 2122936000.0, "pUnit": 23.57},
        "stocks": [{"stocNo": "2330", "stocName": "台積電", "stocEname": "TSMC", "weight": weight, "share": 1544000.0},
                   {"stocNo": "2454", "stocName": "聯發科", "stocEname": "MTK", "weight": 6.58, "share": 736000.0}],
        "bonds": [], "futures": []}}


def nomura(code="00980A"):
    return {"StatusCode": 0, "Entries": {
        "CFundId": code, "CPcfdate": "2026-10-14T00:00:00", "CNavDt": "2026-10-13T00:00:00",
        "CAnceTotalAv": 19579587970.0, "CAnceTotalIssues": 762230000.0, "CAnceNav": 25.69,
        "Stocks": [{"CStockCode": "2330", "CStockName": "台灣積體電路製造", "CQuantity": 753000, "CWeightsPct": 9.58}],
        "Futures": [{"CFuturesCode": "TX", "CFuturesName": "台指期", "CQuantity": 30, "CWeightsPct": 1.48}]}}


class Capital(unittest.TestCase):
    def test_fields_and_dates(self):
        out = e.parse_capital(capital(), "00982A", "399")
        self.assertEqual((out["as_of"], out["effective_date"]), ("2026-10-14", "2026-10-15"))
        self.assertEqual(out["holdings"][0]["name"], "台積電")      # 中文名，不取英文名欄位
        self.assertEqual(out["nav"], 23.57)
        self.assertEqual(out["aum_twd"], 50041797782.0)

    def test_rejects_errors_and_bad_weights(self):
        with self.assertRaises(ValueError):
            e.parse_capital({"code": 400, "data": None}, "00982A", "399")
        with self.assertRaises(ValueError):
            e.parse_capital(capital(weight=101), "00982A", "399")

    def test_registered_as_supported(self):
        self.assertTrue({"00982A", "00992A", "00997A"} <= set(e.CAPITAL))


class Nomura(unittest.TestCase):
    def test_fields_dates_and_futures_separated(self):
        out = e.parse_nomura(nomura(), "00980A")
        self.assertEqual((out["as_of"], out["effective_date"]), ("2026-10-13", "2026-10-14"))
        self.assertEqual([h["code"] for h in out["holdings"]], ["2330"])
        self.assertEqual(out["derivatives"][0]["code"], "TX")
        self.assertEqual(out["units"], 762230000.0)

    def test_wrong_product_is_rejected(self):
        with self.assertRaises(ValueError):
            e.parse_nomura(nomura("00985A"), "00980A")


if __name__ == "__main__":
    unittest.main()


class StoredFallback(unittest.TestCase):
    def setUp(self):
        e._CACHE.clear()

    def test_live_failure_uses_the_daily_stored_snapshot(self):
        from unittest.mock import patch
        snap = e.parse_nomura(nomura(), "00980A")
        catalog = {"rows": [{"code": "00980A", "active": True, "official_url": "x"}]}
        with patch.object(e, "catalog", return_value=catalog), \
                patch.object(e, "fetch_pair", side_effect=OSError("blocked")), \
                patch.object(e, "stored_pair", return_value={"current": snap, "previous": None, "saved_at": "2026-10-14T08:00:00+00:00"}):
            out = e.holdings("00980A")
        self.assertEqual(out["status"], "stored_official")
        self.assertEqual(out["stored_at"], "2026-10-14T08:00:00+00:00")
        self.assertEqual(out["error"], "OSError")

    def test_live_success_is_preferred(self):
        from unittest.mock import patch
        snap = e.parse_nomura(nomura(), "00980A")
        catalog = {"rows": [{"code": "00980A", "active": True, "official_url": "x"}]}
        with patch.object(e, "catalog", return_value=catalog), \
                patch.object(e, "fetch_pair", return_value=(snap, None)), \
                patch.object(e, "stored_pair") as stored:
            self.assertEqual(e.holdings("00980A")["status"], "official_snapshot")
            stored.assert_not_called()
