"""Attributed market rankings; descriptive evidence, never a trading signal."""
from __future__ import annotations

import math


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _code(symbol) -> str:
    return str(symbol or "").split(".")[0]


def regulatory_flags(regulatory: dict | None) -> dict[str, list[str]]:
    """處置股／注意股標記：{代號: [說明]}。僅作風險提示，不改判定。"""
    flags: dict[str, list[str]] = {}
    for row in (regulatory or {}).get("punish") or []:
        period = f"{row.get('start_date') or '?'}～{row.get('end_date') or '?'}"
        flags.setdefault(str(row.get("code")), []).append(f"處置股 {period}")
    for row in (regulatory or {}).get("notice") or []:
        flags.setdefault(str(row.get("code")), []).append("注意股")
    return flags


def build_market_research(state: dict, snapshots: dict, scanners: dict | None = None,
                          regulatory: dict | None = None) -> dict:
    as_of = state.get("as_of")
    evaluated = {row.get("symbol"): row for row in state.get("evaluations", [])}
    by_code = {_code(symbol): row for symbol, row in evaluated.items()}
    flags = regulatory_flags(regulatory)
    rows = []
    for symbol, snap in snapshots.items():
        if snap.get("trade_date") != as_of:
            continue
        item = evaluated.get(symbol, {})
        row = {key: snap.get(key) for key in (
            "close", "change_rate", "total_volume", "total_amount", "volume_ratio",
            "rvol20", "rvol_days", "close_vs_vwap_pct", "spread_pct", "trade_date", "source",
        )}
        row.update(symbol=symbol, name=item.get("name") or snap.get("name"),
                   eligible_pool=item.get("eligible_pool", False),
                   decision=item.get("decision", "未納入品質評估"),
                   regulatory=flags.get(_code(symbol), []))
        rows.append(row)

    def ranked(key, reverse=True):
        valid = [row for row in rows if number(row.get(key)) is not None]
        return sorted(valid, key=lambda row: number(row[key]), reverse=reverse)[:20]

    market_scan = scanners if scanners and scanners.get("trade_date") == as_of else None
    if market_scan:
        # 全市場掃描列只有代號；有在母池的就帶上品質判定，沒有的明示「未在母池」，
        # 不再一律顯示「須另核對基本面」。
        enriched = {}
        for label, group in (market_scan.get("rankings") or {}).items():
            enriched[label] = []
            seen = set()
            for scan_row in group or []:
                # 實測永豐掃描偶有同一代號重複兩列（2026-10-08 成交值排行 2330、2408），只留第一列。
                if str(scan_row.get("code")) in seen:
                    continue
                seen.add(str(scan_row.get("code")))
                item = by_code.get(str(scan_row.get("code")))
                enriched[label].append({
                    **scan_row,
                    "decision": item.get("decision") if item else "未在母池（無品質判定）",
                    "in_pool": bool(item),
                    "regulatory": flags.get(str(scan_row.get("code")), []),
                })
        market_scan = {**market_scan, "rankings": enriched}
    rvol_days = max((number(r.get("rvol_days")) or 0 for r in rows), default=0)
    return {
        "as_of": as_of, "scope": "mother-pool-and-etf-subpool", "coverage": len(rows),
        "rankings": {"rvol20": ranked("rvol20"), "amount": ranked("total_amount"),
                     "volume": ranked("total_volume"),
                     "gainers": ranked("change_rate"), "losers": ranked("change_rate", False),
                     "volume_ratio": ranked("volume_ratio")},
        "rvol_days": int(rvol_days),
        "market_scanners": market_scan,
        "regulatory": {"as_of": (regulatory or {}).get("as_of"), "count": len(flags)},
        "capabilities": {
            "pool_rankings": bool(rows), "market_scanners": bool(market_scan),
            "fundamentals": True, "institutional_streaks": False,
            "constituent_weights": False, "prediction_validated": False,
        },
        "definitions": {
            "rvol20": "當日成交量 ÷ 前 N 日平均成交量（N ≤ 20，永豐盤後快照逐日累積，至少 5 日才計算）",
            "volume_ratio": "永豐提供的量比＝當日成交量 ÷ 昨日成交量（只比一天，雜訊較大）",
        },
        "policy": "行情排行不等於推薦；母池不是指數權重前百名。量能因子尚未驗證預測力，僅供研究，不改買賣判定。",
    }
