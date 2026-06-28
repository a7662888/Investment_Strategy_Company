# -*- coding: utf-8 -*-
"""Offline twstock batch collector.

This module is intentionally not imported by app.py.  twstock is a batch-only
supplemental provider because the upstream TWSE/TPEx endpoints are IP/rate
limited and twstock silently returns empty data on some throttling failures.
"""
from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "web_cache" / "twstock"
DEFAULT_REFERENCE_CACHE_DIR = ROOT / "data_cache"


class TwstockBatchError(RuntimeError):
    """Raised when a batch fetch should not be written to persistent storage."""


@dataclass(frozen=True)
class CollectorConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    reference_cache_dir: Path = DEFAULT_REFERENCE_CACHE_DIR
    min_interval_seconds: float = 2.0
    operation_timeout_seconds: float = 25.0
    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (5.0, 15.0, 45.0)
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 300.0
    close_tolerance: float = 0.01


@dataclass
class RateLimiter:
    min_interval_seconds: float
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _last_call_at: float | None = None

    def wait(self) -> None:
        if self._last_call_at is not None:
            elapsed = self.clock() - self._last_call_at
            delay = self.min_interval_seconds - elapsed
            if delay > 0:
                self.sleeper(delay)
        self._last_call_at = self.clock()


@dataclass
class CircuitBreaker:
    failure_threshold: int
    cooldown_seconds: float
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None
    last_error: str | None = None

    def before_call(self) -> None:
        if self.opened_at is None:
            return
        elapsed = self.clock() - self.opened_at
        if elapsed < self.cooldown_seconds:
            remaining = round(self.cooldown_seconds - elapsed, 1)
            raise TwstockBatchError(
                f"twstock circuit breaker is open for {remaining}s: {self.last_error}"
            )
        self.failures = 0
        self.opened_at = None
        self.last_error = None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.last_error = None

    def record_failure(self, error: str) -> None:
        self.failures += 1
        self.last_error = error
        if self.failures >= self.failure_threshold:
            self.opened_at = self.clock()


def _import_twstock() -> Any:
    try:
        import twstock  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - exercised only without dependency
        raise TwstockBatchError(
            "twstock is required for offline collection. Install it on the batch host "
            "with `python -m pip install twstock`; do not add it to the Render runtime."
        ) from exc
    return twstock


def normalize_code(symbol: str) -> str:
    return symbol.strip().upper().replace(".TW", "").replace(".TWO", "")


def normalize_symbol(symbol: str) -> str:
    code = normalize_code(symbol)
    return f"{code}.TW"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def _json_list(value: Any) -> str:
    return json.dumps(list(value or []), ensure_ascii=False, separators=(",", ":"))


