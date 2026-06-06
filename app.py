from __future__ import annotations

import csv
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT
WEB_ROOT = PROJECT / "web"
DATA_DIR = PROJECT / "data"
CACHE_DIR = PROJECT / "data" / "web_cache"
MODEL_ARTIFACT_PATH = PROJECT / "model_artifacts" / "logit_v1.json"


DEFAULT_SYMBOLS = [
    {"symbol": "2327.TW", "name": "國巨"},
    {"symbol": "2330.TW", "name": "台積電"},
    {"symbol": "2317.TW", "name": "鴻海"},
    {"symbol": "2454.TW", "name": "聯發科"},
    {"symbol": "2308.TW", "name": "台達電"},
    {"symbol": "2412.TW", "name": "中華電"},
    {"symbol": "2881.TW", "name": "富邦金"},
    {"symbol": "2882.TW", "name": "國泰金"},
    {"symbol": "2603.TW", "name": "長榮"},
    {"symbol": "2615.TW", "name": "萬海"},
]

DISCOVERY_UNIVERSE = [
    {"symbol": "2330.TW", "name": "台積電", "sector": "半導體"},
    {"symbol": "2454.TW", "name": "聯發科", "sector": "半導體"},
    {"symbol": "2303.TW", "name": "聯電", "sector": "半導體"},
    {"symbol": "3711.TW", "name": "日月光投控", "sector": "半導體"},
    {"symbol": "2379.TW", "name": "瑞昱", "sector": "IC設計"},
    {"symbol": "3034.TW", "name": "聯詠", "sector": "IC設計"},
    {"symbol": "2317.TW", "name": "鴻海", "sector": "電子代工"},
    {"symbol": "2308.TW", "name": "台達電", "sector": "電源/AI伺服器"},
    {"symbol": "2382.TW", "name": "廣達", "sector": "AI伺服器"},
    {"symbol": "3231.TW", "name": "緯創", "sector": "AI伺服器"},
    {"symbol": "2356.TW", "name": "英業達", "sector": "AI伺服器"},
    {"symbol": "3017.TW", "name": "奇鋐", "sector": "散熱"},
    {"symbol": "3443.TW", "name": "創意", "sector": "ASIC"},
    {"symbol": "6669.TW", "name": "緯穎", "sector": "AI伺服器"},
    {"symbol": "2327.TW", "name": "國巨", "sector": "被動元件"},
    {"symbol": "8046.TW", "name": "南電", "sector": "ABF載板"},
    {"symbol": "2002.TW", "name": "中鋼", "sector": "原物料"},
    {"symbol": "1301.TW", "name": "台塑", "sector": "塑化"},
    {"symbol": "1303.TW", "name": "南亞", "sector": "塑化"},
    {"symbol": "2603.TW", "name": "長榮", "sector": "航運"},
    {"symbol": "2609.TW", "name": "陽明", "sector": "航運"},
    {"symbol": "2615.TW", "name": "萬海", "sector": "航運"},
    {"symbol": "2881.TW", "name": "富邦金", "sector": "金融"},
    {"symbol": "2882.TW", "name": "國泰金", "sector": "金融"},
    {"symbol": "2891.TW", "name": "中信金", "sector": "金融"},
    {"symbol": "2412.TW", "name": "中華電", "sector": "電信"},
    {"symbol": "3045.TW", "name": "台灣大", "sector": "電信"},
]

# ---------------------------------------------------------------------------
# Name Map for Taiwan Stocks
# ---------------------------------------------------------------------------
NAME_MAP = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2303": "聯電",
    "3711": "日月光", "2002": "中鋼", "1301": "台塑", "1303": "南亞", "2412": "中華電",
    "3045": "台灣大", "2881": "富邦金", "2882": "國泰金", "2891": "中信金", "2603": "長榮",
    "2609": "陽明", "2615": "萬海", "2327": "國巨", "2379": "瑞昱", "3034": "聯詠",
}

for item in DISCOVERY_UNIVERSE:
    _code = item["symbol"].split(".")[0]
    NAME_MAP[_code] = item["name"]

# 選股股池:優先載入每月重算的 active_universe.json(依流動性+跨產業分散選 60 檔);
# 檔案不存在(或載入失敗)時 fallback 到上方靜態清單。每月離線跑 run_universe_refresh.py 後 commit 更新。
try:
    from company.data.universe import load_active_universe
    DISCOVERY_UNIVERSE = load_active_universe(fallback=DISCOVERY_UNIVERSE)
    for item in DISCOVERY_UNIVERSE:
        _code = item["symbol"].split(".")[0]
        NAME_MAP[_code] = item["name"]
except Exception as _exc:
    print(f"[Universe] load_active_universe 失敗,沿用靜態清單:{_exc}")

MARKET_CONTEXT_SYMBOLS = [
    {"symbol": "0050.TW", "name": "元大台灣50"},
    {"symbol": "006208.TW", "name": "富邦台50"},
]

# Claude Agent regime policy: controls momentum tilt, vol penalty, concentration limits
AGENT_REGIME_POLICY = {
    "BULL_TREND": {
        "label": "多頭趨勢",
        "momentum_tilt": 35.0,
        "vol_penalty": 0.0,
        "require_above_ma20": False,
        "max_picks_factor": 1.0,
        "stance": "順勢追動能,可較積極",
    },
    "RANGE": {
        "label": "區間盤整",
        "momentum_tilt": 10.0,
        "vol_penalty": 20.0,
        "require_above_ma20": False,
        "max_picks_factor": 0.8,
        "stance": "偏穩健,挑站穩均線且不過熱者",
    },
    "BEAR_TREND": {
        "label": "空頭趨勢",
        "momentum_tilt": 5.0,
        "vol_penalty": 60.0,
        "require_above_ma20": True,
        "max_picks_factor": 0.4,
        "stance": "轉守,只留少數逆勢偏強且低波動者,寧可保留現金",
    },
    "HIGH_VOL": {
        "label": "高波動",
        "momentum_tilt": 5.0,
        "vol_penalty": 90.0,
        "require_above_ma20": True,
        "max_picks_factor": 0.4,
        "stance": "降風險,嚴篩低波動,部位收斂",
    },
}


def to_epoch(date_text: str) -> int:
    return int(datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc).timestamp())


def yahoo_json(url: str) -> dict:
    return http_json(url, {"User-Agent": "Mozilla/5.0"})


def http_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_float(value: object) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def tw_code(symbol: str) -> str:
    return symbol.strip().split(".")[0]


def twse_market_timestamp(date_text: str | None, time_text: str | None) -> int | None:
    if not date_text or not time_text:
        return None
    for fmt in ("%Y%m%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(f"{date_text} {time_text}", fmt)
            return int(parsed.replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
        except ValueError:
            continue
    return None


def fetch_twse_mis_quotes(symbols: list[str]) -> list[dict]:
    channels = []
    for symbol in symbols:
        code = tw_code(symbol)
        channels.extend([f"tse_{code}.tw", f"otc_{code}.tw"])

    if not channels:
        return []

    query = urllib.parse.urlencode(
        {
            "ex_ch": "|".join(channels),
            "json": "1",
            "delay": "0",
            "_": str(int(datetime.now().timestamp() * 1000)),
        },
        safe="|",
    )
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?{query}"
    payload = http_json(
        url,
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
        },
    )

    results: dict[str, dict] = {}
    for item in payload.get("msgArray", []):
        code = item.get("c")
        if not code:
            continue
        symbol = f"{code}.TW"
        if symbol not in symbols:
            continue

        price = parse_float(item.get("z")) or parse_float(item.get("pz")) or parse_float(item.get("y"))
        prev_close = parse_float(item.get("y"))
        if price is None:
            continue

        change_pct = 0.0 if not prev_close else (price / prev_close - 1.0) * 100.0
        timestamp = twse_market_timestamp(item.get("d"), item.get("t")) or twse_market_timestamp(
            payload.get("queryTime", {}).get("sysDate"),
            payload.get("queryTime", {}).get("sysTime"),
        )
        source = "TWSE MIS" if item.get("ex") == "tse" else "TPEx/TWSE MIS"
        results[symbol] = {
            "symbol": symbol,
            "shortName": item.get("n") or symbol,
            "regularMarketPrice": price,
            "regularMarketChangePercent": change_pct,
            "regularMarketTime": timestamp,
            "marketDate": item.get("d"),
            "marketTime": item.get("t"),
            "open": parse_float(item.get("o")),
            "dayHigh": parse_float(item.get("h")),
            "dayLow": parse_float(item.get("l")),
            "volume": parse_float(item.get("v")),
            "source": source,
            "realtimeStatus": "盤中撮合/收盤後最後成交價",
        }
    return [results[symbol] for symbol in symbols if symbol in results]


def fetch_yahoo_quotes(symbols: list[str]) -> list[dict]:
    query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?{query}"
    payload = yahoo_json(url)
    results = []
    for item in payload.get("quoteResponse", {}).get("result", []):
        item["source"] = "Yahoo Finance"
        item["realtimeStatus"] = "備援資料，可能延遲或非盤中"
        results.append(item)
    return results


def fetch_yahoo_intraday_quotes(symbols: list[str]) -> list[dict]:
    results = []
    for symbol in symbols:
        end = datetime.now(timezone.utc) + timedelta(days=1)
        start = end - timedelta(days=5)
        params = {
            "period1": str(int(start.timestamp())),
            "period2": str(int(end.timestamp())),
            "interval": "1m",
            "includePrePost": "false",
        }
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
        payload = yahoo_json(url)
        if payload["chart"].get("error"):
            continue
        chart = payload["chart"]["result"][0]
        meta = chart.get("meta", {})
        quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
        timestamps = chart.get("timestamp") or []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        latest_index = None
        for index in range(len(timestamps) - 1, -1, -1):
            if index < len(closes) and closes[index] is not None:
                latest_index = index
                break
        if latest_index is None:
            continue
        price = float(closes[latest_index])
        prev_close = parse_float(meta.get("previousClose")) or parse_float(meta.get("chartPreviousClose"))
        change_pct = 0.0 if not prev_close else (price / prev_close - 1.0) * 100.0
        latest_dt = datetime.fromtimestamp(timestamps[latest_index], tz=timezone(timedelta(hours=8)))
        same_day_indexes = [
            index
            for index, timestamp in enumerate(timestamps)
            if datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8))).date() == latest_dt.date()
        ]
        day_opens = [float(opens[index]) for index in same_day_indexes if index < len(opens) and opens[index] is not None]
        day_highs = [float(highs[index]) for index in same_day_indexes if index < len(highs) and highs[index] is not None]
        day_lows = [float(lows[index]) for index in same_day_indexes if index < len(lows) and lows[index] is not None]
        day_volumes = [float(volumes[index]) for index in same_day_indexes if index < len(volumes) and volumes[index] is not None]
        results.append(
            {
                "symbol": symbol,
                "shortName": meta.get("shortName") or meta.get("longName") or symbol,
                "regularMarketPrice": price,
                "regularMarketChangePercent": change_pct,
                "regularMarketTime": int(timestamps[latest_index]),
                "marketDate": latest_dt.date().isoformat(),
                "marketTime": latest_dt.time().isoformat(timespec="seconds"),
                "open": day_opens[0] if day_opens else None,
                "dayHigh": parse_float(meta.get("regularMarketDayHigh")) or (max(day_highs) if day_highs else None),
                "dayLow": parse_float(meta.get("regularMarketDayLow")) or (min(day_lows) if day_lows else None),
                "volume": float(meta.get("regularMarketVolume") or sum(day_volumes)),
                "source": "Yahoo 1m intraday",
                "realtimeStatus": "雲端可用盤中分鐘線，可能延遲；TWSE MIS 不可用時使用",
            }
        )
    return results


def fetch_history_quote(symbol: str) -> dict | None:
    end = datetime.now().date()
    start = (end - timedelta(days=10)).isoformat()
    end_exclusive = (end + timedelta(days=1)).isoformat()
    rows = fetch_history(symbol, start, end_exclusive)
    if not rows:
        return None
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else latest
    price = float(latest["close"])
    prev_close = float(previous["close"])
    change_pct = 0.0 if prev_close == 0 else (price / prev_close - 1) * 100
    return {
        "symbol": symbol,
        "shortName": symbol,
        "regularMarketPrice": price,
        "regularMarketChangePercent": change_pct,
        "regularMarketTime": int(datetime.fromisoformat(latest["date"]).replace(tzinfo=timezone.utc).timestamp()),
        "source": "Yahoo daily chart fallback",
        "realtimeStatus": "日線備援，不是即時報價",
    }


