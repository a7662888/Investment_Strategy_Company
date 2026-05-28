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
        return rows
    if not quotes:
        return rows
    quote = quotes[0]
    quote_date = iso_from_tw_date(quote.get("marketDate"))
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
        "quotePolicy": "TWSE/TPEx MIS first; Yahoo only as labeled fallback.",
    }


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def model_evidence(symbol: str, closes: list[float]) -> dict:
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

    return {
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
    model_basis = (
        "C-1 保守價值流：只看當日以前收盤，價格低於 MA60 7% 才分批買，反彈高於 MA60 5% 才賣。"
        if role == "C-1"
        else "C-2 激進動能流：只看當日以前收盤，MA15 上穿 MA45 才買，MA15 跌回 MA45 才賣。"
    )
    return {
        "symbol": symbol,
        "role": role,
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "final_equity": round(final_equity, 2),
        "total_return": round(final_equity / initial_cash - 1, 6),
        "max_drawdown": round(max_drawdown(equity_curve), 6) if equity_curve else 0,
        "trade_count": trades,
        "model_basis": model_basis,
        "training_note": "訊號在 T 日收盤後形成，下一個交易日開盤才執行；不讀取預設日期之後資料。",
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
    last = closes[-1]
    model = model_evidence(symbol, closes)
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
    
    # 1. Short-term OLS slope (last 5 days)
    y = closes[-5:]
    slope = (-2.0 * y[0] - 1.0 * y[1] + 1.0 * y[3] + 2.0 * y[4]) / 10.0

    # 2. Volatility (last 20 days returns)
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

    # 3. Dynamic Probability Calculation
    rsi_contrib = (50.0 - rsi) * 0.3
    
    slope_pct = slope / last_price
    slope_contrib = max(-20.0, min(20.0, slope_pct * 500.0))
    
    macd_contrib = max(-15.0, min(15.0, macd_hist * 2.0))
    
    prob_uptrend = 50.0 + rsi_contrib + slope_contrib + macd_contrib
    prob_uptrend = max(15.0, min(92.0, prob_uptrend))

    if prob_uptrend > 55.0:
        prediction = "Uptrend (看漲)"
        prob = float(prob_uptrend)
        rationale = (
            f"模型顯示明日上漲機率達 {prob:.0f}%。主因 14 日 RSI 目前為 {rsi:.1f}，估值處於偏低或整理安全區，"
            f"且近 5 日股價斜率為 {slope:.2f}。雖然 MACD 柱狀體為 {macd_hist:.2f}，"
            f"但近期震盪收斂，波動度約 {(volatility*100):.1f}%。模型預測明日價格主要運行區間落於 ${pred_low:.1f} 至 ${pred_high:.1f}，"
            f"建議持股或分批左側承接。"
        )
    elif prob_uptrend < 45.0:
        prediction = "Downtrend (看跌)"
        prob = float(100.0 - prob_uptrend)
        rationale = (
            f"模型預期明日有 {prob:.0f}% 機率延續修正趨勢。主要由於 RSI 達 {rsi:.1f} 且短期 5 日斜率為 {slope:.2f} "
            f"呈現下行慣性，且 MACD 柱狀體為 {macd_hist:.2f} 處於負值區。波動度 {(volatility*100):.1f}% 顯示賣壓未消退，"
            f"預測明日運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}。風控建議保留現金，暫避風險。"
        )
    else:
        prediction = "Rangebound (盤整)"
        prob = 50.0
        rationale = (
            f"模型預估明日將呈區間盤整（機率 50%）。短期斜率極微 ({slope:.2f})，RSI 數值為 {rsi:.1f} 處於常態中性區，"
            f"多空拉鋸。預估運行區間為 ${pred_low:.1f} 至 ${pred_high:.1f}，建議空手者觀望，持股者續抱等待明確動能訊號。"
        )

    abs_rsi = abs(rsi_contrib)
    abs_slope = abs(slope_contrib)
    abs_macd = abs(macd_contrib)
    total_abs = abs_rsi + abs_slope + abs_macd

    if total_abs > 0:
        rsi_w = int(round(abs_rsi / total_abs * 100))
        slope_w = int(round(abs_slope / total_abs * 100))
        macd_w = int(round(abs_macd / total_abs * 100))
    else:
        rsi_w, slope_w, macd_w = 30, 40, 30

    total_w = rsi_w + slope_w + macd_w
    if total_w > 0:
        rsi_w = int(round(rsi_w / total_w * 100))
        slope_w = int(round(slope_w / total_w * 100))
        macd_w = 100 - rsi_w - slope_w

    features = [
        {"name": "RSI 超買超賣權重", "weight": rsi_w},
        {"name": "5日 OLS 短期動能", "weight": slope_w},
        {"name": "MACD 柱狀體排列", "weight": macd_w}
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
                self.send_json({"results": output})
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
