"""Opt-in production inventory fetch; never writes the manual portfolio.

No broker calls without --fetch. Supply SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY,
private data-repo configuration via the process environment. Optional
SHIOAJI_BROKER_ID and SHIOAJI_ACCOUNT_ID must be supplied together; without
them exactly one signed stock account is required. No .env loading or CA activation.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys

from company.data import broker_positions as broker


@contextlib.contextmanager
def _quiet_sdk():
    # Suppress SDK logs and native stdout/stderr; SDK exceptions can contain PII.
    old_path = os.environ.get("SJ_LOG_PATH")
    os.environ["SJ_LOG_PATH"] = os.devnull
    saved = []
    try:
        with open(os.devnull, "w") as sink:
            for stream in (sys.stdout, sys.stderr):
                stream.flush()
            for fd in (1, 2):
                saved.append((fd, os.dup(fd)))
                os.dup2(sink.fileno(), fd)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
    finally:
        for fd, original in saved:
            os.dup2(original, fd)
            os.close(original)
        if old_path is None:
            os.environ.pop("SJ_LOG_PATH", None)
        else:
            os.environ["SJ_LOG_PATH"] = old_path


def _fetch():
    if os.environ.get("SHIOAJI_SIMULATION", "").strip().lower() not in ("", "0", "false"):
        raise broker.BrokerPositionsError("PRODUCTION_REQUIRED")
    broker_id = os.environ.get("SHIOAJI_BROKER_ID", "").strip()
    account_id = os.environ.get("SHIOAJI_ACCOUNT_ID", "").strip()
    if bool(broker_id) != bool(account_id):
        raise broker.BrokerPositionsError("ACCOUNT_SELECTION_REQUIRED")
    credentials = [os.environ.get(key, "").strip() for key in ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY")]
    if not all(credentials):
        raise broker.BrokerPositionsError("CREDENTIALS_REQUIRED")
    if not broker.durable_document._config():
        raise broker.BrokerPositionsError("PRIVATE_STORAGE_NOT_CONFIGURED")
    with _quiet_sdk():
        try:
            sj = importlib.import_module("shioaji")
        except ImportError:
            raise broker.BrokerPositionsError("SDK_UNAVAILABLE") from None
        api = sj.Shioaji(simulation=False)
        try:
            api.login(api_key=credentials[0], secret_key=credentials[1], subscribe_trade=False)
            unit_class = getattr(sj, "Unit", None)
            if unit_class is None:
                unit_class = getattr(getattr(sj, "constant", None), "Unit", None)
            share_unit = getattr(unit_class, "Share", None)
            if share_unit is None:
                raise broker.BrokerPositionsError("SHARE_UNIT_REQUIRED")
            return broker.fetch_broker_positions(api, broker_id=broker_id, account_id=account_id, share_unit=share_unit)
        finally:
            try:
                api.logout()
            except Exception:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Explicitly authorize one production inventory query and private snapshot write")
    args = parser.parse_args(argv)
    if not args.fetch:
        print(json.dumps({"error_code": "LIVE_FETCH_NOT_REQUESTED", "broker_calls": 0}))
        return 0
    snapshot, code = None, None
    try:
        snapshot = _fetch()
    except Exception as exc:
        code = broker.error_code(exc)
    try:
        storage = broker.record_broker_attempt(snapshot=snapshot, failure_code=code)
    except Exception:
        storage = {"saved": False, "durable": False, "error_code": "STORAGE_WRITE_FAILED"}
    print(json.dumps({"position_count": len(snapshot["positions"]) if snapshot is not None else 0,
                      "error_code": code, "storage": storage}))
    return 0 if snapshot is not None and storage.get("durable") and not storage.get("error_code") else 1


if __name__ == "__main__":
    raise SystemExit(main())
