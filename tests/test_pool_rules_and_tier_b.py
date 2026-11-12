"""規則式母池（pool_rules_v2）與 B 級並行觀察（tier_b_v1）。全部合成資料。"""
from datetime import date
from unittest.mock import patch

import pytest

from company.data import universe, universe_rules as rules, value_fundamentals
from company.model import daily_history
from company.model.value_daily import _daily_item, build_daily_state, plain_summary, turn_price
from company.screener.value_rescreen import TH, evaluate, tier_b_basis

TODAY = date(2026, 11, 12)


def basics(**over):
    rows = {
        "1111": {"name": "甲", "industry": "24", "listed": "2010-01-01", "shares": 1000.0, "suffix": ".TW"},
        "2222": {"name": "乙", "industry": "17", "listed": "2015-06-01", "shares": 500.0, "suffix": ".TWO"},
        "3333": {"name": "新掛牌", "industry": "24", "listed": "2025-01-02", "shares": 9000.0, "suffix": ".TW"},
        "4444": {"name": "存託憑證", "industry": "91", "listed": "2001-01-01", "shares": 9000.0, "suffix": ".TW"},
        "00999": {"name": "某ETF", "industry": "24", "listed": "2001-01-01", "shares": 9000.0, "suffix": ".TW"},
        "5555": {"name": "無報價", "industry": "24", "listed": "2001-01-01", "shares": 9000.0, "suffix": ".TW"},
    }
    rows.update(over)
    return rows


QUOTES = {code: {"close": 100.0, "day_turnover": 2e8} for code in ("1111", "2222", "3333", "4444", "00999")}


def test_candidates_keep_only_seasoned_common_stock_sorted_by_market_cap():
    out = rules.build_candidates(basics(), QUOTES, TODAY)
    assert [(r["symbol"], r["sector"], r["market_cap"]) for r in out] == [
        ("1111.TW", "半導體業", 100000.0), ("2222.TWO", "金融保險業", 50000.0)]


def test_listing_age_rule_spares_existing_members_only():
    # 上櫃轉上市會讓上市日期重新起算；既有成員的估值歷史其實連續。
    out = rules.build_candidates(basics(), QUOTES, TODAY, exempt_listing_age={"3333"})
    assert out[0]["symbol"] == "3333.TW"
    assert "3333.TW" not in {r["symbol"] for r in rules.build_candidates(basics(), QUOTES, TODAY)}


def test_unknown_industry_code_is_labelled_not_guessed():
    assert rules.sector_name("99") == "其他(99)" and rules.sector_name("1") == "水泥工業"


def _rows(n, sector="甲業", turnover=1e8, start=0):
    return [{"symbol": f"{1000 + start + i}.TW", "sector": sector, "avg_turnover": turnover,
             "market_cap": 1e6 - start - i} for i in range(n)]


def test_select_pool_applies_liquidity_floor_sector_cap_and_size():
    rows = _rows(4, "甲業") + _rows(4, "乙業", start=10) + _rows(2, "丙業", turnover=1e6, start=20)
    pool = universe.select_pool(rows, set(), n=5, cap=3, buffer_n=6)
    assert len(pool) == 5
    assert all(r["avg_turnover"] >= universe.MIN_AVG_TURNOVER for r in pool)      # 丙業流動性不足
    assert sum(r["sector"] == "甲業" for r in pool) == 3                          # 產業上限
    assert [r["market_cap"] for r in pool] == sorted((r["market_cap"] for r in pool), reverse=True)


def test_select_pool_buffer_keeps_member_slightly_outside_but_not_far_outside():
    rows = _rows(10)
    near, far = rows[5]["symbol"], rows[9]["symbol"]          # 名次 6 與 10；取 5 檔、緩衝到 7
    pool = {r["symbol"] for r in universe.select_pool(rows, {near, far}, n=5, cap=99, buffer_n=7)}
    assert near in pool and far not in pool and len(pool) == 5


def fail(key, value, threshold, higher=True, label="項目"):
    return [{"key": key, "label": label, "value": value, "threshold": threshold, "higher_is_better": higher}]


