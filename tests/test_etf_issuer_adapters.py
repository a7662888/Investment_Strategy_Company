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


FUBON_PAGE = """<div>資料日期：<span>2026/10/14</span></div>
<table><tr><th>股票代碼</th><th>股票名稱</th><th>股數</th><th>金額</th><th>權重(%)</th></tr>
<tr><td>6223</td><td>旺矽</td><td>323,000</td><td>1,900,855,000</td><td>7.9057</td></tr>
<tr><td>8046</td><td>南電</td><td>1,299,000</td><td>1,857,570,000</td><td>7.7257</td></tr>
<tr><td>合計</td><td></td><td></td><td>3,758,425,000</td><td>15.6314</td></tr></table>
<table><tr><th>期貨代碼</th><th>期貨名稱</th><th>口數</th><th>金額</th><th>權重(%)</th></tr>
<tr><td>TNZ6F</td><td>超長美債</td><td>20</td><td>67,971,859</td><td>9.1728</td></tr></table>
<table><tr><th>項目</th><th>金額</th></tr><tr><td>現金 (TWD)</td><td>1,220,979,272</td></tr></table>"""


class Fubon(unittest.TestCase):
    def test_tables_total_row_and_date(self):
        out = e.parse_fubon_assets(FUBON_PAGE, "00405A", "src")
        self.assertEqual(out["as_of"], "2026-10-14")
        self.assertEqual([h["code"] for h in out["holdings"]], ["6223", "8046"])   # 合計列排除
        self.assertEqual(out["holdings"][0]["shares"], 323000)
        self.assertEqual(out["derivatives"][0]["code"], "TNZ6F")

    def test_missing_date_is_rejected(self):
        with self.assertRaises(ValueError):
            e.parse_fubon_assets(FUBON_PAGE.replace("資料日期", "日期"), "00405A", "src")


class Cathay(unittest.TestCase):
    def test_official_weights_with_shares_from_the_matching_pcf(self):
        from unittest.mock import patch
        import json as _json
        weights = {"result": {"date": "2026/10/14", "stockWeights": [{"stockCode": "2330", "stockName": "台積電", "weights": "8.85"}]}}
        wrong = {"result": {"stockCode": "00400A", "date": "2026/10/14", "preDateC": "2026/10/13"}}
        right = {"result": {"stockCode": "00400A", "date": "2026/10/15", "preDateC": "2026/10/14", "totUnit": "1,949,140,000",
                            "basketUnit": "500,000", "aum": "30,788,558,230", "nav": "15.8"}}
        basket = {"result": [{"prod": "2330", "basketShares": "281"}]}
        def read(url, body=None):
            if "GetIndexStockWeights" in url: return _json.dumps(weights).encode()
            if "GetStocksList" in url: return _json.dumps(basket).encode()
            return _json.dumps(right if "2026/10/15" in url else wrong).encode()
        with patch.object(e, "read_public", side_effect=read):
            out = e.cathay_snapshot("00400A")
        self.assertEqual((out["as_of"], out["effective_date"]), ("2026-10-14", "2026-10-15"))
        self.assertEqual(out["holdings"][0]["shares"], round(281 * 1949140000 / 500000))
        self.assertEqual(out["holdings"][0]["weight_pct"], 8.85)


KGI_PAGE = """<div>主動凱基台灣(00407A)</div><div>2026/10/15 現金申購買回清單公告</div>
<div>基金淨資產價值(元) TWD$22,353,414,631</div><div>已發行受益權單位總數 2,175,739,000</div>
<div>(2026/10/14)每受益權單位淨資產價值(元) TWD$10.27</div>
<table><tr><th>&#x80A1;&#x7968;&#x4EE3;&#x865F;</th><th>&#x80A1;&#x7968;&#x540D;&#x7A31;</th><th>&#x80A1;&#x6578;</th><th>&#x6B0A;&#x91CD;(%)</th></tr>
<tr><td>2330</td><td>&#x53F0;&#x7A4D;&#x96FB;</td><td>830,000</td><td>9.25</td></tr></table>"""


class Kgi(unittest.TestCase):
    def test_entities_dates_and_meta(self):
        out = e.parse_kgi(KGI_PAGE, "00407A", "src")
        self.assertEqual((out["as_of"], out["effective_date"]), ("2026-10-14", "2026-10-15"))
        self.assertEqual(out["holdings"][0]["name"], "台積電")
        self.assertEqual((out["units"], out["nav"]), (2175739000.0, 10.27))

    def test_wrong_product_rejected(self):
        with self.assertRaises(ValueError):
            e.parse_kgi(KGI_PAGE, "00999A", "src")


def allianz(code="00984A"):
    return {"StatusCode": 0, "Entries": {"CSecuritiesCode": code, "CNavDt": "2026-10-14T00:00:00", "CPcfdate": "2026-10-15T00:00:00",
            "CAnceTotalAv": 10648028944.0, "CAnceTotalIssues": 652458000.0, "CAnceNav": 16.32,
            "DynamicTableData": [
                {"TableTitle": "股票 (97.66%)", "Columns": [{"Name": "序號"}, {"Name": "股票代號"}, {"Name": "股票名稱"}, {"Name": "股數"}, {"Name": "權重(%)"}],
                 "Rows": [["1", "2881", "富邦金控", "2,784,000", "4.20%"], ["2", "2330", "台積電", "180,000", "4.19%"]]},
                {"TableTitle": "期貨", "Columns": [{"Name": "期貨代號"}, {"Name": "期貨名稱"}, {"Name": "口數"}, {"Name": "權重(%)"}],
                 "Rows": [["TX", "台指期", "3", "1.10%"]]}]}}


class Allianz(unittest.TestCase):
    def test_dynamic_tables(self):
        out = e.parse_allianz(allianz(), "00984A", "src")
        self.assertEqual([h["code"] for h in out["holdings"]], ["2881", "2330"])
        self.assertEqual(out["holdings"][0]["weight_pct"], 4.2)
        self.assertEqual(out["holdings"][0]["shares"], 2784000)
        self.assertEqual(out["derivatives"][0]["code"], "TX")
        self.assertEqual(out["as_of"], "2026-10-14")

    def test_securities_code_must_match(self):
        with self.assertRaises(ValueError):
            e.parse_allianz(allianz("00993A"), "00984A", "src")
