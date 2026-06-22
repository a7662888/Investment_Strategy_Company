# -*- coding: utf-8 -*-
"""
測試 Gemini AI 智能分析與降級規則引擎。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from company.model.gemini_analyst import analyze_stock_with_ai, generate_rule_based_analysis

class TestGeminiAnalyst(unittest.TestCase):
    
    def test_generate_rule_based_analysis_grade_a(self):
        # 測試規則引擎 - A級股票
        quant_data = {
            "score": 85.0,
            "grade": "A",
            "grade_label": "A級 長投加碼",
            "safety_margin": 25.0,
            "warnings": [],
            "catalysts": "營收持續增長",
            "buy_range": "800 - 830 元"
        }
        res = generate_rule_based_analysis("2330.TW", "台積電", quant_data, [])
        
        self.assertIn("AI 智能解讀", res)
        self.assertIn("85.0 分", res)
        self.assertIn("25.0%", res)
        self.assertIn("營收持續增長", res)
        self.assertIn("Checklist", res)

    def test_generate_rule_based_analysis_grade_b_low_margin(self):
        # 測試規則引擎 - B級且安全邊際不足股票
        quant_data = {
            "score": 75.0,
            "grade": "B",
            "grade_label": "B級 偏高折價",
            "safety_margin": 5.0,
            "warnings": [],
            "catalysts": "新產品推出",
            "buy_range": "600 - 620 元"
        }
        res = generate_rule_based_analysis("2327.TW", "國巨", quant_data, [])
        
        self.assertIn("安全邊際不足", res)
        self.assertIn("5.0%", res)
        self.assertIn("600 - 620 元", res)
        self.assertIn("MA20", res)

    def test_generate_rule_based_analysis_grade_d(self):
        # 測試規則引擎 - D級股票 / 價值陷阱
        quant_data = {
            "score": 35.0,
            "grade": "D",
            "grade_label": "D級 追高風險",
            "safety_margin": -10.0,
            "warnings": ["本益比偏高", "營收年增率衰退 (價值陷阱)"],
            "catalysts": "無",
            "buy_range": "無資料"
        }
        res = generate_rule_based_analysis("2412.TW", "中華電", quant_data, [])
        
        self.assertIn("價值陷阱", res)
        self.assertIn("趨勢走弱", res)
        self.assertIn("YoY) 何時能", res)

    @patch.dict("os.environ", {}, clear=True)
    def test_analyze_stock_with_ai_no_key_fallback(self):
        # 測試當沒有環境變數 GEMINI_API_KEY 時，應自動降級至規則引擎
        quant_data = {
            "score": 85.0,
            "grade": "A",
            "grade_label": "A級 長投加碼",
            "safety_margin": 25.0,
            "warnings": [],
            "catalysts": "營收持續增長",
            "buy_range": "800 - 830 元"
        }
        res = analyze_stock_with_ai("2330.TW", "台積電", quant_data, [])
        self.assertIn("規則引擎降級", res)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key_123"})
    @patch("urllib.request.urlopen")
    def test_analyze_stock_with_ai_success(self, mock_urlopen):
        # 測試當有金鑰且 API 請求成功時
        mock_response = MagicMock()
        
        # 直接使用包含真實中文字的 JSON 字串編碼為 UTF-8 bytes，完全避免 JSON 中的非法反斜線轉義問題
        success_json = '{"candidates": [{"content": {"parts": [{"text": "### 🤖 AI 智能解讀\\n速讀台積電的機會。\\n\\n### 📋 操盤檢核表 (Checklist)\\n- [ ] 追蹤 MA20 支撐"}]}}]}'
        mock_response.read.return_value = success_json.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        quant_data = {
            "score": 85.0,
            "grade": "A",
            "grade_label": "A級 長投加碼",
            "safety_margin": 25.0,
            "warnings": [],
            "catalysts": "營收持續增長",
            "buy_range": "800 - 830"
        }
        res = analyze_stock_with_ai("2330.TW", "台積電", quant_data, ["新聞標題1", "新聞標題2"])
        
        self.assertIn("AI 智能解讀", res)
        self.assertIn("速讀台積電的機會", res)
        self.assertIn("Checklist", res)
        self.assertIn("追蹤 MA20 支撐", res)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key_123"})
    @patch("urllib.request.urlopen", side_effect=Exception("API limit or Network error"))
    def test_analyze_stock_with_ai_api_failure_fallback(self, mock_urlopen):
        # 測試當 API 呼叫失敗時，也應安全降級至規則引擎而不崩潰
        quant_data = {
            "score": 85.0,
            "grade": "A",
            "grade_label": "A級 長投加碼",
            "safety_margin": 25.0,
            "warnings": [],
            "catalysts": "營收持續增長",
            "buy_range": "800 - 830"
        }
        res = analyze_stock_with_ai("2330.TW", "台積電", quant_data, [])
        self.assertIn("規則引擎降級", res)

if __name__ == "__main__":
    unittest.main()
