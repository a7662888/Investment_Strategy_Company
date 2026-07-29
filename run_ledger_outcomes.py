# -*- coding: utf-8 -*-
"""每日 durable 管線：先更新價值 current-state，再填寫到期 outcomes。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import app
from company.model.ledger import update_outcomes


def main() -> int:
    as_of = sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()
    # 共用既有 16:15 雲端排程，避免另增 workflow／權限；current-state 覆寫私有檔，
    # 不新增 Decision Ledger 事件。失敗時大聲中止，避免電子報默默沿用舊狀態。
    from run_daily_value_state import main as update_value_current_state
    current_rc = update_value_current_state()
    if current_rc != 0:
        print("Daily value current-state durable update failed.", file=sys.stderr)
        return 3
    result = update_outcomes(as_of, app.fetch_history)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if os.environ.get("REQUIRE_DURABLE_LEDGER") == "1" and not result.get("durable"):
        print("Decision Ledger durable storage failed or is not configured.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