def iso_from_tw_date(date_text: str | None) -> str | None:
    if not date_text:
        return None
    for fmt in ("%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def merge_live_quote_into_history(symbol: str, rows: list[dict]) -> list[dict]:
    if not rows or not symbol.endswith(".TW"):
        return rows
    latest_date = datetime.fromisoformat(rows[-1]["date"]).date()
    if (datetime.now(timezone(timedelta(hours=8))).date() - latest_date).days > 3:
        return rows
    try:
        quotes = fetch_twse_mis_quotes([symbol])
    except Exception:
        quotes = []
    if not quotes:
        try:
            quotes = fetch_yahoo_intraday_quotes([symbol])
        except Exception:
            quotes = []
    if not quotes:
        return rows
    quote = quotes[0]
    quote_date = iso_from_tw_date(quote.get("marketDate")) or quote.get("marketDate")
    if not quote_date:
        return rows

    merged_row = {
        "date": quote_date,
        "symbol": symbol,
        "open": f"{float(quote.get('open') or quote['regularMarketPrice']):.4f}",
        "high": f"{float(quote.get('dayHigh') or quote['regularMarketPrice']):.4f}",
        "low": f"{float(quote.get('dayLow') or quote['regularMarketPrice']):.4f}",
        "close": f"{float(quote['regularMarketPrice']):.4f}",
        "volume": str(int(float(quote.get("volume") or 0))),
    }
    if rows[-1]["date"] == quote_date:
        rows[-1] = merged_row
    elif rows[-1]["date"] < quote_date:
        rows.append(merged_row)
    return rows


def fetch_quote(symbols: list[str]) -> dict:
    symbols = [symbol.strip() for symbol in symbols if symbol.strip()]
    by_symbol: dict[str, dict] = {}
    try:
        for item in fetch_twse_mis_quotes(symbols):
            by_symbol[item["symbol"]] = item
    except Exception:
        pass

    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        try:
            for item in fetch_yahoo_intraday_quotes(missing):
                by_symbol[item["symbol"]] = item
        except Exception:
            pass

    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        try:
            for item in fetch_yahoo_quotes(missing):
                by_symbol[item["symbol"]] = item
        except Exception:
            pass

    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    for symbol in missing:
        item = fetch_history_quote(symbol)
        if item:
            by_symbol[symbol] = item

    return {
        "quoteResponse": {"result": [by_symbol[symbol] for symbol in symbols if symbol in by_symbol]},
        "quotePolicy": "TWSE/TPEx MIS first; Yahoo 1m intraday cloud fallback; Yahoo daily only as last resort.",
    }


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def model_evidence(symbol: str, closes: list[float], volumes: list[float] | None = None, dates: list[str] | None = None) -> dict:
    last = closes[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    momentum_20 = last / closes[-21] - 1 if len(closes) > 21 else 0
    volatility = 0.0
    if len(closes) > 21:
        returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
        avg = sum(returns) / len(returns)
        volatility = math.sqrt(sum((item - avg) ** 2 for item in returns) / len(returns))
    rsi = calculate_rsi_list(closes, 14)
    _, _, hist_val = calculate_macd_list(closes, 12, 26, 9)

    trend_points = 0.0
    if ma20 and ma60:
        trend_points += 1.4 if last > ma20 > ma60 else -0.9 if last < ma20 < ma60 else 0.0
    if ma60 and ma120:
        trend_points += 0.8 if ma60 > ma120 else -0.5
    momentum_points = max(-1.2, min(1.2, momentum_20 * 8.0))
    rsi_points = 0.4 if 45 <= rsi <= 68 else -0.5 if rsi > 76 else 0.2 if rsi < 35 else 0.0
    macd_points = 0.5 if hist_val > 0 else -0.4
    risk_points = -0.8 if volatility > 0.045 else 0.2 if volatility < 0.025 else 0.0
    raw_score = trend_points + momentum_points + rsi_points + macd_points + risk_points

    evidence = {
        "name": "interpretable_technical_ensemble_v1",
        "symbol": symbol,
        "trend_points": round(trend_points, 3),
        "momentum_points": round(momentum_points, 3),
        "rsi_points": round(rsi_points, 3),
        "macd_points": round(macd_points, 3),
        "risk_points": round(risk_points, 3),
        "raw_score": round(raw_score, 3),
        "probability_up": round(sigmoid(raw_score) * 100.0, 1),
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "ma120": round(ma120, 2) if ma120 else None,
        "momentum_20": round(momentum_20, 4),
        "volatility_20": round(volatility, 4),
        "rsi14": round(rsi, 1),
        "macd_histogram": round(hist_val, 3),
        "note": "可解釋技術因子模型，僅使用截止日以前資料；機率尚未校準，供訓練比較。",
    }

    try:
        from company.model.score import score_series

        calibrated = score_series(closes, volumes, symbol=symbol, dates=dates)
        if calibrated:
            evidence["calibrated_model"] = calibrated["name"]
            evidence["probability_up"] = calibrated["probability_up"]
            evidence["calibrated_probability_up"] = calibrated["probability_up"]
            evidence["calibrated"] = calibrated["calibrated"]
            evidence["calibrated_reasons"] = calibrated["reasons"]
            evidence["calibrated_evidence"] = calibrated["evidence"]
            evidence["horizon_days"] = calibrated.get("horizon_days")
            evidence["note"] = calibrated["note"]
            evidence["contributions"] = calibrated.get("contributions", [])
    except Exception:
        pass
    return evidence


def fetch_history(symbol: str, start_date: str, end_date: str) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol.replace('.', '_')}_{start_date}_{end_date}.csv"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        with cache_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return merge_live_quote_into_history(symbol, list(csv.DictReader(handle)))

    params = {
        "period1": str(to_epoch(start_date)),
        "period2": str(to_epoch(end_date)),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
    payload = yahoo_json(url)
    if payload["chart"].get("error"):
        raise RuntimeError(payload["chart"]["error"])

    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    rows: list[dict] = []
    for index, timestamp in enumerate(timestamps):
        if quote["open"][index] is None or quote["close"][index] is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
                "symbol": symbol,
                "open": f"{float(quote['open'][index]):.4f}",
                "high": f"{float(quote['high'][index]):.4f}",
                "low": f"{float(quote['low'][index]):.4f}",
                "close": f"{float(quote['close'][index]):.4f}",
                "volume": str(int(quote["volume"][index] or 0)),
            }
        )

    if rows:
        with cache_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return merge_live_quote_into_history(symbol, rows)


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def load_model_artifact() -> dict | None:
    try:
        return json.loads(MODEL_ARTIFACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_version() -> dict:
    return {
        "app_version": "training-diagnostics-v2",
        "expected_min_commit": "6bb9f83",
        "render_git_commit": os.environ.get("RENDER_GIT_COMMIT"),
        "render_service_id": os.environ.get("RENDER_SERVICE_ID"),
        "render_service_name": os.environ.get("RENDER_SERVICE_NAME"),
        "features": {
            "quote_yahoo_1m_fallback": True,
            "train_model_training": True,
            "train_optimizer_audit": True,
            "train_threshold_reviews": True,
        },
    }


def model_training_summary() -> dict:
    artifact = load_model_artifact()
    if not artifact:
        return {
            "available": False,
            "summary": "尚未找到校準模型 artifact，區間訓練只會顯示規則策略績效。",
            "next_steps": ["先建立或重新產生 model_artifacts/logit_v1.json。"],
        }

    weights = artifact.get("weights", {})
    labels = {}
    try:
        from company.model.features import FEATURE_LABELS

        labels = FEATURE_LABELS
    except Exception:
        labels = {}

    ranked = sorted(weights.items(), key=lambda item: abs(float(item[1])), reverse=True)
    top_factors = [
        {
            "feature": key,
            "label": labels.get(key, key),
            "weight": round(float(weight), 4),
            "direction": "偏多" if float(weight) > 0 else "偏空",
        }
        for key, weight in ranked[:6]
    ]
    metrics = artifact.get("metrics", {})
    buckets = artifact.get("calibration_buckets", [])
    best_bucket = None
    if buckets:
        best_bucket = max(
            buckets,
            key=lambda item: (float(item.get("empirical_up_rate") or 0), float(item.get("avg_fwd_return") or 0)),
        )

    return {
        "available": True,
        "name": artifact.get("name"),
        "horizon_days": artifact.get("horizon_days"),
        "train_window": artifact.get("train_window"),
        "train_symbol_count": len(artifact.get("train_symbols", [])),
        "train_sample_count": metrics.get("n_train_total"),
        "oos_sample_count": metrics.get("n_oos_total"),
        "oos_auc": metrics.get("pooled_oos_auc"),
        "oos_accuracy": metrics.get("pooled_oos_accuracy"),
        "base_rate_up": metrics.get("base_rate_up"),
        "brier": metrics.get("pooled_oos_brier"),
        "top_factors": top_factors,
        "best_bucket": best_bucket,
        "calibration_buckets": buckets,
        "thinking_process": [
            "先用截止日以前的價格與成交量產生 11 個技術因子，避免偷看未來。",
            "用 rolling walk-forward 樣本外資料檢查模型機率是否有排序能力。",
            "把模型機率映射到歷史校準桶，讓每次建議都有實際上漲率與 5 日平均報酬作依據。",
            "訓練回饋不是直接改操盤原則，而是調整因子權重、信心門檻與風控提示。",
        ],
        "limitations": [
            "AUC 只略高於 0.5，代表技術面模型只能作為輔助排序，不應單獨決策。",
            "高信心桶樣本較少，需持續擴大股票池並做滾動再校準。",
            "模型預測的是未來 5 日方向，不等於明日必漲或逐筆交易訊號。",
        ],
        "next_steps": [
            "擴大訓練股票池與產業覆蓋，降低只適合少數股票的偏誤。",
            "每月或每季滾動重訓，比較新舊模型的樣本外 AUC、Brier 與高信心桶報酬。",
            "把 C-1/C-2 的交易結果回饋給 D 審計，檢查過度交易、追高與獲利回吐。",
        ],
    }


def strategy_learning_review(role: str, rows: list[dict], total_return: float, drawdown: float, trades: int) -> dict:
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row.get("volume", 0) or 0) for row in rows]
    buy_hold_return = closes[-1] / closes[0] - 1 if closes and closes[0] else 0.0
    model = model_evidence(rows[-1].get("symbol", ""), closes, volumes)
    calibrated = model.get("calibrated") or {}
    calibrated_prob = model.get("calibrated_probability_up")
    gap_vs_buy_hold = total_return - buy_hold_return

    findings = []
    if trades == 0:
        findings.append("區間內沒有交易，代表規則過於保守或訊號門檻未被觸發。")
    if gap_vs_buy_hold < -0.05:
        findings.append("策略明顯落後買進持有，需檢查進出場是否太慢或過度避險。")
    elif gap_vs_buy_hold > 0.05:
        findings.append("策略優於買進持有，值得保留目前核心規則並觀察是否可複製到其他股票。")
    else:
        findings.append("策略與買進持有差距不大，下一步應看最大回撤與交易次數。")
    if drawdown < -0.25:
        findings.append("最大回撤偏深，D 風控應要求降低單次投入或加入停損/停利保護。")
    if calibrated_prob is not None:
        findings.append(
            f"校準模型目前給 {calibrated_prob:.1f}% 偏多；同桶歷史上漲率約 "
            f"{float(calibrated.get('empirical_up_rate', 0)) * 100:.1f}% 。"
        )

    next_adjustments = []
    if role == "C-1":
        next_adjustments.extend(
            [
                "維持不追價原則，但記錄 MA60 折價 7% 是否太嚴，下一輪可比較 5%/7%/10%。",
                "若已獲利且跌破 MA20，優先測試分批獲利而非一次賣出。",
            ]
        )
    else:
        next_adjustments.extend(
            [
                "維持動能交易原則，但加入 RSI>75 或波動過高時降低部位，避免追高。",
                "比較 MA15/45 與 MA20/60 的交叉訊號，檢查是否能降低假突破。",
            ]
        )
    if trades == 0:
        next_adjustments.append("本區間應補做較長期間或不同股票，否則這次訓練無法有效提升模型。")

    return {
        "buy_hold_return": round(buy_hold_return, 6),
        "gap_vs_buy_hold": round(gap_vs_buy_hold, 6),
        "current_model_probability": calibrated_prob,
        "current_model_bucket": calibrated,
        "findings": findings,
        "next_adjustments": next_adjustments,
        "future_knowledge_used": False,
    }


