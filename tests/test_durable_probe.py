# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app


class TestDurableProbe(unittest.TestCase):
    """憑證被撤銷時，網站讀不到任何資料，健康檢查卻曾回 ready／warnings=[]。

    2026-09-23 輪替 DATA_REPO_TOKEN 後實際發生：Render 仍持舊值，
    as_of 變成 None、帳本讀不到，健康檢查完全沒有察覺——因為它只驗
    環境變數「有沒有設」，不驗「能不能用」。
    """

    def setUp(self) -> None:
        app._DURABLE_PROBE.update(at=0.0, ok=None, error=None, running=False)

    def _readiness(self):
        with patch.dict("os.environ", {"GITHUB_DATA_TOKEN": "t", "GITHUB_DATA_REPO": "o/r"}):
            return app.readiness_status()[0]

    def test_revoked_credential_is_surfaced(self) -> None:
        app._DURABLE_PROBE.update(at=time.time(), ok=False, error="remote_read_http_401")
        payload = self._readiness()
        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(any("憑證可能已撤銷或過期" in w for w in payload["warnings"]))

    def test_healthy_storage_produces_no_warning(self) -> None:
        app._DURABLE_PROBE.update(at=time.time(), ok=True, error=None)
        payload = self._readiness()
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["status"], "ready")

    def test_unknown_state_does_not_cry_wolf(self) -> None:
        """尚未探測完成時不得發警告——在確知之前不喊狼來了。"""
        app._DURABLE_PROBE.update(at=time.time(), ok=None, error=None)
        self.assertEqual(self._readiness()["warnings"], [])

    def test_probe_never_blocks_the_request_path(self) -> None:
        """請求路徑不得打網路：同步探測會拖慢每次回應並讓測試依賴外部網路。"""
        started = {}

        class FakeThread:
            def __init__(self, target=None, **kwargs):
                started["target"] = target

            def start(self):
                started["started"] = True

        with patch.object(app.threading, "Thread", FakeThread):
            with patch("company.model.current_state.load_current_state",
                       side_effect=AssertionError("探針不得在請求路徑同步執行")):
                result = app.durable_storage_probe()
        self.assertTrue(started.get("started"))
        self.assertIsNone(result["ok"])
        self.assertNotIn("running", result)

    def test_refresh_records_a_failure_without_raising(self) -> None:
        with patch("company.model.current_state.load_current_state",
                   return_value=(None, {"durable": False, "remote_error": "remote_read_http_401"})):
            app._refresh_durable_probe()
        self.assertIs(app._DURABLE_PROBE["ok"], False)
        self.assertEqual(app._DURABLE_PROBE["error"], "remote_read_http_401")
        self.assertFalse(app._DURABLE_PROBE["running"])


if __name__ == "__main__":
    unittest.main()
