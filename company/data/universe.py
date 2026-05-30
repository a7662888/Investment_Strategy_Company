# -*- coding: utf-8 -*-
"""
三層漏斗股池管理:
  1) 母池(月更,100 檔):從 RAW_CANDIDATES 依「近 60 日平均成交額」排序 + 跨產業分散選 100。
  2) 週選(週更,30 檔):從母池 100 依「加權複合分」(成交額 + 動能,可調權重)+ 產業上限選 30。
  3) 每日:三家 Agent 從週選 30 檔推薦(app.py 的 DISCOVERY_UNIVERSE = 週 30)。

依據可調(可加權):週選複合分 = w_liq×成交額百分位 + w_mom×60日動能百分位。
全程只用 ≤ as_of 資料(PIT)。Render ephemeral → 持久更新需離線跑 run_universe_refresh.py 後 commit。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional



ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = ROOT / "model_artifacts" / "active_pool.json"        # 母池 100(月)
ACTIVE_PATH = ROOT / "model_artifacts" / "active_universe.json"  # 週選 30(供 app.py)

# 選股參數
POOL_N = 100
WEEKLY_N = 30
POOL_SECTOR_CAP = 20      # 母池單一產業上限
WEEKLY_SECTOR_CAP = 8     # 週選單一產業上限
W_LIQUIDITY = 0.6         # 週選加權:成交額(流動性)
W_MOMENTUM = 0.4          # 週選加權:60 日動能

# RAW 候選池(~120 檔流動性佳、跨產業上市股;月更從這裡選 100)
RAW_CANDIDATES = [
    ("2330", "台積電", "半導體"), ("2454", "聯發科", "半導體"), ("2303", "聯電", "半導體"),
    ("3711", "日月光投控", "半導體"), ("2379", "瑞昱", "IC設計"), ("3034", "聯詠", "IC設計"),
    ("3443", "創意", "ASIC"), ("3661", "世芯-KY", "ASIC"), ("3529", "力旺", "矽智財"),
    ("5269", "祥碩", "IC設計"), ("4966", "譜瑞-KY", "IC設計"), ("6415", "矽力-KY", "IC設計"),
    ("8299", "群聯", "IC設計"), ("3014", "聯陽", "IC設計"), ("3257", "虹冠電", "IC設計"),
    ("2408", "南亞科", "記憶體"), ("2344", "華邦電", "記憶體"), ("2337", "旺宏", "記憶體"),
    ("2317", "鴻海", "電子代工"), ("4938", "和碩", "電子代工"), ("2382", "廣達", "AI伺服器"),
    ("3231", "緯創", "AI伺服器"), ("2356", "英業達", "AI伺服器"), ("6669", "緯穎", "AI伺服器"),
    ("2376", "技嘉", "AI伺服器"), ("2357", "華碩", "AI伺服器"), ("2308", "台達電", "電源"),
    ("2301", "光寶科", "電子零組件"), ("2345", "智邦", "網通"), ("3702", "大聯大", "通路"),
    ("3017", "奇鋐", "散熱"), ("3324", "雙鴻", "散熱"), ("2327", "國巨", "被動元件"),
    ("2492", "華新科", "被動元件"), ("8046", "南電", "ABF載板"), ("3037", "欣興", "PCB"),
    ("2368", "金像電", "PCB"), ("2383", "台光電", "PCB"), ("6213", "聯茂", "PCB"),
    ("3044", "健鼎", "PCB"), ("3008", "大立光", "光學"), ("3406", "玉晶光", "光學"),
    ("2474", "可成", "機殼"), ("2409", "友達", "面板"), ("3481", "群創", "面板"),
    ("2881", "富邦金", "金融"), ("2882", "國泰金", "金融"), ("2891", "中信金", "金融"),
    ("2886", "兆豐金", "金融"), ("2884", "玉山金", "金融"), ("2885", "元大金", "金融"),
    ("2892", "第一金", "金融"), ("2880", "華南金", "金融"), ("5880", "合庫金", "金融"),
    ("2890", "永豐金", "金融"), ("2887", "台新金", "金融"), ("2883", "凱基金", "金融"),
    ("2603", "長榮", "航運"), ("2609", "陽明", "航運"), ("2615", "萬海", "航運"),
    ("2610", "華航", "航運"), ("2618", "長榮航", "航運"), ("2002", "中鋼", "原物料"),
    ("2027", "大成鋼", "原物料"), ("1301", "台塑", "塑化"), ("1303", "南亞", "塑化"),
    ("1326", "台化", "塑化"), ("6505", "台塑化", "塑化"), ("1101", "台泥", "水泥"),
    ("1102", "亞泥", "水泥"), ("1216", "統一", "食品"), ("1227", "佳格", "食品"),
    ("2912", "統一超", "零售"), ("2207", "和泰車", "汽車"), ("9921", "巨大", "自行車"),
    ("9914", "美利達", "自行車"), ("2412", "中華電", "電信"), ("3045", "台灣大", "電信"),
    ("4904", "遠傳", "電信"), ("2371", "大同", "重電"), ("1519", "華城", "重電"),
    ("1513", "中興電", "重電"), ("1503", "士電", "重電"), ("1605", "華新", "電線電纜"),
    ("2105", "正新", "輪胎"), ("2106", "建大", "輪胎"), ("9910", "豐泰", "製鞋"),
    ("9904", "寶成", "製鞋"), ("2542", "興富發", "營建"), ("2545", "皇翔", "營建"),
    ("6446", "藥華藥", "生技"), ("1795", "美時", "生技"), ("4137", "麗豐-KY", "生技"),
    ("2049", "上銀", "機械"), ("1590", "亞德客-KY", "機械"), ("2731", "雄獅", "觀光"),
    ("2727", "王品", "餐飲"), ("2748", "雲品", "觀光"), ("9941", "裕融", "租賃"),
    ("5871", "中租-KY", "租賃"), ("3653", "健策", "散熱"), ("6781", "AES-KY", "電池"),
    ("2354", "鴻準", "機殼"), ("2360", "致茂", "儀器"), ("3533", "嘉澤", "連接器"),
    ("6770", "力積電", "記憶體"), ("3035", "智原", "ASIC"), ("4763", "材料-KY", "材料"),
    ("8454", "富邦媒", "電商"), ("1707", "葡萄王", "生技"), ("9945", "潤泰新", "營建"),
    ("6182", "合晶", "半導體"), ("3019", "亞光", "光學"), ("2455", "全新", "化合物半導體"),
    ("4919", "新唐", "IC設計"),
]

# 靜態 fallback(active 檔不存在時用):RAW 前 30
DEFAULT_30 = RAW_CANDIDATES[:30]


def _as_tw(code: str) -> str:
    return code if code.endswith(".TW") else f"{code}.TW"


def _fetch_metrics(code: str, as_of: str, lookback: int = 70, token: Optional[str] = None) -> Optional[dict]:
    """一次抓近期量價,回傳 {turnover(近60日平均成交額), mom60(60日動能)};只用 ≤ as_of。失敗回 None。"""
    import pandas as pd
    from company.data.single_stock import _fetch
    start = (datetime.fromisoformat(as_of) - timedelta(days=lookback * 2 + 60)).date().isoformat()
    try:
        df = _fetch("TaiwanStockPrice", code, start, as_of, token)
    except Exception:
        return None
    if df.empty or "Trading_money" not in df.columns or "close" not in df.columns:
        return None
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 40:
        return None
    money = pd.to_numeric(df["Trading_money"], errors="coerce").dropna()
    closes = pd.to_numeric(df["close"], errors="coerce").dropna().tolist()
    if len(money) < 30 or len(closes) < 61:
        return None
    turnover = float(money.tail(60).mean())
    mom60 = closes[-1] / closes[-61] - 1.0
    if turnover <= 0:
        return None
    return {"turnover": turnover, "mom60": mom60}


def _pct_rank(values: list[float]) -> list[float]:
    """回傳每個值的百分位(0~1)。"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    rank = [0.0] * n
    for pos, i in enumerate(order):
        rank[i] = pos / (n - 1)
    return rank


