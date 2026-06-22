# -*- coding: utf-8 -*-
"""
台股收盤決策與 AI 智能分析自動推送腳本。
執行流程：
1. 分析大盤當前 Regime 與風險燈號。
2. 針對掃描個股生成明日操作計畫。
3. 挑選出精選個股（如 A 級或觸發明日加碼、買進之標的），串接 Gemini AI 或本地規則引擎生成中長期智能診斷。
4. 將格式化後的精美報告透過 Webhook 推送至 Discord 與 Telegram 頻道。

相容性：純標準函式庫實作，支援 Render 免費層無相依環境。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 設定專案路徑
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
from company.screener.potential_3_6m import calculate_potential_score
from company.model.gemini_analyst import analyze_stock_with_ai
from company.data.news_rss import fetch_rss_news

def send_telegram_message(token: str, chat_id: str, html_content: str) -> bool:
    """
    發送 HTML 格式訊息至 Telegram 頻道/群組。
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": html_content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data.get("ok", False)
    except Exception as e:
        print(f"[Telegram Push] 發送失敗: {e}")
        return False

def send_discord_message(webhook_url: str, markdown_content: str) -> bool:
    """
    發送 Markdown 格式訊息至 Discord Webhook。
    """
    # Discord 每篇訊息限制 2000 字元，若超出則進行拆分
    chunks = []
    current_chunk = ""
    for line in markdown_content.split("\n"):
        if len(current_chunk) + len(line) + 1 > 1950:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line
    if current_chunk:
        chunks.append(current_chunk)
        
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    success = True
    for idx, chunk in enumerate(chunks):
        payload = {
            "content": chunk
        }
        try:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                # Discord 成功時通常回傳 204 No Content
                if response.status not in (200, 204):
                    print(f"[Discord Push] 收到異常回應代碼: {response.status}")
                    success = False
        except Exception as e:
            print(f"[Discord Push] 發送區段 {idx+1} 失敗: {e}")
            success = False
            
    return success

