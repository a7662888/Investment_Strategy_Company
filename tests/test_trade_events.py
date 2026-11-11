"""合成資料：事件保留、識別、來源不確定性與官方買進明細。"""
from __future__ import annotations

import copy
import unittest
from datetime import date
from unittest.mock import patch

from company.data.broker_ledger import fetch_lots, fetch_realized, reconcile_lots
from company.model.trade_events import TradeEventsError, merge_events

NOW = "2026-11-11T10:00:00+00:00"
LATER = "2026-11-12T10:00:00+00:00"


def sale(dseq="SYNTH-A", **changes):
    return {"date": "2026-11-10", "symbol": "9999.TW", "shares": 100,
            "price": 20, "dseq": dseq, "seqno": "SYNTH-SEQ", "cond": "Cash", **changes}


def lot_doc(shares=100):
    return {"lots": {"9999.TW": [{"date": "2026-11-09", "shares": shares,
                                 "cost_per_share": 20, "dseq": "SYNTH-BUY", "fee": 1,
                                 "direction": "Buy", "cond": "Cash"}]},
            "status": {"9999.TW": "ok"}}


class Events(unittest.TestCase):
    def test_identical_values_with_distinct_external_ids_are_not_collapsed(self):
        doc = merge_events(None, None, {"trades": [sale("SYNTH-A"), sale("SYNTH-B")]}, "account", NOW)
        self.assertEqual(len(doc["events"]), 2)
        self.assertEqual(len({e["event_key"] for e in doc["events"]}), 2)
        again = merge_events(doc, None, {"trades": [sale("SYNTH-B"), sale("SYNTH-A")]}, "account", NOW)
        self.assertEqual(doc, again)

    def test_old_sales_survive_empty_window_and_sellout(self):
        first = merge_events(None, lot_doc(), {"trades": [sale()]}, "account", NOW)
        saved = copy.deepcopy(first)
        after = merge_events(first, {"lots": {}, "status": {}}, {"trades": []}, "account", "2028-01-01T10:00:00+00:00")
        self.assertEqual(first, saved)
        self.assertEqual(after["events"], first["events"])
        self.assertEqual(after["coverage_start"], "2026-11-09")

    def test_observation_time_only_for_unchanged_present_events(self):
        first = merge_events(None, None, {"trades": [sale("SYNTH-A"), sale("SYNTH-B")]}, "account", NOW)
        after = merge_events(first, None, {"trades": [sale("SYNTH-A")]}, "account", LATER)
        by_id = {e["dseq"]: e for e in after["events"]}
        self.assertEqual(by_id["SYNTH-A"]["last_seen_at"], LATER)
        self.assertEqual(by_id["SYNTH-B"]["last_seen_at"], NOW)
        self.assertEqual(by_id["SYNTH-A"]["first_seen_at"], NOW)

    def test_partial_sell_and_split_are_observations_not_new_buys(self):
        first = merge_events(None, lot_doc(), None, "account", NOW)
        split = lot_doc(200)
        split["lots"]["9999.TW"][0]["cost_per_share"] = 10
        after = merge_events(first, split, None, "account", LATER)
        self.assertEqual(len(after["events"]), 1)
        self.assertEqual(after["events"][0]["shares"], 100)
        self.assertFalse(after["events"][0]["flow_eligible"])
        self.assertEqual(after["issues"], ["SOURCE_CONTENT_CHANGED"])
        self.assertEqual(after["issue_details"], [{"code": "SOURCE_CONTENT_CHANGED", "date": "2026-11-09",
                                                  "symbol": "9999.TW", "source": "broker_lots"}])
        self.assertEqual(len(after["source_changes"]), 1)
        again = merge_events(after, split, None, "account", LATER)
        self.assertEqual(after, again)

    def test_missing_or_ambiguous_identity_never_uses_array_position(self):
        doc = merge_events(None, None, {"trades": [sale(dseq=""), sale(), sale()]}, "account", NOW)
        self.assertEqual(doc["events"], [])
        self.assertEqual(doc["issues"], ["AMBIGUOUS_EXTERNAL_ID", "STABLE_ID_MISSING"])
        self.assertEqual({i["date"] for i in doc["issue_details"]}, {"2026-11-10"})

    def test_unreconciled_unsupported_and_invalid_inputs_are_not_trades(self):
        lots = lot_doc()
        lots["status"]["9999.TW"] = "quantity_mismatch"
        doc = merge_events(None, lots, {"trades": [sale(cond="MarginTrading"), sale(price=float("inf")),
                                                   sale(shares=True), sale(date="2026-13-40")]}, "account", NOW)
        self.assertFalse(doc["events"])
        self.assertEqual(doc["issues"], ["INVALID_EVENT", "LOT_UNRECONCILED", "UNSUPPORTED_EVENT"])
        self.assertIn(None, [i["date"] for i in doc["issue_details"]])

    def test_account_switch_or_corrupt_previous_fail_closed(self):
        previous = merge_events(None, None, {"trades": [sale()]}, "account", NOW)
        with self.assertRaisesRegex(TradeEventsError, "ACCOUNT_BINDING_CHANGED"):
            merge_events(previous, None, {"trades": []}, "different", LATER)
        with self.assertRaisesRegex(TradeEventsError, "INVALID_EVENT_DOCUMENT"):
            merge_events({"events": []}, None, {"trades": []}, "account", NOW)

    def test_two_failed_sources_do_not_advance_success_timestamp(self):
        previous = merge_events(None, None, {"trades": [sale()]}, "account", NOW)
        with self.assertRaisesRegex(TradeEventsError, "EVENT_SOURCES_UNAVAILABLE"):
            merge_events(previous, None, None, "account", LATER)
        self.assertEqual(previous["last_success_at"], NOW)

    def test_unknown_fees_remain_null_and_entry_details_do_not_become_buys(self):
        trade = sale(entry_details=[{"date": "2026-10-10", "price": 10, "fee": 2, "tax": 0}])
        event = merge_events(None, None, {"trades": [trade]}, "account", NOW)["events"][0]
        self.assertEqual(event["side"], "sell")
        self.assertIsNone(event["fee"])
        self.assertIsNone(event["tax"])
        self.assertFalse(event["fees_complete"])
        self.assertEqual(event["entry_details"][0]["fee"], 2)

    def test_unconfirmed_direction_is_not_cashflow(self):
        event = merge_events(None, None, {"trades": [sale()]}, "account", NOW)["events"][0]
        self.assertFalse(event["flow_eligible"])
        self.assertEqual(event["direction_status"], "unconfirmed")
        confirmed = merge_events(None, None, {"trades": [sale(direction="Sell")]}, "account", NOW)["events"][0]
        self.assertTrue(confirmed["flow_eligible"])


