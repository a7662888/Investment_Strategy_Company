# -*- coding: utf-8 -*-
"""以 Shioaji 取一次母池快照，寫入私有資料庫。

預設為盤後定稿；`SHIOAJI_SNAPSHOT_MODE=intraday` 僅供業主明確觸發的單次
盤中研究快照，絕不由網站開頁或定時輪詢。網站本體不安裝 shioaji，維持零依賴。
一次查詢即可涵蓋整個母池（snapshots 單次上限 500 檔），遠低於 10 秒 50 次的
額度，不觸及「盤中反覆輪詢」的違規樣態。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from company.data.shioaji_source import build_document, connect_best_effort, fetch_snapshots
from company.model.durable_document import load_document, save_document

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
LOCAL_PATH = ROOT / "data" / "shioaji_snapshot.json"
REMOTE_PATH = os.environ.get("SHIOAJI_SNAPSHOT_PATH", "market/shioaji_snapshot.json")
LOCAL_DIR = ROOT / "data" / "shioaji"
REMOTE_DIR = os.environ.get("SHIOAJI_SNAPSHOT_DIR", "market/shioaji")
VOLUME_HISTORY = (ROOT / "data" / "volume_history.json", "market/volume_history.json")
RVOL_WINDOW = 20
RVOL_MIN_DAYS = 5


def _backfill_volume_history(trade_date: str) -> dict:
    """第一次執行時，從既有的逐日快照補建成交量歷史（最多回看 45 個日曆日）。"""
    codes: dict[str, list] = {}
    day = datetime.fromisoformat(trade_date).date()
    for back in range(1, 46):
        name = (day - timedelta(days=back)).isoformat()
        doc, _ = load_document(LOCAL_DIR / f"{name}.json", f"{REMOTE_DIR}/{name}.json")
        if not doc or doc.get("mode", "postclose") != "postclose":
            continue
        for symbol, row in (doc.get("snapshots") or {}).items():
            if _row_date(row, doc.get("trade_date")) == name and row.get("total_volume"):
                codes.setdefault(symbol, []).append([name, row["total_volume"]])
    return {"schema_version": 1, "codes": {k: sorted(v) for k, v in codes.items()}}


def _row_date(row: dict, document_date: str | None) -> str | None:
    """逐列交易日。2026-10-05 前的舊快照沒有逐列日期與 timestamp_status，
    只能沿用整份（逐日檔）的日期；新格式則必須逐列驗證，不得以批次日期冒充。"""
    if "timestamp_status" not in row and row.get("trade_date") is None:
        return document_date
    return row.get("trade_date") if row.get("timestamp_status") in (None, "valid") else None


def apply_relative_volume(document: dict, history: dict | None) -> dict:
    """相對成交量 rvol20＝當日量 ÷ 前 N 日平均量（N ≤ 20，至少 5 日才計算）。

    永豐 snapshot 的 volume_ratio 實測等於「當日量 ÷ 昨日量」（2026-10-08 母池 106/106 吻合），
    只拿一天當基準、雜訊大；相對近 20 日均量較能看出量能是否真的異常。
    同一來源（永豐盤後快照）逐日累積，不混用其他來源的成交量單位。
    """
    trade_date = document.get("trade_date")
    history = history or {"schema_version": 1, "codes": {}}
    codes = history.setdefault("codes", {})
    for symbol, row in (document.get("snapshots") or {}).items():
        past = [v for d, v in codes.get(symbol, []) if d < trade_date][-RVOL_WINDOW:]
        volume = row.get("total_volume")
        same_day = _row_date(row, trade_date) == trade_date
        if same_day and volume and len(past) >= RVOL_MIN_DAYS:
            average = sum(past) / len(past)
            row["rvol20"] = round(volume / average, 2) if average else None
            row["rvol_days"] = len(past)
        if same_day and volume:
            kept = [p for p in codes.get(symbol, []) if p[0] != trade_date] + [[trade_date, volume]]
            codes[symbol] = sorted(kept)[-(RVOL_WINDOW + 5):]
    history["last_trade_date"] = trade_date
    return history


def _trade_date(snapshots: dict) -> str | None:
    """以快照時間戳推定交易日；ts 為 epoch 奈秒。"""
    stamps = [item.get("ts") for item in snapshots.values() if item.get("ts")]
    if not stamps:
        return None
    newest = max(int(s) for s in stamps)
    return datetime.fromtimestamp(newest / 1_000_000_000, TAIPEI).date().isoformat()


def pool_symbols() -> list[str]:
    pool = json.loads((ROOT / "model_artifacts" / "active_pool.json").read_text(encoding="utf-8"))
    seed = json.loads((ROOT / "data" / "value_fundamentals.json").read_text(encoding="utf-8"))
    etfs = [f"{code}.TW" for code, item in seed.items()
            if isinstance(item, dict) and item.get("is_etf")]
    return list(dict.fromkeys([row["symbol"] for row in pool.get("stocks", [])] + etfs))


def main() -> int:
    from run_broker_positions import _quiet_sdk

    symbols = pool_symbols()
    mode = (os.environ.get("SHIOAJI_SNAPSHOT_MODE") or "postclose").strip().lower()
    if mode not in {"postclose", "intraday"}:
        raise RuntimeError("SHIOAJI_SNAPSHOT_MODE must be postclose or intraday")
    # SDK native logs contain connection identifiers.  They are diagnostic data,
    # not public build output, so suppress them exactly as the broker-inventory job does.
    with _quiet_sdk():
        api, simulation = connect_best_effort()
        try:
            snapshots, missing = fetch_snapshots(api, symbols)
        finally:
            try:
                api.logout()
            except Exception:  # noqa: BLE001 - 登出失敗不影響已取得的資料
                pass
    if simulation:
        print("[warn] 金鑰無正式環境權限，改用模擬環境（行情為真實資料，但建議補勾權限）",
              file=sys.stderr)

    document = build_document(snapshots, missing, simulation, _trade_date(snapshots), mode=mode)
    trade_date = document["trade_date"]
    volume_storage = None
    if trade_date and mode == "postclose":
        history, _ = load_document(*VOLUME_HISTORY)
        history = apply_relative_volume(document, history or _backfill_volume_history(trade_date))
        volume_storage = save_document(history, *VOLUME_HISTORY, f"chore(market): volume history {trade_date}")

    # 每日各存一份不可變的當日檔：量價訊號是否真的有預測力，必須靠累積的歷史
    # 用既有 outcome 框架驗證。只留「最新一份」等於永遠無法回測，這個決定
    # 要在第一天就做對——資料錯過就補不回來了。
    dated_storage = None
    if trade_date and mode == "postclose":
        dated_storage = save_document(
            document, LOCAL_DIR / f"{trade_date}.json", f"{REMOTE_DIR}/{trade_date}.json",
            f"chore(market): shioaji snapshot {trade_date}",
        )
    # 另存一份「最新」供網站單次讀取，避免前端要先查日期再抓檔。
    storage = save_document(document, LOCAL_PATH, REMOTE_PATH,
                            f"chore(market): latest shioaji snapshot {trade_date}")

    print(json.dumps({
        "trade_date": trade_date, "count": document["count"],
        "missing": missing, "simulation": simulation, "mode": mode,
        "latest_storage": storage, "dated_storage": dated_storage,
        "volume_history_storage": volume_storage,
        "rvol_count": sum(1 for r in document["snapshots"].values() if r.get("rvol20") is not None),
    }, ensure_ascii=False, indent=2))
    return 0 if storage.get("local_saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
