# -*- coding: utf-8 -*-
"""email-daily 的前置檢查：排程觸發時，若最新已完成交易日已重評就整條略過。

網站（14:10 外部排程或開頁）已觸發過當日盤後流程，GitHub 原生排程又常延遲到深夜才跑，
重跑除了浪費 15 分鐘與多次寫入，還會在 Yahoo 深夜回舊資料時被「拒絕回退」擋下而顯示失敗
（2026-10-12、10-13 實例：saved=10-12/10-13, new=10-08）。手動或網站觸發（workflow_dispatch）不略過。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def main() -> int:
    from company.model.current_state import load_current_state
    from company.model.daily_history import latest_completed_market_date

    event = os.environ.get("GITHUB_EVENT_NAME", "")
    now = datetime.now(timezone(timedelta(hours=8)))
    expected = latest_completed_market_date(now)
    state, _ = load_current_state()
    done = (state or {}).get("as_of")
    skip = event == "schedule" and bool(done) and done >= expected
    print(json.dumps({"event": event, "expected": expected, "state_as_of": done, "skip": skip}))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"skip={'true' if skip else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
