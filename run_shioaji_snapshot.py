# -*- coding: utf-8 -*-
"""盤後以 Shioaji 取一次母池快照，寫入私有資料庫。

只在 GitHub Actions 批次環境執行；網站本體不安裝 shioaji，維持零依賴。
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
from company.model.durable_document import save_document

ROOT = Path(__file__).resolve().parent
TAIPEI = timezone(timedelta(hours=8))
LOCAL_PATH = ROOT / "data" / "shioaji_snapshot.json"
REMOTE_PATH = os.environ.get("SHIOAJI_SNAPSHOT_PATH", "market/shioaji_snapshot.json")
LOCAL_DIR = ROOT / "data" / "shioaji"
REMOTE_DIR = os.environ.get("SHIOAJI_SNAPSHOT_DIR", "market/shioaji")


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
    symbols = pool_symbols()
    api, simulation = connect_best_effort()
    if simulation:
        print("[warn] 金鑰無正式環境權限，改用模擬環境（行情為真實資料，但建議補勾權限）",
              file=sys.stderr)
    try:
        snapshots, missing = fetch_snapshots(api, symbols)
    finally:
        try:
            api.logout()
        except Exception:  # noqa: BLE001 - 登出失敗不影響已取得的資料
            pass

    document = build_document(snapshots, missing, simulation, _trade_date(snapshots))
    trade_date = document["trade_date"]

    # 每日各存一份不可變的當日檔：量價訊號是否真的有預測力，必須靠累積的歷史
    # 用既有 outcome 框架驗證。只留「最新一份」等於永遠無法回測，這個決定
    # 要在第一天就做對——資料錯過就補不回來了。
    dated_storage = None
    if trade_date:
        dated_storage = save_document(
            document, LOCAL_DIR / f"{trade_date}.json", f"{REMOTE_DIR}/{trade_date}.json",
            f"chore(market): shioaji snapshot {trade_date}",
        )
    # 另存一份「最新」供網站單次讀取，避免前端要先查日期再抓檔。
    storage = save_document(document, LOCAL_PATH, REMOTE_PATH,
                            f"chore(market): latest shioaji snapshot {trade_date}")

    print(json.dumps({
        "trade_date": trade_date, "count": document["count"],
        "missing": missing, "simulation": simulation,
        "latest_storage": storage, "dated_storage": dated_storage,
    }, ensure_ascii=False, indent=2))
    return 0 if storage.get("local_saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
