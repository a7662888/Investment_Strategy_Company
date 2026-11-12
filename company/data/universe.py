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
POOL_PATH = ROOT / "model_artifacts" / "active_pool.json"        # 母池(月)
ACTIVE_PATH = ROOT / "model_artifacts" / "active_universe.json"  # 週選 30(供 app.py)

# 選股參數（pool_rules_v2，2026-11-12）
POOL_N = 200              # 母池檔數（原 100）
WEEKLY_N = 30
POOL_SECTOR_CAP = 30      # 母池單一產業上限（15%）
POOL_BUFFER_N = 260       # 既有成員跌出市值前 260 名才剔除，避免名單月月進出
POOL_PREFILTER_N = 320    # 只對市值前 320 名逐檔抓 60 日成交額，控制 API 用量
MIN_AVG_TURNOVER = 5e7    # 近 60 日平均成交額下限 0.5 億元（與 ETF 研究候選同一標準）
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
OTC_CODES = {"8299", "3324", "6182", "3529", "4966"}


def _as_tw(code: str) -> str:
    if code.endswith((".TW", ".TWO")):
        return code
    return f"{code}.TWO" if code in OTC_CODES else f"{code}.TW"


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


def _legacy_candidates() -> list[dict]:
    """官方公開資料取不到時的退路：沿用手寫名單，不讓月更整個失敗。"""
    return [{"code": code, "symbol": _as_tw(code), "name": name, "sector": sector,
             "listed": None, "market_cap": None, "day_turnover": 0.0}
            for code, name, sector in RAW_CANDIDATES]


