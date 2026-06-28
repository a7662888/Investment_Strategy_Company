# -*- coding: utf-8 -*-
"""Run the offline twstock batch collector.

This script is for a local/home-IP scheduled host.  Do not run it on Render and
do not wire it into the web request path.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from company.data.twstock_batch import CollectorConfig, TwstockBatchCollector


def _default_start(days: int) -> str:
    return (datetime.now().date() - timedelta(days=days)).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline twstock best-5/order-book and OHLCV collector")
    parser.add_argument("symbols", nargs="+", help="Taiwan stock symbols, e.g. 2330.TW 2454.TW")
    parser.add_argument("--start", default=_default_start(120), help="inclusive OHLCV start date, YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.now().date().isoformat(), help="inclusive OHLCV end date, YYYY-MM-DD")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("TWSTOCK_CACHE_DIR", "data/web_cache/twstock"),
        help="persistent cache directory; set TWSTOCK_CACHE_DIR to a private data repo path for production",
    )
    parser.add_argument("--reference-cache-dir", default="data_cache", help="FinMind cache dir used for close/date validation")
    parser.add_argument("--min-interval", type=float, default=2.0, help="seconds between twstock requests")
    parser.add_argument("--timeout", type=float, default=25.0, help="per twstock operation timeout in seconds")
    parser.add_argument("--no-order-book", action="store_true", help="skip realtime best-5 snapshots")
    parser.add_argument("--no-daily", action="store_true", help="skip daily OHLCV backup collection")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = CollectorConfig(
        output_dir=Path(args.output_dir),
        reference_cache_dir=Path(args.reference_cache_dir),
        min_interval_seconds=args.min_interval,
        operation_timeout_seconds=args.timeout,
    )
    collector = TwstockBatchCollector(config=config)
    report = collector.collect(
        args.symbols,
        start_date=args.start,
        end_date=args.end,
        order_book=not args.no_order_book,
        daily=not args.no_daily,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