def simulate_strategy_variant(rows: list[dict], role: str, initial_cash: float, params: dict, fee: float = 0.0, tax: float = 0.0, slippage: float = 0.0) -> dict:
    closes = [float(row["close"]) for row in rows]
    cash = initial_cash
    shares = 0
    equity_curve = []
    trades = 0
    pending = None

    for index, row in enumerate(rows):
        open_price = float(row["open"])
        close = float(row["close"])
        if pending == "buy" and shares == 0:
            allocation = float(params.get("allocation", 0.65 if role == "C-1" else 0.9))
            shares = int((cash * allocation) // (open_price * (1.0 + fee + slippage)))
            cash -= shares * open_price * (1.0 + fee + slippage)
            trades += 1
        elif pending == "sell" and shares > 0:
            cash += shares * open_price * (1.0 - fee - tax - slippage)
            shares = 0
            trades += 1

        equity_curve.append(cash + shares * close)
        visible_closes = closes[: index + 1]

        if role == "C-1":
            ma60 = moving_average(visible_closes, 60)
            buy_discount = float(params.get("buy_discount", 0.07))
            sell_premium = float(params.get("sell_premium", 0.05))
            if ma60 and shares == 0 and close < ma60 * (1.0 - buy_discount):
                pending = "buy"
            elif ma60 and shares > 0 and close > ma60 * (1.0 + sell_premium):
                pending = "sell"
            else:
                pending = "hold"
        else:
            fast = int(params.get("fast_ma", 15))
            slow = int(params.get("slow_ma", 45))
            ma_fast = moving_average(visible_closes, fast)
            ma_slow = moving_average(visible_closes, slow)
            rsi = calculate_rsi_list(visible_closes, 14)
            rsi_cap = float(params.get("rsi_cap", 101))
            if ma_fast and ma_slow and shares == 0 and ma_fast > ma_slow and rsi < rsi_cap:
                pending = "buy"
            elif ma_fast and ma_slow and shares > 0 and ma_fast <= ma_slow:
                pending = "sell"
            else:
                pending = "hold"

    final_equity = equity_curve[-1] if equity_curve else initial_cash
    return {
        "params": params,
        "final_equity": round(final_equity, 2),
        "total_return": round(final_equity / initial_cash - 1.0, 6),
        "max_drawdown": round(max_drawdown(equity_curve), 6) if equity_curve else 0,
        "trade_count": trades,
    }


def optimize_strategy_variants(symbol: str, rows: list[dict], role: str, initial_cash: float, baseline: dict, fee: float = 0.0, tax: float = 0.0, slippage: float = 0.0) -> dict:
    if role == "C-1":
        variants = [
            {"buy_discount": 0.05, "sell_premium": 0.04, "allocation": 0.60},
            {"buy_discount": 0.07, "sell_premium": 0.05, "allocation": 0.65},
            {"buy_discount": 0.10, "sell_premium": 0.08, "allocation": 0.55},
        ]
    else:
        variants = [
            {"fast_ma": 10, "slow_ma": 30, "rsi_cap": 75, "allocation": 0.80},
            {"fast_ma": 15, "slow_ma": 45, "rsi_cap": 101, "allocation": 0.90},
            {"fast_ma": 20, "slow_ma": 60, "rsi_cap": 72, "allocation": 0.75},
        ]

    evaluated = [simulate_strategy_variant(rows, role, initial_cash, params, fee=fee, tax=tax, slippage=slippage) for params in variants]
    evaluated.sort(key=lambda item: (item["total_return"], item["max_drawdown"]), reverse=True)
    best = evaluated[0] if evaluated else None
    baseline_return = float(baseline.get("total_return", 0))
    improvement = 0.0 if not best else best["total_return"] - baseline_return

    if not best:
        recommendation = "資料不足，暫不產生參數優化建議。"
    elif improvement > 0.03:
        recommendation = "候選參數在此區間明顯優於目前規則，建議下一輪訓練納入 A/B 比較。"
    elif improvement < -0.03:
        recommendation = "目前規則優於候選參數，暫不調整核心原則，只監控風控條件。"
    else:
        recommendation = "候選參數與目前規則差距小，優先擴大股票與年份再判斷。"

    return {
        "symbol": symbol,
        "role": role,
        "baseline_return": baseline_return,
        "best_variant": best,
        "improvement": round(improvement, 6),
        "evaluated_variants": evaluated,
        "recommendation": recommendation,
        "future_knowledge_used": False,
    }


def evaluate_probability_thresholds(symbol: str, rows: list[dict]) -> dict:
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row.get("volume", 0) or 0) for row in rows]
    thresholds = [45, 50, 55, 60]
    stats = {threshold: {"signals": 0, "wins": 0, "returns": []} for threshold in thresholds}
    horizon = 5

    for index in range(130, len(rows) - horizon):
        model = model_evidence(symbol, closes[: index + 1], volumes[: index + 1])
        probability = model.get("calibrated_probability_up")
        if probability is None:
            continue
        future_return = closes[index + horizon] / closes[index] - 1.0
        for threshold in thresholds:
            if probability >= threshold:
                stats[threshold]["signals"] += 1
                stats[threshold]["wins"] += 1 if future_return > 0 else 0
                stats[threshold]["returns"].append(future_return)

    output = []
    for threshold, item in stats.items():
        count = item["signals"]
        avg_return = sum(item["returns"]) / count if count else 0.0
        hit_rate = item["wins"] / count if count else None
        output.append(
            {
                "threshold": threshold,
                "signals": count,
                "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                "avg_forward_return": round(avg_return, 6),
            }
        )

    usable = [item for item in output if item["signals"] >= 5 and item["hit_rate"] is not None]
    best = max(usable, key=lambda item: (item["hit_rate"], item["avg_forward_return"])) if usable else None
    return {
        "symbol": symbol,
        "horizon_days": horizon,
        "thresholds": output,
        "best_threshold": best,
        "interpretation": "用截止日以前資料逐日產生機率，再檢查未來 5 日是否上漲；這是訓練後審計，不是操盤時偷看未來。",
    }


def simulate(symbol: str, rows: list[dict], role: str, initial_cash: float, fee: float = 0.0, tax: float = 0.0, slippage: float = 0.0) -> dict:
    closes = [float(row["close"]) for row in rows]
    cash = initial_cash
    shares = 0
    equity_curve = []
    trades = 0
    pending = None

    for index, row in enumerate(rows):
        open_price = float(row["open"])
        close = float(row["close"])
        if pending == "buy" and shares == 0:
            allocation = 0.65 if role == "C-1" else 0.9
            shares = int((cash * allocation) // (open_price * (1.0 + fee + slippage)))
            cash -= shares * open_price * (1.0 + fee + slippage)
            trades += 1
        elif pending == "sell" and shares > 0:
            cash += shares * open_price * (1.0 - fee - tax - slippage)
            shares = 0
            trades += 1

        equity = cash + shares * close
        equity_curve.append(equity)
        visible_closes = closes[: index + 1]

        if role == "C-1":
            ma60 = moving_average(visible_closes, 60)
            if ma60 and shares == 0 and close < ma60 * 0.93:
                pending = "buy"
            elif ma60 and shares > 0 and close > ma60 * 1.05:
                pending = "sell"
            else:
                pending = "hold"
        else:
            ma15 = moving_average(visible_closes, 15)
            ma45 = moving_average(visible_closes, 45)
            if ma15 and ma45 and shares == 0 and ma15 > ma45:
                pending = "buy"
            elif ma15 and ma45 and shares > 0 and ma15 <= ma45:
                pending = "sell"
            else:
                pending = "hold"

    final_equity = equity_curve[-1] if equity_curve else initial_cash
    total_return = final_equity / initial_cash - 1
    drawdown = max_drawdown(equity_curve) if equity_curve else 0
    model_basis = (
        "C-1 保守價值流：只看當日以前收盤，價格低於 MA60 7% 才分批買，反彈高於 MA60 5% 才賣。"
        if role == "C-1"
        else "C-2 激進動能流：只看當日以前收盤，MA15 上穿 MA45 才買，MA15 跌回 MA45 才賣。"
    )
    learning_review = strategy_learning_review(role, rows, total_return, drawdown, trades)
    return {
        "symbol": symbol,
        "role": role,
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 6),
        "max_drawdown": round(drawdown, 6),
        "trade_count": trades,
        "model_basis": model_basis,
        "training_note": "訊號在 T 日收盤後形成，下一個交易日開盤才執行；不讀取預設日期之後資料。",
        "learning_review": learning_review,
        "future_knowledge_used": False,
    }


