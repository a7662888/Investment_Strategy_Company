# -*- coding: utf-8 -*-
"""每日價值 current-state 的本機／私有 GitHub data repo 持久層。

Decision Ledger 是不可變的績效快照；current-state 則可每日覆寫，專門回答
「今天仍值得研究嗎」。兩者分離，避免為每次價格漂移新增帳本事件。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATH = ROOT / "data" / "current_value_state.json"
REMOTE_PATH = os.environ.get("VALUE_STATE_PATH", "value/current_state.json")


def _remote_config() -> tuple[str, str, str] | None:
    token = os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT")
    repo = os.environ.get("GITHUB_DATA_REPO")
    branch = os.environ.get("GITHUB_DATA_BRANCH", "main")
    return (token, repo, branch) if token and repo else None


def _remote_get(config: tuple[str, str, str]) -> tuple[dict | None, str | None, str | None]:
    token, repo, branch = config
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded}?ref={urllib.parse.quote(branch)}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "User-Agent": "investment-value-current-state",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = base64.b64decode((payload.get("content") or "").replace("\n", ""))
        return json.loads(raw.decode("utf-8")), payload.get("sha"), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None, None
        return None, None, f"remote_read_http_{exc.code}"
    except Exception as exc:
        return None, None, f"remote_read_{type(exc).__name__}"


def load_current_state(prefer_remote: bool = True) -> tuple[dict | None, dict]:
    config = _remote_config()
    if prefer_remote and config:
        doc, _, err = _remote_get(config)
        if doc is not None:
            return doc, {"source": "github", "durable": True}
        remote_error = err
    else:
        remote_error = None
    if LOCAL_PATH.exists():
        try:
            return json.loads(LOCAL_PATH.read_text(encoding="utf-8")), {
                "source": "local", "durable": False, "remote_error": remote_error,
            }
        except Exception as exc:
            return None, {"source": "none", "durable": False, "error": type(exc).__name__}
    return None, {"source": "none", "durable": False, "remote_error": remote_error}


def save_current_state(doc: dict) -> dict:
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"local_saved": True, "remote_saved": False, "durable": False}
    config = _remote_config()
    if not config:
        return result
    token, repo, branch = config
    _, sha, read_error = _remote_get(config)
    if read_error:
        result["remote_error"] = read_error
        return result
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in REMOTE_PATH.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded}"
    payload = {
        "message": "chore(value): update daily current state",
        "content": base64.b64encode(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="PUT", headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "Content-Type": "application/json", "User-Agent": "investment-value-current-state",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            response.read()
        result.update(remote_saved=True, durable=True)
    except urllib.error.HTTPError as exc:
        result["remote_error"] = f"remote_write_http_{exc.code}"
    except Exception as exc:
        result["remote_error"] = f"remote_write_{type(exc).__name__}"
    return result