def format_telegram_html(as_of: str, market_info: dict | None, plans: list[dict], ai_reports: list[dict]) -> str:
    """
    格式化 Telegram 的 HTML 訊息內容。
    """
    lines = []
    lines.append(f"📅 <b>台股收盤決策與明日計畫 ({as_of})</b>\n")
    
    # 1. 大盤狀態
    if market_info:
        lines.append("📊 <b>大盤市場狀態 (TAIEX)</b>")
        lines.append(f"• 收盤價: <code>{market_info.get('close', 0.0)}</code> (變動: {market_info.get('change_percent', 0.0):+.2f}%)")
        lines.append(f"• 風險燈號: {market_info.get('risk_label', '未知')}")
        lines.append(f"• 市場 Regime: <b>{market_info.get('regime', '未知')}</b>")
        lines.append(f"• 持股曝險: <code>{market_info.get('buy_exposure', '100%')}</code>")
        lines.append(f"• 開盤指引: <i>{market_info.get('open_guide', '無')}</i>")
        lines.append(f"• 決策依據: {', '.join(market_info.get('decision_reasons', []))}")
        lines.append("")
    else:
        lines.append("📊 <i>暫無今日大盤指數分析資料</i>\n")
        
    # 2. 明日操作計畫
    lines.append("📝 <b>精選個股明日操作建議</b>")
    active_plans = []
    passive_plans = []
    
    for plan in plans:
        # 明確有交易意圖或 A 級的才特別拉出來，續抱/觀察等低優先級標的合併簡寫
        act = plan.get("action", "")
        grade = plan.get("grade", "D")
        if "買進" in act or "加碼" in act or "減碼" in act or "賣出" in act or grade in ("A", "B"):
            active_plans.append(plan)
        else:
            passive_plans.append(plan)
            
    if active_plans:
        for plan in active_plans:
            sym = plan.get("symbol", "")
            name = sym
            try:
                from company.data.universe import RAW_CANDIDATES
                temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
                name = temp_map.get(sym.split(".")[0], sym)
            except Exception:
                pass
                
            act = plan.get("action", "續抱")
            grade_lbl = plan.get("grade_label", "D")
            score = plan.get("score", 0.0)
            reason = plan.get("reasons", ["無"])[0] if plan.get("reasons") else "無"
            
            lines.append(f"• <b>{sym} {name}</b> | <pre>{grade_lbl} ({score}分)</pre>")
            lines.append(f"  └ <b>計畫：{act}</b>")
            lines.append(f"  └ <i>原因：{reason}</i>")
    else:
        lines.append("• <i>今日無觸發特殊操作之個股計畫。</i>")
        
    if passive_plans:
        tickers = []
        for plan in passive_plans:
            sym = plan.get("symbol", "")
            name = sym
            try:
                from company.data.universe import RAW_CANDIDATES
                temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
                name = temp_map.get(sym.split(".")[0], sym)
            except Exception:
                pass
            tickers.append(f"{name}({sym.split('.')[0]})")
        lines.append(f"\n• <b>其他續抱/觀察個股</b>: {', '.join(tickers)}")
        
    lines.append("")
    
    # 3. AI 智能診斷 (僅針對精選個股)
    if ai_reports:
        lines.append("🤖 <b>中長期 AI 智能診斷報告</b>")
        for rpt in ai_reports:
            lines.append(f"<b>【{rpt['symbol']} {rpt['name']}】</b>")
            # 轉換 markdown 粗體與標題/列表為 HTML
            html_text = rpt["analysis"]
            lines_analysis = []
            import re
            for l in html_text.split("\n"):
                l_strip = l.strip()
                if l_strip.startswith("### "):
                    lines_analysis.append(f"\n👉 <b>{l_strip[4:].strip()}</b>")
                elif l_strip.startswith("## "):
                    lines_analysis.append(f"\n👉 <b>{l_strip[3:].strip()}</b>")
                elif l_strip.startswith("# "):
                    lines_analysis.append(f"\n👉 <b>{l_strip[2:].strip()}</b>")
                elif l_strip.startswith("- [ ] "):
                    lines_analysis.append(f"☐ {l_strip[6:].strip()}")
                elif l_strip.startswith("- [x] "):
                    lines_analysis.append(f"☑ {l_strip[6:].strip()}")
                elif l_strip.startswith("- "):
                    lines_analysis.append(f"• {l_strip[2:].strip()}")
                else:
                    # 替換 **bold** 為 <b>bold</b>
                    processed = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", l_strip)
                    lines_analysis.append(processed)
            lines.append("\n".join(lines_analysis))
            lines.append("--------------------")
            
    lines.append("\n⚠️ <i>買賣僅作研究與模擬用途，非投資建議。</i>")
    return "\n".join(lines)

def format_discord_markdown(as_of: str, market_info: dict | None, plans: list[dict], ai_reports: list[dict]) -> str:
    """
    格式化 Discord 的 Markdown 訊息內容。
    """
    lines = []
    lines.append(f"# 📅 台股收盤決策與明日計畫 ({as_of})")
    lines.append("---")
    
    # 1. 大盤狀態
    if market_info:
        lines.append("## 📊 大盤市場狀態 (TAIEX)")
        lines.append(f"* **收盤價**: `{market_info.get('close', 0.0)}` (變動: {market_info.get('change_percent', 0.0):+.2f}%)")
        lines.append(f"* **風險燈號**: {market_info.get('risk_label', '未知')}")
        lines.append(f"* **市場 Regime**: **{market_info.get('regime', '未知')}**")
        lines.append(f"* **持股曝險**: `{market_info.get('buy_exposure', '100%')}`")
        lines.append(f"* **開盤指引**: *{market_info.get('open_guide', '無')}*")
        lines.append(f"* **決策依據**: {', '.join(market_info.get('decision_reasons', []))}")
        lines.append("")
    else:
        lines.append("## 📊 *暫無今日大盤指數分析資料*\n")
        
    # 2. 明日操作計畫
    lines.append("## 📝 精選個股明日操作建議")
    active_plans = []
    passive_plans = []
    
    for plan in plans:
        act = plan.get("action", "")
        grade = plan.get("grade", "D")
        if "買進" in act or "加碼" in act or "減碼" in act or "賣出" in act or grade in ("A", "B"):
            active_plans.append(plan)
        else:
            passive_plans.append(plan)
            
    if active_plans:
        for plan in active_plans:
            sym = plan.get("symbol", "")
            name = sym
            try:
                from company.data.universe import RAW_CANDIDATES
                temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
                name = temp_map.get(sym.split(".")[0], sym)
            except Exception:
                pass
                
            act = plan.get("action", "續抱")
            grade_lbl = plan.get("grade_label", "D")
            score = plan.get("score", 0.0)
            reason = plan.get("reasons", ["無"])[0] if plan.get("reasons") else "無"
            
            lines.append(f"* **{sym} {name}** | `{grade_lbl} ({score}分)`")
            lines.append(f"  * **計畫：{act}**")
            lines.append(f"  * *原因：{reason}*")
    else:
        lines.append("* *今日無觸發特殊操作之個股計畫。*")
        
    if passive_plans:
        tickers = []
        for plan in passive_plans:
            sym = plan.get("symbol", "")
            name = sym
            try:
                from company.data.universe import RAW_CANDIDATES
                temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
                name = temp_map.get(sym.split(".")[0], sym)
            except Exception:
                pass
            tickers.append(f"{name}({sym.split('.')[0]})")
        lines.append(f"\n* **其他續抱/觀察個股**: {', '.join(tickers)}")
        
    lines.append("")
    
    # 3. AI 智能診斷 (僅針對精選個股)
    if ai_reports:
        lines.append("## 🤖 中長期 AI 智能診斷報告")
        for rpt in ai_reports:
            lines.append(f"### 🟩 【{rpt['symbol']} {rpt['name']}】")
            lines.append(rpt["analysis"])
            lines.append("---")
            
    lines.append("\n*⚠️ 買賣僅作研究與模擬用途，非投資建議。*")
    return "\n".join(lines)

