"""Offline validation regressions for manual portfolios."""
import unittest

from company.model import positions


class PositionValidationTests(unittest.TestCase):
    def test_nonfinite_or_nonpositive_values_are_rejected(self):
        for field in ("shares", "cost"):
            for value in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", 0, -1, 10 ** 1000):
                candidate = {"symbol": "1234.TW", "shares": 1, "cost": 2, field: value}
                with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(ValueError):
                    positions.normalize_positions([candidate])

    def test_oversized_input_rejected_before_deduplication(self):
        row = {"symbol": "1234.TW", "shares": 1, "cost": 2}
        with self.assertRaisesRegex(ValueError, "too many positions"):
            positions.normalize_positions([row] * (positions.MAX_POSITIONS + 1))
        self.assertEqual(len(positions.normalize_positions([row] * positions.MAX_POSITIONS)), 1)

    def test_duplicate_last_wins_semantics_remain(self):
        result = positions.normalize_positions([
            {"symbol": "1234.tw", "shares": "1", "cost": "2"},
            {"symbol": "1234.TW", "shares": 3, "cost": 4},
        ])
        self.assertEqual(result, [{"symbol": "1234.TW", "shares": 3.0, "cost": 4.0}])


if __name__ == "__main__":
    unittest.main()