def _current_pool_symbols() -> set[str]:
    try:
        return {s["symbol"] for s in json.loads(POOL_PATH.read_text(encoding="utf-8")).get("stocks", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def select_pool(rows: list[dict], incumbents: set[str], n: int = POOL_N, cap: int = POOL_SECTOR_CAP,
                buffer_n: int = POOL_BUFFER_N, min_turnover: float = MIN_AVG_TURNOVER) -> list[dict]:
    """純函式：流動性下限 → 既有成員緩衝 → 依市值與產業上限取 n 檔。

    rows 需已依市值由大到小排序，每列含 symbol／sector／avg_turnover。
    """
    liquid = [r for r in rows if (r.get("avg_turnover") or 0) >= min_turnover]
    rank = {r["symbol"]: i for i, r in enumerate(liquid)}
    kept = [r for r in liquid if r["symbol"] in incumbents and rank[r["symbol"]] < buffer_n]
    protected = {r["symbol"] for r in kept}
    others = [r for r in liquid if r["symbol"] not in protected]
    # 緩衝區內的既有成員優先保留，其餘名額依市值由大到小遞補；兩群都受產業上限約束。
    chosen = _sector_capped(kept + others, n, cap)
    return sorted(chosen, key=lambda r: rank[r["symbol"]])


def refresh_all(as_of: Optional[str] = None, token: Optional[str] = None, sleep: float = 0.3) -> dict:
    """規則式母池：官方資料定範圍與市值排序，逐檔只抓前段班的 60 日成交額。"""
    as_of = as_of or datetime.now().date().isoformat()
    token = token or os.environ.get("FINMIND_TOKEN")
    incumbents = _current_pool_symbols()
    source = "official"
    try:
        from company.data.universe_rules import fetch_candidates

        candidates = fetch_candidates(datetime.fromisoformat(as_of).date(),
                                      exempt_listing_age={s.split(".")[0] for s in incumbents})
        if len(candidates) < POOL_N:
            raise RuntimeError(f"only {len(candidates)} official candidates")
    except Exception as exc:  # noqa: BLE001 - 官方來源故障時退回手寫名單
        print(f"[Universe] 官方公開資料不可用（{type(exc).__name__}），退回手寫名單")
        candidates, source = _legacy_candidates(), "legacy_list"
    shortlist = candidates[:POOL_PREFILTER_N]
    seen = {c["symbol"] for c in shortlist}
    shortlist += [c for c in candidates if c["symbol"] in incumbents and c["symbol"] not in seen]

    metrics, fallback = [], 0
    for c in shortlist:
        m = _fetch_metrics(c["code"], as_of, token=token)
        row = {"symbol": c["symbol"], "name": c["name"], "sector": c["sector"],
               "market_cap": c.get("market_cap"), "listed": c.get("listed")}
        if m:
            row.update(avg_turnover=round(m["turnover"], 0), mom60=round(m["mom60"], 4), turnover_basis="60d_avg")
        elif c.get("day_turnover"):
            # 逐檔來源失敗（限流）時不整檔丟掉，改用官方單日成交額並標明。
            row.update(avg_turnover=round(c["day_turnover"], 0), mom60=0.0, turnover_basis="single_day")
            fallback += 1
        else:
            continue
        metrics.append(row)
        time.sleep(sleep)

    if source == "official":
        pool = select_pool(metrics, incumbents)
        basis = (f"上市櫃普通股、上市滿 3 年；依估計市值排序，近 60 日平均成交額 ≥ {MIN_AVG_TURNOVER / 1e8:.1f} 億元；"
                 f"單一產業上限 {POOL_SECTOR_CAP} 檔；既有成員跌出前 {POOL_BUFFER_N} 名才剔除")
    else:
        by_turnover = sorted(metrics, key=lambda x: x["avg_turnover"], reverse=True)
        pool = _sector_capped(by_turnover, min(POOL_N, len(by_turnover)), POOL_SECTOR_CAP)
        basis = "退路：手寫名單依近 60 交易日平均成交額排序 + 各產業上限分散"
    pool = [dict(r) for r in pool]
    for i, r in enumerate(pool, 1):
        r["pool_rank"] = i

    now = datetime.now().isoformat()
    pool_doc = {
        "generated_at": now, "as_of": as_of, "tier": "母池(月更)", "n": len(pool),
        "rule_version": "pool_rules_v2", "candidate_source": source,
        "basis": basis, "per_sector_cap": POOL_SECTOR_CAP,
        "candidates_evaluated": len(metrics), "eligible_universe": len(candidates),
        "turnover_single_day_fallback": fallback,
        "added": sorted(r["symbol"] for r in pool if r["symbol"] not in incumbents),
        "removed": sorted(incumbents - {r["symbol"] for r in pool}),
        "stocks": [{k: r.get(k) for k in ("symbol", "name", "sector", "avg_turnover", "mom60", "pool_rank",
                                          "market_cap", "listed", "turnover_basis")} for r in pool],
    }
    return {"pool": pool_doc, "weekly": None}


def refresh_weekly_from_pool(as_of: Optional[str] = None, token: Optional[str] = None, sleep: float = 0.3) -> Optional[dict]:
    """週更:讀現有母池100,只重抓這100檔量價算加權複合分選30(較省)。母池不存在回 None。"""
    if not POOL_PATH.exists():
        return None
    as_of = as_of or datetime.now().date().isoformat()
    token = token or os.environ.get("FINMIND_TOKEN")
    pool_stocks = json.loads(POOL_PATH.read_text(encoding="utf-8")).get("stocks", [])
    metrics = []
    for s in pool_stocks:
        code = s["symbol"].replace(".TWO", "").replace(".TW", "")
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


POOL_REMOTE_PATH = os.environ.get("UNIVERSE_POOL_PATH", "universe/active_pool.json")


def save_pool_durable(doc: dict) -> dict:
    """母池另存私有資料庫。

    母池是「資料」而非程式，卻只存在 repo 檔案中，於是更新必須靠一次部署才會
    生效——而 Render 的 buildFilter 把 model_artifacts/** 列為 ignoredPaths，
    月更 commit 根本不觸發部署（實測 2026-09-22：repo 已是新母池，
    線上 /api/mother-pool 仍回 2026-09-01 版、缺新增的三檔）。
    寫進資料庫後，網站讀得到最新母池，不必依賴部署。
    """
    try:
        from company.model.durable_document import save_document

        return save_document(doc, POOL_PATH, POOL_REMOTE_PATH,
                             f"chore(universe): active pool {doc.get('as_of') or ''}")
    except Exception as exc:  # noqa: BLE001 - 持久化失敗不該讓月更整個失敗
        return {"durable": False, "error": f"{type(exc).__name__}: {exc}"}


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