def _sector_capped(rows: list[dict], n: int, cap: int) -> list[dict]:
    """已排序的 rows 依產業上限貪婪選 n;湊不滿再放寬補足。"""
    chosen, cnt = [], {}
    for r in rows:
        if len(chosen) >= n:
            break
        if cnt.get(r["sector"], 0) < cap:
            chosen.append(r); cnt[r["sector"]] = cnt.get(r["sector"], 0) + 1
    if len(chosen) < n:
        for r in rows:
            if len(chosen) >= n:
                break
            if r not in chosen:
                chosen.append(r)
    return chosen


def refresh_all(as_of: Optional[str] = None, token: Optional[str] = None, sleep: float = 0.3) -> dict:
    """單次抓取 RAW 候選量價,一次產出母池100 + 週選30(省 API)。"""
    as_of = as_of or datetime.now().date().isoformat()
    token = token or os.environ.get("FINMIND_TOKEN")
    metrics = []
    for code, name, sector in RAW_CANDIDATES:
        m = _fetch_metrics(code, as_of, token=token)
        if m:
            metrics.append({"symbol": _as_tw(code), "name": name, "sector": sector,
                            "avg_turnover": round(m["turnover"], 0), "mom60": round(m["mom60"], 4)})
        time.sleep(sleep)

    # --- 母池 100:成交額排序 + 產業分散 ---
    by_turnover = sorted(metrics, key=lambda x: x["avg_turnover"], reverse=True)
    pool = _sector_capped(by_turnover, POOL_N, POOL_SECTOR_CAP)
    for i, r in enumerate(pool, 1):
        r["pool_rank"] = i

    # --- 週選 30:從母池依加權複合分(成交額 + 動能)+ 產業分散 ---
    liq = _pct_rank([r["avg_turnover"] for r in pool])
    mom = _pct_rank([r["mom60"] for r in pool])
    for r, l, m in zip(pool, liq, mom):
        r["composite"] = round(W_LIQUIDITY * l + W_MOMENTUM * m, 4)
    by_comp = sorted(pool, key=lambda x: x["composite"], reverse=True)
    weekly = _sector_capped(by_comp, WEEKLY_N, WEEKLY_SECTOR_CAP)
    weekly = [dict(r) for r in weekly]
    for i, r in enumerate(weekly, 1):
        r["rank"] = i

    now = datetime.now().isoformat()
    pool_doc = {
        "generated_at": now, "as_of": as_of, "tier": "母池(月更)", "n": len(pool),
        "basis": "近 60 交易日平均成交額(流動性)排序 + 各產業上限分散",
        "per_sector_cap": POOL_SECTOR_CAP, "candidates_evaluated": len(metrics),
        "stocks": [{k: r[k] for k in ("symbol", "name", "sector", "avg_turnover", "mom60", "pool_rank")} for r in pool],
    }
    weekly_doc = {
        "generated_at": now, "as_of": as_of, "tier": "週選(週更,供每日推薦)", "n": len(weekly),
        "basis": f"母池100 → 加權複合分(成交額×{W_LIQUIDITY} + 60日動能×{W_MOMENTUM},百分位)+ 各產業上限{WEEKLY_SECTOR_CAP}分散",
        "weights": {"liquidity": W_LIQUIDITY, "momentum": W_MOMENTUM},
        "per_sector_cap": WEEKLY_SECTOR_CAP, "pool_size": len(pool),
        "stocks": [{k: r[k] for k in ("symbol", "name", "sector", "avg_turnover", "mom60", "composite", "rank")} for r in weekly],
    }
    return {"pool": pool_doc, "weekly": weekly_doc}


