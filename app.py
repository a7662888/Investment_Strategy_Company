from __future__ import annotations

import csv
import json
import math
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


def model_evidence(symbol: str, closes: list[float], volumes: list[float] | None = None) -> dict:
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

        calibrated = score_series(closes, volumes)
        if calibrated:
            evidence["calibrated_model"] = calibrated["name"]
            evidence["calibrated_probability_up"] = calibrated["probability_up"]
            evidence["calibrated"] = calibrated["calibrated"]
            evidence["calibrated_reasons"] = calibrated["reasons"]
            evidence["calibrated_evidence"] = calibrated["evidence"]
            evidence["horizon_days"] = calibrated.get("horizon_days")
            evidence["note"] = calibrated["note"]
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


def simulate_strategy_variant(rows: list[dict], role: str, initial_cash: float, params: dict) -> dict:
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
            shares = int((cash * allocation) // open_price)
            cash -= shares * open_price
            trades += 1
        elif pending == "sell" and shares > 0:
            cash += shares * open_price
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


def optimize_strategy_variants(symbol: str, rows: list[dict], role: str, initial_cash: float, baseline: dict) -> dict:
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

    evaluated = [simulate_strategy_variant(rows, role, initial_cash, params) for params in variants]
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


def simulate(symbol: str, rows: list[dict], role: str, initial_cash: float) -> dict:
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
            shares = int((cash * allocation) // open_price)
            cash -= shares * open_price
            trades += 1
        elif pending == "sell" and shares > 0:
            cash += shares * open_price
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


def analyze_candidate(symbol: str, rows: list[dict]) -> dict:
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row.get("volume", 0) or 0) for row in rows]
    last = closes[-1]
    model = model_evidence(symbol, closes, volumes)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
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
    if ma20 and ma60 and last > ma20 > ma60:
        score += 3
        reasons.append("價格站上 20 日與 60 日均線，短中期趨勢偏強")
    if ma60 and ma120 and ma60 > ma120:
        score += 2
        reasons.append("60 日均線高於 120 日均線，中期結構偏多")
    if momentum_20 > 0.08:
        score += 2
        reasons.append("20 日動能明顯轉強")
    elif momentum_20 < -0.08:
        score -= 2
        reasons.append("20 日動能偏弱，需避免追高或接刀")
    if volatility > 0.045:
        score -= 1
        reasons.append("近期波動偏高，需降低部位或等待確認")

    # Add RSI and MACD factors to score & reasons
    if rsi < 35:
        score += 1
        reasons.append(f"RSI(14) 降至 {rsi:.1f}，顯示超賣且價格進入價值安全區")
    elif rsi > 70:
        score -= 1
        reasons.append(f"RSI(14) 達 {rsi:.1f}，進入超買區，需防範拉回修正")
    else:
        reasons.append(f"RSI(14) 數值為 {rsi:.1f}，處於常態整理區間")

    if hist_val > 0:
        score += 1
        reasons.append(f"MACD 柱狀體攀升至 {hist_val:.2f}，短線動能轉強")
    else:
        score -= 1
        reasons.append(f"MACD 柱狀體位於負值區 ({hist_val:.2f})，空頭慣性存在")

    if not reasons:
        reasons.append("訊號不明確，暫列觀察")

    reasons.append(
        f"AI 因子模型估計隔日偏多機率 {model['probability_up']:.1f}%；"
        f"趨勢 {model['trend_points']}、動能 {model['momentum_points']}、風險 {model['risk_points']}"
    )

    action = "觀察"
    if score >= 5:
        action = "研究買進候選"
    elif score <= -2:
        action = "避開或檢查賣出風險"

    return {
        "symbol": symbol,
        "last_date": rows[-1]["date"],
        "last_close": round(last, 2),
        "score": score,
        "action": action,
        "reasons": reasons,
        "model": model,
        "ai_predictor": generate_ai_prediction(closes, rsi, hist_val),
        "future_knowledge_used": False,
    }


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


def plan_next_session(symbol: str, rows: list[dict], position: dict | None) -> dict:
    analysis = analyze_candidate(symbol, rows)
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
        if gain >= 0.25 and ma20 and last < ma20:
            action = "明日減碼獲利"
            reasons.insert(0, f"持股獲利約 {gain * 100:.2f}%，且價格跌破 20 日均線，優先保護獲利")
        elif gain >= 0.35:
            action = "明日部分獲利了結"
            reasons.insert(0, f"持股獲利約 {gain * 100:.2f}%，即使趨勢仍強，也應考慮分批落袋")
        elif ma60 and last < ma60 and gain > 0:
            action = "明日檢查賣出風險"
            reasons.insert(0, "仍有獲利但價格跌破 60 日均線，需避免獲利回吐")
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
        "reasons": reasons,
        "model": analysis["model"],
        "ai_predictor": generate_ai_prediction(closes, rsi, hist_val),
        "rule": "收盤後產生明日計畫，不做當沖；買賣僅作研究與模擬用途。",
        "future_knowledge_used": False,
    }


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
                self.send_json({"symbols": DEFAULT_SYMBOLS})
                return
            if parsed.path == "/api/health":
                self.send_json(
                    {
                        "status": "ok",
                        "service": "investment-strategy-company",
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                )
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
            super().do_GET()
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/train":
                body = self.read_body()
                end_exclusive = (datetime.fromisoformat(body["end"]) + timedelta(days=1)).date().isoformat()
                output = []
                for symbol in body["symbols"]:
                    rows = fetch_history(symbol, body["start"], end_exclusive)
                    for role in body["roles"]:
                        output.append(simulate(symbol, rows, role, float(body.get("initial_cash", 1_000_000))))
                
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
                
                self.send_json(
                    {
                        "results": output,
                        "model_training": {
                            "available": True,
                            "weights": training_results["weights"] if training_results else {"bias": 0.0, "rsi": 0.15, "slope": 0.25, "macd_hist": 0.15},
                            "epoch_logs": training_results["epoch_logs"] if training_results else [],
                            "accuracy": training_results["accuracy"] if training_results else 50.0,
                            "summary": "AI 預測模型已完成在線優化訓練！",
                            "thinking_process": [
                                "已從選定股票提取歷史 RSI、動能斜率與 MACD 因子。",
                                "使用梯度下降優化器擬合歷史次日漲跌標籤。",
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
                candidates = []
                for symbol in body["symbols"]:
                    rows = fetch_history(symbol, start, end_exclusive)
                    if len(rows) >= 80:
                        candidates.append(analyze_candidate(symbol, rows))
                candidates.sort(key=lambda item: item["score"], reverse=True)
                self.send_json({"as_of": end, "candidates": candidates[: int(body.get("limit", 5))]})
                return
            if self.path == "/api/next-day-plan":
                body = self.read_body()
                end = body.get("end") or datetime.now().date().isoformat()
                start = (datetime.fromisoformat(end) - timedelta(days=int(body.get("lookback_days", 320)))).date().isoformat()
                end_exclusive = (datetime.fromisoformat(end) + timedelta(days=1)).date().isoformat()
                positions = normalize_positions(body.get("positions", []))
                plans = []
                for symbol in body["symbols"]:
                    rows = fetch_history(symbol, start, end_exclusive)
                    if len(rows) >= 80:
                        plans.append(plan_next_session(symbol, rows, positions.get(symbol)))
                plans.sort(key=lambda item: (not item["held"], -item["score"]))
                self.send_json({"as_of": end, "plans": plans, "rule": "after_close_next_session_plan_only"})
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
