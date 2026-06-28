# -*- coding: utf-8 -*-
"""
Freeze ML-model probability signals as `ml-quant` agent for champion/challenger comparison.

Reads the full universe, runs the XGBoost model (score_series), maps probability_up
to action, and freezes into the Decision Ledger as agent_id=ml-quant.

Usage:
    python freeze_ml_quant.py                     # freeze all, cutoff=today
    python freeze_ml_quant.py --dry-run            # preview only, no freeze
    python freeze_ml_quant.py --universe-only 2330.TW 2454.TW  # specific stocks
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta

# Load .env
_env_path = __import__("pathlib").Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            try:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()
            except ValueError:
                pass

from company.model.ledger import freeze_signals
from company.model.score import score_series


# ── Universe ────────────────────────────────────────────────────────────
# Match the value-engine universe for clean champion/challenger comparison.
ML_UNIVERSE = [
    # Value-engine stocks (24 stocks + ETF placeholder for 0050)
    {"symbol": "2330.TW", "name": "台積電", "sector": "半導體"},
    {"symbol": "2308.TW", "name": "台達電", "sector": "電源/AI"},
    {"symbol": "2454.TW", "name": "聯發科", "sector": "半導體"},
    {"symbol": "3045.TW", "name": "台灣大", "sector": "電信"},
    {"symbol": "2891.TW", "name": "中信金", "sector": "金融"},
    {"symbol": "1476.TW", "name": "儒鴻", "sector": "紡織"},
    {"symbol": "3034.TW", "name": "聯詠", "sector": "IC設計"},
    {"symbol": "2303.TW", "name": "聯電", "sector": "半導體"},
    {"symbol": "2882.TW", "name": "國泰金", "sector": "金融"},
    {"symbol": "2884.TW", "name": "玉山金", "sector": "金融"},
    {"symbol": "1301.TW", "name": "台塑", "sector": "塑化"},
    {"symbol": "2002.TW", "name": "中鋼", "sector": "原物料"},
    {"symbol": "1101.TW", "name": "台泥", "sector": "水泥"},
    {"symbol": "1216.TW", "name": "統一", "sector": "食品"},
    {"symbol": "2912.TW", "name": "統一超", "sector": "零售"},
    # Additional DISCOVERY_UNIVERSE stocks the ML model was trained on
    {"symbol": "2317.TW", "name": "鴻海", "sector": "電子代工"},
    {"symbol": "3711.TW", "name": "日月光", "sector": "封測"},
    {"symbol": "2379.TW", "name": "瑞昱", "sector": "IC設計"},
    {"symbol": "2382.TW", "name": "廣達", "sector": "AI伺服器"},
    {"symbol": "3231.TW", "name": "緯創", "sector": "AI伺服器"},
    {"symbol": "2356.TW", "name": "英業達", "sector": "AI伺服器"},
    {"symbol": "3017.TW", "name": "奇鋐", "sector": "散熱"},
    {"symbol": "3443.TW", "name": "創意", "sector": "ASIC"},
    {"symbol": "6669.TW", "name": "緯穎", "sector": "AI伺服器"},
    {"symbol": "2327.TW", "name": "國巨", "sector": "被動元件"},
    {"symbol": "8046.TW", "name": "南電", "sector": "ABF載板"},
    {"symbol": "1303.TW", "name": "南亞", "sector": "塑化"},
    {"symbol": "2603.TW", "name": "長榮", "sector": "航運"},
    {"symbol": "2609.TW", "name": "陽明", "sector": "航運"},
    {"symbol": "2615.TW", "name": "萬海", "sector": "航運"},
    {"symbol": "2412.TW", "name": "中華電", "sector": "電信"},
    {"symbol": "2881.TW", "name": "富邦金", "sector": "金融"},
    {"symbol": "2886.TW", "name": "兆豐金", "sector": "金融"},
]


def fetch_yahoo_history(symbol: str, days: int = 180) -> list[dict]:
    """Fetch price history via Yahoo Finance (simplified inline)."""
    import csv
    import urllib.parse
    import urllib.request
    from pathlib import Path

    end = datetime.now(timezone.utc)
    start = end.replace(year=end.year - 1) if days > 365 else end - timedelta(days=days)
    period1 = int(start.timestamp())
    period2 = int(end.timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?"
        f"period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())

    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    adjclose_raw = ((result.get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or []

    rows = []
    for i, ts in enumerate(timestamps):
        if quote["open"][i] is None or quote["close"][i] is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
            "close": float(quote["close"][i]),
            "adj_close": float(adjclose_raw[i]) if i < len(adjclose_raw) and adjclose_raw[i] else None,
            "volume": int(quote["volume"][i] or 0),
        })
    return rows


def prob_to_action(prob_up: float, reasons: list[str]) -> str:
    """Map model probability to a discrete action."""
    if prob_up >= 65:
        return "accumulate"
    elif prob_up >= 55:
        return "watch"
    elif prob_up >= 40:
        return "hold"
    else:
        return "avoid"


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    specific_symbols = []
    if "--universe-only" in sys.argv:
        idx = sys.argv.index("--universe-only")
        specific_symbols = sys.argv[idx + 1:]

    cutoff = date.today().isoformat()
    signals = []
    errors = []

    universe = [s for s in ML_UNIVERSE if not specific_symbols or s["symbol"] in specific_symbols]
    print(f"[ml-quant] Scoring {len(universe)} stocks, cutoff={cutoff}, dry_run={dry_run}")

    for item in universe:
        sym = item["symbol"]
        code = sym.split(".")[0]
        try:
            rows = fetch_yahoo_history(sym, days=365)
            if len(rows) < 60:
                errors.append({"symbol": sym, "error": f"insufficient history ({len(rows)} rows)"})
                print(f"  [WARN] {sym} {item['name']}: insufficient history ({len(rows)} rows)")
                continue

            closes = [r["close"] for r in rows]
            volumes = [r["volume"] for r in rows]

            result = score_series(closes, volumes, symbol=sym)
            if result is None:
                errors.append({"symbol": sym, "error": "score_series returned None"})
                print(f"  [WARN] {sym} {item['name']}: score_series returned None")
                continue

            prob_up = result["probability_up"]
            action = prob_to_action(prob_up, result.get("reasons", []))
            ref_price = rows[-1]["close"]

            signal = {
                "symbol": sym,
                "name": item["name"],
                "agent_id": "ml-quant",
                "action": action,
                "reference_price": ref_price,
                "data_cutoff": cutoff,
                "horizon": "120D",
                "model_version": f"xgb_v2 / ml-quant {cutoff}",
                "grade": "A" if action == "accumulate" else "B" if action == "watch" else "C" if action == "hold" else "D",
                "score": round(prob_up, 1),
                "evidence": result.get("reasons", []),
                "market_risk": f"ML model probability_up={prob_up}%, calibrated={result.get('calibrated', {})}",
                "data_quality": {"model": "high", "price": "high"},
                "entry_range": None,
                "stop_loss": None,
                "target": None,
                "invalidation": None,
            }
            signals.append(signal)
            print(f"  [OK] {sym} {item['name']}: prob_up={prob_up:.1f}% -> {action} @ {ref_price}")
            time.sleep(0.3)  # rate-limit Yahoo

        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})
            print(f"  [ERR] {sym} {item['name']}: {e}")

    print(f"\n[ml-quant] Summary: {len(signals)} signals, {len(errors)} errors")

    if dry_run:
        print(json.dumps({"signals": signals, "errors": errors}, ensure_ascii=False, indent=2))
        return 0

    if not signals:
        print("[ml-quant] No signals to freeze. Aborting.")
        return 1

    result = freeze_signals(signals)
    print(json.dumps({k: v for k, v in result.items() if k != "error"}, ensure_ascii=False, indent=2))
    if result.get("invalid"):
        print(f"  Invalid signals: {len(result['invalid'])}")
    print(f"[ml-quant] DONE — added={result.get('added', 0)}, durable={result.get('durable', False)}")
    return 0 if result.get("added", 0) > 0 or not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
