# -*- coding: utf-8 -*-
"""每日盤後：讀取所有已串接主動 ETF 的官方 PCF（本期＋前一期），存進資料庫。

網站在即時讀取投信失敗時改用這份存檔（etf_research.stored_pair）。持股為投信公開資料，
log 列出每家投信成功／失敗檔數，供判斷哪些投信擋國外主機。
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import etf_research as e
from company.model.durable_document import save_document

ROOT = Path(__file__).resolve().parent


def issuer_of(code: str) -> str:
    for name, group in (("統一", e.EZMONEY), ("復華", e.FH), ("聯博", e.AB), ("元大", e.YUANTA),
                        ("群益", e.CAPITAL), ("野村", e.NOMURA),
                        ("國泰", e.CATHAY), ("富邦", e.FUBON), ("凱基", e.KGI), ("安聯", e.ALLIANZ)):
        if code in group:
            return name
    return "其他"


def main() -> int:
    from company.model.daily_jobs import session_unsettled
    if session_unsettled():
        print(json.dumps({"skipped": "session not settled; PCF for the next session is published after close"}))
        return 0
    codes = sorted(r["code"] for r in e.catalog()["rows"] if r.get("active") and e.supported_issuer(r["code"]))
    report: dict[str, dict] = defaultdict(lambda: {"ok": 0, "failed": 0, "errors": []})
    for code in codes:
        token = e._DEADLINE.set(time.monotonic() + 40)
        try:
            current, previous = e.fetch_pair(code)
            doc = {"schema_version": 1, "code": code, "saved_at": e.now_iso(), "current": current, "previous": previous}
            save_document(doc, ROOT / "data" / "etf" / "holdings" / f"{code}.json",
                          e.STORED_REMOTE.format(code=code), f"chore(etf): official holdings {code} {current['as_of']}")
            report[issuer_of(code)]["ok"] += 1
        except Exception as exc:  # noqa: BLE001 - 單檔失敗不影響其他檔
            report[issuer_of(code)]["failed"] += 1
            report[issuer_of(code)]["errors"].append(f"{code}:{type(exc).__name__}")
        finally:
            e._DEADLINE.reset(token)
    print(json.dumps({"codes": len(codes), "by_issuer": report}, ensure_ascii=False))
    return 0 if any(v["ok"] for v in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
