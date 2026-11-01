from datetime import date, timedelta

from company.model.drawdown_stats import drawdown_profile, repair_series


def series(prices, start=date(2020, 1, 1)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "adj_close": p} for i, p in enumerate(prices)]


def flat_then(*moves):
    prices = [100.0] * 300
    for target, steps in moves:
        current = prices[-1]
        prices += [current + (target - current) * (k + 1) / steps for k in range(steps)]
    return prices


def test_episode_counts_recovery_and_current_drawdown():
    # -25% then full recovery, then -12% still open.
    rows = series(flat_then((75.0, 30), (101.0, 40), (88.88, 20)))
    profile = drawdown_profile(rows)
    assert profile["available"] is True
    bands = {b["threshold"]: b for b in profile["bands"]}
    assert bands[0.10]["episodes"] == 2 and bands[0.10]["recovered"] == 1
    assert bands[0.20]["episodes"] == 1 and bands[0.20]["recovered"] == 1
    assert bands[0.30]["episodes"] == 0 and bands[0.30]["median_days_to_new_high"] is None
    assert round(profile["current_drawdown"], 2) == -0.12
    assert profile["days_since_peak"] > 0


def test_unadjusted_split_is_back_adjusted_not_counted_as_crash():
    prices = [100.0] * 300 + [25.0] * 10 + [26.0] * 5
    profile = drawdown_profile(series(prices))
    assert profile["data_repairs"]["splits_adjusted"]
    assert all(b["episodes"] == 0 for b in profile["bands"])
    assert profile["current_drawdown"] == 0


def test_impossible_daily_move_trims_history_to_clean_tail():
    prices = [50.0] * 20 + [60.0] + [60.0] * 300
    rows, repairs = repair_series(series(prices))
    assert repairs["anomaly_days"] and rows[0]["date"] == repairs["anomalies_dropped_before"]
    assert len(rows) == 301


def test_short_or_invalid_history_is_unavailable():
    assert drawdown_profile(series([100.0] * 100))["available"] is False
    bad = series([100.0] * 300) + [{"date": "2021-01-01", "adj_close": float("nan")}]
    assert drawdown_profile(bad)["available"] is True