def load_positions() -> list[dict]:
    """
    從 config/positions.json 載入持倉資料，如果不存在則回傳空列表。
    """
    pos_path = ROOT / "config" / "positions.json"
    if pos_path.exists():
        try:
            with pos_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Positions] 載入失敗: {e}")
    return []

def main():
    ap = argparse.ArgumentParser(description="台股決策日計畫與 AI 診斷推送服務")
    ap.add_argument("--date", default=None, help="目標基準日期 (YYYY-MM-DD，預設為今日)")
    ap.add_argument("--symbols", default=None, help="自訂掃描股票代號 (逗號分隔，例如 2330.TW,2317.TW)")
    ap.add_argument("--telegram", action="store_true", help="強制發送 Telegram 推送")
    ap.add_argument("--discord", action="store_true", help="強制發送 Discord 推送")
    ap.add_argument("--dry-run", action="store_true", help="乾跑模式，僅在終端機輸出結果而不發送推送")
    args = ap.parse_args()

    # 1. 決定目標日期
    as_of = args.date
    if not as_of:
        # 取台北時間 (UTC+8)
        tz_taipei = timezone(timedelta(hours=8))
        as_of = datetime.now(tz_taipei).date().isoformat()
        
    print(f"[*] 啟動收盤自動推送任務，基準日期: {as_of}")

    # 2. 分析大盤
    market_info = app.analyze_market_index(as_of)
    market_regime = market_info["regime"] if market_info else None
    risk_level = market_info.get("risk_level") if market_info else None
    
    if market_info:
        print(f"[+] 大盤狀態: {market_info['regime']} | 風險級別: {market_info['risk_level']}")
    else:
        print("[!] 警告: 無法獲取大盤數據")

    # 3. 載入掃描股票與模擬持股
    symbols_to_scan = []
    if args.symbols:
        symbols_to_scan = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols_to_scan = [item["symbol"] for item in app.DEFAULT_SYMBOLS]
        
    raw_positions = load_positions()
    positions_dict = app.normalize_positions(raw_positions)
    
    # 如果有持股且不在 symbols_to_scan 中，將其加入
    for sym in positions_dict:
        if sym not in symbols_to_scan:
            symbols_to_scan.append(sym)
            
    print(f"[*] 預備掃描個股數量: {len(symbols_to_scan)}")

    # 4. 生成個股操作計畫
    plans = []
    start_fetch = (datetime.fromisoformat(as_of) - timedelta(days=320)).date().isoformat()
    end_exclusive = (datetime.fromisoformat(as_of) + timedelta(days=1)).date().isoformat()
    
    for symbol in symbols_to_scan:
        try:
            rows = app.fetch_history(symbol, start_fetch, end_exclusive)
            # 過濾 lookahead
            rows = [r for r in rows if r["date"] <= as_of]
            if len(rows) >= 80:
                plan = app.plan_next_session(
                    symbol, 
                    rows, 
                    positions_dict.get(symbol), 
                    market_regime=market_regime, 
                    risk_level=risk_level, 
                    market_info=market_info
                )
                plans.append(plan)
            else:
                print(f"[-] 忽略 {symbol}: 歷史價格天數不足 80 天 (僅 {len(rows)} 天)")
        except Exception as e:
            print(f"[!] 處理 {symbol} 時發生錯誤: {e}")
            
    # 排序：持股優先，再按評分排序
    plans.sort(key=lambda item: (not item["held"], -item["score"]))

    # 5. 挑選重點個股進行 AI 智能分析 (限額最多 3 檔以防 API 超載)
    # 挑選規則：優先選 Action 是明日加碼/研究買進，或是評級為 A 的股票
    ai_candidate_plans = []
    for plan in plans:
        act = plan.get("action", "")
        grade = plan.get("grade", "D")
        if "加碼" in act or "買進" in act or grade == "A":
            ai_candidate_plans.append(plan)
            
    # 若不足則按分數補齊
    if len(ai_candidate_plans) < 2:
        for plan in plans:
            if plan not in ai_candidate_plans:
                ai_candidate_plans.append(plan)
            if len(ai_candidate_plans) >= 2:
                break
                
    ai_reports = []
    for plan in ai_candidate_plans[:2]:
        sym = plan["symbol"]
        try:
            # 獲取量化數據與近期新聞
            quant_data = calculate_potential_score(sym, as_of)
            clean_sym = sym.split(".")[0]
            stock_name = clean_sym
            try:
                from company.data.universe import RAW_CANDIDATES
                temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
                stock_name = temp_map.get(clean_sym, clean_sym)
            except Exception:
                pass
                
            news_items = fetch_rss_news(stock_name, limit=5, before_date=as_of)
            news_headlines = [item["title"] for item in news_items]
            
            # 執行 AI 診斷
            print(f"[*] 執行 {sym} {stock_name} 的 AI 智能診斷...")
            analysis = analyze_stock_with_ai(sym, stock_name, quant_data, news_headlines)
            
            ai_reports.append({
                "symbol": sym,
                "name": stock_name,
                "analysis": analysis
            })
        except Exception as e:
            print(f"[!] {sym} AI 診斷失敗: {e}")

    # 6. 生成推送內容
    tg_text = format_telegram_html(as_of, market_info, plans, ai_reports)
    dc_text = format_discord_markdown(as_of, market_info, plans, ai_reports)

    # 7. 執行推送
    if args.dry_run:
        print("\n=== [DRY RUN] 輸出 Telegram HTML 內容 ===")
        print(tg_text)
        print("\n=== [DRY RUN] 輸出 Discord Markdown 內容 ===")
        print(dc_text)
    else:
        # 讀取系統環境變數或直接由參數觸發
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        dc_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
        
        # Telegram 推送
        if (args.telegram or (tg_token and tg_chat_id)) and not args.dry_run:
            if not tg_token or not tg_chat_id:
                print("[Telegram Push] 錯誤: 未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 環境變數")
            else:
                print("[*] 正在發送 Telegram 訊息...")
                ok = send_telegram_message(tg_token, tg_chat_id, tg_text)
                if ok:
                    print("[+] Telegram 訊息發送成功")
                else:
                    print("[!] Telegram 訊息發送失敗")
                    
        # Discord 推送
        if (args.discord or dc_webhook) and not args.dry_run:
            if not dc_webhook:
                print("[Discord Push] 錯誤: 未設定 DISCORD_WEBHOOK_URL 環境變數")
            else:
                print("[*] 正在發送 Discord 訊息...")
                ok = send_discord_message(dc_webhook, dc_text)
                if ok:
                    print("[+] Discord 訊息發送成功")
                else:
                    print("[!] Discord 訊息發送失敗")

    print("[*] 自動推送任務執行完畢。")

if __name__ == "__main__":
    main()
