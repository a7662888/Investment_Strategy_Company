# -*- coding: utf-8 -*-
"""
測試收盤自動化推送腳本的格式化與發送邏輯。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from run_daily_push import format_telegram_html, format_discord_markdown, send_telegram_message, send_discord_message

class TestDailyPush(unittest.TestCase):
    
    def setUp(self):
        # 準備測試用的模擬數據
        self.as_of = "2026-06-05"
        self.market_info = {
            "close": 21858.38,
            "change_percent": -0.20,
            "risk_label": "🟢 綠色 · 正常選股",
            "regime": "區間整理",
            "buy_exposure": "100%",
            "open_guide": "依各 Agent 推薦名單開盤限額進場",
            "decision_reasons": ["大盤當日收盤下跌 -0.20%"]
        }
        self.plans = [
            {
                "symbol": "2330.TW",
                "action": "明日研究買進候選",
                "grade": "B",
                "grade_label": "B級 偏高折價",
                "score": 75.0,
                "reasons": ["超賣反彈(14日RSI=22.30)"],
                "held": False
            },
            {
                "symbol": "2317.TW",
                "action": "明日續抱",
                "grade": "B",
                "grade_label": "B級 偏高折價",
                "score": 68.0,
                "reasons": ["短中期上升結構"],
                "held": True
            }
        ]
        self.ai_reports = [
            {
                "symbol": "2330.TW",
                "name": "台積電",
                "analysis": "### 🤖 AI 智能解讀\n基本面優異。\n### 📋 操盤檢核表 (Checklist)\n- [ ] 監測 MA20 支撐\n- [x] 檢查成交量"
            }
        ]

    def test_format_telegram_html(self):
        # 測試 Telegram HTML 格式化
        html = format_telegram_html(self.as_of, self.market_info, self.plans, self.ai_reports)
        
        self.assertIn("台股收盤決策與明日計畫 (2026-06-05)", html)
        self.assertIn("<b>大盤市場狀態 (TAIEX)</b>", html)
        self.assertIn("21858.38", html)
        self.assertIn("🟢 綠色 · 正常選股", html)
        self.assertIn("<b>2330.TW 台積電</b>", html)
        self.assertIn("明日研究買進候選", html)
        self.assertIn("🤖 <b>中長期 AI 智能診斷報告</b>", html)
        self.assertIn("👉 <b>🤖 AI 智能解讀</b>", html)
        self.assertIn("☐ 監測 MA20 支撐", html)
        self.assertIn("☑ 檢查成交量", html)

    def test_format_discord_markdown(self):
        # 測試 Discord Markdown 格式化
        md = format_discord_markdown(self.as_of, self.market_info, self.plans, self.ai_reports)
        
        self.assertIn("# 📅 台股收盤決策與明日計畫 (2026-06-05)", md)
        self.assertIn("## 📊 大盤市場狀態 (TAIEX)", md)
        self.assertIn("`21858.38`", md)
        self.assertIn("**2330.TW 台積電**", md)
        self.assertIn("### 🟩 【2330.TW 台積電】", md)

    @patch("urllib.request.urlopen")
    def test_send_telegram_message(self, mock_urlopen):
        # 測試 Telegram 訊息發送成功
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"ok": true}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        ok = send_telegram_message("token", "chat_id", "content")
        self.assertTrue(ok)

    @patch("urllib.request.urlopen")
    def test_send_discord_message(self, mock_urlopen):
        # 測試 Discord 訊息發送成功 (通常回傳 204 No Content，無 response 內容)
        mock_response = MagicMock()
        mock_response.status = 204
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        ok = send_discord_message("http://discord.com/webhook", "content")
        self.assertTrue(ok)

if __name__ == "__main__":
    unittest.main()