def eight_quarters(recent_ocf, older_ocf=100.0):
    periods = ("2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
               "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
    return [{"period": p, "operating_cash_flow": older_ocf if i < 4 else recent_ocf, "net_income": 100.0}
            for i, p in enumerate(periods)]


@pytest.mark.parametrize("items,schema,sector,expect", [
    (fail("debt", 61.2, 60.0, False), "generic", "半導體業", True),       # 差距在一成內
    (fail("debt", 67.6, 60.0, False), "generic", "半導體業", False),      # 超過一成
    (fail("debt", 71.6, 60.0, False), "generic", "電機機械", True),       # 資本密集產業放寬到 75
    (fail("debt", 77.7, 60.0, False), "generic", "建材營造業", False),
    (fail("roe", 11.7, 12.0), "generic", "通信網路業", True),
    (fail("roe", 10.4, 12.0), "generic", "電機機械", False),
    (fail("roe", 9.66, 12.0), "finhold", "金融保險業", True),             # 金融業 ROE 9%
    (fail("roe", 8.63, 12.0), "finhold", "金融保險業", False),
    (fail("gpm", 18.6, 20.0), "generic", "光電業", True),
    ([{"key": "cyclical", "label": "景氣子軌", "value": None, "threshold": None, "higher_is_better": True}],
     "cyclical", "水泥工業", False),                                       # 複合條件不放寬
    (fail("debt", 61.0, 60.0, False) + fail("roe", 11.9, 12.0), "generic", "半導體業", False),   # 只限差一項
])
def test_tier_b_rules_are_principled_and_single_failure_only(items, schema, sector, expect):
    assert bool(tier_b_basis(items, schema, sector, [])) is expect


def test_tier_b_cash_conversion_uses_eight_quarter_total():
    weak_recent = fail("eq", -0.2, 0.6)
    assert tier_b_basis(weak_recent, "generic", "半導體業", eight_quarters(-20.0, 160.0))   # 八季累計 0.70
    assert tier_b_basis(weak_recent, "generic", "半導體業", eight_quarters(-20.0, 60.0)) is None
    assert tier_b_basis(weak_recent, "generic", "半導體業", eight_quarters(-20.0)[:5]) is None  # 不足八季


def stock(debt=61.0, roe_q=4.0, gpm=30.0):
    periods = ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
    quarterly = [{"period": p, "operating_cash_flow": 100.0, "net_income": 100.0, "roe": roe_q,
                  "gross_profit_margin": gpm, "operating_income_margin": 15.0, "debt_ratio": debt} for p in periods]
    return {"quarterly": quarterly, "valuation": {"date": "2026-11-12", "pe": 10, "pb": 1.0,
            "pe_quantiles_5pct": [10 + i for i in range(21)], "pb_quantiles_5pct": [1 + i / 10 for i in range(21)]}}


def run(symbol, info, sector=None, price=60.0):
    prices = [{"date": "2026-11-12", "close": price, "adj_close": price} for _ in range(60)]
    with patch("company.screener.value_rescreen._yahoo_history", return_value=prices):
        return evaluate(symbol, {symbol.split(".")[0]: info}, sector)


def test_official_decision_is_unchanged_and_tier_b_runs_alongside():
    result = run("7777.TW", stock(debt=61.0), "半導體業")
    assert result["quality_pass"] is False and result["quality_tier"] == "B"      # 正式門檻沒改
    assert result["action"] == "watch" and result["relaxed_action"] == "accumulate"
    assert "負債比" in result["tier_b_basis"]
    assert TH["debt"] == 60.0 and TH["roe_ttm"] == 12.0
    clean = run("7777.TW", stock(debt=40.0), "半導體業")
    assert clean["quality_tier"] == "A" and "relaxed_action" not in clean
    bad = run("7777.TW", stock(debt=80.0), "半導體業")
    assert bad["quality_tier"] == "C" and "relaxed_action" not in bad


def test_new_financial_stock_uses_financial_schema_from_official_sector():
    info = stock()
    for row in info["quarterly"]:
        row.update(gross_profit_margin=None, operating_income_margin=None, debt_ratio=92.0, roe=2.5)
    result = run("2801.TW", info, "金融保險業")
    assert result["quality_schema"] == "finhold" and result["quality_missing"] == []
    assert result["valuation_basis"] == "PBR"
    assert result["quality_pass"] is False and result["quality_tier"] == "B"      # ROE 10% 介於 9 與 12
    # 沒有產業別（舊母池）時維持原行為，不猜測。
    assert run("2801.TW", info)["quality_schema"] == "generic"


def test_daily_item_keeps_formal_decision_and_adds_parallel_one():
    item = _daily_item(run("7777.TW", stock(debt=61.0), "半導體業"), True)
    assert item["decision"] == "未過品質篩，不新增"
    assert item["quality_tier"] == "B" and item["relaxed_decision"] == "可分批研究"
    assert "B 級並行觀察" in item["plain_summary"] and "不新買" in item["plain_summary"]
    plain = _daily_item(run("7777.TW", stock(debt=40.0), "半導體業"), True)
    assert plain["decision"] == "可分批研究" and plain["relaxed_decision"] is None
    assert plain["plain_summary"].startswith("體質過關")


def test_turn_price_is_lower_moving_average_only_in_downtrend():
    assert turn_price("下跌趨勢", 105.0, 98.4) == 98.4
    assert turn_price("整理", 105.0, 98.4) is None and turn_price("下跌趨勢", None, 98.4) is None
    text = plain_summary("等待止跌", "估值歷史低位（≤P20）", "下跌趨勢", 98.4, [], [])
    assert "還在跌" in text and "98.4" in text


@pytest.mark.parametrize("decision", ["定期定額", "可分批研究", "等待止跌", "高檔，等待拉回", "續列觀察", "等待更便宜",
                                      "高風險反轉觀察", "基本面待確認，暫緩新增", "未過品質篩，不新增", "資料不足"])
def test_every_decision_has_a_plain_sentence(decision):
    text = plain_summary(decision, "合理區（P40–P70）", "整理", None, ["負債比 70.0>60.0"], ["毛利率連兩季下降"])
    assert text.endswith("。") and len(text) >= 12


def test_state_reports_tier_b_without_touching_top_picks():
    results = [run("7777.TW", stock(debt=61.0), "半導體業"), run("8888.TW", stock(debt=40.0), "半導體業")]
    state = build_daily_state(results, {"7777", "8888"}, 2)
    assert [p["symbol"] for p in state["top_picks"]] == ["8888.TW"]
    assert state["tier_b"]["count"] == 1
    assert [p["symbol"] for p in state["tier_b"]["candidates"]] == ["7777.TW"]
    assert len(state["tier_b"]["rules"]) == 4


def test_daily_history_keeps_parallel_track_fields():
    item = _daily_item(run("7777.TW", stock(debt=61.0), "半導體業"), True)
    saved = daily_history._item(item)
    assert saved["quality_tier"] == "B" and saved["relaxed_decision"] == "可分批研究" and saved["tier_b_basis"]


def test_fundamentals_fallback_uses_market_suffix_from_pool(monkeypatch):
    seen = []
    monkeypatch.setattr(value_fundamentals, "_yahoo_timeseries", lambda symbol, types, years=6: seen.append(symbol) or {})
    monkeypatch.setattr(value_fundamentals, "_yahoo_prices", lambda symbol, days=1300: [])
    monkeypatch.setattr(value_fundamentals, "fetch_stock",
                        lambda code, name, today=None: value_fundamentals.fetch_stock_yahoo(code, name))
    value_fundamentals.refresh_pool({"stocks": [{"symbol": "5347.TWO", "name": "世界"}]}, {}, batch_size=1)
    assert seen == ["5347.TWO"]


def test_daily_item_is_idempotent_because_the_api_reprocesses_saved_items():
    once = _daily_item(run("7777.TW", stock(debt=61.0), "半導體業"), True)
    twice = _daily_item(once, True)
    for key in ("decision", "quality_tier", "relaxed_decision", "tier_b_basis", "plain_summary", "turn_price"):
        assert twice[key] == once[key], key
    assert twice["relaxed_decision"] == "可分批研究"


def test_stale_state_hides_parallel_decision_too():
    import app
    item = _daily_item(run("7777.TW", stock(debt=61.0), "半導體業"), True)
    prepared = app.prepare_decision_state({"as_of": "2020-01-02", "evaluations": [item], "top_picks": []})
    assert prepared["freshness"]["status"] == "stale"
    assert prepared["evaluations"][0]["relaxed_decision"] is None
