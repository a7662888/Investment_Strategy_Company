from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from company.screener.codex_long_term_3_6m import _grade_action, load_pool_symbols


def test_codex_a_grade_requires_real_margin_of_safety():
    grade, label, action = _grade_action(82.0, 0.18, False)
    assert grade == "A"
    assert action == "ACCUMULATE"
    assert "長投" in label


def test_codex_high_score_without_value_is_watch_not_accumulate():
    grade, label, action = _grade_action(88.0, -0.05, False)
    assert grade == "B"
    assert action == "WAIT_FOR_VALUE"
    assert "拉回" in label


def test_codex_deep_overvaluation_is_avoid():
    grade, label, action = _grade_action(95.0, -0.30, False)
    assert grade == "D"
    assert action == "AVOID"
    assert "避開" in label


def test_pool_loader_returns_investable_rows():
    rows = load_pool_symbols(limit=5, cached_only=False)
    assert rows
    assert all(row["symbol"].endswith(".TW") for row in rows)


if __name__ == "__main__":
    test_codex_a_grade_requires_real_margin_of_safety()
    test_codex_high_score_without_value_is_watch_not_accumulate()
    test_codex_deep_overvaluation_is_avoid()
    test_pool_loader_returns_investable_rows()
    print("test_codex_long_term_3_6m: PASS")
