# -*- coding: utf-8 -*-
"""
中期潛力股 3-6M 評分與合理價區間計算引擎。
採用 100 分制「長期潛力股評分」框架，區分內在價值低估與中期催化劑。
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Optional, Any

from company.data.single_stock import load as load_single_stock

# 預設產業分類對照表
SECTOR_MAP = {
    "2330": "半導體", "2454": "半導體", "2303": "半導體", "3711": "半導體",
    "2379": "IC設計", "3034": "IC設計", "2317": "電子代工", "2308": "電源/AI伺服器",
    "2382": "AI伺服器", "3231": "AI伺服器", "2356": "AI伺服器", "3017": "散熱",
    "3443": "ASIC", "6669": "AI伺服器", "2327": "被動元件", "8046": "ABF載板",
    "2002": "原物料", "1301": "塑化", "1303": "塑化", "2603": "航運",
    "2609": "航運", "2615": "航運", "2881": "金融", "2882": "金融",
    "2891": "金融", "2412": "電信", "3045": "電信"
}

def calculate_potential_score(symbol: str, as_of: str) -> dict:
    """
    計算給定個股在中期(3-6M)視角下的潛力評分、合理價區間與操作建議。
    完全遵守 Point-in-Time 視角，只使用截止日以前的資料。
    """
    clean_sym = symbol.split(".")[0]
    as_of_ts = pd.Timestamp(as_of)
    
    # 載入 as_of 日期前 365 天的歷史資料，確保有足夠的 lookback 區間
    start_dt = (as_of_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    end_dt = as_of_ts.strftime("%Y-%m-%d")
    
    # 初始化預設的回傳結構
    fallback_res = {
        "symbol": symbol,
        "name": symbol,
        "score": 0.0,
        "grade": "D",
        "grade_label": "D級 追高風險",
        "close": 0.0,
        "fair_range": [0.0, 0.0],
        "undervaluation_pct": 0.0,
        "safety_margin": 0.0,
        "catalysts": "暫無資料",
        "warnings": ["載入資料失敗或歷史數據不足"],
        "buy_range": "暫無資料",
        "stop_loss": "暫無資料",
        "take_profit": "暫無資料",
        "valuation_score": 0.0,
        "growth_score": 0.0,
        "quality_score": 0.0,
        "catalyst_score": 0.0,
        "risk_score": 0.0
    }
    
    try:
        stock_data = load_single_stock(clean_sym, start_dt, end_dt)
        view = stock_data.view(as_of_ts)
    except Exception as e:
        fallback_res["warnings"] = [f"無法載入該股快取資料: {str(e)}"]
        return fallback_res

    # 1. 取得歷史價量與 PER/PBR
    hist_df = view.history()
    if hist_df is None or len(hist_df) < 30:
        fallback_res["warnings"] = ["歷史股價交易日數不足 30 天，無法評分"]
        return fallback_res
        
    closes = hist_df["close"].astype(float).tolist()
    volumes = hist_df["volume"].astype(float).tolist()
    highs = hist_df["high"].astype(float).tolist()
    lows = hist_df["low"].astype(float).tolist()
    dates = hist_df.index.strftime('%Y-%m-%d').tolist()
    
    close = closes[-1]
    per = view.per()
    pbr = view.pbr()
    rev_yoy = view.rev_yoy()
    
    # 取得歷史 PER/PBR 中位數 (250 天 lookback)
    s_per = view._data._per
    sub_per = s_per.loc[s_per.index <= view.as_of]
    
    div_yield = 0.0
    if len(sub_per) and "dividend_yield" in sub_per.columns:
        div_yield = float(sub_per["dividend_yield"].iloc[-1]) if not pd.isna(sub_per["dividend_yield"].iloc[-1]) else 0.0

    has_per = "PER" in sub_per.columns
    has_pbr = "PBR" in sub_per.columns

    hist_per_sub = sub_per.tail(250)
    median_per = float(hist_per_sub["PER"].median()) if has_per and len(hist_per_sub) and not hist_per_sub["PER"].isna().all() else 15.0
    median_pbr = float(hist_per_sub["PBR"].median()) if has_pbr and len(hist_per_sub) and not hist_per_sub["PBR"].isna().all() else 2.0

    # 避免除以零或無意義的負值
    eps = (close / per) if (per is not None and per > 0) else None
    bps = (close / pbr) if (pbr is not None and pbr > 0) else None

    # 2. 合理價估計 (Fair Value Center)
    pe_fair = (eps * median_per) if eps is not None else None
    pb_fair = (bps * median_pbr) if bps is not None else None
    
    sector = SECTOR_MAP.get(clean_sym, "其他")
    
    # 依板塊特性選擇估值模型
    if sector in ("金融", "塑化", "原物料", "航運") or per is None or per <= 0:
        fair_price = pb_fair if pb_fair is not None else close
        method = "PBR 淨值比模型"
    else:
        if pe_fair is not None and pb_fair is not None:
            fair_price = 0.7 * pe_fair + 0.3 * pb_fair
            method = "PER/PBR 混合模型"
        elif pe_fair is not None:
            fair_price = pe_fair
            method = "PER 本益比模型"
        else:
            fair_price = pb_fair if pb_fair is not None else close
            method = "PBR 淨值比模型"

    # 防呆：避免極端值
    fair_price = max(0.4 * close, min(2.5 * close, fair_price))
    
    fair_range_low = round(fair_price * 0.9, 1)
    fair_range_high = round(fair_price * 1.1, 1)
    
    # 安全邊際與低估幅度
    safety_margin = (fair_price - close) / fair_price * 100.0
    undervaluation_pct = safety_margin

    # ----------------------------------------------------
    # 各面向得分計算
    # ----------------------------------------------------
    
    # 1) 估值便宜度 (Valuation Score - max 25)
    # 安全邊際占 20 分，殖利率占 5 分
    val_pts = 0.0
    if safety_margin >= 25.0:
        val_pts = 20.0
    elif safety_margin >= 0.0:
        val_pts = 5.0 + (safety_margin / 25.0) * 15.0
    else:
        val_pts = max(0.0, 5.0 + (safety_margin / 20.0) * 5.0)
        
    div_pts = 0.0
    if div_yield > 0:
        if div_yield >= 5.0:
            div_pts = 5.0
        else:
            div_pts = (div_yield / 5.0) * 5.0
            
    valuation_score = round(min(25.0, val_pts + div_pts), 1)

    # 2) 成長動能 (Growth Score - max 25)
    # 營收年增率占 12 分，營收加速占 5 分，EPS趨勢占 8 分
    rev_pts = 0.0
    if rev_yoy is not None:
        if rev_yoy >= 0.20:
            rev_pts = 12.0
        elif rev_yoy >= 0.0:
            rev_pts = 4.0 + (rev_yoy / 0.20) * 8.0
        else:
            rev_pts = max(0.0, 4.0 + (rev_yoy / 0.20) * 4.0)
            
    accel_pts = 0.0
    prev_avg_yoy = 0.0
    if len(view._data._rev_yoy) > 0:
        rev_sub = view._data._rev_yoy.loc[view._data._rev_yoy.index <= view.as_of].tail(3)
        if len(rev_sub) >= 2:
            prev_avg_yoy = float(rev_sub.iloc[:-1].mean())
            if (rev_yoy or 0.0) > prev_avg_yoy:
                accel_pts = 5.0
                
    eps_pts = 0.0
    # 建立合併價格與 PER 的 DataFrame 來算歷史 EPS 趨勢
    df_prices = pd.DataFrame({"close": closes}, index=pd.to_datetime(dates))
    if has_per and has_pbr:
        merged_df = pd.merge(df_prices, sub_per[["PER", "PBR"]], left_index=True, right_index=True, how="inner")
        if not merged_df.empty and "PER" in merged_df.columns and "PBR" in merged_df.columns:
            merged_df["EPS"] = merged_df["close"] / merged_df["PER"]
            merged_df["ROE"] = merged_df["PBR"] / merged_df["PER"]
        else:
            merged_df = pd.DataFrame(columns=["close", "PER", "PBR", "EPS", "ROE"])
    else:
        merged_df = pd.DataFrame(columns=["close", "PER", "PBR", "EPS", "ROE"])
    
    if len(merged_df) >= 90:
        eps_recent = merged_df["EPS"].tail(10).mean()
        eps_prev = merged_df["EPS"].iloc[-90:-60].mean()
        if not pd.isna(eps_recent) and not pd.isna(eps_prev) and eps_recent > eps_prev:
            eps_pts += 5.0
            if eps_recent > eps_prev * 1.05:
                eps_pts += 3.0
                
    growth_score = round(min(25.0, rev_pts + accel_pts + eps_pts), 1)

    # 3) 財務品質 (Financial Quality Score - max 20)
    # ROE水準占 12 分，ROE穩定度占 4 分，融資槓桿安全性占 4 分
    roe = (pbr / per) if (per is not None and per > 0 and pbr is not None and pbr > 0) else 0.0
    roe_pts = 0.0
    if roe >= 0.15:
        roe_pts = 12.0
    elif roe >= 0.08:
        roe_pts = 4.0 + ((roe - 0.08) / 0.07) * 8.0
    else:
        roe_pts = max(0.0, (roe / 0.08) * 4.0)
        
    stab_pts = 2.0
    if "ROE" in merged_df.columns:
        roe_std = merged_df["ROE"].tail(60).std()
        if not pd.isna(roe_std) and roe_std < 0.02:
            stab_pts = 4.0
            
    margin_chg = view.margin_balance_chg_5()
    margin_pts = 0.0
    if margin_chg <= 0:
        margin_pts = 4.0
    else:
        margin_pts = max(0.0, 4.0 - margin_chg * 2.0)
        
    quality_score = round(min(20.0, roe_pts + stab_pts + margin_pts), 1)

    # 4) 催化劑強度 (Catalyst Score - max 15)
    # 營收年增加速占 3 分，法人吸籌占 5 分，籌碼共振占 2 分，均線/量能 breakout 占 5 分
    cat_rev = 3.0 if (rev_yoy is not None and rev_yoy >= 0.10 and accel_pts > 0) else 0.0
    
    inst_net_20 = view.inst_net(20)
    avg_vol = float(np.mean(volumes[-60:])) if len(volumes) >= 60 else 1.0
    cat_inst = 0.0
    if inst_net_20 > 0.005 * avg_vol:
        cat_inst = 5.0
    elif inst_net_20 > 0:
        cat_inst = 3.0
        
    foreign_today = view.foreign_net_daily()
    trust_today = view.trust_net_daily()
    if foreign_today > 0 and trust_today > 0:
        cat_inst += 2.0
        
    cat_breakout = 0.0
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else close
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else close
    if close > ma20:
        cat_breakout += 1.5
    if close > ma60:
        cat_breakout += 1.5
        
    avg_vol5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0.0
    avg_vol60 = sum(volumes[-60:]) / 60 if len(volumes) >= 60 else 1.0
    if avg_vol60 > 0 and avg_vol5 > 1.4 * avg_vol60:
        cat_breakout += 2.0
        
    catalyst_score = round(min(15.0, cat_rev + cat_inst + cat_breakout), 1)

    # 5) 風險控制 (Risk Score - max 15)
    # 波動度占 5 分，大盤及回撤控制占 5 分，籌碼穩定度占 5 分
    returns = [closes[i]/closes[i-1] - 1.0 for i in range(len(closes)-20, len(closes))] if len(closes) >= 21 else []
    vol = float(np.std(returns)) if returns else 0.03
    vol_pts = 0.0
    if vol < 0.025:
        vol_pts = 5.0
    elif vol < 0.045:
        vol_pts = 3.0
    else:
        vol_pts = 1.0
        
    high_120 = max(highs[-120:]) if len(highs) >= 120 else max(highs)
    dd = (close / high_120 - 1.0) * 100.0
    dd_pts = 0.0
    if dd >= -12.0:
        dd_pts = 5.0
    elif dd >= -25.0:
        dd_pts = 3.0
    else:
        dd_pts = 1.0
        
    margin_stability = 5.0
    if margin_chg > 1.2:
        margin_stability = 2.0
        
    risk_score = round(min(15.0, vol_pts + dd_pts + margin_stability), 1)

    # ----------------------------------------------------
    # 價值陷阱與警訊扣分
    # ----------------------------------------------------
    deductions = 0.0
    warnings = []
    
    # 價值陷阱扣分：低本益比但營收年增顯著衰退
    if per is not None and per < 10.0 and rev_yoy is not None and rev_yoy < -0.05:
        deductions += 15.0
        warnings.append("價值陷阱警告：本益比極低但營收持續衰退，可能進入結構性衰退。")
        
    # ROE 下滑警訊
    if "ROE" in merged_df.columns and len(merged_df) >= 90:
        roe_recent = merged_df["ROE"].tail(10).mean()
        roe_prev = merged_df["ROE"].iloc[-90:-60].mean()
        if not pd.isna(roe_recent) and not pd.isna(roe_prev) and roe_recent < roe_prev - 0.03:
            deductions += 10.0
            warnings.append("獲利品質警訊：股東權益報酬率 (ROE) 近期顯著下滑。")
            
    # 中線空頭扣分
    if close < ma60:
        deductions += 5.0
        warnings.append("趨勢偏弱：股價位於 60 日均線下方，中線偏弱。")

    # ----------------------------------------------------
    # 計算最終評分與等級
    # ----------------------------------------------------
    final_score = valuation_score + growth_score + quality_score + catalyst_score + risk_score - deductions
    final_score = max(0.0, min(100.0, round(final_score, 1)))
    
    # A 級門檻：分數 >= 70 且安全邊際 (折價幅度) >= 15%
    if final_score >= 70.0:
        if safety_margin >= 15.0:
            grade = "A"
            grade_label = "A級 長期潛力股"
        else:
            grade = "B"
            grade_label = "B級 觀察股"
            # 移除預設的「無顯著轉弱警訊」以顯示安全邊際不足警告
            if "無顯著價值陷阱或轉弱警訊，財務結構穩健。" in warnings:
                warnings.remove("無顯著價值陷阱或轉弱警訊，財務結構穩健。")
            warnings.append("⚠️ 估值偏高 (安全邊際 < 15%)：雖評分達 A 級標準，但為防追高降為 B 級觀察股。")
    elif final_score >= 50.0:
        grade = "B"
        grade_label = "B級 觀察股"
    elif final_score >= 35.0:
        grade = "C"
        grade_label = "C級 避免"
    else:
        grade = "D"
        grade_label = "D級 追高風險"

    # ----------------------------------------------------
    # 建議操作指引
    # ----------------------------------------------------
    buy_low = round(fair_price * 0.75, 1)
    buy_high = round(fair_price * 0.85, 1)
    if close < buy_high:
        buy_range = f"{buy_low} - {buy_high} 元 (目前已進入合理買進區間)"
    else:
        buy_range = f"{buy_low} - {buy_high} 元 (目前股價偏高，建議靜待拉回)"
        
    stop_loss_price = round(close * 0.90, 1)
    stop_loss = f"股價跌破 {stop_loss_price} 元 (約 -10%)，或營收年增率 (YoY) 連續 2 個月衰退幅度擴大。"
    
    tp_low = round(fair_price * 1.0, 1)
    tp_high = round(fair_price * 1.1, 1)
    take_profit = f"股價達合理區間 {tp_low} - {tp_high} 元，或法人持續 5 日出貨、營收加速動能消失。"

    # 整理催化劑文字
    cats = []
    if rev_yoy is not None and rev_yoy >= 0.10:
        cats.append("月營收雙位數成長")
    if accel_pts > 0:
        cats.append("營收加速增長")
    if inst_net_20 > 0.005 * avg_vol:
        cats.append("三大法人強勢吸籌")
    elif inst_net_20 > 0:
        cats.append("法人近20日淨買")
    if foreign_today > 0 and trust_today > 0:
        cats.append("外資投信今日同買")
    if close > ma20 and close > ma60:
        cats.append("站穩MA20與MA60")
    if avg_vol60 > 0 and avg_vol5 > 1.4 * avg_vol60:
        cats.append("5日成交量突破")
        
    catalysts_text = " + ".join(cats) if cats else "尚無明顯中期催化點，以區間整理為主"

    # 若無警告則補一個友善提示
    if not warnings:
        warnings = ["無顯著價值陷阱或轉弱警訊，財務結構穩健。"]

    # 取得股票名稱
    stock_name = clean_sym
    try:
        from company.data.universe import RAW_CANDIDATES, load_active_universe
        temp_map = {item[0]: item[1] for item in RAW_CANDIDATES}
        try:
            active_univ = load_active_universe()
            for s in active_univ:
                code = s["symbol"].split(".")[0]
                temp_map[code] = s["name"]
        except Exception:
            pass
        stock_name = temp_map.get(clean_sym, clean_sym)
    except Exception:
        pass

    return {
        "symbol": symbol,
        "name": stock_name,
        "score": final_score,
        "grade": grade,
        "grade_label": grade_label,
        "close": round(close, 1),
        "fair_range": [fair_range_low, fair_range_high],
        "undervaluation_pct": round(undervaluation_pct, 1),
        "safety_margin": round(safety_margin, 1),
        "catalysts": catalysts_text,
        "warnings": warnings,
        "buy_range": buy_range,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "valuation_score": valuation_score,
        "growth_score": growth_score,
        "quality_score": quality_score,
        "catalyst_score": catalyst_score,
        "risk_score": risk_score
    }
