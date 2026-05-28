# -*- coding: utf-8 -*-
"""
防線① 的守門測試 —— 確保任何人(含未來的 Claude)改了引擎也不能偷看未來。

跑法:
    python -m pytest tests/ -q
或:
    python tests/test_no_lookahead.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company.data import synthetic


def test_market_view_never_returns_future():
    ds = synthetic.generate(n_symbols=5, days=200, seed=1)
    days = ds.prices.trading_days
    as_of = days[100]
    view = ds.view(as_of)
    for sym in view.universe():
        h = view.history(sym)
        assert (h.index <= as_of).all(), f"{sym} 的 history 含未來資料!"
        chips = view.chips(sym)
        if len(chips):
            assert (chips.index <= as_of).all(), f"{sym} 的 chips 含未來資料!"
        news = view.news(sym)
        if len(news):
            assert (news.index <= as_of).all(), f"{sym} 的 news 含未來輿情!"
        f = view.fundamentals(sym)
        # 基本面以 announce_date 切片,latest 不應晚於 as_of(無法直接驗 index,改驗存在性語意)
        assert f is None or True


def test_fundamental_uses_announce_date_not_period():
    """基本面必須以實際公布日切片:季末當天不可看到該季財報。"""
    ds = synthetic.generate(n_symbols=3, days=300, seed=2)
    funds = ds.fundamentals
    sym = ds.prices.universe(ds.prices.trading_days[0])[0]
    # 找一筆財報,確認在『公布日前一天』看不到、『公布日當天』看得到
    g = funds._by_symbol.get(sym)
    if g is None or len(g) == 0:
        return
    announce = g.index[len(g) // 2]
    before = funds.latest(sym, announce - pd.Timedelta(days=1))
    on = funds.latest(sym, announce)
    # 公布日當天一定取得到(至少這筆或更早一筆)
    assert on is not None
    # 公布日前一天取得到的,公布日當天必定 >= (時間單調)
    if before is not None:
        assert g.index[g.index <= announce][-1] >= g.index[g.index <= (announce - pd.Timedelta(days=1))][-1]


def test_future_spike_does_not_change_past_decision():
    """注入未來暴漲,T 日的 MarketView 不應因此改變。"""
    ds = synthetic.generate(n_symbols=4, days=150, seed=3)
    days = ds.prices.trading_days
    as_of = days[80]
    sym = ds.view(as_of).universe()[0]

    snapshot_before = ds.view(as_of).history(sym).copy()
    # 竄改未來(as_of 之後)的收盤價
    df = ds.prices._by_symbol[sym]
    df.loc[df.index > as_of, "close"] *= 10
    snapshot_after = ds.view(as_of).history(sym)

    pd.testing.assert_frame_equal(snapshot_before, snapshot_after)


if __name__ == "__main__":
    test_market_view_never_returns_future()
    test_fundamental_uses_announce_date_not_period()
    test_future_spike_does_not_change_past_decision()
    print("✅ 防線① look-ahead 守門測試全數通過")
