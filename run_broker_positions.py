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
import re
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
            snapshot = broker.fetch_broker_positions(api, broker_id=broker_id, account_id=account_id,
                                                     share_unit=share_unit, production_confirmed=True)
            _LEDGER.update(_fetch_ledger(api, broker_id, account_id, share_unit, snapshot))
            return snapshot
        finally:
            try:
                api.logout()
            except Exception:
                pass


_LEDGER: dict = {}


def _fetch_ledger(api, broker_id, account_id, share_unit, snapshot) -> dict:
    """同一次登入順便讀逐筆買進紀錄與已實現損益。失敗只回錯誤代碼，不影響庫存。"""
    from company.data import broker_ledger

    result = {}
    try:
        account = broker.select_stock_account(api, broker_id=broker_id, account_id=account_id)
    except Exception as exc:
        return {"lots_error": broker.error_code(exc), "realized_error": broker.error_code(exc)}

    def symbol_of(code):
        return broker._symbol(api, code)

    try:
        result["lots"] = broker_ledger.fetch_lots(api, account, share_unit, symbol_of)
    except Exception as exc:
        result["lots_error"] = broker.error_code(exc)
    try:
        result["realized"] = broker_ledger.fetch_realized(api, account, share_unit, symbol_of)
    except Exception as exc:
        result["realized_error"] = broker.error_code(exc)
    result["account_key"] = snapshot.get("account_key")
    return result


def _save_ledger() -> dict:
    """只寫私有資料庫；回傳筆數與狀態（不含任何持股內容），供公開 log 列印。"""
    from company.data import broker_ledger
    from company.model import durable_document

    local_dir = broker.LOCAL_PATH.parent   # 已被 .gitignore 排除的私有目錄
    report = {}
    now = broker._now()
    if "lots" in _LEDGER:
        lots = _LEDGER["lots"]
        doc = {"schema_version": 1, "as_of": now, "account_key": _LEDGER.get("account_key"),
               "lots": lots["lots"], "status": lots["status"]}
        storage = durable_document.save_document(doc, local_dir / "lots.json", broker_ledger.LOTS_REMOTE,
                                                 "chore(private): broker lots")
        report["lots"] = {"symbols": len(lots["lots"]), "lot_count": sum(len(v) for v in lots["lots"].values()),
                          "unreconciled": sum(1 for v in lots["status"].values() if v != "ok"),
                          "durable": storage.get("durable")}
    elif _LEDGER.get("lots_error"):
        report["lots"] = {"error_code": _LEDGER["lots_error"]}
    if "realized" in _LEDGER:
        realized = _LEDGER["realized"]
        doc = {"schema_version": 1, "as_of": now, "account_key": _LEDGER.get("account_key"), **realized}
        storage = durable_document.save_document(doc, local_dir / "realized.json",
                                                 broker_ledger.REALIZED_REMOTE, "chore(private): realized pnl")
        report["realized"] = {"trades": len(realized["trades"]), "summary": len(realized["summary"]),
                              "durable": storage.get("durable")}
    elif _LEDGER.get("realized_error"):
        report["realized"] = {"error_code": _LEDGER["realized_error"]}
    return report


def _account_diagnostics(api):
    """Whitelist structural metadata only; never serialize an SDK object."""
    accounts = api.list_accounts()
    if not isinstance(accounts, (list, tuple)) or len(accounts) > 100:
        raise broker.BrokerPositionsError("INVALID_RESPONSE")
    default = getattr(api, "stock_account", None)
    rows = []
    for index, account in enumerate(accounts):
        kind = broker._enum(broker._value(account, "account_type"))
        signed = broker._value(account, "signed")
        signed_type = {bool: "boolean", str: "string", int: "integer", type(None): "none"}.get(type(signed), "other")
        if signed is True:
            state = "true"
        elif signed is False:
            state = "false"
        elif signed is None:
            state = "missing_or_null"
        elif type(signed) is str and signed in ("True", "true", "False", "false"):
            state = "text_true" if signed.lower() == "true" else "text_false"
        else:
            state = "unexpected_type_or_value"
        ids = [broker._value(account, key) for key in ("broker_id", "account_id")]
        default_ids = [broker._value(default, key) for key in ("broker_id", "account_id")]
        rows.append({"index": index, "account_type": kind if kind in ("S", "F", "H") else "unknown",
                     "signed_present": "signed" in account if isinstance(account, dict) else hasattr(account, "signed"),
                     "signed_type": signed_type, "signed_state": state,
                     "is_default_stock": account is default or (all(isinstance(v, str) and v for v in ids) and ids == default_ids)})
    broker_id = os.environ.get("SHIOAJI_BROKER_ID", "").strip()
    account_id = os.environ.get("SHIOAJI_ACCOUNT_ID", "").strip()
    # Reuse the exact selection policy on the already fetched account list.
    class AccountList:
        def list_accounts(self):
            return accounts
    try:
        broker.select_stock_account(AccountList(), broker_id=broker_id, account_id=account_id)
        selection_error = None
    except Exception as exc:
        selection_error = broker.error_code(exc)
    return {"account_count": len(rows), "accounts": rows,
            "selector_configured": bool(broker_id or account_id), "selection_error": selection_error}


def _diagnose():
    """Production login/list_accounts/logout only: no inventory or storage writes."""
    if os.environ.get("SHIOAJI_SIMULATION", "").strip().lower() not in ("", "0", "false"):
        raise broker.BrokerPositionsError("PRODUCTION_REQUIRED")
    credentials = [os.environ.get(key, "").strip() for key in ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY")]
    if not all(credentials):
        raise broker.BrokerPositionsError("CREDENTIALS_REQUIRED")
    with _quiet_sdk():
        try:
            sj = importlib.import_module("shioaji")
        except ImportError:
            raise broker.BrokerPositionsError("SDK_UNAVAILABLE") from None
        api = sj.Shioaji(simulation=False)
        try:
            api.login(api_key=credentials[0], secret_key=credentials[1], subscribe_trade=False)
            result = _account_diagnostics(api)
            version = getattr(sj, "__version__", "")
            result["sdk_version"] = version if isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) else "unknown"
            return result
        finally:
            try:
                api.logout()
            except Exception:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--fetch", action="store_true", help="Explicitly authorize one production inventory query and private snapshot write")
    modes.add_argument("--diagnose", action="store_true", help="Read account metadata only, without inventory queries or storage writes")
    args = parser.parse_args(argv)
    if args.diagnose:
        try:
            result = _diagnose()
        except Exception as exc:
            result = {"error_code": broker.error_code(exc)}
        print(json.dumps(result))
        return 1 if result.get("error_code") else 0
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
    try:
        ledger_report = _save_ledger() if snapshot is not None else {}
    except Exception:
        ledger_report = {"error_code": "LEDGER_SAVE_FAILED"}
    print(json.dumps({"position_count": len(snapshot["positions"]) if snapshot is not None else 0,
                      "error_code": code, "storage": storage, "ledger": ledger_report}))
    return 0 if snapshot is not None and storage.get("durable") and not storage.get("error_code") else 1


if __name__ == "__main__":
    raise SystemExit(main())