class BrokerDetails(unittest.TestCase):
    def test_reconciled_lot_keeps_identity_and_remaining_scope(self):
        doc = reconcile_lots(100, 20, [{"date": "2026-11-09", "quantity": 100, "price": 20,
                                        "dseq": "SYNTH-BUY", "direction": "Buy", "cond": "Cash"}])
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["lots"][0]["dseq"], "SYNTH-BUY")
        self.assertEqual(doc["lots"][0]["quantity_scope"], "remaining_inventory")

    def test_same_symbol_position_rows_preserve_all_reconciled_lots(self):
        class Api:
            def list_positions(self, **kwargs):
                return [{"code": "9999", "id": i, "quantity": 100, "price": 20} for i in (1, 2)]

            def list_position_detail(self, **kwargs):
                return [{"date": "2026-11-09", "quantity": 100, "price": 20,
                         "dseq": f"SYNTH-{kwargs['detail_id']}"}]
        with patch("company.data.broker_ledger.time.sleep"):
            doc = fetch_lots(Api(), "synthetic", "Share", lambda c: c + ".TW")
        self.assertEqual(len(doc["lots"]["9999.TW"]), 2)

    def test_failure_of_one_position_row_rejects_partial_symbol(self):
        class Api:
            def list_positions(self, **kwargs):
                return [{"code": "9999", "id": i, "quantity": 100, "price": 20} for i in (1, 2, 3)]

            def list_position_detail(self, **kwargs):
                if kwargs["detail_id"] == 2:
                    raise RuntimeError("synthetic")
                return [{"date": "2026-11-09", "quantity": 100, "price": 20,
                         "dseq": f"SYNTH-{kwargs['detail_id']}"}]
        with patch("company.data.broker_ledger.time.sleep"):
            doc = fetch_lots(Api(), "synthetic", "Share", lambda c: c + ".TW")
        self.assertEqual(doc["lots"], {})
        self.assertEqual(doc["status"]["9999.TW"], "detail_query_failed")

    def test_realized_detail_uses_share_units_and_does_not_assign_entry_fees_to_sale(self):
        class Api:
            def list_profit_loss(self, **kwargs):
                return [{"id": 7, "code": "9999", "date": "2026-11-10", "quantity": 100,
                         "price": 20, "cond": "Cash", "dseq": "SYNTH-SELL", "seqno": "SYNTH-SEQ"}]

            def list_profit_loss_detail(self, **kwargs):
                self.kwargs = kwargs
                return [{"code": "9999", "date": "2026-11-09", "quantity": 100,
                         "price": 19, "cost": 1901, "fee": 1, "tax": 0, "dseq": "SYNTH-BUY",
                         "currency": "TWD", "ex_dividend_amt": 8, "cond": "Cash"}]

            def list_profit_loss_summary(self, **kwargs):
                raise RuntimeError("synthetic error")
        api = Api()
        with patch("company.data.broker_ledger.time.sleep"):
            result = fetch_realized(api, "synthetic", "Share", lambda c: c + ".TW", today=date(2026, 11, 11))
        trade = result["trades"][0]
        self.assertEqual(api.kwargs["unit"], "Share")
        self.assertEqual(api.kwargs["detail_id"], 7)
        self.assertNotIn("id", trade)
        self.assertEqual(trade["dseq"], "SYNTH-SELL")
        self.assertEqual(trade["detail_status"], "quantity_reconciled")
        self.assertEqual(trade["entry_details"][0]["fee"], 1)
        self.assertIsNone(trade["fee"])
        self.assertIsNone(trade["tax"])

    def test_detail_failure_preserves_sale_and_only_fixed_status(self):
        class Api:
            def list_profit_loss(self, **kwargs):
                return [{"id": 1, "code": "9999", "date": "2026-11-10", "quantity": 100,
                         "price": 20, "cond": "Cash", "dseq": "SYNTH-SELL"}]

            def list_profit_loss_detail(self, **kwargs):
                raise RuntimeError("do not retain this synthetic secret marker")

            def list_profit_loss_summary(self, **kwargs):
                return {}
        with patch("company.data.broker_ledger.time.sleep"):
            result = fetch_realized(Api(), "synthetic", "Share", lambda c: c + ".TW", today=date(2026, 11, 11))
        self.assertEqual(result["trades"][0]["detail_status"], "detail_query_failed")
        self.assertNotIn("secret marker", str(result))


if __name__ == "__main__":
    unittest.main()
