# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.model import positions


def main() -> None:
    normalized = positions.normalize_positions([
        {"symbol": "0056.tw", "shares": "1000", "cost": "54"},
        {"symbol": "0056.TW", "shares": 1200, "cost": 53.5},
    ])
    assert normalized == [{"symbol": "0056.TW", "shares": 1200.0, "cost": 53.5}]

    try:
        positions.normalize_positions([{"symbol": "2330.TW", "shares": 0, "cost": 900}])
        raise AssertionError("zero shares should fail")
    except ValueError:
        pass

    old_explicit = os.environ.get("POSITIONS_SYNC_TOKEN")
    old_data = os.environ.get("GITHUB_DATA_TOKEN")
    try:
        os.environ["POSITIONS_SYNC_TOKEN"] = "test-sync-token"
        assert positions.is_authorized("Bearer test-sync-token")
        assert not positions.is_authorized("Bearer wrong")
        os.environ.pop("POSITIONS_SYNC_TOKEN", None)
        os.environ["GITHUB_DATA_TOKEN"] = "github-data-secret"
        derived = positions.expected_sync_token()
        assert derived and derived != "github-data-secret"
        assert positions.is_authorized(f"Bearer {derived}")
    finally:
        if old_explicit is None:
            os.environ.pop("POSITIONS_SYNC_TOKEN", None)
        else:
            os.environ["POSITIONS_SYNC_TOKEN"] = old_explicit
        if old_data is None:
            os.environ.pop("GITHUB_DATA_TOKEN", None)
        else:
            os.environ["GITHUB_DATA_TOKEN"] = old_data

    original_path = positions.LOCAL_PATH
    original_config = positions.durable_document._config
    with tempfile.TemporaryDirectory() as tmp:
        positions.LOCAL_PATH = Path(tmp) / "positions.json"
        positions.durable_document._config = lambda: None
        doc, storage = positions.save_positions(
            [{"symbol": "2330.TW", "shares": 10, "cost": 900}], expected_version=0
        )
        assert doc["version"] == 1
        assert storage["local_saved"] is True and storage["durable"] is False
        loaded, loaded_storage = positions.load_positions(prefer_remote=False)
        assert loaded == doc
        assert loaded_storage["source"] == "local"
        try:
            positions.save_positions(doc["positions"], expected_version=0)
            raise AssertionError("stale version should conflict")
        except positions.PositionConflict:
            pass
    positions.LOCAL_PATH = original_path
    positions.durable_document._config = original_config
    print("Positions tests passed")


if __name__ == "__main__":
    main()
