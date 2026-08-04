# -*- coding: utf-8 -*-
"""Private, GitHub-backed portfolio positions shared by web and email."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from company.model import durable_document

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH = ROOT / "data" / "private_positions.json"
REMOTE_PATH = os.environ.get("POSITIONS_REMOTE_PATH", "private/positions.json")
MAX_POSITIONS = 100


class PositionConflict(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_positions(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("positions must be a list")
    normalized: dict[str, dict] = {}
    for item in raw[:MAX_POSITIONS]:
        if not isinstance(item, dict):
            raise ValueError("each position must be an object")
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or len(symbol) > 20:
            raise ValueError("invalid position symbol")
        try:
            shares = float(item.get("shares") or 0)
            cost = float(item.get("cost") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"invalid shares/cost for {symbol}")
        if shares <= 0 or cost <= 0:
            raise ValueError(f"shares and cost must be positive for {symbol}")
        normalized[symbol] = {"symbol": symbol, "shares": shares, "cost": cost}
    return list(normalized.values())


def expected_sync_token() -> str | None:
    explicit = os.environ.get("POSITIONS_SYNC_TOKEN", "").strip()
    if explicit:
        return explicit
    data_token = (os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT") or "").strip()
    if not data_token:
        return None
    return hmac.new(data_token.encode("utf-8"), b"positions-sync-v1", hashlib.sha256).hexdigest()


def is_authorized(authorization: str | None) -> bool:
    expected = expected_sync_token()
    if not expected or not authorization:
        return False
    prefix = "Bearer "
    supplied = authorization[len(prefix):].strip() if authorization.startswith(prefix) else ""
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _document(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    return {
        "schema_version": 1,
        "version": max(0, int(source.get("version") or 0)),
        "updated_at": source.get("updated_at"),
        "positions": normalize_positions(source.get("positions") or []),
    }


def load_positions(prefer_remote: bool = True) -> tuple[dict, dict]:
    doc, storage = durable_document.load_document(LOCAL_PATH, REMOTE_PATH, prefer_remote=prefer_remote)
    return _document(doc), storage


def save_positions(raw_positions: object, expected_version: int | None = None) -> tuple[dict, dict]:
    positions = normalize_positions(raw_positions)
    config = durable_document._config()
    current_doc = None
    sha = None
    if config:
        current_doc, sha, read_error = durable_document._remote_get(REMOTE_PATH, config)
        if read_error:
            return _document(None), {"durable": False, "remote_saved": False, "error": read_error}
    elif LOCAL_PATH.exists():
        try:
            current_doc = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
        except Exception:
            current_doc = None
    current = _document(current_doc)
    if expected_version is not None and int(expected_version) != current["version"]:
        raise PositionConflict(f"version conflict: expected {expected_version}, current {current['version']}")
    doc = {
        "schema_version": 1,
        "version": current["version"] + 1,
        "updated_at": _utc_now(),
        "positions": positions,
    }
    body = json.dumps(doc, ensure_ascii=False, indent=2)
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(body, encoding="utf-8")
    result = {"local_saved": True, "remote_saved": False, "durable": False}
    if not config:
        return doc, result

    token, repo, branch = config
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded}"
    payload = {
        "message": f"chore(positions): update private portfolio v{doc['version']}",
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "investment-private-positions",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
        result.update(remote_saved=True, durable=True)
    except urllib.error.HTTPError as exc:
        result["error"] = f"remote_write_http_{exc.code}"
    except Exception as exc:
        result["error"] = f"remote_write_{type(exc).__name__}"
    return doc, result