def refresh_weekly_from_pool(as_of: Optional[str] = None, token: Optional[str] = None, sleep: float = 0.3) -> Optional[dict]:
    """週更:讀現有母池100,只重抓這100檔量價算加權複合分選30(較省)。母池不存在回 None。"""
    if not POOL_PATH.exists():
        return None
    as_of = as_of or datetime.now().date().isoformat()
    token = token or os.environ.get("FINMIND_TOKEN")
    pool_stocks = json.loads(POOL_PATH.read_text(encoding="utf-8")).get("stocks", [])
    metrics = []
    for s in pool_stocks:
        code = s["symbol"].replace(".TW", "")
        m = _fetch_metrics(code, as_of, token=token)
        if m:
            metrics.append({"symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
                            "avg_turnover": round(m["turnover"], 0), "mom60": round(m["mom60"], 4)})
        time.sleep(sleep)
    if not metrics:
        return None
    liq = _pct_rank([r["avg_turnover"] for r in metrics])
    mom = _pct_rank([r["mom60"] for r in metrics])
    for r, l, m in zip(metrics, liq, mom):
        r["composite"] = round(W_LIQUIDITY * l + W_MOMENTUM * m, 4)
    by_comp = sorted(metrics, key=lambda x: x["composite"], reverse=True)
    weekly = _sector_capped(by_comp, WEEKLY_N, WEEKLY_SECTOR_CAP)
    for i, r in enumerate(weekly, 1):
        r["rank"] = i
    return {
        "generated_at": datetime.now().isoformat(), "as_of": as_of,
        "tier": "週選(週更,供每日推薦)", "n": len(weekly),
        "basis": f"母池100 → 加權複合分(成交額×{W_LIQUIDITY} + 60日動能×{W_MOMENTUM},百分位)+ 各產業上限{WEEKLY_SECTOR_CAP}分散",
        "weights": {"liquidity": W_LIQUIDITY, "momentum": W_MOMENTUM},
        "per_sector_cap": WEEKLY_SECTOR_CAP, "pool_size": len(metrics),
        "stocks": [{k: r[k] for k in ("symbol", "name", "sector", "avg_turnover", "mom60", "composite", "rank")} for r in weekly],
    }


def save_pool(doc: dict) -> None:
    POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    POOL_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def save_active_universe(doc: dict) -> None:
    ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def load_active_universe(fallback: Optional[list[dict]] = None) -> list[dict]:
    """供 app.py 載入 DISCOVERY_UNIVERSE = 週選 30。檔不存在→fallback→母池→DEFAULT_30。"""
    for path in (ACTIVE_PATH, POOL_PATH):
        if path.exists():
            try:
                stocks = json.loads(path.read_text(encoding="utf-8")).get("stocks", [])
                if stocks:
                    return [{"symbol": s["symbol"], "name": s["name"], "sector": s.get("sector", "")} for s in stocks]
            except Exception:
                pass
    if fallback:
        return fallback
    return [{"symbol": _as_tw(c), "name": nm, "sector": sec} for c, nm, sec in DEFAULT_30]
