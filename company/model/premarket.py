# -*- coding: utf-8 -*-
"""盤前簡報（每個交易日一次）。

台股開盤前唯一會變的是隔夜海外市場與新聞。本模組把它們整理成「今天怎麼執行」
的脈絡，**刻意不改變選股名單與買賣判定**：

- 選股由價值引擎在盤後決定，那是經過品質硬篩與估值位階的結論；
  隔夜漲跌與新聞標題屬於情緒與事件，雜訊高且極易事後合理化。
- 新聞尤其危險：它幾乎永遠能為任何決定找到理由。因此只以「旗標」呈現，
  不進評分、不排序，也不自動判定「論點失效」——論點失效仍由 Exit Engine
  以財務證據認定。
- 隔夜資訊真正有用的地方，是**執行面**：開盤要不要追價、賣出觸發價今天
  是否更可能被觸及。這正是本簡報的定位。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))

# 新聞只取少數幾檔：查詢成本之外，更重要的是避免把整頁標題當成研究。
MAX_NEWS_SYMBOLS = 5
NEWS_PER_SYMBOL = 3

# 標題若出現這些字樣，屬於可能影響投資論點的事件，值得人工看一眼。
# 這是「提醒人去看」，不是「自動判定」——判定仍需財報與公告。
MATERIAL_HINTS = (
    "財報", "法說", "重訊", "重大訊息", "下修", "上修", "減資", "增資",
    "併購", "收購", "解禁", "調降", "調升", "目標價", "停工", "罷工", "訴訟",
)


def flag_material(title: str) -> list[str]:
    return [hint for hint in MATERIAL_HINTS if hint in (title or "")]


def build_watchlist(state: dict, news_by_symbol: dict[str, list[dict]] | None = None) -> list[dict]:
    """把盤後選出的候選，附上新聞旗標後帶進盤前。順序完全沿用價值引擎。"""
    news_by_symbol = news_by_symbol or {}
    watchlist = []
    for bucket, label in (("top_picks", "可分批研究"), ("waiting_list", "等待止跌／觀察")):
        for item in state.get(bucket) or []:
            symbol = item.get("symbol")
            articles = news_by_symbol.get(symbol) or []
            flagged = []
            for article in articles:
                hints = flag_material(article.get("title", ""))
                if hints:
                    flagged.append({"title": article.get("title"), "link": article.get("link"),
                                    "hints": hints})
            watchlist.append({
                "symbol": symbol,
                "name": item.get("name"),
                "bucket": bucket,
                "bucket_label": label,
                "decision": item.get("decision"),
                "price": item.get("price"),
                "valuation_pct": item.get("valuation_pct"),
                "entry_range": item.get("entry_range"),
                "entry_evidence": item.get("entry_evidence"),
                "news_count": len(articles),
                "material_news": flagged,
            })
    return watchlist


def build_brief(state: dict, markets: dict, regime: dict,
                news_by_symbol: dict | None = None,
                market_news: list[dict] | None = None,
                now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    watchlist = build_watchlist(state, news_by_symbol)
    material_count = sum(1 for item in watchlist if item["material_news"])

    return {
        "schema_version": 1,
        "date": moment.astimezone(TAIPEI).date().isoformat(),
        "generated_at": moment.astimezone(timezone.utc).isoformat(),
        "state_as_of": state.get("as_of"),
        "regime": regime,
        "markets": markets,
        "watchlist": watchlist,
        "market_news": (market_news or [])[:5],
        "material_news_count": material_count,
        "execution_note": regime.get("guidance"),
        "discipline": (
            "選股名單由盤後價值引擎決定，本簡報不改變其內容與排序；"
            "新聞僅作旗標提醒人工查看，不自動判定論點失效——"
            "論點失效仍由 Exit Engine 以財務證據認定。"
        ),
        "disclaimer": "研究提示，不自動下單；開盤跳空不構成訊號，賣出仍以收盤確認為準。",
    }
