"""合成私有歷史儲存測試：CAS、失敗保留與併發追加，不連遠端。"""
from __future__ import annotations

import base64
import copy
import json
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from company.model import private_history_store as history

CONFIG = ("synthetic-token", "synthetic-owner/synthetic-private-history", "main")
REMOTE = "private/synthetic_history.json"


def response():
    reply = MagicMock()
    reply.__enter__.return_value.read.return_value = b"{}"
    return reply


def conflict():
    return urllib.error.HTTPError("https://synthetic.invalid", 409, "Conflict", {}, None)


def append(event):
    def transform(old):
        doc = copy.deepcopy(old or {"events": []})
        if event not in doc["events"]:
            doc["events"].append(event)
        return doc
    return transform


def decoded(request):
    payload = json.loads(request.data)
    return payload, json.loads(base64.b64decode(payload["content"]))


def test_cas_conflict_rereads_latest_sha_and_merges_concurrent_append(tmp_path):
    path = tmp_path / "history.json"
    old = {"events": ["retained-old-event"]}
    concurrent = {"events": ["retained-old-event", "other-writer-event"]}
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", side_effect=[(old, "sha-1", None), (concurrent, "sha-2", None)]) as read, \
            patch.object(history.urllib.request, "urlopen", side_effect=[conflict(), response()]) as write:
        doc, storage = history.update(path, REMOTE, append("my-new-event"))
    assert doc["events"] == ["retained-old-event", "other-writer-event", "my-new-event"]
    assert storage == {"source": "github", "durable": True, "local_saved": True}
    assert read.call_count == write.call_count == 2
    first_payload, first_doc = decoded(write.call_args_list[0].args[0])
    last_payload, last_doc = decoded(write.call_args_list[1].args[0])
    assert first_payload["sha"] == "sha-1" and last_payload["sha"] == "sha-2"
    assert first_doc["events"] == ["retained-old-event", "my-new-event"]
    assert last_doc == doc == json.loads(path.read_text(encoding="utf-8"))


def test_remote_read_failure_never_writes_or_falls_back_to_local(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["local-old-event"]}', encoding="utf-8")
    before = path.read_bytes()
    transform = MagicMock()
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", return_value=(None, None, "remote_read_http_403")), \
            patch.object(history.urllib.request, "urlopen") as write:
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.load(path, REMOTE)
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.update(path, REMOTE, transform)
    transform.assert_not_called()
    write.assert_not_called()
    assert path.read_bytes() == before


def test_remote_write_failure_does_not_change_local_success(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["old-event"]}', encoding="utf-8")
    before = path.read_bytes()
    failures = [urllib.error.HTTPError("https://synthetic.invalid", 503, "Unavailable", {}, None),
                OSError("synthetic-sensitive-network-error")]
    for failure in failures:
        with patch.object(history.store, "_config", return_value=CONFIG), \
                patch.object(history.store, "_remote_get", return_value=({"events": ["old-event"]}, "sha-1", None)), \
                patch.object(history.urllib.request, "urlopen", side_effect=failure):
            with pytest.raises(history.HistoryStoreError, match="^HISTORY_WRITE_FAILED$"):
                history.update(path, REMOTE, append("new-event"))
        assert path.read_bytes() == before


def test_corrupt_local_document_fails_closed_and_does_not_run_transform(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":[', encoding="utf-8")
    before = path.read_bytes()
    transform = MagicMock()
    with patch.object(history.store, "_config", return_value=None):
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.load(path, REMOTE)
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.update(path, REMOTE, transform)
    transform.assert_not_called()
    assert path.read_bytes() == before


def test_remote_success_local_failure_remains_durable_and_reports_cache_failure(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["local-old"]}', encoding="utf-8")
    before = path.read_bytes()
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", return_value=({"events": ["remote-old"]}, "sha-1", None)), \
            patch.object(history.urllib.request, "urlopen", return_value=response()), \
            patch.object(history, "_local_write", side_effect=OSError("synthetic-cache-permission")):
        doc, storage = history.update(path, REMOTE, append("new-event"))
    assert doc["events"] == ["remote-old", "new-event"]
    assert storage == {"source": "github", "durable": True, "local_saved": False}
    assert path.read_bytes() == before


def test_exhausted_conflicts_preserve_local_and_have_bounded_retries(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["last-success"]}', encoding="utf-8")
    before = path.read_bytes()
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", return_value=({"events": ["remote-old"]}, "sha-1", None)) as read, \
            patch.object(history.urllib.request, "urlopen", side_effect=conflict()) as write:
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_CONFLICT$"):
            history.update(path, REMOTE, append("new-event"))
    assert read.call_count == write.call_count == 3
    assert path.read_bytes() == before


def test_local_concurrent_appends_retain_every_event_and_retry_is_idempotent(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["retained-event"]}', encoding="utf-8")
    gate = threading.Barrier(8)

    def worker(index):
        gate.wait(timeout=5)
        return history.update(path, REMOTE, append(f"synthetic-{index}"))

    with patch.object(history.store, "_config", return_value=None), ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))
        doc, storage = history.update(path, REMOTE, append("synthetic-3"))
    assert set(doc["events"]) == {"retained-event", *(f"synthetic-{index}" for index in range(8))}
    assert len(doc["events"]) == 9
    assert all(item[1] == {"source": "local", "durable": False} for item in results)
    assert storage == {"source": "local", "durable": False}
    assert json.loads(path.read_text(encoding="utf-8")) == doc
    assert list(tmp_path.glob("*.tmp")) == []


def test_new_remote_document_has_no_sha_and_preserves_unicode(tmp_path):
    path = tmp_path / "history.json"
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", return_value=(None, None, None)), \
            patch.object(history.urllib.request, "urlopen", return_value=response()) as write:
        doc, storage = history.update(path, REMOTE, append("合成事件"))
    payload, submitted = decoded(write.call_args.args[0])
    assert "sha" not in payload
    assert submitted == doc == {"events": ["合成事件"]}
    assert storage["durable"] is True


def test_failed_atomic_replace_keeps_original_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["old"]}', encoding="utf-8")
    before = path.read_bytes()
    with patch.object(history.os, "replace", side_effect=OSError("synthetic-replace-failure")):
        with pytest.raises(OSError):
            history._local_write(path, {"events": ["new"]})
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_remote_json_null_with_existing_sha_is_corrupt_not_an_empty_history(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"events":["last-local-success"]}', encoding="utf-8")
    before = path.read_bytes()
    transform = MagicMock()
    with patch.object(history.store, "_config", return_value=CONFIG), \
            patch.object(history.store, "_remote_get", return_value=(None, "existing-null-blob-sha", None)), \
            patch.object(history.urllib.request, "urlopen") as write:
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.load(path, REMOTE)
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.update(path, REMOTE, transform)
    transform.assert_not_called()
    write.assert_not_called()
    assert path.read_bytes() == before


def test_local_json_null_is_corrupt_and_cannot_be_replaced_with_empty_history(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("null", encoding="utf-8")
    transform = MagicMock()
    with patch.object(history.store, "_config", return_value=None):
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.load(path, REMOTE)
        with pytest.raises(history.HistoryStoreError, match="^HISTORY_READ_FAILED$"):
            history.update(path, REMOTE, transform)
    transform.assert_not_called()
    assert path.read_text(encoding="utf-8") == "null"
    assert list(tmp_path.glob("*.tmp")) == []
