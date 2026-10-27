"""Synthetic end-to-end guards; never access live portfolios or providers."""
import copy
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app
from test_repair_endpoints import server_url


def evaluation():
    return {"symbol": "2356.TW", "name": "測試代工", "as_of": "2026-10-23",
            "price": 60.5, "ma20": 59.1, "ma60": 62.7, "action": "accumulate",
            "quality_pass": True, "roe_ttm": 15.7, "valuation_pct": 29.3,
            "quality_schema": "ems", "eligible_pool": True,
            "distance_from_high_252": -0.29,
            "fundamental_trend": {"earnings_quality_ttm": 0.57,
                "earnings_quality_method": "ttm_sum_ocf_over_sum_ni_v1",
                "gross_margin_decline_2q": True, "operating_margin_decline_2q": False,
                "monthly_revenue_yoy_latest": 62.8, "monthly_revenue_negative_streak": 0,
                "quarterly_revenue_yoy": 44.6, "quarterly_eps_yoy": 78.6,
                "debt_ratio_change_yoy": 2.7}}


def state(row=None):
    row = row or evaluation()
    return {"as_of": "2026-10-23", "evaluations": [row], "top_picks": [row],
            "coverage": {"price_stale": 0}}


def freshness(status="current"):
    return {"status": status, "expected_completed_date": "2026-10-23", "message": status}


def test_cached_candidate_is_reclassified_before_rebuild_without_mutation():
    original = state()
    with patch.object(app, "current_state_freshness", return_value=freshness()):
        result = app.prepare_decision_state(original)
    assert result["top_picks"] == []
    assert result["evaluations"][0]["decision"] == "基本面待確認，暫緩新增"
    assert original["evaluations"][0]["action"] == "accumulate"


def test_legacy_ratio_and_stale_inputs_never_keep_buy_ranges():
    row = evaluation()
    del row["fundamental_trend"]["earnings_quality_method"]
    for source, f in ((state(row), freshness()), (state(), freshness("stale"))):
        with patch.object(app, "current_state_freshness", return_value=f):
            result = app.prepare_decision_state(source)
        item = result["evaluations"][0]
        assert item["decision_context"]["executable"] is False
        assert item["entry_range"] is None
        assert result["top_picks"] == []


def request(base, body=None, auth=True, origin=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if auth:
        headers["Authorization"] = "Bearer synthetic-owner"
    if origin:
        headers["Origin"] = origin
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + "/api/exit-progress", data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc), exc.headers


@contextmanager
def owner():
    with patch("company.model.positions.expected_sync_token", return_value="synthetic-owner"), \
         patch.object(app, "positions_sync_enabled", return_value=True), \
         patch.object(app, "allow_exit_progress_write", return_value=True):
        yield


def test_private_progress_auth_origin_schema_and_rate_limit():
    with owner(), patch("company.model.exit_progress.load_progress") as load, server_url() as base:
        assert request(base, auth=False)[0] == 401
        load.assert_not_called()
        assert request(base, {"action": "start", "symbol": "2356.TW", "version": 0}, origin="https://evil.test")[0] == 403
        assert request(base, {"action": "start", "symbol": "2356.TW", "version": 0, "price": 1})[0] == 400
        assert request(base, [], content_type="text/plain")[0] == 415
        assert request(base, {"action": "start", "symbol": "../secret", "version": 0})[0] == 400
        with patch.object(app, "allow_exit_progress_write", return_value=False):
            assert request(base, {"action": "start", "symbol": "2356.TW", "version": 0})[0] == 429


def test_failed_private_read_is_not_empty_success():
    with owner(), patch("company.model.exit_progress.load_progress", return_value=({}, {"available": False})), server_url() as base:
        code, data, headers = request(base)
        assert code == 503
        assert "plans" not in data
        assert headers["Cache-Control"] == "no-store"


def test_history_reports_full_exit_but_unavailable_inventory_is_never_zero():
    now = datetime.now(timezone.utc)
    plan = {"symbol": "2356.TW", "plan_id": "a" * 64, "status": "active", "original_shares": 1000,
            "created_at": now.isoformat(), "expires_at": (now + timedelta(days=7)).isoformat(),
            "basis_date": "2026-10-23", "completed_stages": [],
            "stages": [{"stage": 1, "trigger": "ma20", "label": "退出", "price": 60,
                        "fraction": 1.0, "target_shares": 1000}]}
    doc = {"schema_version": 1, "version": 1, "plans": {"2356.TW": plan}}
    with owner(), patch("company.model.exit_progress.load_progress", return_value=(doc, {"durable": True})), \
         patch.object(app, "private_progress_positions", return_value=[]), server_url() as base:
        code, data, _ = request(base)
        assert code == 200
        assert data["completion_reports"]["2356.TW"]["stage"] == 1
        assert data["completion_reports"]["2356.TW"]["requires_server_verification"] is True
    with owner(), patch("company.model.exit_progress.load_progress", return_value=(doc, {"durable": True})), \
         patch.object(app, "private_progress_positions", side_effect=ValueError("unavailable")), server_url() as base:
        code, data, _ = request(base)
        assert code == 200
        assert data["completion_reports"] == {}
        assert data["completion_reports_available"] is False