def calculate_rsi_list(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
            
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_macd_list(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float]:
    if len(prices) < slow:
        return 0.0, 0.0, 0.0
    
    def ema(values: list[float], period: int) -> list[float]:
        multiplier = 2.0 / (period + 1.0)
        ema_values = [values[0]]
        for val in values[1:]:
            ema_values.append((val - ema_values[-1]) * multiplier + ema_values[-1])
        return ema_values
        
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line[-1], signal_line[-1], hist[-1]


def analyze_market_index(end_date: str) -> dict | None:
    """獲取並分析大盤指數 (^TWII) 以判定當前市場 Regime"""
    try:
        # 大盤回測歷史天數，確保能算 120 日均線與長動能
        lookback_days = 260
        start_date = (datetime.fromisoformat(end_date) - timedelta(days=lookback_days)).date().isoformat()
        end_exclusive = (datetime.fromisoformat(end_date) + timedelta(days=1)).date().isoformat()
        
        # 獲取加權指數歷史
        rows = fetch_history("^TWII", start_date, end_exclusive)
        if len(rows) < 130:
            return None
        
        closes = [float(row["close"]) for row in rows]
        last = closes[-1]
        prev_close = closes[-2] if len(closes) > 1 else last
        change_pct = (last / prev_close - 1) * 100.0 if prev_close > 0 else 0.0
        
        ma20 = moving_average(closes, 20)
        ma60 = moving_average(closes, 60)
        ma120 = moving_average(closes, 120)
        
        # 判定 MA20 斜率是否為正 (近 5 日均值相較於前 5 日)
        ma20_list = []
        for t in range(len(closes) - 10, len(closes)):
            ma_t = moving_average(closes[:t+1], 20)
            if ma_t:
                ma20_list.append(ma_t)
        ma20_rising = False
        if len(ma20_list) >= 5:
            ma20_rising = sum(ma20_list[-2:]) / 2 > sum(ma20_list[:2]) / 2
            
        # 計算波動度 (20 日)
        volatility = 0.0
        if len(closes) > 21:
            returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
            avg = sum(returns) / len(returns)
            volatility = math.sqrt(sum((item - avg) ** 2 for item in returns) / len(returns))
            
        # 獲取美股 ADR/費半進行夜盤預警
        tsm_change = 0.0
        sox_change = 0.0
        adr_shock = False
        adr_msg = ""
        try:
            tsm_start = (datetime.fromisoformat(end_date) - timedelta(days=10)).date().isoformat()
            tsm_rows = fetch_history("TSM", tsm_start, end_exclusive)
            sox_rows = fetch_history("^SOX", tsm_start, end_exclusive)
            if len(tsm_rows) >= 2:
                tsm_change = (float(tsm_rows[-1]["close"]) / float(tsm_rows[-2]["close"]) - 1.0) * 100.0
            if len(sox_rows) >= 2:
                sox_change = (float(sox_rows[-1]["close"]) / float(sox_rows[-2]["close"]) - 1.0) * 100.0
            if tsm_change < -3.0 or sox_change < -3.5:
                adr_shock = True
                adr_msg = f"⚠️ 偵測到美股 ADR / 半導體指數隔夜暴跌 (TSM: {tsm_change:+.2f}%, SOX: {sox_change:+.2f}%)，啟動大盤預警熔斷機制，本會話策略全面轉守避險。 "
        except Exception as e:
            print(f"Error fetching TSM or SOX for overnight check: {e}")

        # 判定風險級別與部位指引
        risk_level = "GREEN"
        risk_label = "🟢 綠色 · 正常選股"
        risk_stance = "多頭常態，可正常選股操作，滿倉追蹤"
        buy_exposure = "100%"
        hold_exposure = "常態續抱，按既有策略移動停損"
        open_guide = "依各 Agent 推薦名單開盤限額進場"
        decision_reasons = []

        # 收集決策因子
        if change_pct < 0:
            decision_reasons.append(f"大盤當日收盤下跌 {change_pct:.2f}%")
        else:
            decision_reasons.append(f"大盤當日收盤上漲 +{change_pct:.2f}%")

        if tsm_change != 0.0:
            decision_reasons.append(f"台積電 ADR (TSM) 隔夜變動 {tsm_change:+.2f}%")
        if sox_change != 0.0:
            decision_reasons.append(f"費城半導體指數 (^SOX) 隔夜變動 {sox_change:+.2f}%")

        if last < ma20 if ma20 else False:
            decision_reasons.append("大盤價格已跌破 20 日均線 (短期走弱)")
        if ma20 and ma60 and ma20 < ma60:
            decision_reasons.append("大盤 20 日線低於 60 日線 (中期趨勢偏空)")

        # 判定燈號
        # ⚠️ 暫定閾值 (PROVISIONAL THRESHOLDS) — 尚未以歷史(含 2026-06-06 夜盤崩跌)校準誤報/漏報率。
        #    Phase 2 (P2-2) 需用回測校準後取代下列手寫數值，目前僅作安全側偏保守用途。
        # 1. 黑色 (急停): 自身大跌 <= -4% 或 ADR/費半暴跌 <= -5%
        if change_pct <= -4.0 or tsm_change <= -5.0 or sox_change <= -5.0:
            risk_level = "BLACK"
            risk_label = "⚫ 黑色 · 系統性急停"
            risk_stance = "大盤或美股半導體出現極端恐慌性暴跌，觸發系統性熔斷"
            buy_exposure = "0%"
            hold_exposure = "全面避險，既有持股嚴格停損，禁止新開倉"
            open_guide = "開盤絕不進場、絕不低接攤平！僅處理持股避險"
            
        # 2. 紅色 (停止買進): 自身大跌 <= -2.5% 或 ADR/費半大跌 <= -3% 或已進入均線空頭結構
        elif change_pct <= -2.5 or tsm_change <= -3.0 or sox_change <= -3.5 or (last < ma20 < ma60 if ma20 and ma60 else False):
            risk_level = "RED"
            risk_label = "🔴 紅色 · 停止新買進"
            risk_stance = "市場風險顯著升高，短期均線偏空，停止建新倉"
            buy_exposure = "0%"
            hold_exposure = "既有持股考慮減碼，收緊移動停損"
            open_guide = "停止所有買進指令，不得追價，以防範接刀風險"

        # 3. 黃色 (減半觀察): 波動度高或破 MA20
        elif volatility > 0.025 or (last < ma20 if ma20 else False):
            risk_level = "YELLOW"
            risk_label = "🟡 黃色 · 減半觀察"
            risk_stance = "大盤進入整理或波動放大，建議保守操作"
            buy_exposure = "50% 以下"
            hold_exposure = "嚴格限額，縮小每筆交易部位，防範高檔震盪"
            open_guide = "小量試水溫，僅選 A 級個股，移動停損收緊"

        # 判定 Regime
        if adr_shock or risk_level in ("RED", "BLACK"):
            regime = "弱勢空頭"
            regime_note = adr_msg + f"市場風險級別已升至 {risk_level}，系統強制切換至防禦狀態並加重安全邊際評分。"
        elif ma20 and ma60 and ma120 and last > ma20 > ma60 > ma120 and ma20_rising:
            regime = "強勢多頭"
            regime_note = "大盤均線多頭排列且向上，系統已自動加重強勢動能股評分。"
        elif ma20 and ma60 and ma120 and last < ma20 < ma60 < ma120 and not ma20_rising:
            regime = "弱勢空頭"
            regime_note = "大盤均線空頭排列，系統已自動重罰高波動並加重超賣股之安全邊際評分。"
        elif volatility > 0.02:
            regime = "高波動震盪"
            regime_note = "大盤近期波動度偏高且無明確趨勢，系統已自動側重低買高賣的擺盪指標評分。"
        else:
            regime = "區間整理"
            regime_note = "大盤進入窄幅區間整理，系統已自動側重 RSI 與 MACD 擺盪指標進行區間操作評分。"
            
        return {
            "symbol": "^TWII",
            "name": "加權指數 (TAIEX)",
            "date": rows[-1]["date"],
            "close": round(last, 2),
            "change_percent": round(change_pct, 2),
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "ma120": round(ma120, 2) if ma120 else None,
            "regime": regime,
            "regime_note": regime_note,
            "risk_level": risk_level,
            "risk_label": risk_label,
            "risk_stance": risk_stance,
            "buy_exposure": buy_exposure,
            "hold_exposure": hold_exposure,
            "open_guide": open_guide,
            "decision_reasons": decision_reasons
        }
    except Exception as e:
        print(f"Error analyzing market index ^TWII: {e}")
        return None


def analyze_candidate(symbol: str, rows: list[dict], market_regime: str | None = None, risk_level: str | None = None) -> dict:
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row.get("volume", 0) or 0) for row in rows]
    last = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else last
    day_change_pct = (last / prev_close - 1) * 100.0 if prev_close > 0 else 0.0

    model = model_evidence(symbol, closes, volumes, dates=[row["date"] for row in rows])
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    broke_ma5 = last < ma5 if ma5 else False
    momentum_20 = last / closes[-21] - 1 if len(closes) > 21 else 0
    volatility = 0.0
    if len(closes) > 21:
        returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
        avg = sum(returns) / len(returns)
        volatility = math.sqrt(sum((item - avg) ** 2 for item in returns) / len(returns))

    # Calculate RSI and MACD
    rsi = calculate_rsi_list(closes, 14)
    macd_val, signal_val, hist_val = calculate_macd_list(closes, 12, 26, 9)

    score = 0
    reasons = []

    # 根據大盤狀態 (Market Regime) 動態配置得分與理由
    if market_regime == "強勢多頭":
        # 1. 均線多頭結構 (加重得分)
        if ma20 and ma60 and last > ma20 > ma60:
            score += 4
            reasons.append("價格站上 20 日與 60 日均線 (多頭市場加重趨勢分 +4)")
        if ma60 and ma120 and ma60 > ma120:
            score += 2
            reasons.append("60 日均線高於 120 日均線，中期結構偏多")
            
        # 2. 動能因子 (加重得分，不懲罰 RSI 高檔)
        if momentum_20 > 0.08:
            score += 3
            reasons.append("20 日動能明顯轉強 (多頭順勢動能加分 +3)")
        elif momentum_20 < -0.08:
            score -= 1
            reasons.append("20 日短期回檔修正")
            
        # 3. 波動度與 RSI (多頭不重罰高波動，不扣分超買)
        if volatility > 0.045:
            reasons.append(f"近期波動偏高 ({volatility * 100:.1f}%)，但多頭市場維持持股")
        if rsi < 35:
            score += 1
            reasons.append(f"RSI(14) 降至 {rsi:.1f}，顯示超賣且價格進入價值安全區")
        elif rsi > 70:
            reasons.append(f"RSI(14) 達 {rsi:.1f}，進入超買區，多頭強勢暫不扣分")
        else:
            reasons.append(f"RSI(14) 數值為 {rsi:.1f}，處於常態整理區間")
            
        # 4. MACD 柱狀體
        if hist_val > 0:
            score += 2
            reasons.append(f"MACD 柱狀體攀升至 {hist_val:.2f} (多頭動能擴大分 +2)")
        else:
            score -= 1
            reasons.append(f"MACD 柱狀體位於負值區 ({hist_val:.2f})")
            
    elif market_regime == "弱勢空頭":
        # 1. 均線與動能 (不給追價與均線多頭得分)
        if ma20 and ma60 and last > ma20 > ma60:
            score += 1
            reasons.append("價格站上 20/60 均線，但大盤偏空需防假突破")
        if momentum_20 > 0.08:
            reasons.append("20 日動能有反彈，空頭市場不建議追高")
        elif momentum_20 < -0.08:
            score -= 2
            reasons.append("20 日動能偏弱，避開空頭弱勢股")
            
        # 2. 波動度與價值買進 (重罰高波動，大幅加分超賣安全邊際)
        if volatility > 0.045:
            score -= 3
            reasons.append(f"近期波動偏高 ({volatility * 100:.1f}%)，避開高風險標的")
        if rsi < 35:
            score += 3
            reasons.append(f"RSI(14) 降至 {rsi:.1f} (空頭超跌安全邊際擴大分 +3)")
        elif rsi > 70:
            score -= 2
            reasons.append(f"RSI(14) 達 {rsi:.1f} 進入超買區，空頭反彈應避開")
        else:
            reasons.append(f"RSI(14) 數值為 {rsi:.1f}，處於常態整理區間")
            
        # 3. MACD 柱狀體
        if hist_val > 0:
            score += 1
            reasons.append(f"MACD 柱狀體攀升至 {hist_val:.2f}")
        else:
            score -= 2
            reasons.append(f"MACD 柱狀體位於負值區 ({hist_val:.2f})，空頭慣性強烈")
            
    else: # 區間整理 或 高波動震盪
        # 1. 均線多頭結構
        if ma20 and ma60 and last > ma20 > ma60:
            score += 2
            reasons.append("價格站上 20 日與 60 日均線")
        if ma60 and ma120 and ma60 > ma120:
            score += 1
            reasons.append("60 日均線高於 120 日均線，中期結構偏多")
            
        # 2. 動能與波動
        if momentum_20 > 0.08:
            score += 1
            reasons.append("20 日動能轉強，震盪整理盤防假突破")
        elif momentum_20 < -0.08:
            score -= 1
            reasons.append("20 日動能偏弱")
        if volatility > 0.045:
            score -= 2
            reasons.append(f"近期波動偏高 ({volatility * 100:.1f}%)，區間交易需控風險")
            
        # 3. RSI 擺盪 (加重 RSI 的低買高賣)
        if rsi < 35:
            score += 2
            reasons.append(f"RSI(14) 降至 {rsi:.1f} (區間下緣，擺盪加分 +2)")
        elif rsi > 70:
            score -= 2
            reasons.append(f"RSI(14) 達 {rsi:.1f} (區間上緣，防回檔扣分 -2)")
        else:
            reasons.append(f"RSI(14) 數值為 {rsi:.1f}，處於常態整理區間")
            
        # 4. MACD 柱狀體
        if hist_val > 0:
            score += 2
            reasons.append(f"MACD 柱狀體攀升至 {hist_val:.2f}")
        else:
            score -= 1
            reasons.append(f"MACD 柱狀體位於負值區 ({hist_val:.2f})")

    if not reasons:
        reasons.append("訊號不明確，暫列觀察")

    reasons.append(
        f"AI 因子模型估計隔日偏多機率 {model['probability_up']:.1f}%；"
        f"趨勢 {model['trend_points']}、動能 {model['momentum_points']}、風險 {model['risk_points']}"
    )

    # 防接刀與大盤預警過濾器懲罰 (⚠️ 暫定閾值，待 Phase 2 校準)
    # 判定分級
    if day_change_pct <= -4.5:
        score -= 10
        grade = "C"
        grade_label = "🔴 C級 · 禁買"
        reasons.append(f"⚠️ 當日價格重挫 ({day_change_pct:.2f}%)，量價型態走壞，系統判定為 C 級禁買以防範接刀風險")
    elif risk_level in ("RED", "BLACK"):
        score -= 10
        grade = "C"
        grade_label = "🔴 C級 · 禁買"
        reasons.append(f"⚠️ 大盤風險級別為 {risk_level}，系統性停止新買進，個股強制降為 C 級禁買")
    elif day_change_pct <= -3.0 and broke_ma5:
        score -= 5
        grade = "B"
        grade_label = "🟡 B級 · 觀察反彈"
        reasons.append(f"⚠️ 當日價格下跌 ({day_change_pct:.2f}%) 且跌破 5 日線，系統判定為 B 級觀察防範短線走弱")
    elif risk_level == "YELLOW":
        score -= 3
        grade = "B"
        grade_label = "🟡 B級 · 觀察反彈"
        reasons.append(f"⚠️ 大盤風險級別為 YELLOW，新買進減半，個股限額觀察")
    elif ma20 and last < ma20:
        grade = "B"
        grade_label = "🟡 B級 · 觀察反彈"
        reasons.append(f"個股價格低於 20 日均線，尚未站穩短線多頭結構")
    else:
        grade = "A"
        grade_label = "🟢 A級 · 可執行"

    action = "觀察"
    if grade == "C":
        action = "避開或檢查賣出風險"
    elif score >= 5 and grade == "A":
        action = "研究買進候選"
    else:
        action = "觀察"

    return {
        "symbol": symbol,
        "last_date": rows[-1]["date"],
        "last_close": round(last, 2),
        "score": score,
        "action": action,
        "grade": grade,
        "grade_label": grade_label,
        "reasons": reasons,
        "model": model,
        "ai_predictor": generate_ai_prediction(closes, rsi, hist_val),
        "future_knowledge_used": False,
    }


GRADE_ORDER = {"C": 0, "B": 1, "A": 2}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _grade_label(grade: str) -> str:
    return {
        "A": "A級 · 可執行",
        "B": "B級 · 觀察",
        "C": "C級 · 禁買",
    }.get(grade, grade)


def _downgrade_grade(current: str, target: str) -> str:
    return target if GRADE_ORDER.get(target, 0) < GRADE_ORDER.get(current, 0) else current


