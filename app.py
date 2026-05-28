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
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_quote(symbols: list[str]) -> dict:
    try:
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?{query}"
        return yahoo_json(url)
    except Exception:
        results = []
        end = datetime.now().date()
        start = (end - timedelta(days=10)).isoformat()
        end_exclusive = (end + timedelta(days=1)).isoformat()
        for symbol in symbols:
            rows = fetch_history(symbol, start, end_exclusive)
            if not rows:
                continue
            latest = rows[-1]
            previous = rows[-2] if len(rows) > 1 else latest
            price = float(latest["close"])
            prev_close = float(previous["close"])
            change_pct = 0.0 if prev_close == 0 else (price / prev_close - 1) * 100
            results.append(
                {
                    "symbol": symbol,
                    "shortName": symbol,
                    "regularMarketPrice": price,
                    "regularMarketChangePercent": change_pct,
                    "regularMarketTime": int(datetime.fromisoformat(latest["date"]).replace(tzinfo=timezone.utc).timestamp()),
                }
            )
        return {"quoteResponse": {"result": results}}


def fetch_history(symbol: str, start_date: str, end_date: str) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol.replace('.', '_')}_{start_date}_{end_date}.csv"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        with cache_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

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
    return rows


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
    return {
        "symbol": symbol,
        "role": role,
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "final_equity": round(final_equity, 2),
        "total_return": round(final_equity / initial_cash - 1, 6),
        "max_drawdown": round(max_drawdown(equity_curve), 6) if equity_curve else 0,
        "trade_count": trades,
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
        "future_knowledge_used": False,
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