def test_start_uses_server_inventory_and_same_entry_anchor():
    holdings = [{"symbol": "2356.TW", "shares": 1000, "cost": 50}]
    timing = {"executable": True, "plan": [{"stage": 1, "price": 59, "fraction": 0.25}]}
    with owner(), patch.object(app, "private_progress_positions", return_value=holdings), \
         patch.object(app, "private_entry_summaries", return_value={"2356.TW": {"first_buy_date": "2026-09-01"}}) as entries, \
         patch.object(app, "build_sell_timing", return_value={"items": [{"symbol": "2356.TW", "timing": timing}]}) as build, \
         patch("company.model.exit_progress.start_plan", return_value=({"version": 1, "plans": {}}, {"durable": True})) as start, server_url() as base:
        code, data, _ = request(base, {"action": "start", "symbol": "2356.TW", "version": 0})
        assert code == 200 and data["automatic_trading"] is False
        assert build.call_args.args == (holdings, entries.return_value)
        assert start.call_args.args == ("2356.TW", timing, 1000, 0)


def test_completion_accepts_inventory_after_actual_reduction_and_final_zero():
    for fraction, holdings in ((0.25, [{"symbol": "2356.TW", "shares": 750, "cost": 50}]), (1.0, [])):
        frozen = {"plan_id": "a" * 64, "original_shares": 1000, "completed_stages": [],
                  "stages": [{"stage": 1, "fraction": fraction}], "status": "active"}
        document = {"version": 1, "plans": {"2356.TW": frozen}}
        with owner(), patch.object(app, "private_progress_positions", return_value=holdings), \
             patch.object(app, "private_entry_summaries", return_value=None), \
             patch.object(app, "build_sell_timing", return_value={"items": [{"symbol": "2356.TW", "timing": {}}]}), \
             patch("company.model.exit_progress.load_progress", return_value=(document, {"durable": True})), \
             patch("company.model.exit_progress.apply_progress", return_value={"executable": True, "plan": [{"stage": 1, "confirmed_break": True}]}), \
             patch("company.model.exit_progress.complete_stage", return_value=({"version": 2, "plans": {}}, {"durable": True})) as complete, server_url() as base:
            code, _, _ = request(base, {"action": "complete", "symbol": "2356.TW", "plan_id": "a" * 64,
                                        "version": 1, "stage": 1, "confirmed": True})
            assert code == 200
            complete.assert_called_once()


def test_unconfirmed_trigger_cannot_be_completed_even_with_valid_owner():
    frozen = {"plan_id": "a" * 64, "original_shares": 1000, "completed_stages": [],
              "stages": [{"stage": 1, "fraction": 0.25}]}
    with owner(), patch.object(app, "private_progress_positions", return_value=[{"symbol": "2356.TW", "shares": 1000}]), \
         patch.object(app, "private_entry_summaries", return_value=None), \
         patch.object(app, "build_sell_timing", return_value={"items": [{"symbol": "2356.TW", "timing": {}}]}), \
         patch("company.model.exit_progress.load_progress", return_value=({"version": 1, "plans": {"2356.TW": frozen}}, {"durable": True})), \
         patch("company.model.exit_progress.apply_progress", return_value={"executable": True, "plan": [{"stage": 1, "confirmed_break": False}]}), \
         patch("company.model.exit_progress.complete_stage") as complete, server_url() as base:
        assert request(base, {"action": "complete", "symbol": "2356.TW", "plan_id": "a" * 64,
                             "version": 1, "stage": 1, "confirmed": True})[0] == 400
        complete.assert_not_called()


def test_completion_uses_frozen_whole_share_target_not_fractional_percentage():
    frozen = {"plan_id": "a" * 64, "original_shares": 999, "completed_stages": [],
              "stages": [{"stage": 1, "fraction": 0.25, "target_shares": 249}]}
    with owner(), patch.object(app, "private_progress_positions", return_value=[{"symbol": "2356.TW", "shares": 750}]), \
         patch.object(app, "private_entry_summaries", return_value=None), \
         patch.object(app, "build_sell_timing", return_value={"items": [{"symbol": "2356.TW", "timing": {}}]}), \
         patch("company.model.exit_progress.load_progress", return_value=({"version": 1, "plans": {"2356.TW": frozen}}, {"durable": True})), \
         patch("company.model.exit_progress.apply_progress", return_value={"executable": True, "plan": [{"stage": 1, "confirmed_break": True}]}), \
         patch("company.model.exit_progress.complete_stage", return_value=({"version": 2, "plans": {}}, {"durable": True})) as complete, server_url() as base:
        assert request(base, {"action": "complete", "symbol": "2356.TW", "plan_id": "a" * 64,
                             "version": 1, "stage": 1, "confirmed": True})[0] == 200
        complete.assert_called_once()
