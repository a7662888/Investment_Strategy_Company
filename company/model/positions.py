# -*- coding: utf-8 -*-
"""Private, GitHub-backed portfolio positions shared by web and email."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
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
        if not re.fullmatch(r"[A-Z0-9^.-]{1,20}", symbol):
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


SYNC_SECRET_LOCAL = ROOT / "data" / "sync_secret.json"
SYNC_SECRET_REMOTE = os.environ.get("SYNC_SECRET_PATH", "private/sync_secret.json")
_SYNC_SECRET_CACHE: dict = {"at": 0.0, "value": None}
_SYNC_SECRET_TTL = 300.0


def _derived_sync_token() -> str | None:
    """由資料庫 token 推導的舊式密鑰。

    問題在於它會隨 GITHUB_DATA_TOKEN 輪替而失效，使用者存在瀏覽器裡的密鑰
    因此突然不能用。保留它只為了兩個用途：既有部署的相容退路，
    以及第一次固化時的種子（種子取現行值，使用者手上那把才不會失效）。
    """
    data_token = (os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT") or "").strip()
    if not data_token:
        return None
    return hmac.new(data_token.encode("utf-8"), b"positions-sync-v1", hashlib.sha256).hexdigest()


def stored_sync_token(refresh: bool = False) -> str | None:
    """已固化、與 token 輪替無關的同步密鑰。

    存放在私有資料庫（能讀它的人本來就已握有全部資料存取權，故未降低安全性），
    但改讀它之後，輪替 GITHUB_DATA_TOKEN 不再使使用者手上的密鑰失效。
    以 TTL 快取，避免每次請求都打一次 GitHub API。
    """
    import time

    now = time.time()
    if not refresh and _SYNC_SECRET_CACHE["value"] and now - _SYNC_SECRET_CACHE["at"] < _SYNC_SECRET_TTL:
        return _SYNC_SECRET_CACHE["value"]
    try:
        document, _ = durable_document.load_document(SYNC_SECRET_LOCAL, SYNC_SECRET_REMOTE)
    except Exception:  # noqa: BLE001 - 讀不到就退回推導值，不要讓同步整個失效
        return None
    value = str((document or {}).get("token") or "").strip() or None
    if value:
        _SYNC_SECRET_CACHE.update(at=now, value=value)
    return value


def ensure_sync_token() -> tuple[str | None, dict]:
    """把目前有效的密鑰固化，使其不再隨 token 輪替失效。

    刻意以「現行推導值」為種子而非新亂數：使用者已經把那把密鑰存進瀏覽器，
    換成新值等於現在就把它弄失效——正好是這次要避免的事。
    """
    existing = stored_sync_token(refresh=True)
    if existing:
        return existing, {"created": False, "reason": "already_persisted"}
    seed = _derived_sync_token()
    if not seed:
        return None, {"created": False, "error": "no_source_token"}
    storage = durable_document.save_document(
        {"schema_version": 1, "token": seed, "created_at": _utc_now(),
         "note": "seeded from the token-derived key so existing browser keys keep working"},
        SYNC_SECRET_LOCAL, SYNC_SECRET_REMOTE, "chore(positions): persist sync secret",
    )
    _SYNC_SECRET_CACHE.update(at=0.0, value=None)
    return seed, {"created": True, "storage": storage}


def sync_token_source() -> str:
    """密鑰目前來自哪一層。只回來源名稱，不回值——供健康檢查診斷用。

    加這個是因為線上曾出現「本機三處指紋一致、伺服器卻回 401」，
    沒有來源資訊就只能靠猜；後來正是靠它查出伺服器在用一把已遺失的環境變數密鑰。
    順序須與 expected_sync_token 一致。
    """
    if stored_sync_token():
        return "persisted"
    if os.environ.get("POSITIONS_SYNC_TOKEN", "").strip():
        return "explicit_env"
    if _derived_sync_token():
        return "derived_from_data_token"
    return "none"


def rotate_sync_token() -> tuple[str, dict]:
    """產生並固化一把新的同步密鑰（32-byte CSPRNG）。

    沿用 codex 2026-09-07 的強度標準，但改存私有資料庫而非 Render 環境變數：
    當時那把只放在剪貼簿、未留任何副本，事後無法取回，使用者因此被鎖在外面。
    存進資料庫後可隨時重新交付，且仍與 GITHUB_DATA_TOKEN 輪替解耦。
    """
    import secrets

    token = secrets.token_urlsafe(32)
    storage = durable_document.save_document(
        {"schema_version": 1, "token": token, "created_at": _utc_now(),
         "note": "32-byte CSPRNG; persisted so it can be re-delivered without a redeploy"},
        SYNC_SECRET_LOCAL, SYNC_SECRET_REMOTE, "chore(positions): rotate sync secret",
    )
    _SYNC_SECRET_CACHE.update(at=0.0, value=None)
    return token, storage


def expected_sync_token() -> str | None:
    # 固化密鑰優先於環境變數。原本環境變數最優先，結果是 2026-09-07 設在 Render
    # 的那把（只存在於剪貼簿、已遺失）長期勝出，使用者無論拿到什麼密鑰都是 401，
    # 而 Render 環境變數既非業主、也非本 agent 所能修改，等於死鎖。
    # 固化密鑰存在私有資料庫，可重新交付且同樣與 GITHUB_DATA_TOKEN 輪替解耦。
    persisted = stored_sync_token()
    if persisted:
        return persisted
    explicit = os.environ.get("POSITIONS_SYNC_TOKEN", "").strip()
    if explicit:
        return explicit
    return _derived_sync_token()


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
