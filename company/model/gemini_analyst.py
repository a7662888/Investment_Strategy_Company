# -*- coding: utf-8 -*-
"""
Gemini AI 智能選股分析模組。
採用純標準函式庫 (urllib/json) 實作，以支援輕量級 Render 生產環境運作。
當 API 金鑰未設定或請求失敗時，會自動安全退回「規則引擎 (Rule-based)」分析，確保服務高可用性。
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse
from typing import Optional

def analyze_stock_with_ai(symbol: str, name: str, quant_data: dict, news_headlines: list[str]) -> str:
    """
    結合量化指標與實時新聞，利用 Gemini AI 生成台股個股分析與操作建議。
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    
    # 建立 Prompt
    prompt = f"""
你是一位專業的台股投資分析師助理。請根據以下個股量化數據與近期新聞，為【{symbol} {name}】生成一份【繁體中文】的中期 (3-6個月) 投資評估。

## 量化指標
- 目前收盤價: {quant_data.get('close', 0.0)} 元
- 長期潛力得分 (100分制): {quant_data.get('score', 0.0)} 分 (評級: {quant_data.get('grade_label', 'D')})
- 保守合理價區間: {quant_data.get('fair_range', [0.0, 0.0])[0]} - {quant_data.get('fair_range', [0.0, 0.0])[1]} 元
- 安全邊際 (折價率): {quant_data.get('safety_margin', 0.0)}%
- 各分項得分: 估值 {quant_data.get('valuation_score', 0.0)}/25 | 成長 {quant_data.get('growth_score', 0.0)}/25 | 品質 {quant_data.get('quality_score', 0.0)}/20 | 催化劑 {quant_data.get('catalyst_score', 0.0)}/15 | 風險 {quant_data.get('risk_score', 0.0)}/15
- 系統警訊: {', '.join(quant_data.get('warnings', []))}
- 建議分批買進區間: {quant_data.get('buy_range', '無')}
- 系統催化點: {quant_data.get('catalysts', '無')}

## 相關近期新聞
{chr(10).join(['- ' + n for n in news_headlines[:5]]) if news_headlines else '- 暫無即時新聞'}

請依據上述數據及新聞進行綜合評估，產出以下結構的繁體中文分析（請控制在 250 字以內，排版清爽，避免廢話，不加引號或多餘前言）：

### 🤖 AI 智能解讀 (約150字)
[綜合分析此股在當前估值與催化劑下的機會與價值陷阱]

### 📋 操盤檢核表 (Checklist)
- [ ] [填入一項針對此股未來 3 個月最需要追蹤的關鍵指標/催化點]
- [ ] [填入一項此股最需要注意的下行風險或財報警戒線]
"""

    if not gemini_key:
        print("[Gemini Analyst] GEMINI_API_KEY 未設定，使用規則引擎生成分析。")
        return generate_rule_based_analysis(symbol, name, quant_data, news_headlines)
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ]
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            
        # 解析 Gemini 回傳內容
        output_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
        return output_text.strip()
        
    except Exception as e:
        print(f"[Gemini Analyst] 呼叫 Gemini API 失敗: {e}，自動退回規則引擎。")
        return generate_rule_based_analysis(symbol, name, quant_data, news_headlines)

def generate_rule_based_analysis(symbol: str, name: str, quant_data: dict, news_headlines: list[str]) -> str:
    """
    本地規則引擎 (Rule-based) 降級分析生成器。
    """
    score = quant_data.get('score', 0.0)
    grade = quant_data.get('grade', 'D')
    margin = quant_data.get('safety_margin', 0.0)
    warnings = quant_data.get('warnings', [])
    catalysts = quant_data.get('catalysts', '無')
    
    # 根據評分與警訊推導結論
    if grade == "A":
        interpretation = f"個股量化評分高達 {score} 分，且安全邊際達 {margin}%，屬低估之優質標的。結合催化點（{catalysts}），中期有強烈重新定價動能，適合逢低分批建倉。"
        checklist_items = [
            f"追高風險極低，重點監測月營收成長是否持續大於 10% 以印證成長性。",
            f"檢查法人買盤（投信/外資）是否出現連續 3 日轉賣出貨跡象。"
        ]
    elif grade == "B":
        if margin < 15.0:
            interpretation = f"個股基本面優異且動能強勁，但目前折價僅 {margin}%，安全邊際不足，估值稍顯偏高。建議切勿盲目追高，靜待回檔至分批買進區間 {quant_data.get('buy_range', '合理帶')} 再行承接。"
            checklist_items = [
                f"監測技術面回檔至 20 日均線 (MA20) 附近的支撐力道。",
                f"追蹤主力大戶持股比例是否隨股價回檔而流失。"
            ]
        else:
            interpretation = f"個股估值雖便宜（安全邊際 {margin}%），但中期催化劑偏弱或面臨一定警訊。屬於「便宜但需時間等待重新定價」的標的，建議小量試單，以時間換取空間。"
            checklist_items = [
                f"觀察未來一至兩季財報，確認是否有營收或毛利率見底反彈訊號。",
                f"跌破停損條件時是否果斷執行風險控制。"
            ]
    else: # C 或 D
        trap_warning = ""
        if any("價值陷阱" in w for w in warnings):
            trap_warning = " 且疑似面臨本益比極低但營收持續衰退之『價值陷阱』，"
        interpretation = f"個股量化評分偏低 ({score} 分){trap_warning}警訊偏多（{', '.join(warnings[:2])}）。目前處於趨勢走弱或高估區間，強烈建議保守觀望，暫避開此類標的。"
        checklist_items = [
            f"關注營收年增率 (YoY) 何時能連續 2 個月衰退幅度收斂。",
            f"密切注意股價是否在 60 日季線下方持續沉澱，靜待均線糾結轉多。"
        ]
        
    checklist_md = "\n".join([f"- [ ] {item}" for item in checklist_items])
    
    return f"""### 🤖 AI 智能解讀 (規則引擎降級)
{interpretation}

### 📋 操盤檢核表 (Checklist)
{checklist_md}"""
