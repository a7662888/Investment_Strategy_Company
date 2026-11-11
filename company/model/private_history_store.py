"""私有歷史文件：CAS 重試合併，禁止以讀取失敗當成空歷史覆寫。"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from company.model import durable_document as store

_LOCK = threading.RLock()


class HistoryStoreError(ValueError):
    pass


def _local_write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, allow_nan=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load(path: Path, remote: str):
    config = store._config()
    if config:
        doc, sha, error = store._remote_get(remote, config)
        if error or doc is None and sha is not None:
            raise HistoryStoreError("HISTORY_READ_FAILED")
        return doc, {"source": "github", "durable": True}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError
        except (ValueError, OSError):
            raise HistoryStoreError("HISTORY_READ_FAILED") from None
        return doc, {"source": "local", "durable": False}
    return None, {"source": "none", "durable": False}


def update(path: Path, remote: str, transform):
    """重新讀取後合併；409 重新計算，永不在新的 SHA 上送舊的文件。"""
    with _LOCK:
        config = store._config()
        if not config:
            old, _ = load(path, remote)
            doc = transform(old)
            _local_write(path, doc)
            return doc, {"source": "local", "durable": False}
        token, repo, branch = config
        encoded = "/".join(urllib.parse.quote(p, safe="") for p in remote.split("/"))
        url = f"https://api.github.com/repos/{repo}/contents/{encoded}"
        for _ in range(3):
            old, sha, error = store._remote_get(remote, config)
            if error or old is None and sha is not None:
                raise HistoryStoreError("HISTORY_READ_FAILED")
            doc = transform(old)
            body = json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")
            payload = {"message": "chore(private): portfolio history", "branch": branch,
                       "content": base64.b64encode(body).decode("ascii")}
            if sha:
                payload["sha"] = sha
            request = urllib.request.Request(url, method="PUT", data=json.dumps(payload).encode(), headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json",
                "Accept": "application/vnd.github+json", "User-Agent": "portfolio-history"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    continue
                raise HistoryStoreError("HISTORY_WRITE_FAILED") from None
            except Exception:
                raise HistoryStoreError("HISTORY_WRITE_FAILED") from None
            try:
                _local_write(path, doc)
                local_saved = True
            except OSError:
                local_saved = False
            return doc, {"source": "github", "durable": True, "local_saved": local_saved}
        raise HistoryStoreError("HISTORY_CONFLICT")
