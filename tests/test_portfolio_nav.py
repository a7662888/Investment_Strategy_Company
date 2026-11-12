"""僅合成庫存、成交、股息與行情；不讀正式帳戶。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import patch

import pytest

from company.data import broker_positions
from company.model import portfolio_nav as nav
from company.model import private_history_store as store

KEY = "a" * 64
DAY = "2026-11-11"
NOW = datetime.fromisoformat(DAY + "T08:30:00+00:00")
SYM = "7777.TW"


def inputs(shares=10, price=10, cost=8, day=DAY):
    stamp = day + "T08:00:00+00:00"
    snapshot = {"account_key": KEY, "as_of": stamp, "positions": [] if not shares else [
        {"symbol": SYM, "shares": shares, "cost": cost}],
        "source": "shioaji", "simulation": False, "unit": "Share"}
    lots = {"account_key": KEY, "as_of": stamp, "status": {SYM: "ok"} if shares else {},
            "lots": {SYM: [{"date": "2026-11-02", "shares": shares, "cost_per_share": cost, "dseq": "synthetic"}]} if shares else {}}
    realized = {"account_key": KEY, "as_of": stamp, "trades": [], "range": ["2026-01-01", day]}
    histories = {SYM: [{"date": day, "close": price}], "0050.TW": [
        {"date": "2026-11-10", "close": 100, "adj_close": 90},
        {"date": day, "close": 102, "adj_close": 91.8}]}
    actions = {"complete": True, "dividends": [], "splits": []}
    return snapshot, lots, realized, {"events": [], "issues": []}, histories, actions


def snap(shares=10, price=10, cost=8, previous=None, **extra):
    args = list(inputs(shares, price, cost))
    for k, v in extra.items():
        args[{"snapshot": 0, "lots": 1, "realized": 2, "events": 3, "histories": 4, "actions": 5}[k]] = v
    return nav.make_snapshot(*args, previous, NOW)


def prior(shares=10, price=10, cost=8):
    row = snap(shares, price, cost)
    row["date"] = "2026-11-10"
    return row


def test_flat_and_buy_sell_cashflow_formula():
    prev = prior()
    events = {"events": [{"date": DAY, "symbol": SYM, "side": "buy", "shares": 5, "price": 11, "flow_eligible": True}]}
    row = snap(15, 11, previous=prev, events=events)
    result = nav.calculate_returns([prev, row], inputs()[4]["0050.TW"])
    assert result[-1]["buy_amount"] == 55
    assert result[-1]["daily_return"] == pytest.approx(.1)
    assert result[-1]["benchmark_return"] == pytest.approx(.02)
    assert result[-1]["cashflow_benchmark_value"] == pytest.approx(157)
    assert result[-1]["cashflow_benchmark_return"] == pytest.approx(157 / 155 - 1)
    # 同日賣出，賣出金流抵消部位變小，不能當作虧損。
    sell = {"events": [{"date": DAY, "symbol": SYM, "side": "sell", "shares": 5, "price": 11, "flow_eligible": True}]}
    result = nav.calculate_returns([prev, snap(5, 11, previous=prev, events=sell)], inputs()[4]["0050.TW"])
    assert result[-1]["daily_return"] == pytest.approx(.1)
    assert result[-1]["sell_amount"] == 55
    assert result[-1]["cashflow_benchmark_value"] == pytest.approx(47)


def test_ex_dividend_uses_previous_shares_and_benchmark_adjustment():
    prev = prior()
    actions = {"complete": True, "dividends": [{"symbol": SYM, "date": DAY, "amount_per_share": 1}], "splits": []}
    row = snap(10, 9, previous=prev, actions=actions)
    result = nav.calculate_returns([prev, row], [
        {"date": prev["date"], "close": 100, "adj_close": 90},
        {"date": DAY, "close": 90, "adj_close": 90}])
    assert result[-1]["dividend_amount"] == 10
    assert result[-1]["daily_return"] == 0
    assert result[-1]["benchmark_return"] == 0


def test_split_is_no_trade_no_flow_no_artificial_profit():
    prev = prior(10, 100, 80)
    actions = {"complete": True, "dividends": [], "splits": [{"symbol": SYM, "date": DAY, "ratio": 4}]}
    row = snap(40, 25, 20, previous=prev, actions=actions)
    result = nav.calculate_returns([prev, row], inputs()[4]["0050.TW"])
    assert row["flow_complete"] and row["buy_amount"] == row["sell_amount"] == 0
    assert result[-1]["daily_return"] == 0


@pytest.mark.parametrize("reason", ["unknown_buy", "unknown_dividend", "provisional", "unreconciled_buy", "missing_session"])
def test_incomplete_evidence_never_becomes_zero_return(reason):
    prev, args = prior(), list(inputs())
    if reason == "unknown_buy":
        args = list(inputs(15))
    elif reason == "unknown_dividend":
        args[5]["complete"] = False
    elif reason == "provisional":
        args[4][SYM][0]["date"] = "2026-11-10"
    elif reason == "unreconciled_buy":
        # 明細對不上的標的當日股數變了：沒有可信金流，不能算報酬。
        args = list(inputs(15))
        args[1]["status"][SYM] = "quantity_mismatch"
        args[1]["lots"] = {}
    else:
        args[4]["0050.TW"].insert(1, {"date": "2026-11-09", "adj_close": 90})
        prev["date"] = "2026-11-06"
    row = nav.make_snapshot(*args, prev, NOW)
    result = nav.calculate_returns([prev, row], args[4]["0050.TW"])
    assert result[-1]["daily_return"] is None and result[-1]["cumulative_return"] is None


def test_zero_value_restarts_and_never_borrows_benchmark():
    prev = prior()
    sell = {"events": [{"date": DAY, "symbol": SYM, "side": "sell", "shares": 10, "price": 11, "flow_eligible": True}]}
    row = snap(0, previous=prev, events=sell)
    result = nav.calculate_returns([prev, row], inputs()[4]["0050.TW"])
    assert result[-1]["daily_return"] == pytest.approx(.1)
    assert result[-1]["cashflow_benchmark_value"] is None  # 0050 不夠賣，不能借款。
    base = deepcopy(prev)
    base.update(date="2026-11-12", buy_amount=100, sell_amount=0, dividend_amount=0, flow_complete=True)
    result = nav.calculate_returns([prev, row, base], inputs()[4]["0050.TW"])
    assert result[-1]["return_status"] == "reset_base"
    assert result[-1]["return_start"] == base["date"]
    assert result[-1]["daily_return"] is None and result[-1]["cumulative_return"] == 0


def test_missing_adjusted_price_stays_missing():
    prev = prior()
    result = nav.calculate_returns([prev, snap(previous=prev)], [
        {"date": prev["date"], "close": 100}, {"date": DAY, "close": 101}])
    assert result[-1]["daily_return"] == 0
    assert result[-1]["benchmark_return"] is None
    assert result[-1]["cashflow_benchmark_value"] is None


def test_daily_freeze_and_account_binding():
    row = snap()
    history = inputs()[4]["0050.TW"]
    doc = nav.merge_nav(None, row, KEY, history, NOW.isoformat())
    updated = deepcopy(row)
    updated["market_value"] = 99999
    assert nav.merge_nav(doc, updated, KEY, history, NOW.isoformat()) == doc
    with pytest.raises(nav.NavError, match="ACCOUNT_BINDING_CHANGED"):
        nav.merge_nav(doc, row, "b" * 64, history, NOW.isoformat())
    old_row = deepcopy(row)
    old_row["date"] = "2026-11-09"
    with pytest.raises(nav.NavError, match="HISTORICAL_INVENTORY_FORBIDDEN"):
        nav.merge_nav(doc, old_row, KEY, history, NOW.isoformat())


def test_previous_benchmark_results_survive_later_missing_history():
    prev = prior()
    history = inputs()[4]["0050.TW"]
    doc = nav.merge_nav(None, prev, KEY, history, NOW.isoformat())
    doc = nav.merge_nav(doc, snap(previous=prev), KEY, history, NOW.isoformat())
    frozen = deepcopy(doc["rows"])
    nextrow = deepcopy(doc["rows"][-1])
    nextrow.update(date="2026-11-12", flow_complete=True)
    # 下一次取數漏掉所有舊日期，但不能抹掉舊列已成功的報酬。
    updated = nav.merge_nav(doc, nextrow, KEY, [{"date": "2026-11-12", "adj_close": 100}], NOW.isoformat())
    assert updated["rows"][:2] == frozen
    assert updated["rows"][-1]["benchmark_return"] is None


def test_old_remaining_lot_change_does_not_block_new_flat_day():
    events = {"events": [], "issues": ["SOURCE_CONTENT_CHANGED"], "issue_details": [
        {"code": "SOURCE_CONTENT_CHANGED", "date": "2026-11-02", "source": "broker_lots"}]}
    row = snap(previous=prior(), events=events)
    assert row["flow_complete"] is True
    events["issue_details"] = [{"code": "AMBIGUOUS_EXTERNAL_ID", "date": DAY, "source": "broker_realized"}]
    assert snap(previous=prior(), events=events)["flow_complete"] is False


@pytest.mark.parametrize("fault", ["stale_inventory", "future_inventory", "intraday", "stale_lots", "bad_account", "bad_price", "nan_cost"])
def test_rejects_invalid_snapshot(fault):
    args, now = list(inputs()), NOW
    if fault == "stale_inventory":
        args[0]["as_of"] = "2026-11-10T08:00:00+00:00"
    elif fault == "future_inventory":
        args[0]["as_of"] = "2026-11-11T09:00:00+00:00"
    elif fault == "intraday":
        now = datetime.fromisoformat(DAY + "T04:00:00+00:00")
    elif fault == "stale_lots":
        args[1]["as_of"] = "2026-11-10T08:00:00+00:00"
    elif fault == "bad_account":
        args[2]["account_key"] = "different"
    elif fault == "bad_price":
        args[4][SYM][0]["close"] = float("inf")
    else:
        args[0]["positions"][0]["cost"] = float("nan")
    with pytest.raises(nav.NavError):
        nav.make_snapshot(*args, None, now)


def test_refresh_failure_retains_success_and_reports_safe_code(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_positions, "LOCAL_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr(store.store, "_config", lambda: None)
    args = inputs()
    def history(symbol, start, end):
        return args[4].get(symbol, [])
    report = nav.record_refresh(args[0], args[1], args[2], history, lambda s, d: args[5], now=NOW)
    assert report["status"]["ok"] is True
    frozen = (tmp_path / "portfolio_nav.json").read_bytes()
    report = nav.record_refresh(None, None, None, history, lambda s, d: args[5], now=NOW)
    assert report["error_code"] == "INVENTORY_UNAVAILABLE"
    assert (tmp_path / "portfolio_nav.json").read_bytes() == frozen
    public = nav.load_public("nav")
    assert public["last_success_at"] == NOW.isoformat() and public["data_days"] == 1
    assert "account_key" not in public
    assert public["last_attempt"]["ok"] is False


def test_refresh_second_day_and_rerun_are_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_positions, "LOCAL_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr(store.store, "_config", lambda: None)
    args = inputs()
    def history(symbol, start, end):
        return args[4].get(symbol, [])
    assert "error_code" not in nav.record_refresh(args[0], args[1], args[2], history, lambda s, d: args[5], now=NOW)
    old = nav.load_public("nav")
    # 已有當日值時不再取公開行情，避免盤中合併或新版本改寫。
    report = nav.record_refresh(args[0], args[1], args[2], lambda *a: (_ for _ in ()).throw(Exception("private")), lambda *a: {}, now=NOW)
    assert report["nav"]["unchanged"] is True
    new = nav.load_public("nav")
    assert old["rows"] == new["rows"]


@pytest.mark.parametrize("reason", ["cost_mismatch", "quantity_mismatch", "no_detail", "detail_shares_differ"])
def test_unreconciled_lot_detail_is_flagged_but_never_changes_value_or_return(reason):
    """實例（2026-11-12）：12 檔中 3 檔明細對不上，整天的市值快照被擋下。
    市值來自券商庫存股數，明細只是輔助；股數沒變的日子金流確定為零，報酬照算。"""
    prev, args = prior(), list(inputs(10, 11))
    if reason == "detail_shares_differ":
        args[1]["lots"][SYM][0]["shares"] = 9
    else:
        args[1]["status"][SYM] = reason
        args[1]["lots"] = {}
    row = nav.make_snapshot(*args, prev, NOW)
    assert row["market_value"] == 110 and row["total_cost"] == 80
    assert row["inventory_reconciled"] is False
    assert row["lot_reconciliation"] == {"ok": 0, "unreconciled": 1}
    assert row["flow_complete"] is True and row["buy_amount"] == row["sell_amount"] == 0
    result = nav.calculate_returns([prev, row], args[4]["0050.TW"])
    assert result[-1]["daily_return"] == pytest.approx(.1)


def test_fully_reconciled_row_reports_zero_gap():
    row = snap()
    assert row["inventory_reconciled"] is True
    assert row["lot_reconciliation"] == {"ok": 1, "unreconciled": 0}


def test_unreconciled_refresh_still_saves_the_daily_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_positions, "LOCAL_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr(store.store, "_config", lambda: None)
    args = inputs()
    args[1]["lots"][SYM][0]["shares"] = 9
    report = nav.record_refresh(args[0], args[1], args[2], lambda s, *a: args[4].get(s, []),
                               lambda *a: args[5], now=NOW)
    assert "error_code" not in report and report["status"]["ok"] is True
    public = nav.load_public("nav")
    assert public["data_days"] == 1 and public["last_attempt"]["ok"] is True
    assert public["rows"][0]["inventory_reconciled"] is False
    assert public["rows"][0]["lot_reconciliation"]["unreconciled"] == 1


def test_runner_history_failure_does_not_change_inventory_exit(capsys):
    import run_broker_positions as runner
    with patch.object(runner, "_fetch", return_value={"positions": []}), patch.object(
        runner.broker, "record_broker_attempt", return_value={"durable": True}), patch.object(
        runner, "_save_ledger", return_value={"lots": {"durable": True}, "realized": {"durable": True}}), patch.object(
        runner, "_save_portfolio_history", side_effect=Exception("synthetic-private-value")):
        assert runner.main(["--fetch", "--require-ledger", "--portfolio-history"]) == 0
    log = capsys.readouterr().out
    assert "synthetic-private-value" not in log
    assert json.loads(log)["portfolio_history"]["error_code"] == "PORTFOLIO_HISTORY_FAILED"
