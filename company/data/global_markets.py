# -*- coding: utf-8 -*-
"""隔夜全球市場快照（盤前用）。

台股開盤前唯一能更新的資訊，是隔夜海外市場與新聞。其中**費城半導體指數**
對台股特別關鍵：台股權值高度集中於半導體，開盤缺口常跟著它走。

本模組刻意只取少數幾個指標，不做大而全的總經儀表板：
指標越多越容易被拿來事後合理化，而盤前真正要回答的只有一個問題——
「今天照既定計畫執行，還是該放慢？」

純標準函式庫，可在網站行程內執行（不需 shioaji，也就不需改工作流）。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# 少而準：美股大盤、科技股、費半、恐慌指數、台幣匯率、台股自身前收。
TRACKED = [
    ("^GSPC", "標普500", "us_broad"),
    ("^IXIC", "那斯達克", "us_tech"),
    ("^SOX", "費城半導體", "semis"),
    ("^VIX", "波動率VIX", "risk"),
    ("TWD=X", "美元兌台幣", "fx"),
    ("^TWII", "台股加權", "taiwan"),
]

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"


def _fetch_one(symbol: str, timeout: float = 20.0) -> dict | None:
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 單一指標失敗不得讓整份簡報消失
        return None
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    closes = [c for c in (((result.get("indicators") or {}).get("quote") or [{}])[0]
                          ).get("close") or [] if c is not None]
    if len(closes) < 2:
        return None
    last, previous = float(closes[-1]), float(closes[-2])
    meta = result.get("meta") or {}
    return {
        "symbol": symbol,
        "last": round(last, 2),
        "previous": round(previous, 2),
        "change_pct": round((last / previous - 1.0) * 100, 2) if previous else None,
        "currency": meta.get("currency"),
    }


def fetch_global_markets() -> dict[str, dict]:
    results: dict[str, dict] = {}
    for symbol, name, role in TRACKED:
        row = _fetch_one(symbol)
        if row:
            results[role] = {**row, "name": name, "role": role}
    return results


def risk_regime(markets: dict[str, dict]) -> dict:
    """由隔夜資料判定風險氛圍。

    門檻刻意寬鬆且少：這不是預測模型，只是「今天要不要放慢」的粗略閘門。
    台股權值集中於半導體，故以費半為主、VIX 為輔。
    """
    semis = (markets.get("semis") or {}).get("change_pct")
    vix = (markets.get("risk") or {}).get("last")
    vix_change = (markets.get("risk") or {}).get("change_pct")

    reasons = []
    level = "neutral"
    if semis is not None and float(semis) <= -2.0:
        level = "risk_off"
        reasons.append(f"費半隔夜跌 {abs(float(semis)):.2f}%")
    if vix is not None and float(vix) >= 25:
        level = "risk_off"
        reasons.append(f"VIX 達 {float(vix):.1f}（高波動）")
    elif vix_change is not None and float(vix_change) >= 15:
        level = "risk_off"
        reasons.append(f"VIX 單日跳升 {float(vix_change):.1f}%")
    if level == "neutral" and semis is not None and float(semis) >= 2.0:
        level = "risk_on"
        reasons.append(f"費半隔夜漲 {float(semis):.2f}%")
    if not reasons:
        reasons.append("隔夜海外市場無極端變動")

    guidance = {
        "risk_off": "開盤情緒偏弱：買進候選不宜在開盤追價，賣出觸發價較可能被觸及；"
                    "但仍以收盤確認為準，不因開盤跳空提前動作。",
        "risk_on": "開盤情緒偏強：留意買進候選可能開高，勿追價超過不追價上限。",
        "neutral": "隔夜無極端變動，照既定計畫執行即可。",
    }[level]

    return {"level": level, "reasons": reasons, "guidance": guidance,
            "generated_at": datetime.now(timezone.utc).isoformat()}