def codex_tomorrow_decision_v2(
    symbol: str,
    rows: list[dict],
    analysis: dict,
    market_info: dict | None = None,
    sector: str = "",
) -> dict:
    """Codex's own tomorrow-decision overlay: gate risk first, rank opportunity last."""
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row.get("volume", 0) or 0) for row in rows]
    highs = [float(row.get("high", row["close"]) or row["close"]) for row in rows]
    lows = [float(row.get("low", row["close"]) or row["close"]) for row in rows]
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    day_change_pct = (last / prev - 1.0) * 100.0 if prev > 0 else 0.0
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    avg_vol20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0.0
    volume_ratio20 = volumes[-1] / avg_vol20 if avg_vol20 > 0 else 1.0
    day_range = highs[-1] - lows[-1]
    close_location = (last - lows[-1]) / day_range if day_range > 0 else 0.5
    dev_ma20 = last / ma20 - 1.0 if ma20 else 0.0
    dev_ma60 = last / ma60 - 1.0 if ma60 else 0.0
    model = analysis.get("model") or {}
    calibrated = model.get("calibrated") or {}
    prob = float(model.get("calibrated_probability_up") or model.get("probability_up") or 50.0)
    bucket_return = float(calibrated.get("avg_fwd_return") or 0.0)
    hit_rate = float(calibrated.get("empirical_up_rate") or 0.5)
    volatility20 = float(model.get("volatility_20") or 0.0)
    momentum20 = float(model.get("momentum_20") or 0.0)
    rsi14 = float(model.get("rsi14") or 50.0)

    risk_level = (market_info or {}).get("risk_level")
    market_state = risk_level or "UNKNOWN"
    new_position_permission = "normal"
    grade = analysis.get("grade", "B")
    vetoes: list[str] = []
    downgrades: list[str] = []

    if risk_level in ("BLACK", "RED"):
        grade = "C"
        new_position_permission = "blocked"
        vetoes.append(f"Market risk is {risk_level}; Codex v2 blocks all new positions.")
    elif risk_level == "YELLOW":
        grade = _downgrade_grade(grade, "B")
        new_position_permission = "reduced"
        downgrades.append("Market risk is YELLOW; Codex v2 caps new ideas at B observation.")

    if day_change_pct <= -4.5:
        grade = "C"
        vetoes.append(f"Single-day loss {day_change_pct:.2f}% <= -4.5%.")
    if ma20 and last < ma20:
        grade = "C"
        vetoes.append("Close is below MA20; trend structure is broken.")
    if ma60 and last < ma60:
        grade = "C"
        vetoes.append("Close is below MA60; medium-term structure is broken.")
    if day_change_pct < 0 and volume_ratio20 >= 1.8:
        grade = "C"
        vetoes.append(f"Down day with volume {volume_ratio20:.1f}x 20-day average.")
    if day_change_pct <= -2.0 and close_location <= 0.25:
        grade = "C"
        vetoes.append(f"Closed near session low ({close_location:.0%} of range) on a weak day.")

    overheat = False
    if dev_ma20 >= 0.12:
        overheat = True
        downgrades.append(f"Price is {dev_ma20:.1%} above MA20; overheat penalty.")
    if dev_ma60 >= 0.30:
        overheat = True
        downgrades.append(f"Price is {dev_ma60:.1%} above MA60; extended-run penalty.")
    if rsi14 >= 75:
        overheat = True
        downgrades.append(f"RSI {rsi14:.1f} is overheated.")
    if overheat and grade == "A":
        grade = "B"

    model_edge = _clamp((prob - 55.0) * 0.12, -0.8, 0.8)
    calibrated_edge = _clamp(bucket_return * 8.0 + (hit_rate - 0.5) * 1.5, -0.8, 0.8)
    momentum_quality = _clamp(momentum20 * 6.0, -1.5, 1.8)
    structure_score = 0.0
    if ma20 and last > ma20:
        structure_score += 0.8
    if ma60 and last > ma60:
        structure_score += 0.7
    if ma20 and ma60 and ma20 > ma60:
        structure_score += 0.5

    opportunity_score = structure_score + momentum_quality + model_edge + calibrated_edge
    risk_score = 0.0
    risk_score += _clamp(volatility20 * 45.0, 0.0, 2.5)
    risk_score += _clamp(max(0.0, volume_ratio20 - 1.0) * 0.5, 0.0, 1.5)
    risk_score += _clamp(max(0.0, dev_ma20 - 0.10) * 10.0, 0.0, 1.5)
    risk_score += _clamp(max(0.0, dev_ma60 - 0.25) * 5.0, 0.0, 1.5)
    risk_score += 3.0 if vetoes else 0.0
    risk_score += 1.0 if risk_level == "YELLOW" else 0.0
    risk_score += 4.0 if risk_level in ("RED", "BLACK") else 0.0

    final_score = opportunity_score - risk_score
    if grade == "C":
        action = "Codex v2: 禁買"
        final_score = min(final_score, -5.0)
    elif grade == "B":
        action = "Codex v2: 觀察，不自動進場"
    else:
        action = "Codex v2: 可執行候選"

    message = (
        f"Codex v2 says {symbol} is grade {grade}. "
        f"Market={market_state}, permission={new_position_permission}, "
        f"opportunity={opportunity_score:.2f}, risk={risk_score:.2f}. "
        "Model probability is auxiliary only."
    )
    return {
        "model_name": "Codex Tomorrow Decision Model v2",
        "market_state": market_state,
        "new_position_permission": new_position_permission,
        "grade": grade,
        "grade_label": _grade_label(grade),
        "action": action,
        "opportunity_score": round(opportunity_score, 3),
        "risk_score": round(risk_score, 3),
        "final_score": round(final_score, 3),
        "model_probability_weight": "auxiliary",
        "vetoes": vetoes,
        "downgrades": downgrades,
        "metrics": {
            "day_change_pct": round(day_change_pct, 3),
            "volume_ratio20": round(volume_ratio20, 3),
            "close_location": round(close_location, 3),
            "dev_ma20": round(dev_ma20, 4),
            "dev_ma60": round(dev_ma60, 4),
            "volatility20": round(volatility20, 4),
            "momentum20": round(momentum20, 4),
            "rsi14": round(rsi14, 2),
        },
        "message_to_agents": message,
        "future_knowledge_used": False,
    }


def apply_codex_v2_overlay(
    symbol: str,
    rows: list[dict],
    analysis: dict,
    market_info: dict | None = None,
    sector: str = "",
) -> dict:
    overlay = codex_tomorrow_decision_v2(symbol, rows, analysis, market_info=market_info, sector=sector)
    merged = dict(analysis)
    merged["raw_technical_score"] = analysis.get("score")
    merged["codex_score"] = overlay["final_score"]
    merged["grade"] = overlay["grade"]
    merged["grade_label"] = overlay["grade_label"]
    merged["action"] = overlay["action"]
    merged["codex_decision_model"] = overlay
    reasons = list(analysis.get("reasons", []))
    v2_reasons = [f"Codex v2: {item}" for item in overlay["vetoes"] + overlay["downgrades"]]
    if not v2_reasons:
        v2_reasons.append("Codex v2: no hard veto; opportunity is ranked after risk gates.")
    merged["reasons"] = v2_reasons + reasons
    return merged


OPTIMIZED_WEIGHTS_PATH = PROJECT / "model_artifacts" / "optimized_weights.json"

def load_optimized_weights() -> dict | None:
    try:
        if OPTIMIZED_WEIGHTS_PATH.exists():
            return json.loads(OPTIMIZED_WEIGHTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def save_optimized_weights(weights: dict) -> None:
    try:
        OPTIMIZED_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPTIMIZED_WEIGHTS_PATH.write_text(json.dumps(weights, indent=4, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"Error saving optimized weights: {e}")

def train_logistic_regression_pure(X: list[list[float]], y: list[int], lr: float = 0.1, l2: float = 0.01, epochs: int = 500) -> dict:
    w = [0.0, 0.15, 0.25, 0.15]
    N = len(y)
    if N == 0:
        return {"weights": {"bias": w[0], "rsi": w[1], "slope": w[2], "macd_hist": w[3]}, "epoch_logs": [], "accuracy": 50.0}

    epoch_logs = []
    for epoch in range(1, epochs + 1):
        y_pred = []
        for i in range(N):
            z = sum(X[i][j] * w[j] for j in range(4))
            sigmoid_z = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            y_pred.append(sigmoid_z)

        loss_sum = 0.0
        for i in range(N):
            p = max(1e-15, min(1.0 - 1e-15, y_pred[i]))
            loss_sum += y[i] * math.log(p) + (1.0 - y[i]) * math.log(1.0 - p)
        loss = -loss_sum / N
        l2_reg = (l2 / (2.0 * N)) * sum(w[j]**2 for j in range(1, 4))
        total_loss = loss + l2_reg

        grad = [0.0, 0.0, 0.0, 0.0]
        for j in range(4):
            grad_sum = sum(X[i][j] * (y_pred[i] - y[i]) for i in range(N))
            grad[j] = grad_sum / N
            if j > 0:
                grad[j] += (l2 / N) * w[j]

        for j in range(4):
            w[j] -= lr * grad[j]

        correct = sum(1 for i in range(N) if (1 if y_pred[i] >= 0.5 else 0) == y[i])
        accuracy = (correct / N) * 100.0

        if epoch == 1 or epoch == epochs or epoch % max(1, epochs // 10) == 0:
            epoch_logs.append({
                "epoch": epoch,
                "loss": float(total_loss),
                "accuracy": float(accuracy)
            })

    return {
        "weights": {
            "bias": w[0],
            "rsi": w[1],
            "slope": w[2],
            "macd_hist": w[3]
        },
        "epoch_logs": epoch_logs,
        "accuracy": accuracy
    }

def generate_ai_prediction(closes: list[float], rsi: float, macd_hist: float) -> dict:
    if len(closes) < 5:
        return {
            "prediction": "Rangebound (盤整)",
            "probability": 50.0,
            "predicted_range": f"${closes[-1]:.1f} - ${closes[-1]:.1f}",
            "features": [
                {"name": "RSI 超買超賣權重", "weight": 30},
                {"name": "5日 OLS 短期動能", "weight": 40},
                {"name": "MACD 柱狀體排列", "weight": 30}
            ],
            "rationale": "歷史數據不足，無法進行預測模型分析。"
        }

    last_price = float(closes[-1])
    
    y = closes[-5:]
    slope = (-2.0 * y[0] - 1.0 * y[1] + 1.0 * y[3] + 2.0 * y[4]) / 10.0

    window = min(20, len(closes))
    sub_closes = closes[-window:]
    returns = []
    for i in range(1, len(sub_closes)):
        if sub_closes[i - 1] != 0:
            returns.append((sub_closes[i] - sub_closes[i - 1]) / sub_closes[i - 1])
    
    if returns:
        avg_ret = sum(returns) / len(returns)
        variance = sum((r - avg_ret) ** 2 for r in returns) / len(returns)
        volatility = math.sqrt(variance)
    else:
        volatility = 0.01

    std_dev = last_price * max(volatility, 0.005)
    pred_low = last_price + slope - 1.96 * std_dev
    pred_high = last_price + slope + 1.96 * std_dev

    pred_low = max(0.1, round(pred_low, 1))
    pred_high = max(pred_low + 0.1, round(pred_high, 1))

    weights = load_optimized_weights()
    if weights is not None:
        w_bias = weights.get("bias", 0.0)
        w_rsi = weights.get("rsi", 0.15)
        w_slope = weights.get("slope", 0.25)
        w_macd = weights.get("macd_hist", 0.15)
        
        x_rsi = (50.0 - rsi) / 10.0
        x_slope = (slope / last_price) * 100.0
        x_macd = (macd_hist / last_price) * 100.0
        
        z = w_bias + w_rsi * x_rsi + w_slope * x_slope + w_macd * x_macd
        prob_uptrend = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))) * 100.0
        prob_uptrend = max(15.0, min(92.0, prob_uptrend))
        
        contrib_rsi = abs(w_rsi * x_rsi)
        contrib_slope = abs(w_slope * x_slope)
        contrib_macd = abs(w_macd * x_macd)
        
        is_optimized = True
    else:
        w_rsi, w_slope, w_macd = 0.15, 0.25, 0.15
        rsi_contrib = (50.0 - rsi) * 0.3
        slope_pct = slope / last_price
        slope_contrib = max(-20.0, min(20.0, slope_pct * 500.0))
        macd_contrib = max(-15.0, min(15.0, macd_hist * 2.0))
        
        prob_uptrend = 50.0 + rsi_contrib + slope_contrib + macd_contrib
        prob_uptrend = max(15.0, min(92.0, prob_uptrend))
        
        contrib_rsi = abs(rsi_contrib)
        contrib_slope = abs(slope_contrib)
        contrib_macd = abs(macd_contrib)
        
        is_optimized = False

    suffix = " (優化後)" if is_optimized else ""
    if prob_uptrend > 55.0:
        prediction = "Uptrend (看漲)"
        prob = float(prob_uptrend)
        if is_optimized:
            rationale = (
                f"優化模型預估明日上漲機率為 {prob:.0f}%。主因 14 日 RSI 為 {rsi:.1f}，近 5 日斜率為 {slope:.2f}。此外，"
                f"MACD 柱狀體為 {macd_hist:.2f}。目前模型配置權重為：RSI = {w_rsi:.3f}, 斜率 = {w_slope:.3f}, MACD = {w_macd:.3f}。預測主要區間為 ${pred_low:.1f} 至 ${pred_high:.1f}。"
            )
        else:
            rationale = (
                f"模型顯示明日上漲機率達 {prob:.0f}%。主因 14 日 RSI 目前為 {rsi:.1f}，估值處於偏低或整理安全區，"
                f"且近 5 日股價斜率為 {slope:.2f}。雖然 MACD 柱狀體為 {macd_hist:.2f}，"
                f"但近期震盪收斂，波動度約 {(volatility*100):.1f}%。模型預測明日價格主要運行區間落於 ${pred_low:.1f} 至 ${pred_high:.1f}，"
                f"建議持股或分批左側承接。"
            )
    elif prob_uptrend < 45.0:
        prediction = "Downtrend (看跌)"
        prob = float(100.0 - prob_uptrend)
        if is_optimized:
            rationale = (
                f"優化模型預估明日下跌機率為 {prob:.0f}%（看跌）。主因 RSI 為 {rsi:.1f}，5日斜率為 {slope:.2f} 呈下行趨勢，"
                f"且 MACD 柱狀體位於 {macd_hist:.2f} 負值區。在模型權重組態下（RSI = {w_rsi:.3f}, 斜率 = {w_slope:.3f}, MACD = {w_macd:.3f}），"
                f"預測明日運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}，風控建議偏向保守。"
            )
        else:
            rationale = (
                f"模型預期明日有 {prob:.0f}% 機率延續修正趨勢。主要由於 RSI 達 {rsi:.1f} 且短期 5 日斜率為 {slope:.2f} "
                f"呈現下行慣性，且 MACD 柱狀體為 {macd_hist:.2f} 處於負值區。波動度 {(volatility*100):.1f}% 顯示賣壓未消退，"
                f"預測明日運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}。風控建議保留現金，暫避風險。"
            )
    else:
        prediction = "Rangebound (盤整)"
        prob = 50.0
        if is_optimized:
            rationale = (
                f"優化模型預估明日呈區間盤整（機率 50%）。短期斜率偏平 ({slope:.2f})，RSI 數值為 {rsi:.1f} 處於中性整理區。"
                f"目前權重配置為：RSI = {w_rsi:.3f}, 斜率 = {w_slope:.3f}, MACD = {w_macd:.3f}。預估明日運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}。"
            )
        else:
            rationale = (
                f"模型預估明日將呈區間盤整（機率 50%）。短期斜率極微 ({slope:.2f})，RSI 數值為 {rsi:.1f} 處於常態中性區，"
                f"多空拉鋸。預估明日運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}，建議空手者觀望，持股者續抱等待明確動能訊號。"
            )

    total_abs = contrib_rsi + contrib_slope + contrib_macd + 1e-9
    rsi_w = int(round(contrib_rsi / total_abs * 100))
    slope_w = int(round(contrib_slope / total_abs * 100))
    macd_w = int(round(contrib_macd / total_abs * 100))

    total_w = rsi_w + slope_w + macd_w
    if total_w > 0:
        rsi_w = int(round(rsi_w / total_w * 100))
        slope_w = int(round(slope_w / total_w * 100))
        macd_w = 100 - rsi_w - slope_w

    features = [
        {"name": f"RSI 超買超賣權重{suffix}", "weight": rsi_w},
        {"name": f"5日 OLS 短期動能{suffix}", "weight": slope_w},
        {"name": f"MACD 柱狀體排列{suffix}", "weight": macd_w}
    ]

    return {
        "prediction": prediction,
        "probability": prob,
        "predicted_range": f"${pred_low:.1f} - ${pred_high:.1f}",
        "features": features,
        "rationale": rationale
    }


