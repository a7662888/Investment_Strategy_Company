# -*- coding: utf-8 -*-
"""
選股股池(universe)管理:依「近 N 日平均成交額(流動性)」+ 跨產業分散,
每月離線重算出 60 檔可交易名單,寫成 active_universe.json(committed,durable)。

依據(basis):流動性 = FinMind TaiwanStockPrice 的 Trading_money(成交金額)近 lookback 日平均。
只用 ≤ as_of 的資料(PIT)。Render 為 ephemeral,故「持久更新」= 離線跑 run_universe_refresh.py 後 commit。
app.py 透過 load_active_universe() 載入;檔案不存在時 fallback 到 DEFAULT_60。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from company.data.single_stock import _fetch  # 重用 FinMind 抓取

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PATH = ROOT / "model_artifacts" / "active_universe.json"

# 母池:約 80 檔流動性佳、跨產業的上市股(前段較大型,select 會依實際成交額重排)
# 格式:(代號, 名稱, 產業)
MASTER_POOL = [
    ("2330", "台積電", "半導體"), ("2454", "聯發科", "半導體"), ("2317", "鴻海", "電子代工"),
    ("2308", "台達電", "電源/AI伺服器"), ("2382", "廣達", "AI伺服器"), ("2891", "中信金", "金融"),
    ("2881", "富邦金", "金融"), ("2882", "國泰金", "金融"), ("3231", "緯創", "AI伺服器"),
    ("2303", "聯電", "半導體"), ("3711", "日月光投控", "半導體"), ("2412", "中華電", "電信"),
    ("2603", "長榮", "航運"), ("2886", "兆豐金", "金融"), ("2884", "玉山金", "金融"),
    ("2357", "華碩", "AI伺服器"), ("3034", "聯詠", "IC設計"), ("2379", "瑞昱", "IC設計"),
    ("6669", "緯穎", "AI伺服器"), ("2345", "智邦", "網通"), ("2376", "技嘉", "AI伺服器"),
    ("3017", "奇鋐", "散熱"), ("2356", "英業達", "AI伺服器"), ("4938", "和碩", "電子代工"),
    ("2327", "國巨", "被動元件"), ("3008", "大立光", "光學"), ("2885", "元大金", "金融"),
    ("2892", "第一金", "金融"), ("2890", "永豐金", "金融"), ("2880", "華南金", "金融"),
    ("5880", "合庫金", "金融"), ("2887", "台新金", "金融"), ("2609", "陽明", "航運"),
    ("2615", "萬海", "航運"), ("2610", "華航", "航運"), ("2618", "長榮航", "航運"),
    ("2002", "中鋼", "原物料"), ("1301", "台塑", "塑化"), ("1303", "南亞", "塑化"),
    ("1326", "台化", "塑化"), ("1101", "台泥", "水泥"), ("1216", "統一", "食品"),
    ("2912", "統一超", "零售"), ("2207", "和泰車", "汽車"), ("9921", "巨大", "自行車"),
    ("9914", "美利達", "自行車"), ("3045", "台灣大", "電信"), ("4904", "遠傳", "電信"),
    ("3037", "欣興", "PCB"), ("8046", "南電", "ABF載板"), ("2368", "金像電", "PCB"),
    ("2383", "台光電", "PCB"), ("6213", "聯茂", "PCB"), ("3443", "創意", "ASIC"),
    ("3529", "力旺", "矽智財"), ("5269", "祥碩", "IC設計"), ("4966", "譜瑞-KY", "IC設計"),
    ("3661", "世芯-KY", "ASIC"), ("6415", "矽力-KY", "IC設計"), ("2408", "南亞科", "記憶體"),
    ("2344", "華邦電", "記憶體"), ("2409", "友達", "面板"), ("3481", "群創", "面板"),
    ("2474", "可成", "機殼"), ("3406", "玉晶光", "光學"), ("2301", "光寶科", "電子零組件"),
    ("3324", "雙鴻", "散熱"), ("2371", "大同", "重電"), ("1605", "華新", "電線電纜"),
    ("1519", "華城", "重電"), ("1513", "中興電", "重電"), ("2105", "正新", "輪胎"),
    ("9910", "豐泰", "製鞋"), ("9904", "寶成", "製鞋"), ("2542", "興富發", "營建"),
    ("6446", "藥華藥", "生技"), ("1795", "美時", "生技"), ("2049", "上銀", "機械"),
    ("1590", "亞德客-KY", "機械"), ("2731", "雄獅", "觀光"), ("2727", "王品", "餐飲"),
]

# 靜態 fallback 60(母池前 60,確保 active_universe.json 不存在時仍有 60 檔)
DEFAULT_60 = MASTER_POOL[:60]

PER_SECTOR_CAP = 12   # 單一產業最多入選檔數,確保分散


def _as_tw(code: str) -> str:
    return code if code.endswith(".TW") else f"{code}.TW"


def _avg_turnover(code: str, as_of: str, lookback: int = 60, token: Optional[str] = None) -> Optional[float]:
    """近 lookback 交易日平均成交額(Trading_money);只用 ≤ as_of 資料。失敗回 None。"""
    start = (datetime.fromisoformat(as_of) - timedelta(days=lookback * 2 + 40)).date().isoformat()
    try:
        df = _fetch("TaiwanStockPrice", code, start, as_of, token)
    except Exception:
        return None
    if df.empty or "Trading_money" not in df.columns:
        return None
    df = df[df["date"] <= as_of].tail(lookback)
    if len(df) < lookback // 2:
        return None
    vals = pd.to_numeric(df["Trading_money"], errors="coerce").dropna()
    return float(vals.mean()) if len(vals) else None


def select_universe(n: int = 60, as_of: Optional[str] = None, per_sector_cap: int = PER_SECTOR_CAP,
                    token: Optional[str] = None, sleep: float = 0.3) -> dict:
    """依近 60 日平均成交額排序 + 各產業上限,選出 n 檔。回傳含依據的 dict。"""
    as_of = as_of or datetime.now().date().isoformat()
    token = token or os.environ.get("FINMIND_TOKEN")
    ranked = []
    for code, name, sector in MASTER_POOL:
        to = _avg_turnover(code, as_of, token=token)
        if to is not None and to > 0:
            ranked.append({"symbol": _as_tw(code), "name": name, "sector": sector, "avg_turnover": round(to, 0)})
        time.sleep(sleep)  # 輕量節流,避免 FinMind rate limit
    ranked.sort(key=lambda x: x["avg_turnover"], reverse=True)

    # 跨產業分散:貪婪選取,單一產業不超過 per_sector_cap
    chosen, sector_cnt = [], {}
    for r in ranked:
        if len(chosen) >= n:
            break
        if sector_cnt.get(r["sector"], 0) < per_sector_cap:
            chosen.append(r)
            sector_cnt[r["sector"]] = sector_cnt.get(r["sector"], 0) + 1
    # 若因產業上限湊不滿 n,放寬補足
    if len(chosen) < n:
        for r in ranked:
            if len(chosen) >= n:
                break
            if r not in chosen:
                chosen.append(r)
    for i, r in enumerate(chosen, 1):
        r["rank"] = i
    return {
        "generated_at": datetime.now().isoformat(),
        "as_of": as_of,
        "basis": "近 60 交易日平均成交額(流動性)排序 + 各產業上限分散",
        "per_sector_cap": per_sector_cap,
        "n": len(chosen),
        "candidates_evaluated": len(ranked),
        "stocks": chosen,
    }


def save_active_universe(data: dict) -> None:
    ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_active_universe(fallback: Optional[list[dict]] = None) -> list[dict]:
    """供 app.py 載入 DISCOVERY_UNIVERSE。回傳 [{symbol,name,sector}, ...]。"""
    if ACTIVE_PATH.exists():
        try:
            data = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
            if stocks:
                return [{"symbol": s["symbol"], "name": s["name"], "sector": s.get("sector", "")} for s in stocks]
        except Exception:
            pass
    if fallback:
        return fallback
    return [{"symbol": _as_tw(c), "name": nm, "sector": sec} for c, nm, sec in DEFAULT_60]
