# -*- coding: utf-8 -*-
"""The timing layer must not contradict the Exit Engine, and a previous close
must never be presented as an intraday trade."""
from __future__ import annotations

import app as appmod
from company.model.sell_timing import compute_sell_timing
from tests.test_sell_timing import complete_item, make_rows, trusted_exit


def broken_rows():
    return make_rows(close=90.0, high=91.0, low=89.0)


def timing(gain, exit_result=None, quote=None, item=None, rows=None):
    return compute_sell_timing(item=item or complete_item(price=90.0), exit_result=exit_result or trusted_exit(),
                               rows=broken_rows() if rows is None else rows, live_quote=quote, cost=100.0,
                               gain=gain, market_open=True)


def test_loss_position_with_zero_exit_fraction_is_a_review_not_a_sell_order():
    result = timing(-0.10)
    assert result["urgency"] == "review"
    assert "不是減碼指令" in result["headline"] and "執行" not in result["headline"]
    assert all("獲利保護" not in step["mode"] for step in result["plan"])


def test_failed_quality_asks_for_thesis_check():
    result = timing(-0.16, trusted_exit(fundamental_assessment={"status": "thesis_review"}))
    assert result["urgency"] == "review" and "核對投資論點" in result["headline"]


def test_profit_protection_and_requested_reduction_still_act():
    assert timing(0.20)["urgency"] == "act"
    assert timing(-0.10, trusted_exit(suggested_fraction=0.25))["urgency"] == "act"


def test_headline_stage_matches_the_plan_step():
    result = timing(0.20)
    first = result["plan"][0]
    assert first["label"] in result["headline"] and f"第 {first['stage']} 階段" in result["headline"]


def test_previous_close_quote_is_not_used_as_live_price():
    quote = {"regularMarketPrice": 90.0, "priceKind": "previous_close", "regularMarketTime": 1}
    result = timing(-0.10, quote=quote)
    assert result["live_price"] is None and "日線收盤" in result["price_basis"]


def test_bid_ask_mid_is_labelled():
    quote = {"regularMarketPrice": 91.5, "priceKind": "bid_ask_mid", "regularMarketTime": 1}
    result = timing(-0.10, quote=quote)
    assert result["live_price"] == 91.5 and "非成交價" in result["price_basis"]


def test_etf_uses_state_close_when_daily_bars_lag():
    item = complete_item(is_etf=True, as_of="2026-08-31", price=88.0)
    result = timing(-0.05, item=item, rows=make_rows(count=30))
    assert result["executable"] is True and result["last_close_date"] == "2026-08-31"
    assert result["last_close"] == 88.0 and result["urgency"] == "advisory"


def test_stock_with_lagging_bars_stays_blocked():
    item = complete_item(as_of="2026-08-31", price=88.0)
    assert timing(-0.05, item=item, rows=make_rows(count=30))["executable"] is False


def test_mis_price_order():
    assert appmod.mis_price({"z": "2265.0000", "pz": "-", "y": "2260"}) == (2265.0, "trade")
    assert appmod.mis_price({"z": "-", "pz": "2262", "y": "2260"}) == (2262.0, "trade")
    mid = {"z": "-", "pz": "-", "y": "2260.0000", "b": "2265.0000_2260.0000_", "a": "2270.0000_2275.0000_"}
    assert appmod.mis_price(mid) == (2267.5, "bid_ask_mid")
    assert appmod.mis_price({"z": "-", "pz": "-", "y": "2260", "b": "-", "a": "-"}) == (2260.0, "previous_close")
    assert appmod.mis_price({"z": "-", "pz": "-", "y": "2260", "b": "2265_", "a": "-"}) == (2265.0, "bid_ask_mid")
    assert appmod.mis_price({"z": "-", "pz": "-", "y": "-"})[0] is None
