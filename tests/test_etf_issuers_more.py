# -*- coding: utf-8 -*-
"""兆豐、第一金、永豐、台新、摩根、貝萊德主動 ETF 官方持股解析（離線樣本）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_research as e


def table(header, rows):
    cells = lambda r, tag: "".join(f"<{tag}>{c}</{tag}>" for c in r)  # noqa: E731
    return f"<table><thead><tr>{cells(header, 'th')}</tr></thead><tbody>" + "".join(f"<tr>{cells(r, 'td')}</tr>" for r in rows) + "</tbody></table>"


class Mega(unittest.TestCase):
    PAGE = ("<p>（股票代號：00996A 簡稱：主動兆豐台灣豐收）</p><p>查詢日期 2026/10/21 現金 申購買回清單公告</p>"
            "<div>基金淨資產價值(元)</div><div>TWD$ 5,523,512,540</div><div>已發行受益權單位總數</div><div>421,899,000</div>"
            "<div>每受益權單位淨資產價值(元)</div><div>TWD$ 13.0900</div><div>2026/10/20 每基數實際申購總價金(元)</div>"
            + table(["股票代號", "股票名稱", "股數", "持股權重"], [["2330", "台積電   ", "204,000", "8.88%"], ["4958", "臻鼎-KY", "734,000", "7.27%"]])
            + table(["期貨代號", "期貨名稱", "契約年月", "口數", "持股權重"], [["MTX", "小型臺指期貨", "2026/11", "38", "1.37%"]]))

    def test_parse(self):
        snap = e.parse_mega(self.PAGE, "00996A", "src")
        self.assertEqual((snap["as_of"], snap["effective_date"]), ("2026-10-20", "2026-10-21"))
        self.assertEqual([h["code"] for h in snap["holdings"]], ["2330", "4958"])
        self.assertEqual(snap["holdings"][0]["name"], "台積電")
        self.assertEqual((snap["units"], snap["nav"]), (421899000.0, 13.09))
        self.assertEqual(snap["derivatives"][0]["shares"], 38.0)

    def test_wrong_product_rejected(self):
        with self.assertRaises(ValueError):
            e.parse_mega(self.PAGE, "00690", "src")


class Fsitc(unittest.TestCase):
    def test_parse_uses_stock_group_only(self):
        meta = [{"fundid": "182", "sdate": "2026-10-21", "A": "基金淨資產價值(元)", "B": "TWD 3,910,809,603"},
                {"fundid": "182", "sdate": "2026-10-21", "A": "已發行受益權單位總數-台幣交易", "B": "242,690,000"}]
        rows = [{"fundid": "182", "sdate": "2026-10-20", "group": "1", "A": "2330", "B": "台積電", "C": "16.04", "D": "261,000"},
                {"fundid": "182", "sdate": "2026-10-20", "group": "4", "A": "現金/存款", "B": "TWD 1", "C": "6.61", "D": ""},
                {"fundid": "182", "sdate": "2026-10-20", "group": "5", "A": "股票", "B": "94.93%", "C": "1", "D": ""}]
        snap = e.parse_fsitc(meta, rows, "00994A", "182", "src")
        self.assertEqual((snap["as_of"], snap["effective_date"]), ("2026-10-20", "2026-10-21"))
        self.assertEqual([h["code"] for h in snap["holdings"]], ["2330"])
        self.assertEqual((snap["units"], snap["aum_twd"]), (242690000.0, 3910809603.0))
        with self.assertRaises(ValueError):
            e.parse_fsitc(meta, rows, "00408A", "183", "src")


class Sinopac(unittest.TestCase):
    def test_parse_skips_per_stock_mobile_tables(self):
        page = ("<p>永豐台灣科技趨勢主動式ETF（證劵代碼：00410A）2026/10/21</p><p>2026/10/20&nbsp;基金淨資產價值(元) NT$&nbsp;2,121,350,442</p>"
                + table(["證券代碼", "證券名稱", "股數", "佔基金淨資產之權重(%)"], [["2330", "台積電", "50,000", "5.66"], ["3653", "健策", "17,000", "5.33"]])
                + table(["證券代碼", "2330"], [["證券名稱", "台積電"]]))
        snap = e.parse_sinopac(page, "00410A", "src")
        self.assertEqual((snap["as_of"], snap["effective_date"]), ("2026-10-20", "2026-10-21"))
        self.assertEqual(len(snap["holdings"]), 2)
        self.assertEqual(snap["aum_twd"], 2121350442.0)


class Taishin(unittest.TestCase):
    def test_parse_strips_tt_suffix_and_totals(self):
        page = ("<p>台新台灣優勢成長主動式ETF基金 (00987A)</p><p>2026/10/20每基數實際申購總價金(元) TWD 8,050,910</p>"
                + table(["代號", "名稱", "股數", "持股權重"], [["2330 TT", "台積電", "90,000", "9.6730%"], ["NVDA US", "NVIDIA", "1,000", "2.0%"], ["股票合計", "96.5391%"]]))
        snap = e.parse_taishin(page, "00987A", "src")
        self.assertEqual(snap["as_of"], "2026-10-20")
        self.assertEqual([h["code"] for h in snap["holdings"]], ["2330", "NVDA US"])


class Jpm(unittest.TestCase):
    def test_parse_market_value_percent(self):
        raw = {"fundData": {"holdings": {
            "pcfEquityHoldings": {"effectiveDate": "2026-10-20", "data": [
                {"securityTicker": "2330", "securityDescription": "台灣積體電路製造", "marketValuePercent": 20.7069, "shares": 327760, "marketValue": 788262800.0}]},
            "pcfCashHoldings": {"data": [{"marketValue": 100.0}]},
            "pcfFutureHoldings": {"data": [{"securityTicker": "FTX6", "securityDescription": "台股期貨", "marketValuePercent": -0.07, "shares": -1, "marketValue": -2667600.0}]}}}}
        snap = e.parse_jpm(raw, "00401A", "TW00000401A1")
        self.assertEqual(snap["as_of"], "2026-10-20")
        self.assertEqual(snap["holdings"][0]["weight_pct"], 20.7069)
        self.assertEqual(snap["derivatives"][0]["code"], "FTX6")


class BlackRock(unittest.TestCase):
    def test_parse_keys_bonds_by_maturity_and_skips_cash(self):
        body = ("﻿截至,\"2026年10月19日\"\n \nTicker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Par Value,Price,Location,Exchange,Currency,Duration,FX Rate,Maturity,Coupon (%),Market Currency,Effective Date\n"
                '"TLTW","iShares TLT","ETF(指數股票型基金)","Fixed Income","1","20.04","1","354,782.00","354,782.00","1","美國","-","TWD","1","1","-","0.00","USD","x"\n'
                '"CVS","CVS HEALTH","企業債券","Fixed Income","1","1.52","1","575,000.00","575,000.00","1","美國","-","TWD","1","1","2044年6月1日","6.00","USD","x"\n'
                '"CVS","CVS HEALTH","企業債券","Fixed Income","1","0.80","1","5.00","5.00","1","美國","-","TWD","1","1","2048年3月25日","5.05","USD","x"\n'
                '"HSBFT","CASH COLLATERAL","現金及衍生性商品","Cash Collateral and Margins","1","1.38","1","1","1","1","美國","-","TWD","0","1","-","0","USD","x"\n'
                '"USZ6","US LONG BOND","現金及衍生性商品","Futures","0","0.00","1","10.00","10.00","1","美國","-","TWD","1","1","-","0","USD","x"\n').encode("utf-8")
        snap = e.parse_blackrock(body, "00985D", "src")
        self.assertEqual(snap["as_of"], "2026-10-19")
        self.assertEqual(len(snap["holdings"]), 3)
        self.assertEqual(len({h["code"] for h in snap["holdings"]}), 3)
        self.assertEqual(snap["holdings"][0]["asset_type"], "fund")
        self.assertEqual(snap["derivatives"][0]["code"], "USZ6")


class Dispatch(unittest.TestCase):
    def test_new_issuers_are_supported(self):
        for code in ("00415A", "00996A", "00994A", "00408A", "00410A", "00986A", "00987A", "00401A", "00989A", "00985D"):
            self.assertTrue(e.supported_issuer(code), code)
        self.assertFalse(e.supported_issuer("00406A"))   # 中信：業主尚未自行同意使用條款


if __name__ == "__main__":
    unittest.main()