def _date_to_iso(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


def _month_iter(start: date, end: date) -> Iterable[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _run_with_timeout(timeout_seconds: float, func: Callable[[], Any]) -> Any:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError(f"twstock operation exceeded {timeout_seconds}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _write_csv(path: Path, rows: list[dict[str, Any]], *, append: bool = False) -> None:
    if not rows:
        raise TwstockBatchError(f"refusing to write empty twstock payload: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if append and exists else "w"
    with path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if mode == "w":
            writer.writeheader()
        writer.writerows(rows)


def _latest_reference_close(symbol: str, cache_dir: Path) -> dict[str, Any] | None:
    code = normalize_code(symbol)
    candidates = [
        cache_dir / f"{code}_price.csv",
        cache_dir / f"{normalize_symbol(symbol)}_price.csv",
    ]
    for path in candidates:
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if row.get("date") and row.get("close")]
        if not rows:
            continue
        latest = sorted(rows, key=lambda row: row["date"])[-1]
        return {
            "provider": "FinMind cache",
            "path": str(path),
            "date": latest.get("date"),
            "close": _parse_float(latest.get("close")),
        }
    return None


def validate_against_reference(
    symbol: str,
    twstock_rows: list[dict[str, Any]],
    reference_cache_dir: Path,
    *,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    if not twstock_rows:
        return {"symbol": normalize_symbol(symbol), "status": "twstock_empty"}
    latest = sorted(twstock_rows, key=lambda row: row["date"])[-1]
    reference = _latest_reference_close(symbol, reference_cache_dir)
    result = {
        "symbol": normalize_symbol(symbol),
        "twstock_latest_date": latest.get("date"),
        "twstock_close": _parse_float(latest.get("close")),
        "reference": reference,
    }
    if reference is None:
        result["status"] = "no_reference_cache"
        result["stale"] = False
        return result
    if str(latest.get("date")) < str(reference.get("date")):
        result["status"] = "stale_vs_reference"
        result["stale"] = True
        return result
    if str(latest.get("date")) > str(reference.get("date")):
        result["status"] = "newer_than_reference"
        result["stale"] = False
        return result
    tw_close = result["twstock_close"]
    ref_close = reference.get("close")
    if tw_close is None or ref_close is None:
        result["status"] = "missing_close"
        result["stale"] = False
        return result
    result["close_diff"] = round(tw_close - ref_close, 4)
    result["status"] = "ok" if abs(tw_close - ref_close) <= tolerance else "close_mismatch"
    result["stale"] = False
    return result


@dataclass
class TwstockBatchCollector:
    config: CollectorConfig = field(default_factory=CollectorConfig)
    twstock_module: Any | None = None
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.twstock = self.twstock_module or _import_twstock()
        self.rate_limiter = RateLimiter(
            self.config.min_interval_seconds,
            sleeper=self.sleeper,
            clock=self.clock,
        )
        self.breaker = CircuitBreaker(
            self.config.breaker_failure_threshold,
            self.config.breaker_cooldown_seconds,
            clock=self.clock,
        )

    def _guarded_call(
        self,
        label: str,
        func: Callable[[], Any],
        validator: Callable[[Any], None],
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_attempts):
            self.breaker.before_call()
            self.rate_limiter.wait()
            try:
                result = _run_with_timeout(self.config.operation_timeout_seconds, func)
                validator(result)
            except Exception as exc:  # noqa: BLE001 - normalized for batch log
                last_error = exc
                self.breaker.record_failure(f"{label}: {type(exc).__name__}: {exc}")
                if attempt + 1 < self.config.max_attempts:
                    delay = self.config.backoff_seconds[min(attempt, len(self.config.backoff_seconds) - 1)]
                    self.sleeper(delay)
                continue
            self.breaker.record_success()
            return result
        raise TwstockBatchError(f"{label} failed after {self.config.max_attempts} attempts: {last_error}")

    def fetch_order_book_snapshot(self, symbol: str) -> dict[str, Any]:
        code = normalize_code(symbol)

        def _call() -> dict[str, Any]:
            return self.twstock.realtime.get(code, retry=0)

        def _validate(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict) or not payload.get("success"):
                raise TwstockBatchError(f"realtime payload not successful: {payload}")
            if not payload.get("realtime"):
                raise TwstockBatchError("realtime payload missing `realtime` block")

        payload = self._guarded_call(f"realtime:{code}", _call, _validate)
        info = payload.get("info") or {}
        realtime = payload.get("realtime") or {}
        provider_time = info.get("time")
        stale = False
        stale_reason = ""
        if provider_time:
            provider_date = str(provider_time)[:10]
            today = datetime.now().date().isoformat()
            stale = provider_date < today
            stale_reason = "provider_time_before_today" if stale else ""
        return {
            "fetched_at_utc": _utc_now(),
            "provider": "twstock.realtime",
            "symbol": normalize_symbol(code),
            "code": code,
            "name": info.get("name") or "",
            "provider_time": provider_time or "",
            "provider_timestamp": payload.get("timestamp") or "",
            "latest_trade_price": realtime.get("latest_trade_price") or "",
            "trade_volume": realtime.get("trade_volume") or "",
            "accumulate_trade_volume": realtime.get("accumulate_trade_volume") or "",
            "best_bid_price": _json_list(realtime.get("best_bid_price")),
            "best_bid_volume": _json_list(realtime.get("best_bid_volume")),
            "best_ask_price": _json_list(realtime.get("best_ask_price")),
            "best_ask_volume": _json_list(realtime.get("best_ask_volume")),
            "success": True,
            "stale": stale,
            "stale_reason": stale_reason,
        }

    def fetch_daily_ohlcv(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        code = normalize_code(symbol)
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < start:
            raise TwstockBatchError(f"end_date before start_date: {start_date} > {end_date}")

        stock = self.twstock.Stock(code, initial_fetch=False)
        rows: list[dict[str, Any]] = []
        for year, month in _month_iter(start, end):
            def _call(year: int = year, month: int = month) -> list[Any]:
                return stock.fetch(year, month)

            def _validate(payload: list[Any]) -> None:
                if not payload:
                    raise TwstockBatchError(f"empty daily OHLCV for {code} {year}-{month:02d}")

            month_rows = self._guarded_call(f"daily:{code}:{year}-{month:02d}", _call, _validate)
            for row in month_rows:
                row_date = _date_to_iso(getattr(row, "date", ""))
                if start_date <= row_date <= end_date:
                    rows.append(
                        {
                            "date": row_date,
                            "symbol": normalize_symbol(code),
                            "open": getattr(row, "open", ""),
                            "high": getattr(row, "high", ""),
                            "low": getattr(row, "low", ""),
                            "close": getattr(row, "close", ""),
                            "volume": getattr(row, "capacity", ""),
                            "turnover": getattr(row, "turnover", ""),
                            "transaction": getattr(row, "transaction", ""),
                            "change": getattr(row, "change", ""),
                            "note": getattr(row, "note", ""),
                            "provider": "twstock.Stock.fetch",
                            "fetched_at_utc": _utc_now(),
                        }
                    )
        if not rows:
            raise TwstockBatchError(f"no twstock rows in requested range for {normalize_symbol(code)}")
        return sorted(rows, key=lambda row: row["date"])

    def write_order_book_snapshots(self, rows: list[dict[str, Any]]) -> Path:
        path = self.config.output_dir / "twstock_order_book_snapshots.csv"
        _write_csv(path, rows, append=True)
        return path

    def write_daily_ohlcv(self, symbol: str, rows: list[dict[str, Any]]) -> Path:
        path = self.config.output_dir / f"{normalize_symbol(symbol).replace('.', '_')}_twstock_ohlcv.csv"
        _write_csv(path, rows, append=False)
        return path

    def collect(
        self,
        symbols: list[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        order_book: bool = True,
        daily: bool = True,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "generated_at_utc": _utc_now(),
            "provider": "twstock",
            "output_dir": str(self.config.output_dir),
            "symbols": [normalize_symbol(symbol) for symbol in symbols],
            "order_book_file": None,
            "daily_files": {},
            "validation": [],
            "errors": [],
        }
        if order_book:
            snapshots = []
            for symbol in symbols:
                try:
                    snapshots.append(self.fetch_order_book_snapshot(symbol))
                except Exception as exc:  # noqa: BLE001 - keep collecting other symbols
                    report["errors"].append({"symbol": normalize_symbol(symbol), "stage": "order_book", "error": str(exc)})
            if snapshots:
                report["order_book_file"] = str(self.write_order_book_snapshots(snapshots))

        if daily:
            if not start_date or not end_date:
                raise TwstockBatchError("start_date and end_date are required when daily=True")
            for symbol in symbols:
                try:
                    rows = self.fetch_daily_ohlcv(symbol, start_date, end_date)
                    path = self.write_daily_ohlcv(symbol, rows)
                    report["daily_files"][normalize_symbol(symbol)] = str(path)
                    report["validation"].append(
                        validate_against_reference(
                            symbol,
                            rows,
                            self.config.reference_cache_dir,
                            tolerance=self.config.close_tolerance,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append({"symbol": normalize_symbol(symbol), "stage": "daily", "error": str(exc)})

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        validation_path = self.config.output_dir / "twstock_validation.json"
        validation_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["validation_file"] = str(validation_path)
        return report
