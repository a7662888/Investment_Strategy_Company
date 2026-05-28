# -*- coding: utf-8 -*-
"""
Claude Agent 選股端點測試(網路無關:monkeypatch fetch_history)。
驗證 claude_screen_candidates 回傳 context/picks/agent,且 schema 正常。
跑法:python tests/test_claude_screen.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod


def _fake_rows(seed: float):
    rows, px, d = [], 100.0 + seed, date(2025, 1, 1)
    for i in range(200):
        px *= 1.0 + (0.004 if (i + int(seed)) % 5 else -0.003)
        d += timedelta(days=1)
        rows.append({"date": d.isoformat(), "symbol": "X", "open": f"{px*0.997:.4f}",
                     "high": f"{px*1.01:.4f}", "low": f"{px*0.99:.4f}",
                     "close": f"{px:.4f}", "volume": "1000000"})
    return rows


def test_claude_screen():
    orig_fetch, orig_uni = appmod.fetch_history, appmod.DISCOVERY_UNIVERSE
    appmod.DISCOVERY_UNIVERSE = [
        {"symbol": "1111.TW", "name": "甲", "sector": "電子"},
        {"symbol": "2222.TW", "name": "乙", "sector": "金融"},
        {"symbol": "3333.TW", "name": "丙", "sector": "航運"},
    ]
    appmod.fetch_history = lambda sym, s, e: _fake_rows(hash(sym) % 17)
    try:
        res = appmod.claude_screen_candidates("2026-05-27", limit=5)
        assert "error" not in res, res.get("error")
        assert res["agent"].startswith("Claude")
        assert "regime" in res["context"]
        assert "picks" in res and isinstance(res["picks"], list)
        assert res["future_knowledge_used"] is False
        print(f"✅ Claude Agent 選股:regime={res['context']['regime_label']} "
              f"掃 {res['candidates_scored']} → 選 {len(res['picks'])}")
        for p in res["picks"]:
            assert "why_selected" in p and "probability_up" in p
        print("✅ picks schema 正常(含 why_selected / probability_up)")
    finally:
        appmod.fetch_history, appmod.DISCOVERY_UNIVERSE = orig_fetch, orig_uni


if __name__ == "__main__":
    test_claude_screen()
    print("✅ Claude Agent 選股端點測試通過")
