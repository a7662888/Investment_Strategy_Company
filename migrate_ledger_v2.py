# -*- coding: utf-8 -*-
"""Archive the legacy ledger and rebuild one canonical outcome per horizon.

The migration is intentionally explicit: dry-run is the default, and --apply
first writes an immutable archive before replacing the active ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path

import app
from company.model import ledger
from company.model.durable_document import save_document


def _checksum(events: list[dict]) -> str:
    return hashlib.sha256(ledger._serialize_jsonl(events).encode("utf-8")).hexdigest()


def _validate(events: list[dict], require_unique_outcomes: bool = True) -> dict:
    event_ids = [event.get("event_id") for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise RuntimeError("canonical ledger contains duplicate event_id")
    signals = {event["signal_id"] for event in events if event.get("event_type") == "signal"}
    outcomes = [event for event in events if event.get("event_type") == "outcome"]
    pairs = [(event.get("signal_id"), event.get("horizon")) for event in outcomes]
    if require_unique_outcomes and len(pairs) != len(set(pairs)):
        raise RuntimeError("canonical ledger contains duplicate signal/horizon outcomes")
    missing = [event.get("event_id") for event in outcomes if event.get("signal_id") not in signals]
    if missing:
        raise RuntimeError(f"canonical outcomes without signals: {len(missing)}")
    return {
        "events": len(events),
        "signals": len(signals),
        "outcomes": len(outcomes),
        "unique_outcome_pairs": len(set(pairs)),
        "bytes": len(ledger._serialize_jsonl(events).encode("utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--apply", action="store_true", help="write archive and canonical ledger")
    args = parser.parse_args()

    events, storage = ledger.load_events(use_cache=False)
    if storage.get("source") != "github" or not storage.get("durable"):
        raise RuntimeError(f"durable remote ledger required: {storage}")
    signal_events = [event for event in events if event.get("event_type") == "signal"]
    signals = ledger.materialize(signal_events)
    if not signals:
        raise RuntimeError("no signals found")

    def throttled_fetch(symbol: str, start: str, end: str) -> list[dict]:
        rows = app.fetch_history(symbol, start, end)
        time.sleep(0.15)
        return rows

    outcomes, diagnostics = ledger.compute_outcome_events(signals, args.as_of, throttled_fetch)
    if diagnostics.get("fetch_errors"):
        raise RuntimeError(f"history fetch incomplete: {diagnostics['fetch_errors']}")
    canonical = signal_events + outcomes
    legacy_stats = _validate(events, require_unique_outcomes=False)
    canonical_stats = _validate(canonical)
    source_hash = _checksum(events)
    canonical_hash = _checksum(canonical)
    archive_path = f"data/archive/decision_ledger_legacy_20260804_{source_hash[:12]}.jsonl"
    report = {
        "migration": "decision-ledger-v2",
        "as_of": args.as_of,
        "source_sha256": source_hash,
        "canonical_sha256": canonical_hash,
        "archive_path": archive_path,
        "legacy": legacy_stats,
        "canonical": canonical_stats,
        "diagnostics": diagnostics,
        "applied": bool(args.apply),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        return 0

    archive_events, archive_sha, archive_error = ledger._fetch_remote(archive_path)
    if archive_error:
        raise RuntimeError(f"archive read failed: {archive_error}")
    if archive_events:
        if _checksum(archive_events) != source_hash:
            raise RuntimeError("archive path already exists with different content")
    else:
        archive_ok, archive_error = ledger._write_remote(
            events,
            archive_sha,
            remote_path=archive_path,
            message=f"chore(ledger): archive legacy ledger {source_hash[:12]}",
        )
        if not archive_ok:
            raise RuntimeError(f"archive write failed: {archive_error}")

    _, active_sha, active_error = ledger._fetch_remote()
    if active_error:
        raise RuntimeError(f"active ledger reread failed: {active_error}")
    main_ok, main_error = ledger._write_remote(
        canonical,
        active_sha,
        message=f"fix(ledger): migrate canonical outcomes v2 {canonical_hash[:12]}",
    )
    if not main_ok:
        raise RuntimeError(f"canonical ledger write failed: {main_error}")

    local_archive = ledger.ROOT / "data" / "ledger_archive" / "migration_20260804.json"
    report["applied"] = True
    manifest_result = save_document(
        report,
        local_archive,
        "data/archive/ledger_migration_20260804.json",
        "chore(ledger): record v2 migration manifest",
    )
    if not manifest_result.get("durable"):
        raise RuntimeError(f"migration manifest was not durable: {manifest_result}")

    ledger._invalidate_read_cache()
    verified, verified_storage = ledger.load_events(use_cache=False)
    if _checksum(verified) != canonical_hash or not verified_storage.get("durable"):
        raise RuntimeError("post-write verification failed")
    print(json.dumps({"verified": True, "storage": verified_storage}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
