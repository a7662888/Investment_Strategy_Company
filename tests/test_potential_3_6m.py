# -*- coding: utf-8 -*-
"""
測試中期潛力股 3-6M 評分與合理價區間系統。
"""
from __future__ import annotations

import unittest
import pandas as pd
from company.screener.potential_3_6m import calculate_potential_score

class TestPotential3_6M(unittest.TestCase):
    
    def test_calculate_potential_score_valid(self):
        # 測試台積電 (2330) 在 2026-06-05 的評分與合理價計算
        res = calculate_potential_score("2330.TW", "2026-06-05")
        
        # 驗證回傳結構中的所有重要欄位
        self.assertEqual(res["symbol"], "2330.TW")
        self.assertIn("score", res)
        self.assertIn("grade", res)
        self.assertIn("grade_label", res)
        self.assertIn("close", res)
        self.assertIn("fair_range", res)
        self.assertIn("undervaluation_pct", res)
        self.assertIn("safety_margin", res)
        self.assertIn("catalysts", res)
        self.assertIn("warnings", res)
        self.assertIn("buy_range", res)
        self.assertIn("stop_loss", res)
        self.assertIn("take_profit", res)
        
        # 驗證分數區間
        self.assertTrue(0.0 <= res["score"] <= 100.0)
        self.assertIn(res["grade"], ["A", "B", "C", "D"])
        
        # 驗證合理價區間合理性
        self.assertTrue(res["fair_range"][0] <= res["fair_range"][1])
        self.assertTrue(res["fair_range"][0] > 0.0)
        
        # 驗證各子分數
        self.assertTrue(0.0 <= res["valuation_score"] <= 25.0)
        self.assertTrue(0.0 <= res["growth_score"] <= 25.0)
        self.assertTrue(0.0 <= res["quality_score"] <= 20.0)
        self.assertTrue(0.0 <= res["catalyst_score"] <= 15.0)
        self.assertTrue(0.0 <= res["risk_score"] <= 15.0)

    def test_calculate_potential_score_financials(self):
        # 測試金融股富邦金 (2881)
        res = calculate_potential_score("2881.TW", "2026-06-05")
        self.assertEqual(res["symbol"], "2881.TW")
        self.assertTrue(0.0 <= res["score"] <= 100.0)
        self.assertTrue(res["fair_range"][0] > 0.0)

    def test_calculate_potential_score_invalid(self):
        # 測試不存在的代號，應該回傳預設結構並附帶警訊
        res = calculate_potential_score("9999.TW", "2026-06-05")
        self.assertEqual(res["symbol"], "9999.TW")
        self.assertEqual(res["score"], 0.0)
        self.assertEqual(res["grade"], "D")
        self.assertTrue(any("無法載入" in w or "不足" in w for w in res["warnings"]))

if __name__ == "__main__":
    unittest.main()