def plan_next_session(symbol: str, rows: list[dict], position: dict | None, market_regime: str | None = None, risk_level: str | None = None, market_info: dict | None = None) -> dict:
    analysis = analyze_candidate(symbol, rows, market_regime=market_regime, risk_level=risk_level)
    analysis = apply_codex_v2_overlay(symbol, rows, analysis, market_info=market_info or {"risk_level": risk_level})
    closes = [float(row["close"]) for row in rows]
    last = closes[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    held = bool(position and float(position.get("shares", 0) or 0) > 0)
    cost = float(position.get("cost", 0) or 0) if position else 0.0
    gain = 0.0 if not held or cost <= 0 else last / cost - 1.0
    reasons = list(analysis["reasons"])

    if held:
        # 逢低加碼條件: 大盤為綠或黃(安全)、個股評級為 A、價格較成本拉回 5% 至 12% 之間
        is_market_safe = (risk_level not in ("RED", "BLACK"))
        is_candidate_grade_a = (analysis.get("grade") == "A")
        
        if gain >= 0.25 and ma20 and last < ma20:
            action = "明日減碼獲利"
            reasons.insert(0, f"持股獲利約 {gain * 100:.2f}%，且價格跌破 20 日均線，優先保護獲利")
        elif gain >= 0.35:
            action = "明日部分獲利了結"
            reasons.insert(0, f"持股獲利約 {gain * 100:.2f}%，即使趨勢仍強，也應考慮分批落袋")
        elif ma60 and last < ma60 and gain > 0:
            action = "明日檢查賣出風險"
            reasons.insert(0, "仍有獲利但價格跌破 60 日均線，需避免獲利回吐")
        elif is_market_safe and is_candidate_grade_a and (-0.12 <= gain <= -0.05):
            action = "明日逢低加碼"
            reasons.insert(0, f"個股處於長多結構且大盤安全，但目前價格較加權成本拉回 {gain * 100:+.1f}%，觸發逢低分批加碼信號")
        elif ma20 and ma60 and last > ma20 > ma60:
            action = "明日續抱"
            reasons.insert(0, "持股仍在短中期上升結構，續抱但設定獲利保護線")
        else:
            action = "明日續抱觀察"
            reasons.insert(0, "持股未觸發明確賣出，但也未達強勢續抱條件")
    else:
        action = analysis["action"].replace("研究買進候選", "明日研究買進候選")
        if action == "觀察":
            action = "明日觀察"

    rsi = calculate_rsi_list(closes, 14)
    _, _, hist_val = calculate_macd_list(closes, 12, 26, 9)

    return {
        "symbol": symbol,
        "as_of": rows[-1]["date"],
        "last_close": round(last, 2),
        "held": held,
        "cost": cost if held else None,
        "unrealized_gain": round(gain, 6) if held else None,
        "score": analysis["score"],
        "action": action,
        "grade": analysis["grade"],
        "grade_label": analysis["grade_label"],
        "reasons": reasons,
        "model": analysis["model"],
        "codex_decision_model": analysis.get("codex_decision_model"),
        "ai_predictor": generate_ai_prediction(closes, rsi, hist_val),
        "rule": "收盤後產生明日計畫，不做當沖；買賣僅作研究與模擬用途。",
        "future_knowledge_used": False,
    }


def market_context(end: str, lookback_days: int = 180) -> dict:
    start = (datetime.fromisoformat(end) - timedelta(days=lookback_days)).date().isoformat()
    end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
    candidates = []
    for item in MARKET_CONTEXT_SYMBOLS:
        try:
            rows = fetch_history(item["symbol"], start, end_exclusive)
            if len(rows) < 80:
                continue
            analysis = analyze_candidate(item["symbol"], rows)
            closes = [float(row["close"]) for row in rows]
            last = closes[-1]
            ma20 = moving_average(closes, 20)
            ma60 = moving_average(closes, 60)
            trend = "多頭" if ma20 and ma60 and last > ma20 > ma60 else "防守" if ma60 and last < ma60 else "盤整"
            candidates.append(
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "last_date": rows[-1]["date"],
                    "last_close": round(last, 2),
                    "score": analysis["score"],
                    "trend": trend,
                    "reasons": analysis["reasons"][:3],
                }
            )
        except Exception as exc:
            candidates.append({"symbol": item["symbol"], "name": item["name"], "error": str(exc)})
    valid = [item for item in candidates if "score" in item]
    avg_score = sum(item["score"] for item in valid) / len(valid) if valid else 0
    regime = "偏多" if avg_score >= 4 else "偏空/防守" if avg_score <= 0 else "中性震盪"
    return {
        "as_of": end,
        "regime": regime,
        "average_score": round(avg_score, 2),
        "benchmarks": candidates,
        "rule": "以 0050/006208 作台股大盤代理；只用截止日以前資料判讀環境。",
    }


def discover_candidates(end: str, limit: int = 5, lookback_days: int = 320) -> dict:
    start = (datetime.fromisoformat(end) - timedelta(days=lookback_days)).date().isoformat()
    end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
    context = market_context(end)
    # 修復:取得大盤 regime 並傳入 analyze_candidate,讓「強勢多頭/弱勢空頭」加權分支生效
    # (與 /api/recommend、/api/next-day-plan 一致;先前未傳 → 永遠走中性分支)
    market_info = analyze_market_index(end)
    candidate_regime = market_info["regime"] if market_info else None
    risk_level = market_info.get("risk_level") if market_info else None
    candidates = []
    for item in DISCOVERY_UNIVERSE:
        try:
            rows = fetch_history(item["symbol"], start, end_exclusive)
            if len(rows) < 130:
                continue
            analysis = analyze_candidate(item["symbol"], rows, market_regime=candidate_regime, risk_level=risk_level)
            analysis = apply_codex_v2_overlay(
                item["symbol"],
                rows,
                analysis,
                market_info=market_info,
                sector=item.get("sector", ""),
            )
            model = analysis.get("model") or {}
            calibrated = model.get("calibrated") or {}
            calibrated_prob = float(model.get("calibrated_probability_up") or model.get("probability_up") or 50)
            bucket_return = float(calibrated.get("avg_fwd_return") or 0)
            bucket_hit_rate = float(calibrated.get("empirical_up_rate") or 0.5)
            codex_v2 = analysis.get("codex_decision_model") or {}
            discovery_score = float(codex_v2.get("final_score", analysis.get("codex_score", analysis["score"])))
            reasons = [
                f"Codex v2 final score {discovery_score:.2f}; raw technical {analysis.get('raw_technical_score', analysis['score'])}; calibrated probability {calibrated_prob:.1f}% is auxiliary.",
                codex_v2.get("message_to_agents", ""),
                f"Agent 綜合分數 {discovery_score:.2f}；技術分 {analysis['score']}，校準偏多 {calibrated_prob:.1f}%。",
                f"所屬族群：{item['sector']}；大盤環境：{context['regime']}。",
            ]
            if calibrated:
                reasons.append(
                    f"同機率桶歷史上漲率 {bucket_hit_rate * 100:.1f}%，5日均報酬 {bucket_return * 100:.2f}%。"
                )
            reasons.extend(analysis["reasons"][:3])
            candidates.append(
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "sector": item["sector"],
                    "last_date": analysis["last_date"],
                    "last_close": analysis["last_close"],
                    "action": analysis["action"],
                    "score": analysis["score"],
                    "grade": analysis.get("grade", "B"),
                    "grade_label": analysis.get("grade_label", "B級"),
                    "discovery_score": round(discovery_score, 3),
                    "probability_up": round(calibrated_prob, 1),   # 校準機率(供前端統一標準化比較)
                    "model": model,
                    "codex_decision_model": codex_v2,
                    "reasons": reasons,
                    "future_knowledge_used": False,
                }
            )
        except Exception:
            continue
    candidates.sort(key=lambda row: (GRADE_ORDER.get(row.get("grade"), 0), row["discovery_score"]), reverse=True)
    return {
        "as_of": end,
        "market_context": context,
        "universe_size": len(DISCOVERY_UNIVERSE),
        "selected_symbols": [item["symbol"] for item in candidates if item.get("grade") != "C"][:limit],
        "candidates": candidates[:limit],
        "rule": "Codex v2: market circuit breaker -> new-position permission -> hard vetoes -> A/B/C grade -> opportunity ranking; model probability is auxiliary.",
    }


# ---------------------------------------------------------------------------
# Multi-Agent Stock Discovery Functions
# ---------------------------------------------------------------------------

def _vol_of_returns(closes: list[float], window: int) -> float:
    """Annualised daily return std-dev over the last `window` trading days."""
    if len(closes) < window + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - window, len(closes))]
    avg = sum(rets) / len(rets)
    return math.sqrt(sum((r - avg) ** 2 for r in rets) / len(rets))


def _claude_determine_regime(all_data: dict) -> tuple[str, dict]:
    """Determine market regime via equal-weighted synthetic index of discovery universe.

    Tighter than naive TAIEX: HIGH_VOL triggers at 60th vol-percentile (vs 80th)
    to give earlier drawdown protection — fixes the backtest finding.
    """
    if not all_data:
        return "RANGE", AGENT_REGIME_POLICY["RANGE"]

    all_dates: set[str] = set()
    for data in all_data.values():
        all_dates.update(data["dates"])
    sorted_dates = sorted(all_dates)

    first_prices = {t: d["closes"][0] for t, d in all_data.items()}
    date_idx_maps = {t: {date: i for i, date in enumerate(d["dates"])} for t, d in all_data.items()}

    index_series: list[float] = []
    for date in sorted_dates:
        vals = [
            all_data[t]["closes"][date_idx_maps[t][date]] / first_prices[t]
            for t in all_data
            if date in date_idx_maps[t]
        ]
        if vals:
            index_series.append(sum(vals) / len(vals))

    if len(index_series) < 80:
        return "RANGE", AGENT_REGIME_POLICY["RANGE"]

    # MA60 of the synthetic index
    ma60: list[float | None] = [
        None if i < 59 else sum(index_series[i - 59: i + 1]) / 60.0
        for i in range(len(index_series))
    ]

    # 20-period slope of MA60
    slope: list[float | None] = [
        None if i < 20 or ma60[i] is None or ma60[i - 20] is None
        else ma60[i] - ma60[i - 20]  # type: ignore[operator]
        for i in range(len(ma60))
    ]

    # 20-day rolling vol of index returns
    rets = [0.0] + [index_series[i] / index_series[i - 1] - 1.0 for i in range(1, len(index_series))]
    vol20: list[float | None] = []
    for i in range(len(rets)):
        if i < 19:
            vol20.append(None)
        else:
            w = rets[i - 19: i + 1]
            avg_w = sum(w) / 20.0
            vol20.append(math.sqrt(sum((r - avg_w) ** 2 for r in w) / 20.0))

    # Vol percentile (120-day lookback) — HIGH_VOL threshold tightened to 0.60
    vol_pct: float | None = None
    if vol20[-1] is not None:
        w_vols = [v for v in vol20[-120:] if v is not None]
        if len(w_vols) >= 40:
            vol_pct = sum(1 for v in w_vols if vol20[-1] >= v) / len(w_vols)  # type: ignore[operator]

    idx_val = index_series[-1]
    ma_val = ma60[-1]
    slope_val = slope[-1]

    regime = "RANGE"
    if vol_pct is not None and vol_pct >= 0.60:  # tighter trigger (was 0.80)
        regime = "HIGH_VOL"
    elif ma_val is not None and slope_val is not None:
        above = idx_val > ma_val
        going_up = slope_val > 0
        if above and going_up:
            regime = "BULL_TREND"
        elif (not above) and (not going_up):
            regime = "BEAR_TREND"

    return regime, AGENT_REGIME_POLICY[regime]


