# -*- coding: utf-8 -*-
"""
純標準函式庫的 FinMind 資料獲取與快取工具。
供 Render 生產環境 (app.py / score.py) 使用，無 pandas/numpy 相依。
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

API_URL = "https://api.finmindtrade.com/api/v4/data"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data_cache"


def _http_get_json(dataset: str, symbol: str, start_date: str, end_date: str, token: str | None = None) -> list[dict]:
    params = {
        "dataset": dataset,
        "data_id": symbol,
        "start_date": start_date,
        "end_date": end_date
    }
    if token:
        params["token"] = token
    
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == 200:
                return data.get("data", [])
    except Exception as e:
        print(f"[FinMind Raw] Error fetching {dataset} for {symbol}: {e}")
    return []


def get_latest_metrics(symbol: str, token: str | None = None) -> dict:
    """
    獲取最新 30 天的籌碼與融資券資料，以及最新 3 個月的月營收。
    快取於本地，快取效期 12 小時。
    """
    # symbol on Render is like "2330.TW", we need just "2330" for FinMind
    clean_sym = symbol.split(".")[0]
    
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{clean_sym}_extra_raw.json"
    
    # 檢查快取是否有效 (12小時)
    if cache_path.exists():
        try:
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
            if datetime.now() - mtime < timedelta(hours=12):
                with cache_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass

    # 開始下載
    token = token or os.environ.get("FINMIND_TOKEN")
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_30_str = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    start_90_str = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
    
    # 1. 三大法人
    chips_raw = _http_get_json("TaiwanStockInstitutionalInvestorsBuySell", clean_sym, start_30_str, today_str, token)
    # 2. 融資融券
    margin_raw = _http_get_json("TaiwanStockMarginPurchaseShortSale", clean_sym, start_30_str, today_str, token)
    # 3. 月營收
    rev_raw = _http_get_json("TaiwanStockMonthRevenue", clean_sym, start_90_str, today_str, token)
    
    # 解析法人淨買超
    foreign_net = {}
    trust_net = {}
    for item in chips_raw:
        dt = item.get("date")
        if not dt:
            continue
        net = int(item.get("buy", 0)) - int(item.get("sell", 0))
        name = item.get("name")
        if name == "Foreign_Investor":
            foreign_net[dt] = foreign_net.get(dt, 0) + net
        elif name == "Investment_Trust":
            trust_net[dt] = trust_net.get(dt, 0) + net
            
    # 解析融資券餘額
    margin_bal = {}
    short_bal = {}
    for item in margin_raw:
        dt = item.get("date")
        if not dt:
            continue
        margin_bal[dt] = int(item.get("MarginPurchaseTodayBalance", 0))
        short_bal[dt] = int(item.get("ShortSaleTodayBalance", 0))
        
    # 解析月營收 YoY
    # 由於需要 pct_change(12) 算 YoY，如果只抓 3 個月，沒辦法算。
    # 為了避開對 Render 進行大批量歷史營收抓取 (FinMind 免費額度限制)，
    # 我們在本地訓練時把算好的 YoY 存進快取，或者線上直接利用 FinMind 月營收的 YoY。
    # 等等！FinMind 月營收表其實沒有直接給 YoY。
    # 但如果我們抓取最近 3 個月的營收，我們可以搭配之前已有的 YoY，或者簡單處理：
    # 其實最安全的做法是：月營收年增率我們直接透過 API 獲取。
    # Wait, can we fetch TaiwanStockMonthRevenue for the last 2 years?
    # Last 2 years is only 24 records, which is extremely small!
    # Yes, 2 years of monthly revenue is only 24 items, so fetching 2 years of monthly revenue is very fast and safe!
    # Let's fetch last 2 years (750 days) of monthly revenue:
    start_2y_str = (datetime.now() - timedelta(days=780)).strftime("%Y-%m-%d")
    rev_2y_raw = _http_get_json("TaiwanStockMonthRevenue", clean_sym, start_2y_str, today_str, token)
    
    # Sort and compute YoY
    rev_sorted = sorted(rev_2y_raw, key=lambda x: (x.get("revenue_year", 0), x.get("revenue_month", 0)))
    rev_yoy = {}
    
    # Map from (year, month) to revenue
    rev_map = {}
    for r in rev_sorted:
        y = r.get("revenue_year")
        m = r.get("revenue_month")
        rev_map[(y, m)] = float(r.get("revenue", 0))
        
    # Calculate YoY for each month
    for r in rev_sorted:
        y = r.get("revenue_year")
        m = r.get("revenue_month")
        prev_y = y - 1
        if (prev_y, m) in rev_map and rev_map[(prev_y, m)] > 0:
            yoy = rev_map[(y, m)] / rev_map[(prev_y, m)] - 1.0
            # Announce date: roughly next month's 10th
            # Let's say it's announced on the 10th of next month
            ann_m = m + 1 if m < 12 else 1
            ann_y = y if m < 12 else y + 1
            ann_date = f"{ann_y}-{ann_m:02d}-10"
            rev_yoy[ann_date] = yoy

    # 彙整結果
    result = {
        "symbol": symbol,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "foreign_net": foreign_net,
        "trust_net": trust_net,
        "margin_bal": margin_bal,
        "short_bal": short_bal,
        "rev_yoy": rev_yoy
    }
    
    # 寫入快取
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
        
    return result


def align_extra_features(
    dates: list[str],
    symbol: str,
    token: str | None = None
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """
    根據傳入的價格日期列表，對齊最新下載的籌碼與融資券資料。
    回傳: foreign_net, trust_net, margin_purchase, short_sale, revenue_yoy
    """
    n = len(dates)
    foreign = [0.0] * n
    trust = [0.0] * n
    margin = [0.0] * n
    short = [0.0] * n
    rev_yoy = [0.0] * n
    
    try:
        metrics = get_latest_metrics(symbol, token)
    except Exception as e:
        print(f"[FinMind Raw] Failed to load metrics: {e}")
        return foreign, trust, margin, short, rev_yoy
        
    f_map = metrics.get("foreign_net", {})
    t_map = metrics.get("trust_net", {})
    m_map = metrics.get("margin_bal", {})
    s_map = metrics.get("short_bal", {})
    r_map = metrics.get("rev_yoy", {})
    
    # Align by date
    # For margin balance, we carry forward the latest balance if not updated daily
    last_m = 0.0
    last_s = 0.0
    
    # For revenue YoY, we carry forward the latest announced YoY
    last_r = 0.0
    # Pre-populate initial values by scanning historically sorted rev_yoy keys
    r_keys = sorted(r_map.keys())
    
    for i, d in enumerate(dates):
        # Format date to YYYY-MM-DD
        dt_str = d.split("T")[0]
        
        # Foreign / Trust net buy
        foreign[i] = float(f_map.get(dt_str, 0.0))
        trust[i] = float(t_map.get(dt_str, 0.0))
        
        # Margin / Short balance (carry forward)
        if dt_str in m_map:
            last_m = float(m_map[dt_str])
        margin[i] = last_m
        
        if dt_str in s_map:
            last_s = float(s_map[dt_str])
        short[i] = last_s
        
        # Revenue YoY (carry forward announced)
        # Find the latest announce date <= dt_str
        for r_k in r_keys:
            if r_k <= dt_str:
                last_r = float(r_map[r_k])
            else:
                break
        rev_yoy[i] = last_r
        
    return foreign, trust, margin, short, rev_yoy
