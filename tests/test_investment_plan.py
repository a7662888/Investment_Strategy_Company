import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

import app as appmod
from company.model import investment_plan as plan_mod


def valid_plan(**overrides):
    plan = {"horizon_years": 10, "target_equity_pct": 70, "cash_amount": 300000,
            "rebalance_band_pct": 5, "max_single_stock_pct": 15,
            "reserve": {"total": 600000, "tranches": 3, "mode": "drawdown",
                        "drawdown_steps_pct": [20, 10, 30], "executed": 1}}
    plan.update(overrides)
    return plan


def test_normalize_sorts_steps_and_keeps_only_owner_values():
    plan = plan_mod.normalize_plan(valid_plan())
    assert plan["reserve"]["drawdown_steps_pct"] == [10.0, 20.0, 30.0]
    assert plan["target_equity_pct"] == 70.0 and plan["notes"] == ""
    monthly = plan_mod.normalize_plan(valid_plan(reserve={"total": 1, "tranches": 6, "mode": "monthly",
                                                          "start_month": "2026-11"}))
    assert monthly["reserve"]["drawdown_steps_pct"] == [] and monthly["reserve"]["executed"] == 0


@pytest.mark.parametrize("bad", [
    valid_plan(target_equity_pct=120),
    valid_plan(rebalance_band_pct=0),
    valid_plan(cash_amount=float("nan")),
    valid_plan(reserve={"tranches": 3, "mode": "drawdown", "drawdown_steps_pct": [10, 20]}),
    valid_plan(reserve={"tranches": 2, "mode": "weekly"}),
    valid_plan(reserve={"tranches": 2, "executed": 3}),
    valid_plan(reserve={"tranches": 2, "start_month": "2026-13"}),
    valid_plan(notes="x" * 501),
    "not a dict",
])
def test_invalid_plans_are_rejected(bad):
    with pytest.raises(ValueError):
        plan_mod.normalize_plan(bad)


def test_save_rejects_stale_version(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "LOCAL_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(plan_mod.durable_document, "_config", lambda: None)
    doc, _ = plan_mod.save_plan(valid_plan(), 0)
    assert doc["version"] == 1
    with pytest.raises(plan_mod.PlanConflict):
        plan_mod.save_plan(valid_plan(), 0)
    loaded, _ = plan_mod.load_plan()
    assert loaded["version"] == 1 and loaded["plan"]["target_equity_pct"] == 70.0


def test_plan_endpoint_requires_auth_and_rejects_foreign_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "LOCAL_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(plan_mod.durable_document, "_config", lambda: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/api/plan"
    try:
        with patch.dict(os.environ, {"POSITIONS_SYNC_TOKEN": "plan-key", "POSITIONS_SYNC_ENABLED": "1"}), \
                patch("company.model.positions.stored_sync_token", return_value=None):
            with pytest.raises(urllib.error.HTTPError) as unauth:
                urllib.request.urlopen(url, timeout=5)
            assert unauth.value.code == 401
            body = json.dumps({"plan": valid_plan(), "version": 0}).encode()
            headers = {"Authorization": "Bearer plan-key", "Content-Type": "application/json"}
            foreign = urllib.request.Request(url, data=body, method="POST",
                                             headers={**headers, "Origin": "https://evil.example"})
            with pytest.raises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(foreign, timeout=5)
            assert rejected.value.code == 403
            saved = json.loads(urllib.request.urlopen(
                urllib.request.Request(url, data=body, method="POST", headers=headers), timeout=5).read())
            assert saved["version"] == 1
            read = json.loads(urllib.request.urlopen(
                urllib.request.Request(url, headers={"Authorization": "Bearer plan-key"}), timeout=5).read())
            assert read["plan"]["reserve"]["mode"] == "drawdown"
            with pytest.raises(urllib.error.HTTPError) as conflict:
                urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST", headers=headers), timeout=5)
            assert conflict.value.code == 409
            bad = json.dumps({"plan": valid_plan(target_equity_pct=-1), "version": 1}).encode()
            with pytest.raises(urllib.error.HTTPError) as invalid:
                urllib.request.urlopen(urllib.request.Request(url, data=bad, method="POST", headers=headers), timeout=5)
            assert invalid.value.code == 400
    finally:
        server.shutdown()
