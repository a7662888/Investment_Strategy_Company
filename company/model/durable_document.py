# -*- coding: utf-8 -*-
"""Small GitHub-backed JSON document store used by replaceable snapshots."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _config() -> tuple[str, str, str] | None:
    token = os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT")
    repo = os.environ.get("GITHUB_DATA_REPO")
    branch = os.environ.get("GITHUB_DATA_BRANCH", "main")
    return (token, repo, branch) if token and repo else None


def _remote_get(remote_path: str, config: tuple[str, str, str]) -> tuple[dict | None, str | None, str | None]:
    token, repo, branch = config
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "investment-durable-document",
    }
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in remote_path.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded}?ref={urllib.parse.quote(branch)}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload.get("content") or ""
        # GitHub Contents API stops embedding file content once it exceeds 1 MiB
        # (`encoding: none`). Fetch the same immutable blob by SHA instead.
        if payload.get("encoding") != "base64" or not content:
            sha = payload.get("sha")
            if not sha:
                return None, None, "remote_read_missing_sha"
            blob_url = payload.get("git_url") or f"https://api.github.com/repos/{repo}/git/blobs/{sha}"
            blob_req = urllib.request.Request(blob_url, headers=headers)
            with urllib.request.urlopen(blob_req, timeout=60) as response:
                blob = json.loads(response.read().decode("utf-8"))
            if blob.get("encoding") != "base64" or not blob.get("content"):
                return None, sha, "remote_blob_content_unavailable"
            content = blob["content"]
        raw = base64.b64decode(content.replace("\n", ""))
        return json.loads(raw.decode("utf-8")), payload.get("sha"), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None, None
        return None, None, f"remote_read_http_{exc.code}"
    except Exception as exc:
        return None, None, f"remote_read_{type(exc).__name__}"


def load_document(local_path: Path, remote_path: str, prefer_remote: bool = True) -> tuple[dict | None, dict]:
    config = _config()
    remote_error = None
    if prefer_remote and config:
        doc, _, remote_error = _remote_get(remote_path, config)
        if doc is not None:
            return doc, {"source": "github", "durable": True}
    if local_path.exists():
        try:
            return json.loads(local_path.read_text(encoding="utf-8")), {
                "source": "local", "durable": False, "remote_error": remote_error,
            }
        except Exception as exc:
            return None, {"source": "none", "durable": False, "error": type(exc).__name__}
    return None, {"source": "none", "durable": False, "remote_error": remote_error}


def save_document(doc: dict, local_path: Path, remote_path: str, message: str) -> dict:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, indent=2)
    local_path.write_text(body, encoding="utf-8")
    result = {"local_saved": True, "remote_saved": False, "durable": False}
    config = _config()
    if not config:
        return result
    token, repo, branch = config
    _, sha, read_error = _remote_get(remote_path, config)
    if read_error:
        result["remote_error"] = read_error
        return result
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in remote_path.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded}"
    payload = {
        "message": message,
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="PUT", headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "investment-durable-document",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            response.read()
        result.update(remote_saved=True, durable=True)
    except urllib.error.HTTPError as exc:
        result["remote_error"] = f"remote_write_http_{exc.code}"
    except Exception as exc:
        result["remote_error"] = f"remote_write_{type(exc).__name__}"
    return result
