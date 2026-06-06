# -*- coding: utf-8 -*-
"""
app.py 整合最小測試(Codex 要求:啟動 / /api/health / next-day-plan schema + 模擬持股)。
不依賴外部網路:用合成 rows 直接測 plan schema 與校準模型 additive 欄位;
另在執行緒啟動伺服器測 /api/health。

跑法:python tests/test_app_integration.py
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod

REQUIRED_PLAN_KEYS = {
    "symbol", "as_of", "last_close", "held", "cost", "unrealized_gain",
    "score", "action", "reasons", "rule", "future_knowledge_used",
}


def _fake_rows(n: int = 200) -> list[dict]:
    rows = []
    px = 100.0
    d = date(2024, 1, 1)
    for i in range(n):
        px *= 1.0 + (0.004 if i % 5 else -0.003)  # 緩升帶回檔
        d += timedelta(days=1)
        rows.append({
            "date": d.isoformat(), "symbol": "9999.TW",
            "open": f"{px*0.997:.4f}", "high": f"{px*1.01:.4f}",
            "low": f"{px*0.99:.4f}", "close": f"{px:.4f}", "volume": "1000000",
        })
    return rows


def test_plan_schema_and_calibrated():
    rows = _fake_rows()
    plan = appmod.plan_next_session("9999.TW", rows, None)
    missing = REQUIRED_PLAN_KEYS - set(plan)
    assert not missing, f"next-day-plan 缺欄位:{missing}"
    # additive 校準模型欄位應存在(artifact 在 → enrich;不在 → 略過但 schema 仍完整)
    model = plan["model"]
    assert model["name"] == "interpretable_technical_ensemble_v1"
    if "calibrated_probability_up" in model:
        assert isinstance(model["calibrated_probability_up"], (int, float))
        assert model["calibrated_evidence"]  # 樣本外指標
    print("✅ next-day-plan schema 完整;校準欄位 additive 正常")


def test_held_position():
    rows = _fake_rows()
    plan = appmod.plan_next_session("9999.TW", rows, {"shares": 1000, "cost": 90.0})
    assert plan["held"] is True
    assert plan["unrealized_gain"] is not None
    print("✅ 模擬持股:held/unrealized_gain 正確")


def test_health_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            payload = json.loads(r.read().decode("utf-8"))
        assert payload["status"] == "ok"
        print(f"✅ /api/health 正常(port {port})")
    finally:
        server.shutdown()


def test_codex_v2_blocks_new_positions_on_red_market():
    rows = _fake_rows()
    analysis = appmod.analyze_candidate("9999.TW", rows, risk_level="RED")
    overlaid = appmod.apply_codex_v2_overlay("9999.TW", rows, analysis, {"risk_level": "RED"})
    assert overlaid["grade"] == "C"
    assert overlaid["action"] == "Codex v2: 禁買"
    assert overlaid["codex_decision_model"]["new_position_permission"] == "blocked"
    assert overlaid["codex_decision_model"]["vetoes"]
    print("✅ Codex v2 RED market blocks new positions")


if __name__ == "__main__":
    test_plan_schema_and_calibrated()
    test_held_position()
    test_codex_v2_blocks_new_positions_on_red_market()
    test_health_endpoint()
    print("✅ app 整合測試全數通過")
