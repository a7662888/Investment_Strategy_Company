# -*- coding: utf-8 -*-
"""
D 首席風控官 / 審計 Sage。

職責:不操盤,只看硬指標與交易日誌,以『規則化旗標』抓策略邏輯漏洞,
產出 Dev_log 審計報告。LLM(Claude)接手時,在這些客觀旗標之上補敘事與優化建議
(見 company/roles/D_auditor.md 的 prompt 規格)。
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from . import attribution as ATTR
from . import metrics as M


def _detect_flags(result, m: dict) -> list[str]:
    flags = []

    if m["turnover"] > 12:
        flags.append(
            f"⚠️ 過度交易:年化週轉率 {m['turnover']:.1f}x,成本拖累 {m['cost_drag']:.2%},"
            "高頻邊際效益可能被手續費與證交稅吃掉。"
        )
    if m["max_drawdown"] < -0.30:
        flags.append(
            f"⚠️ 回撤過深:MDD {m['max_drawdown']:.1%},超過 30% 風險上限,"
            "停損紀律或部位控管需檢討。"
        )
    if m["sharpe"] < 0.5:
        flags.append(
            f"⚠️ 風險調整後報酬偏弱:Sharpe {m['sharpe']:.2f} < 0.5,"
            "報酬不足以補償波動。"
        )
    if m["profit_factor"] < 1.2 and m["num_trades"] > 10:
        flags.append(
            f"⚠️ 獲利因子偏低:{m['profit_factor']:.2f},賺賠比不健康,接近隨機。"
        )

    # 逆勢攤平偵測:在『已持有且帳上虧損』時又加碼買進(重建/月配不算)
    trades_by_sym = defaultdict(list)
    for t in result.trades:
        trades_by_sym[t.symbol].append(t)
    averaging_down = 0
    for sym, lst in trades_by_sym.items():
        lst.sort(key=lambda x: x.date)
        held, basis = 0.0, 0.0
        for t in lst:
            if t.side == "buy":
                if held > 0 and t.fill < basis * 0.97:  # 已套牢還加碼
                    averaging_down += 1
                basis = (held * basis + t.shares * t.fill) / (held + t.shares)
                held += t.shares
            else:  # sell
                held = max(0.0, held - t.shares)
                if held == 0:
                    basis = 0.0
    if averaging_down >= 3:
        flags.append(
            f"⚠️ 疑似逆勢攤平:偵測到 {averaging_down} 次『越跌越買』加碼,"
            "可能放大單一部位的尾部風險。"
        )

    if not flags:
        flags.append("✅ 未觸發重大風控旗標。")
    return flags


def audit_report(results: list, period_label: str) -> str:
    """產生對照式審計報告(markdown 字串)。"""
    named = {r.name: M.compute(r) for r in results}

    lines = [f"# D 審計報告 — {period_label}", ""]
    lines.append("## 一、硬指標對照(由程式計算,非主觀評分)")
    lines.append("")
    lines.append(M.to_table(named))
    lines.append("")

    # 勝負裁定:以 Calmar(報酬/回撤)為主,Sharpe 為輔
    winner = max(named, key=lambda n: (named[n]["calmar"], named[n]["sharpe"]))
    lines.append("## 二、勝負裁定")
    lines.append("")
    lines.append(
        f"本期最佳:**{winner}**(以 Calmar 為主、Sharpe 為輔的客觀排序)。"
    )
    lines.append("")

    lines.append("## 三、各策略風控旗標")
    for r in results:
        lines.append("")
        lines.append(f"### {r.name}")
        if getattr(r, "breaker_trips", 0):
            lines.append(
                f"- 🛑 組合熔斷:本期觸發 {r.breaker_trips} 次、持現金 {r.breaker_halted_days} 天"
                "(能力②:回撤超標時強制停損)。"
            )
        for f in _detect_flags(r, named[r.name]):
            lines.append(f"- {f}")

    lines.append("")
    lines.append("## 四、單筆交易貢獻度(能力③)")
    for r in results:
        lines.append("")
        lines.append(ATTR.to_markdown(r.name, ATTR.analyze(r.trades)))

    lines.append("")
    lines.append("## 五、給 Claude(D 人格)的接手提示")
    lines.append("")
    lines.append(
        "> 以上旗標為規則化偵測結果。請依 `company/roles/D_auditor.md` 的 prompt 規格,"
        "針對觸發旗標的策略提出具體 prompt/參數優化建議,並標注此優化僅可在『訓練期』"
        "套用、需於樣本外(walk-forward)重新驗證,嚴禁在測試期反覆調參(防線③)。"
    )
    return "\n".join(lines)
