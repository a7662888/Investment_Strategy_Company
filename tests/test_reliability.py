import math
import unittest

from company.model.reliability import assessment


def complete(**updates):
    value = {"as_of": "2026-10-23", "price": 60.5, "ma20": 59.1, "ma60": 62.7,
             "valuation_pct": 29.3, "quality_pass": True}
    value.update(updates)
    return value


class ReliabilityTests(unittest.TestCase):
    def test_same_completed_day_is_verified(self):
        result = assessment(complete(), "2026-10-23")
        self.assertTrue(result["executable"])
        self.assertTrue(result["freshness_verified"])

    def test_historical_call_does_not_compare_wall_clock(self):
        result = assessment(complete())
        self.assertTrue(result["executable"])
        self.assertFalse(result["freshness_verified"])

    def test_missing_and_old_dates_are_not_current(self):
        self.assertEqual(assessment(complete(as_of=None), "2026-10-23")["status"], "insufficient")
        self.assertEqual(assessment(complete(), "2026-10-26")["status"], "stale")

    def test_explicit_noncurrent_context_cannot_be_overridden_by_valid_prices(self):
        for context in ("freshness", "decision_context", "reliability"):
            with self.subTest(context=context):
                self.assertEqual(assessment(complete(**{context: {"status": "stale"}}))["status"], "stale")
                self.assertFalse(assessment(complete(**{context: {"executable": False}}))["executable"])

    def test_nonfinite_or_invalid_price_and_valuation_are_not_safe(self):
        for field in ("price", "ma20", "ma60", "valuation_pct"):
            for value in (None, math.nan, math.inf, "not-a-number", True):
                with self.subTest(field=field, value=value):
                    self.assertFalse(assessment(complete(**{field: value}))["executable"])
        self.assertFalse(assessment(complete(valuation_pct=101))["executable"])
        self.assertFalse(assessment(complete(price=-1))["executable"])

    def test_unknown_quality_is_not_a_completed_hard_screen(self):
        self.assertFalse(assessment(complete(quality_pass=None))["executable"])
        self.assertTrue(assessment(complete(quality_pass=False))["executable"])

    def test_provenance_mismatch_is_blocked(self):
        result = assessment(complete(data_provenance={"price_date": "2026-10-22"}))
        self.assertEqual(result["status"], "stale")

    def test_etf_does_not_require_individual_company_valuation_or_roe(self):
        self.assertTrue(assessment(complete(is_etf=True, valuation_pct=None, quality_pass=None))["executable"])

    def test_etf_dca_does_not_require_stock_timing_moving_averages(self):
        item = {"is_etf": True, "dca": True, "price": 32.5, "as_of": "2026-10-23"}
        self.assertTrue(assessment(item, "2026-10-23")["executable"])
        self.assertFalse(assessment(item, "2026-10-26")["executable"])
        self.assertFalse(assessment({**item, "price": float("nan")})["executable"])
        self.assertFalse(assessment({**item, "decision_context": {"status": "stale"}})["executable"])
