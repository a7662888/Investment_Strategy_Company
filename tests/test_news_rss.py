# -*- coding: utf-8 -*-
"""
測試 RSS 新聞解析與 Point-in-Time 歷史過濾機制。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO
from company.data.news_rss import fetch_rss_news

MOCK_RSS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>\xe5\x8f\xb0\xe7\xa9\x8d\xe9\x9b\xbb\xe5\x89\xb5\xe6\xad\xb7\xe5\x8f\xb2\xe6\x96\xb0\xe9\xab\x98\xe9\xa0\x98\xe6\xbc\xb2\xe5\x8f\xb0\xe8\x82\xa1 - \xe8\x87\xaa\xe7\x94\xb1\xe6\x99\x82\xe5\xa0\xb1</title>
      <link>https://news.google.com/item1</link>
      <pubDate>Mon, 22 Jun 2026 12:00:00 GMT</pubDate>
      <source url="https://www.ltn.com.tw">\xe8\x87\xaa\xe7\x94\xb1\xe6\x99\x82\xe5\xa0\xb1</source>
    </item>
    <item>
      <title>\xe5\x9c\x8b\xe5\xb7\xa8\xe8\xa2\xab\xe5\x8b\x95\xe5\x85\x83\xe4\xbb\xb6\xe9\x9c\x80\xe6\xb1\x82\xe5\xbc\xb7\xe5\x8b\x81 - \xe5\xb7\xa5\xe5\x95\x86\xe6\x99\x82\xe5\xa0\xb1</title>
      <link>https://news.google.com/item2</link>
      <pubDate>Sun, 21 Jun 2026 09:30:00 GMT</pubDate>
      <source url="https://www.ctee.com.tw">\xe5\xb7\xa5\xe5\x95\x86\xe6\x99\x82\xe5\xa0\xb1</source>
    </item>
    <item>
      <title>\xe6\x9c\xaa\xe4\xbe\x86\xe7\x8d\xb8\xe4\xbb\xb6\xe6\xb8\xac\xe8\xa9\xa6\xe6\x96\xb0\xe8\x81\x9e - \xe6\x9c\xaa\xe4\xbe\x86\xe5\xaa\x92\xe9\xab\x94</title>
      <link>https://news.google.com/item3</link>
      <pubDate>Wed, 24 Jun 2026 15:00:00 GMT</pubDate>
      <source url="https://www.future.com.tw">\xe6\x9c\xaa\xe4\xbe\x86\xe5\xaa\x92\xe9\xab\x94</source>
    </item>
  </channel>
</rss>
"""

class TestNewsRSS(unittest.TestCase):
    
    @patch("urllib.request.urlopen")
    def test_fetch_rss_news_basic(self, mock_urlopen):
        # 模擬網路回傳
        mock_response = MagicMock()
        mock_response.read.return_value = MOCK_RSS_XML
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # 測試不帶日期的抓取
        results = fetch_rss_news("台積電", limit=5)
        
        # 驗證長度與欄位
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "台積電創歷史新高領漲台股")
        self.assertEqual(results[0]["source"], "自由時報")
        self.assertEqual(results[1]["title"], "國巨被動元件需求強勁")
        self.assertEqual(results[1]["source"], "工商時報")
        
    @patch("urllib.request.urlopen")
    def test_fetch_rss_news_pit_filtering(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = MOCK_RSS_XML
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # 測試帶 before_date="2026-06-22" 的 Point-in-Time 過濾
        # 發佈時間大於 2026-06-22 23:59:59 的新聞應該被過濾
        results = fetch_rss_news("台積電", limit=5, before_date="2026-06-22")
        
        # item3 是 2026-06-24，應該被過濾，所以只會回傳 2 筆
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "台積電創歷史新高領漲台股")
        self.assertEqual(results[1]["title"], "國巨被動元件需求強勁")
        
    @patch("urllib.request.urlopen")
    def test_fetch_rss_news_limit(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = MOCK_RSS_XML
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # 測試 limit 參數
        results = fetch_rss_news("台積電", limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "台積電創歷史新高領漲台股")

    def test_fetch_rss_news_network_failure_graceful(self):
        # 測試當網路發生錯誤或連線逾時，函式應優雅回傳空陣列而非崩潰
        with patch("urllib.request.urlopen", side_effect=Exception("Timeout / DNS resolution error")):
            results = fetch_rss_news("台積電")
            self.assertEqual(results, [])

if __name__ == "__main__":
    unittest.main()
