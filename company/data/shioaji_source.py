# -*- coding: utf-8 -*-
"""永豐 Shioaji 盤後快照來源（批次用，不供網站行程即時輪詢）。

**使用政策（務必遵守）**：官方[使用限制]明列違規樣態為「盤中反覆輪詢 snapshots
當作即時報價」，罰則是行情查詢回傳空值，反覆違規則 IP／ID 停權。
本模組只在**盤後批次**執行一次：`snapshots()` 一次可取 500 檔，母池 100 檔
只需 1 次查詢（實測 0.05 秒），距 10 秒 50 次的額度極遠。
網站的盤中即時報價仍走 TWSE MIS（證交所官方即時源，無此限制）。

**為什麼值得接**：
1. 同一來源同時提供當日與歷史，消除「Yahoo 與 TWSE OpenAPI 發布時間不同步」
   造成的日期錯亂——那一類問題曾讓整條管線連續兩天完全停止更新。
   實測 2026-09-22 19:53，TWSE OpenAPI 仍停在 09-21，Shioaji 已有當日收盤。
2. 帶來系統原本沒有的微結構欄位：當日 VWAP（average_price）、量比
   （volume_ratio）、買賣價差。這些直接回答「跌破是否有量、收盤是否弱於均價」，
   正是既有引擎判斷假跌破所缺的證據。

**相依性邊界**：shioaji 是編譯型套件，只安裝在 GitHub Actions 批次環境；
網站本體維持 requirements.txt 的 standard-library only，不受影響。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# 模擬環境同樣回傳真實行情（實測與 Yahoo／TWSE 三方一致），
# 但正式環境才是長期該用的；金鑰未勾「正式環境」時以此退路維持可用。
SIMULATION_ENV = "SHIOAJI_SIMULATION"


def _credentials() -> tuple[str, str] | None:
    api_key = (os.environ.get("SHIOAJI_API_KEY") or "").strip()
    secret_key = (os.environ.get("SHIOAJI_SECRET_KEY") or "").strip()
    return (api_key, secret_key) if api_key and secret_key else None


def connect(simulation: bool | None = None):
    """登入並回傳 api 物件。

    login 每日上限 1000 次，故批次腳本應只登入一次後重複使用，
    不要在迴圈或每次請求中呼叫。
    """
    import shioaji as sj

    credentials = _credentials()
    if not credentials:
        raise RuntimeError("SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY 未設定")
    if simulation is None:
        simulation = os.environ.get(SIMULATION_ENV, "").strip() == "1"
    api = sj.Shioaji(simulation=bool(simulation))
    # 本系統只取資料、永不下單，因此不訂閱成交回報：既無必要，
    # 也避免用到帳務／交易權限（預設 subscribe_trade=True 會去訂閱）。
    api.login(*credentials, subscribe_trade=False)
    return api


def connect_best_effort() -> tuple[object, bool]:
    """依環境變數決定連線模式，回傳 (api, simulation)。

    刻意**不做同行程的自動退路**：實測在同一行程先嘗試正式環境登入失敗後，
    再建立新的 Shioaji(simulation=True) 會得到 401「Token doesn't have permission」，
    但在乾淨行程中同樣呼叫卻成功——失敗的登入會污染行程狀態。
    與其埋一個時好時壞的重試，不如由 SHIOAJI_SIMULATION 明確指定，
    並在權限不足時給出可執行的指示。
    """
    simulation = os.environ.get(SIMULATION_ENV, "").strip() == "1"
    try:
        return connect(simulation=simulation), simulation
    except Exception as exc:  # noqa: BLE001
        if "production permission" in str(exc):
            raise RuntimeError(
                "金鑰缺少「正式環境」權限。請至永豐 API 管理頁為金鑰加勾『正式環境』，"
                f"或先設定環境變數 {SIMULATION_ENV}=1 以模擬環境取行情（資料為真實行情）。"
            ) from exc
        raise


def _tick_pressure(tick_type: object) -> str | None:
    """最後一筆成交落在買盤或賣盤。內外盤是即時的買賣壓力線索。"""
    text = str(tick_type or "")
    if text.endswith("Buy"):
        return "buy"
    if text.endswith("Sell"):
        return "sell"
    return None


def normalize_snapshot(snapshot: object, symbol: str) -> dict:
    """把 Shioaji snapshot 轉成本系統欄位，並保留微結構欄位。"""
    def value(name):
        return getattr(snapshot, name, None)

    close = value("close")
    vwap = value("average_price")
    # 收盤相對當日均價：低於 VWAP 表示當日賣方主導，可用來評估跌破的成色。
    vwap_gap = None
    if close is not None and vwap:
        vwap_gap = round((float(close) / float(vwap) - 1.0) * 100, 2)

    bid, ask = value("buy_price"), value("sell_price")
    spread_pct = None
    if bid and ask and float(ask) > 0:
        spread_pct = round((float(ask) - float(bid)) / float(ask) * 100, 3)

    return {
        "symbol": symbol,
        "code": value("code"),
        "open": value("open"),
        "high": value("high"),
        "low": value("low"),
        "close": close,
        "vwap": vwap,
        "close_vs_vwap_pct": vwap_gap,
        "change_price": value("change_price"),
        "change_rate": value("change_rate"),
        "total_volume": value("total_volume"),
        "volume_ratio": value("volume_ratio"),
        "bid": bid,
        "ask": ask,
        "spread_pct": spread_pct,
        "tick_pressure": _tick_pressure(value("tick_type")),
        "ts": value("ts"),
        "source": "Shioaji snapshots",
    }


def fetch_snapshots(api, symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """一次取回所有標的的盤後快照。回傳 (by_symbol, 找不到合約的標的)。"""
    contracts, missing, by_code = [], [], {}
    for symbol in symbols:
        code = symbol.split(".")[0]
        contract = api.Contracts.Stocks.get(code)
        if contract is None:
            missing.append(symbol)
            continue
        contracts.append(contract)
        by_code[code] = symbol
    if not contracts:
        return {}, missing

    results: dict[str, dict] = {}
    # 單次上限 500 檔；母池遠小於此，仍分批以免未來池子擴大時超限。
    for start in range(0, len(contracts), 500):
        for snapshot in api.snapshots(contracts[start:start + 500]):
            symbol = by_code.get(str(getattr(snapshot, "code", "")))
            if symbol:
                results[symbol] = normalize_snapshot(snapshot, symbol)
    return results, missing


def build_document(snapshots: dict[str, dict], missing: list[str],
                   simulation: bool, trade_date: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "simulation": bool(simulation),
        "count": len(snapshots),
        "missing": missing,
        "snapshots": snapshots,
        "policy": (
            "盤後批次取一次；不得在盤中反覆輪詢 snapshots 當即時報價"
            "（官方使用限制之違規樣態，罰則至 IP／ID 停權）。"
        ),
    }