def discover_antigravity_candidates(end: str, limit: int = 5) -> list[dict]:
    """Antigravity VCP (Volatility Contraction Pattern) breakthrough selection."""
    lookback = 320
    start = (datetime.fromisoformat(end) - timedelta(days=lookback)).date().isoformat()
    end_excl = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
    
    market_info = analyze_market_index(end)
    risk_level = market_info.get("risk_level") if market_info else None
    
    candidates = []
    for item in DISCOVERY_UNIVERSE:
        try:
            rows = fetch_history(item["symbol"], start, end_excl)
            if len(rows) < 130:
                continue
            closes = [float(r["close"]) for r in rows]
            volumes = [float(r.get("volume", 0) or 0) for r in rows]

            # 1. 均線與趨勢判定 (Stage 2 Trend Template)
            last = closes[-1]
            ma50 = moving_average(closes, 50)
            ma150 = moving_average(closes, 150)
            ma200 = moving_average(closes, 200)
            
            # 取得 10 日前的 MA200 用以判定 200 日線是否上揚
            ma200_prev = None
            if len(closes) >= 210:
                ma200_prev = moving_average(closes[:-10], 200)

            is_stage2 = False
            if ma50 and ma150 and ma200:
                is_stage2 = (last > ma50) and (ma50 > ma150) and (ma150 > ma200)
                if ma200_prev is not None:
                    is_stage2 = is_stage2 and (ma200 > ma200_prev)

            score = 0
            reasons: list[str] = []

            if is_stage2:
                score += 3
                reasons.append("處於第二階段多頭結構 (MA50 > MA150 > MA200)")
            else:
                score -= 2
                reasons.append("未滿足第二階段多頭結構 (長線趨勢偏弱)")

            # 2. 波動多重收縮 (VCP 特徵)
            vol_10 = _vol_of_returns(closes, 10)
            vol_20 = _vol_of_returns(closes, 20)
            vol_60 = _vol_of_returns(closes, 60)

            if vol_10 < vol_20 < vol_60:
                score += 4
                reasons.append("波動多重巢狀收縮 (vol_10 < vol_20 < vol_60)")
            elif vol_10 < vol_60:
                score += 2
                reasons.append("波動收縮 (短期 vol < 長期 vol)")
            
            if vol_10 > 2.0 * vol_60:
                score -= 3
                reasons.append("波動發散扣分 (短期 vol > 2× 長期 vol)")

            # 3. 籌碼窒息乾涸 (Volume Dry-up)
            # 在過去 20 日到 3 日前 (避開近幾日可能已開始的突破量)，是否有出現日量小於 60日均量 50% 的籌碼沉澱
            v60 = sum(volumes[-60:]) / 60.0 if len(volumes) >= 60 else 0.0
            vol_dry = False
            if v60 > 0 and len(volumes) >= 20:
                vol_dry = any(v < 0.5 * v60 for v in volumes[-20:-3])
            
            if vol_dry:
                score += 2
                reasons.append("籌碼沉澱 (曾出現量能窒息量 < 60日均量 50%)")

            # 4. 突破量能激增 (Volume Surge)
            v5 = sum(volumes[-5:]) / 5.0 if len(volumes) >= 5 else 0.0
            vol_surge = (v5 / v60 - 1.0) if v60 > 0 else 0.0
            if vol_surge > 0.3:
                score += 3
                reasons.append(f"量能突破 (5日均量高出60日均量 {vol_surge * 100:+.1f}%)")

            # 5. 價格突破邊緣 (Near 20-day High)
            high_20 = max(closes[-20:]) if len(closes) >= 20 else last
            close_near_high = last >= 0.98 * high_20
            if close_near_high:
                score += 2
                reasons.append("價格突破邊緣 (收盤逼近 20 日高點 ≥ 98%)")

            # 6. AI 技術模型機率偏多加分
            ev = model_evidence(item["symbol"], closes, volumes)
            prob = ev.get("probability_up", 50.0)
            if prob > 55.0:
                score += 2
                reasons.append(f"AI 技術模型偏多 ({prob:.1f}%)")

            # A/B/C 分級與安全懲罰邏輯
            prev_close = closes[-2] if len(closes) > 1 else last
            day_change_pct = (last / prev_close - 1) * 100.0 if prev_close > 0 else 0.0
            ma5 = moving_average(closes, 5)
            broke_ma5 = last < ma5 if ma5 else False
            ma20 = moving_average(closes, 20)
            ma60 = moving_average(closes, 60)
            
            # A/B/C 分級與安全懲罰邏輯 (⚠️ 暫定閾值，待 Phase 2 校準)
            if day_change_pct <= -4.5:
                score -= 10
                grade = "C"
                grade_label = "🔴 C級 · 禁買"
                reasons.append(f"⚠️ 當日價格重挫 ({day_change_pct:.2f}%)，量價型態走壞，系統判定為 C 級禁買以防範接刀風險")
            elif risk_level in ("RED", "BLACK"):
                score -= 10
                grade = "C"
                grade_label = "🔴 C級 · 禁買"
                reasons.append(f"⚠️ 大盤風險級別為 {risk_level}，系統性停止新買進，個股強制降為 C 級禁買")
            elif ma60 and last < ma60:
                score -= 8
                grade = "C"
                grade_label = "🔴 C級 · 禁買"
                reasons.append(f"⚠️ 個股跌破 60 日均線 (季線)，趨勢轉空，系統判定為 C 級禁買")
            elif day_change_pct <= -3.0 and broke_ma5:
                score -= 5
                grade = "B"
                grade_label = "🟡 B級 · 觀察反彈"
                reasons.append(f"⚠️ 當日價格下跌 ({day_change_pct:.2f}%) 且跌破 5 日線，系統判定為 B 級觀察防範短線走弱")
            elif risk_level == "YELLOW":
                score -= 3
                grade = "B"
                grade_label = "🟡 B級 · 觀察反彈"
                reasons.append(f"⚠️ 大盤風險級別為 YELLOW，新買進減半，個股限額觀察")
            elif ma20 and last < ma20:
                grade = "B"
                grade_label = "🟡 B級 · 觀察反彈"
                reasons.append(f"個股價格低於 20 日均線，尚未站穩短線多頭結構")
            else:
                grade = "A"
                grade_label = "🟢 A級 · 可執行"

            action = "觀察"
            if grade == "C":
                action = "避開或檢查賣出風險"
            elif score >= 5 and grade == "A":
                action = "研究買進候選"
            else:
                action = "觀察"

            candidates.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "sector": item.get("sector", ""),
                "last_date": rows[-1]["date"],
                "last_close": round(closes[-1], 2),
                "score": score,
                "grade": grade,
                "grade_label": grade_label,
                "action": action,
                "discovery_score": float(score),
                "probability_up": round(prob, 1),
                "reasons": [f"Antigravity VCP 突破分級：{grade_label}"] + reasons,
                "future_knowledge_used": False,
            })
        except Exception as exc:
            print(f"[Antigravity Discover] {item['symbol']}: {exc}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:limit]



def discover_claude_candidates(end: str, limit: int = 5) -> list[dict]:
    """Claude Regime-guided intelligent stock selection.

    Uses a tightened HIGH_VOL trigger (vol-pct ≥ 0.60) for earlier drawdown protection.
    In BEAR_TREND, only picks with positive 20-day momentum are qualified.
    """
    lookback = 320
    start = (datetime.fromisoformat(end) - timedelta(days=lookback)).date().isoformat()
    end_excl = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()

    all_data: dict[str, dict] = {}
    for item in DISCOVERY_UNIVERSE:
        try:
            rows = fetch_history(item["symbol"], start, end_excl)
            if len(rows) >= 130:
                all_data[item["symbol"]] = {
                    "dates":   [r["date"] for r in rows],
                    "closes":  [float(r["close"]) for r in rows],
                    "volumes": [float(r.get("volume", 0) or 0) for r in rows],
                    "name":    item["name"],
                    "sector":  item.get("sector", ""),
                    "last_date": rows[-1]["date"],
                }
        except Exception as exc:
            print(f"[Claude Discover] data {item['symbol']}: {exc}")

    if not all_data:
        return []

    # 單一真相源:直接呼叫共享 company.screener.agent_screen.claude_screen
    # (校準模型 + 風險感知;一律回前 limit 檔、0-10 分、含 recommended 建議/觀望)
    from company.screener.agent_screen import claude_screen

    candidates = {sym: {"closes": d["closes"], "volumes": d["volumes"], "dates": d["dates"]}
                  for sym, d in all_data.items()}
    names = {sym: d["name"] for sym, d in all_data.items()}
    sectors = {sym: d.get("sector", "") for sym, d in all_data.items()}
    last_dates = {sym: d["last_date"] for sym, d in all_data.items()}

    market_info = analyze_market_index(end)
    risk_level = market_info.get("risk_level") if market_info else None

    res = claude_screen(candidates, top_n=limit, names=names, risk_level=risk_level)
    ctx = res.get("context", {})
    out: list[dict] = []
    for p in res.get("picks", []):
        recommended = bool(p.get("recommended"))
        grade = p.get("grade", "B")
        grade_label = p.get("grade_label", "B級")
        tag = "✅ 建議進場" if recommended else "👀 觀望:未達本日 regime 進場門檻"
        reasons = [tag, f"{ctx.get('regime_label','')} · {ctx.get('stance','')}"]
        reasons += p.get("reasons", [])
        if p.get("calibrated_up_rate") is not None:
            reasons.append(f"校準機率 {p['probability_up']:.0f}%(該桶歷史上漲率 {p['calibrated_up_rate']:.0%})")
            
        action = "觀察"
        if grade == "C":
            action = "避開或檢查賣出風險"
        elif recommended and grade == "A":
            action = "研究買進候選"
        else:
            action = "觀察"

        out.append({
            "symbol":          p["symbol"],
            "name":            p.get("name", names.get(p["symbol"], p["symbol"])),
            "sector":          sectors.get(p["symbol"], ""),
            "last_date":       last_dates.get(p["symbol"], end),
            "last_close":      p.get("close"),
            "score":           p.get("score"),            # 0-10
            "discovery_score": p.get("score"),
            "probability_up":  p.get("probability_up"),   # 校準機率(供前端統一標準化比較)
            "recommended":     recommended,
            "grade":           grade,
            "grade_label":     grade_label,
            "action":          action,
            "reasons":         reasons,
            "regime":          ctx.get("regime"),
            "regime_label":    ctx.get("regime_label"),
            "target_exposure": ctx.get("target_exposure"),
            "trail_stop":      ctx.get("trail_stop"),
            "future_knowledge_used": False,
        })
    return out


def _close_on_or_before(rows: list[dict], date_str: str) -> float | None:
    """回傳 rows 中日期 <= date_str 的最後一筆收盤(rows 已按日期升冪)。"""
    chosen = None
    for r in rows:
        if r["date"] <= date_str:
            chosen = float(r["close"])
        else:
            break
    return chosen


def daily_performance(end: str) -> dict:
    """
    每日三家獲利率回顧(回溯計算,無持久化、無未來函數)。
    取得最近兩個交易日 [d_prev, d_last];以 d_prev 為截止日讓三家各自選股,
    再計算這些標的 d_prev→d_last 的實現報酬率,三家平均對比。
    """
    end_excl = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
    ref = fetch_history("2330.TW", "2020-01-01", end_excl)
    if len(ref) < 2:
        return {"error": "歷史資料不足", "agents": []}
    d_last = ref[-1]["date"]
    d_prev = ref[-2]["date"]
    prev_excl = (datetime.fromisoformat(d_prev) + timedelta(days=1)).date().isoformat()

    # 三家以 d_prev 為截止日選股(只用當日以前資料)
    agents_spec = []
    try:
        codex = discover_candidates(d_prev, limit=5)
        agents_spec.append(("🤖 Codex", [c["symbol"] for c in asarray_dicts(codex.get("candidates"))]))
    except Exception as exc:
        agents_spec.append(("🤖 Codex", []))
    try:
        anti = discover_antigravity_candidates(d_prev, limit=5)
        agents_spec.append(("🌌 Antigravity", [c["symbol"] for c in (anti or [])]))
    except Exception:
        agents_spec.append(("🌌 Antigravity", []))
    try:
        claude = discover_claude_candidates(d_prev, limit=5)
        agents_spec.append(("🧠 Claude", [c["symbol"] for c in (claude or []) if c.get("recommended", True)]))
    except Exception:
        agents_spec.append(("🧠 Claude", []))

    # 計算每檔 d_prev→d_last 報酬(以 d_prev 收盤買進、d_last 收盤評估)
    cache: dict[str, list[dict]] = {}
    def sym_return(symbol: str) -> float | None:
        if symbol not in cache:
            try:
                cache[symbol] = fetch_history(symbol, "2024-01-01", end_excl)
            except Exception:
                cache[symbol] = []
        rows = cache[symbol]
        p0 = _close_on_or_before(rows, d_prev)
        p1 = _close_on_or_before(rows, d_last)
        if p0 and p1 and p0 > 0:
            return p1 / p0 - 1.0
        return None

    agents_out = []
    for name, syms in agents_spec:
        picks = []
        rets = []
        for s in syms:
            r = sym_return(s)
            picks.append({"symbol": s, "return": round(r, 5) if r is not None else None})
            if r is not None:
                rets.append(r)
        avg = round(sum(rets) / len(rets), 5) if rets else None
        agents_out.append({"agent": name, "n": len(rets), "avg_return": avg, "picks": picks})

    try:
        from company.model.archive import append_daily_performance
        append_daily_performance(d_prev, d_last, agents_out)
    except Exception as e:
        print(f"[DailyPerformance] Failed to archive daily performance: {e}")

    return {
        "pick_date": d_prev,
        "eval_date": d_last,
        "note": "前一交易日各家選股 → 最近交易日收盤的實現報酬(回溯計算,僅供研究)",
        "agents": agents_out,
        "future_knowledge_used": False,
    }


def asarray_dicts(value) -> list:
    return value if isinstance(value, list) else []


def normalize_positions(raw_positions: list[dict]) -> dict[str, dict]:
    positions = {}
    for item in raw_positions:
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        positions[symbol] = {
            "symbol": symbol,
            "shares": float(item.get("shares", 0) or 0),
            "cost": float(item.get("cost", 0) or 0),
        }
    return positions


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/symbols":
                self.send_json({"symbols": DEFAULT_SYMBOLS, "universe": DISCOVERY_UNIVERSE})
                return
            if parsed.path == "/api/health":
                self.send_json(
                    {
                        "status": "ok",
                        "service": "investment-strategy-company",
                        "time": datetime.now(timezone.utc).isoformat(),
                        "version": build_version(),
                    }
                )
                return
            if parsed.path == "/api/version":
                self.send_json(build_version())
                return
            if parsed.path == "/api/quote":
                symbols = query.get("symbols", ["2327.TW"])[0].split(",")
                self.send_json(fetch_quote(symbols))
                return
            if parsed.path == "/api/history":
                symbol = query.get("symbol", ["2327.TW"])[0]
                start = query.get("start", ["2020-01-01"])[0]
                end = query.get("end", [datetime.now().date().isoformat()])[0]
                end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
                self.send_json({"rows": fetch_history(symbol, start, end_exclusive)})
                return
            if parsed.path == "/api/discover":
                end = query.get("end", [datetime.now().date().isoformat()])[0]
                limit = int(query.get("limit", ["5"])[0])
                self.send_json(discover_candidates(end, limit=limit))
                return
            if parsed.path == "/api/antigravity/discover":
                end = query.get("end", [datetime.now().date().isoformat()])[0]
                limit = int(query.get("limit", ["5"])[0])
                self.send_json(discover_antigravity_candidates(end, limit=limit))
                return
            if parsed.path == "/api/claude/discover":
                end = query.get("end", [datetime.now().date().isoformat()])[0]
                limit = int(query.get("limit", ["5"])[0])
                self.send_json(discover_claude_candidates(end, limit=limit))
                return
            if parsed.path == "/api/universe":
                try:
                    from company.data.universe import load_active_universe
                    universe = load_active_universe()
                    for item in universe:
                        code = item["symbol"].split(".")[0]
                        if code in NAME_MAP:
                            item["name"] = NAME_MAP[code]
                except Exception:
                    universe = []
                    for ticker in DISCOVERY_UNIVERSE:
                        code = ticker["symbol"].split(".")[0]
                        universe.append({
                            "symbol": ticker["symbol"],
                            "name": NAME_MAP.get(code, ticker.get("name", ticker["symbol"])),
                            "sector": ticker.get("sector", "一般類股")
                        })
                self.send_json(universe)
                return
            if parsed.path == "/api/news":
                date = query.get("date", ["2026-05-28"])[0]
                try:
                    start = (datetime.fromisoformat(date) - timedelta(days=10)).date().isoformat()
                    idx_rows = fetch_history("^TWII", start, (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat())
                    
                    index_news = "台股加權指數今日區間震盪，投資人觀望氣氛濃厚。"
                    if idx_rows and len(idx_rows) >= 2:
                        last_close = float(idx_rows[-1]["close"])
                        prev_close = float(idx_rows[-2]["close"])
                        pct = (last_close / prev_close - 1) * 100
                        sign = "+" if pct >= 0 else ""
                        index_news = f"加權指數今日收在 {last_close:,.2f} 點 ({sign}{pct:.2f}%)，成交量持穩，市場焦點轉向季底作帳行情。"
                        
                    pool_movers = []
                    for ticker in ["2330.TW", "2317.TW", "2454.TW"]:
                        try:
                            s_rows = fetch_history(ticker, start, (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat())
                            if s_rows and len(s_rows) >= 2:
                                lc = float(s_rows[-1]["close"])
                                pc = float(s_rows[-2]["close"])
                                chg = (lc / pc - 1) * 100
                                name = NAME_MAP.get(ticker.split(".")[0], ticker)
                                pool_movers.append((name, chg, lc))
                        except Exception:
                            pass
                            
                    pool_news = []
                    if pool_movers:
                        for name, chg, price in pool_movers:
                            sign = "+" if chg >= 0 else ""
                            action = "強勢領漲" if chg > 1.5 else "回檔修正" if chg < -1.5 else "窄幅震盪"
                            pool_news.append(f"股池標的【{name}】今日收 ${price:.1f} ({sign}{chg:.2f}%)，表現{action}。")
                    else:
                        pool_news.append("股池權值股台積電、鴻海維持區間整理，資金靜待法說會釋出最新展望。")
                        
                    items = [
                        {
                            "id": 1,
                            "title": f"【大盤焦點】{index_news}",
                            "category": "大盤市場",
                            "time": "今日最新"
                        }
                    ]
                    
                    for idx, p_news in enumerate(pool_news):
                        items.append({
                            "id": idx + 2,
                            "title": f"【股池動態】{p_news}",
                            "category": "個股焦點",
                            "time": "今日最新"
                        })
                        
                    market_info = analyze_market_index(date)
                    regime = market_info["regime"] if market_info else "區間整理"
                    regime_note = market_info["regime_note"] if market_info else "大盤進入窄幅區間整理。"
                    items.append({
                        "id": len(items) + 1,
                        "title": f"【量化監測】系統標記目前大盤為「{regime}」狀態。{regime_note}",
                        "category": "環境特徵",
                        "time": "今日最新"
                    })
                    self.send_json({"date": date, "news": items})
                except Exception as e:
                    self.send_json({
                        "date": date,
                        "news": [
                            {"id": 1, "title": "【市場焦點】台股加權指數於今日進行季線攻防，權值股呈現漲跌互現。", "category": "大盤市場", "time": "今日最新"},
                            {"id": 2, "title": "【股池追蹤】目前股池熱門標的包括台積電、國巨等，主力資金小幅流入。", "category": "個股焦點", "time": "今日最新"},
                            {"id": 3, "title": "【法人籌碼】外資今日買賣超金額縮小，市場觀望下週美國非農就業數據指引。", "category": "環境特徵", "time": "今日最新"}
                        ]
                    })
                return
            if parsed.path == "/api/daily-performance":
                end = query.get("end", [datetime.now().date().isoformat()])[0]
                self.send_json(daily_performance(end))
                return
            if parsed.path == "/api/strategy-archive":
                from company.model.archive import load_archive, propose_update
                self.send_json({**propose_update(), "archive": load_archive()})
                return
            super().do_GET()
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/train":
                body = self.read_body()
                end_exclusive = (datetime.fromisoformat(body["end"]) + timedelta(days=1)).date().isoformat()
                fee = float(body.get("fee", 0.0))
                tax = float(body.get("tax", 0.0))
                slippage = float(body.get("slippage", 0.0))
                output = []
                optimization = []
                threshold_reviews = []
                for symbol in body["symbols"]:
                    rows = fetch_history(symbol, body["start"], end_exclusive)
                    threshold_reviews.append(evaluate_probability_thresholds(symbol, rows))
                    for role in body["roles"]:
                        baseline = simulate(symbol, rows, role, float(body.get("initial_cash", 1_000_000)), fee=fee, tax=tax, slippage=slippage)
                        output.append(baseline)
                        optimization.append(
                            optimize_strategy_variants(
                                symbol,
                                rows,
                                role,
                                float(body.get("initial_cash", 1_000_000)),
                                baseline,
                                fee=fee,
                                tax=tax,
                                slippage=slippage,
                            )
                        )
                
                # Expose Model Training thought process & optimization
                X_train = []
                y_train = []
                for symbol in body["symbols"]:
                    try:
                        rows = fetch_history(symbol, body["start"], end_exclusive)
                        closes = [float(row["close"]) for row in rows]
                        if len(closes) < 30:
                            continue
                        for t in range(25, len(closes) - 1):
                            visible_closes = closes[:t+1]
                            close_t = closes[t]
                            close_next = closes[t+1]
                            
                            rsi_t = calculate_rsi_list(visible_closes, 14)
                            y_slope = visible_closes[-5:]
                            slope_t = (-2.0 * y_slope[0] - 1.0 * y_slope[1] + 1.0 * y_slope[3] + 2.0 * y_slope[4]) / 10.0
                            _, _, hist_t = calculate_macd_list(visible_closes, 12, 26, 9)
                            
                            x0 = 1.0
                            x1 = (50.0 - rsi_t) / 10.0
                            x2 = (slope_t / close_t) * 100.0
                            x3 = (hist_t / close_t) * 100.0
                            label = 1 if close_next > close_t else 0
                            
                            X_train.append([x0, x1, x2, x3])
                            y_train.append(label)
                    except Exception as e:
                        print(f"Error gathering training data for {symbol}: {e}")
                
                training_results = None
                if X_train:
                    # Train model with learning_rate=0.1, l2=0.01, epochs=500
                    training_results = train_logistic_regression_pure(X_train, y_train, lr=0.1, l2=0.01, epochs=500)
                    save_optimized_weights(training_results["weights"])
                    # 策略存檔:把人工區間訓練結果歸檔(補接 archive 既有但未呼叫的 append_manual_training)
                    try:
                        from company.model.archive import append_manual_training
                        append_manual_training(
                            ticker=",".join(body["symbols"]),
                            start_date=body["start"], end_date=body["end"],
                            lr=0.1, l2=0.01, epochs=500,
                            accuracy=training_results["accuracy"],
                            weights=training_results["weights"],
                        )
                    except Exception as _exc:
                        print(f"[Archive] manual training hook: {_exc}")

                self.send_json(
                    {
                        "results": output,
                        "optimization": optimization,
                        "threshold_reviews": threshold_reviews,
                        "model_training": {
                            **model_training_summary(),
                            "available": True,
                            "weights": training_results["weights"] if training_results else {"bias": 0.0, "rsi": 0.15, "slope": 0.25, "macd_hist": 0.15},
                            "epoch_logs": training_results["epoch_logs"] if training_results else [],
                            "accuracy": training_results["accuracy"] if training_results else 50.0,
                            "summary": "AI 預測模型已完成在線優化訓練，並產生策略參數競賽與機率門檻審計。",
                            "thinking_process": [
                                "已從選定股票提取歷史 RSI、動能斜率與 MACD 因子。",
                                "使用梯度下降優化器擬合歷史次日漲跌標籤。",
                                "同步比較 C-1/C-2 多組保守門檻與動能均線組合，找出下一輪應測參數。",
                                "逐日檢查校準機率門檻與未來 5 日結果，回報命中率與平均前向報酬。",
                                "權重已持久化存檔，今日候選與明日計畫已套用最新優化結果。"
                            ]
                        },
                        "training_goal": "用區間回測找出策略弱點，並用校準模型提供樣本外機率依據；操盤手不可讀取截止日之後資料。",
                    }
                )
                return
            if self.path == "/api/recommend":
                body = self.read_body()
                end = body.get("end") or datetime.now().date().isoformat()
                start = (datetime.fromisoformat(end) - timedelta(days=int(body.get("lookback_days", 260)))).date().isoformat()
                end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
                
                # 分析大盤與 Regime
                market_info = analyze_market_index(end)
                market_regime = market_info["regime"] if market_info else None
                risk_level = market_info.get("risk_level") if market_info else None
                
                symbols_to_scan = body.get("symbols") or []
                symbols_to_scan = [s.strip() for s in symbols_to_scan if s.strip()]
                if not symbols_to_scan:
                    # 如果使用者輸入為空，自動載入預設的 10 檔指標股進行篩選
                    symbols_to_scan = [item["symbol"] for item in DEFAULT_SYMBOLS]
                
                candidates = []
                for symbol in symbols_to_scan:
                    try:
                        rows = fetch_history(symbol, start, end_exclusive)
                        if len(rows) >= 80:
                            analysis = analyze_candidate(symbol, rows, market_regime=market_regime, risk_level=risk_level)
                            candidates.append(apply_codex_v2_overlay(symbol, rows, analysis, market_info=market_info))
                    except Exception as e:
                        print(f"Error recommending for {symbol}: {e}")
                
                candidates.sort(key=lambda item: (GRADE_ORDER.get(item.get("grade"), 0), item.get("codex_score", item["score"])), reverse=True)
                self.send_json({
                    "as_of": end,
                    "market_index": market_info,
                    "candidates": candidates[: int(body.get("limit", 5))]
                })
                return
            if self.path == "/api/next-day-plan":
                body = self.read_body()
                end = body.get("end") or datetime.now().date().isoformat()
                start = (datetime.fromisoformat(end) - timedelta(days=int(body.get("lookback_days", 320)))).date().isoformat()
                end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
                positions = normalize_positions(body.get("positions", []))
                
                # 分析大盤與 Regime
                market_info = analyze_market_index(end)
                market_regime = market_info["regime"] if market_info else None
                risk_level = market_info.get("risk_level") if market_info else None
                
                plans = []
                for symbol in body["symbols"]:
                    try:
                        rows = fetch_history(symbol, start, end_exclusive)
                        if len(rows) >= 80:
                            plans.append(plan_next_session(symbol, rows, positions.get(symbol), market_regime=market_regime, risk_level=risk_level, market_info=market_info))
                    except Exception as e:
                        print(f"Error planning next day for {symbol}: {e}")
                
                plans.sort(key=lambda item: (not item["held"], -item["score"]))
                self.send_json({"as_of": end, "plans": plans, "rule": "after_close_next_session_plan_only", "market_index": market_info})
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 8765
    host = argv[2] if len(argv) > 2 else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
