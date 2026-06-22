# -*- coding: utf-8 -*-
"""
即時 RSS 新聞抓取與解析模組。
採用純標準函式庫實作，不依賴第三方套件。
"""
from __future__ import annotations

import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

def fetch_rss_news(query: str = "台股", limit: int = 5, before_date: Optional[str] = None) -> list[dict]:
    """
    從 Google News TW RSS 抓取即時中文財經新聞。
    支援依據 before_date (YYYY-MM-DD 格式) 過濾發佈時間，確保 Point-in-Time 安全。
    
    回傳格式：
    [
        {
            "id": int,
            "title": str,
            "link": str,
            "time": str,          # 格式化後的時間顯示，如 "2026-06-22 12:00"
            "category": str,      # 預設為 "個股焦點" 或 "大盤市場"
            "source": str         # 來源媒體名稱
        },
        ...
    ]
    """
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    results = []
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        
        # 解析基準過濾日期
        limit_dt = None
        if before_date:
            try:
                # 將 YYYY-MM-DD 轉為當日結束的 datetime (含時區 UTC)
                limit_dt = datetime.fromisoformat(before_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            except Exception:
                pass
                
        idx = 1
        for item in items:
            title_raw = item.find("title")
            link_raw = item.find("link")
            pub_date_raw = item.find("pubDate")
            source_raw = item.find("source")
            
            title = title_raw.text if title_raw is not None else ""
            link = link_raw.text if link_raw is not None else ""
            pub_date_str = pub_date_raw.text if pub_date_raw is not None else ""
            source = source_raw.text if source_raw is not None else "網路新聞"
            
            # 解析 RFC 2822 日期時間
            pub_dt = None
            time_display = "未知時間"
            if pub_date_str:
                try:
                    pub_dt = parsedate_to_datetime(pub_date_str)
                    # 轉成本地時區或 UTC 呈現
                    time_display = pub_dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            
            # Point-in-Time 檢查：發佈時間必須小於或等於基準日期
            if limit_dt and pub_dt and pub_dt > limit_dt:
                continue
                
            # 去除標題末尾的來源名稱 (例如 "台積電創新高 - 自由時報" -> "台積電創新高")
            clean_title = title
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                clean_title = parts[0]
                source = parts[1]
                
            results.append({
                "id": idx,
                "title": clean_title,
                "link": link,
                "time": time_display,
                "category": "個股焦點" if query != "台股" and query != "加權指數" else "大盤市場",
                "source": source
            })
            
            idx += 1
            if len(results) >= limit:
                break
                
    except Exception as e:
        # 即使報錯也優雅回傳空陣列，不致使主程式崩潰
        print(f"[RSS News] 抓取 RSS 發生錯誤: {e}")
        
    return results
