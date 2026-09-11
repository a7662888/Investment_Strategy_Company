# -*- coding: utf-8 -*-
"""造訪觸發的每日任務排程（每個交易日各限一次）。

為什麼不在網站行程內直接算：盤後母池重評要跑 100 檔 rescreen，Render 免費方案
會在閒置後休眠並殺掉背景執行緒，重工作跑到一半消失。而這件事在 GitHub Actions
上本來就每天可靠地跑。因此本模組的職責只有兩件：

1. 判斷「今天這個任務是否已經做過」——真相來自產物本身（current-state 的
   analysis_date_taipei、快訊文件的 date），而不是另存一份可能與現實脫節的旗標。
2. 若尚未做過且時窗正確，觸發對應的 workflow（盤後）或就地產生（盤中快訊，很輕）。

已知限制：台股休市日未建表，僅以週一～週五判斷。遇到國定假日最多多觸發一次
workflow，該次會算出與前一交易日相同的結果，不會污染資料；寧可多跑一次，
也不要為了省一次執行而漏掉真正的交易日。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))

# 與既有價格護欄一致：台股 13:30 收盤，保守以 14:00 視為盤後定稿。
MARKET_OPEN_MINUTES = 9 * 60
MARKET_CLOSE_MINUTES = 13 * 60 + 30
POSTCLOSE_MINUTES = 14 * 60

POSTCLOSE_JOB = "postclose_rescreen"
INTRADAY_JOB = "intraday_flash"

# 盤後重評要做兩件事，分別由既有的兩個 workflow 承擔：
#   email-daily.yml   → run_daily_value_state.py，重算「今日優質股與進場時機」
#   value-rescreen.yml→ run_value_rescreen.py，母池重篩並在判定改變時凍新帳本卡
# 本來想合併成一支不寄信的 daily-refresh.yml，但現用 PAT 無 workflow scope
# （push 被 GitHub 拒絕），無法新增工作流檔；改為觸發既有兩支。
# 影響：安全網啟動時會多寄一封每日摘要——而那正是當日排程漏掉的東西，
# 因此語意上可接受；若日後 token 補上 workflow scope，可改回單一無寄信流程。
POSTCLOSE_WORKFLOWS = ("email-daily.yml", "value-rescreen.yml")

# 單一 Render 實例，行程內鎖即足夠；實例重啟後以產物日期重新判斷，不會重複做完的事。
_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def is_trading_weekday(now: datetime) -> bool:
    return now.weekday() < 5


def in_market_session(now: datetime) -> bool:
    return is_trading_weekday(now) and MARKET_OPEN_MINUTES <= _minutes(now) <= MARKET_CLOSE_MINUTES


def is_after_close(now: datetime) -> bool:
    return is_trading_weekday(now) and _minutes(now) >= POSTCLOSE_MINUTES


def postclose_due(state: dict | None, now: datetime) -> tuple[bool, str]:
    """盤後母池重評是否該跑。state 為 current-state 文件。"""
    if not is_trading_weekday(now):
        return False, "非交易日（週末）"
    if not is_after_close(now):
        return False, f"尚未到盤後（台北 {now:%H:%M}，14:00 後才重評）"
    today = now.date().isoformat()
    done = (state or {}).get("analysis_date_taipei")
    if done == today:
        return False, f"今日（{today}）已完成盤後重評"
    return True, f"今日尚未重評（最後一次 {done or '無紀錄'}）"


def intraday_due(flash: dict | None, now: datetime) -> tuple[bool, str]:
    """盤中研究快訊是否該產生。"""
    if not is_trading_weekday(now):
        return False, "非交易日（週末）"
    if not in_market_session(now):
        return False, f"非盤中時段（台北 {now:%H:%M}，09:00–13:30 才產生）"
    today = now.date().isoformat()
    done = (flash or {}).get("date")
    if done == today:
        return False, f"今日（{today}）快訊已產生"
    return True, f"今日尚未產生快訊（最後一次 {done or '無紀錄'}）"


def job_state(job: str) -> dict:
    with _LOCK:
        return dict(_STATE.get(job) or {})


def _set(job: str, **fields) -> None:
    with _LOCK:
        _STATE.setdefault(job, {}).update(fields)


def claim(job: str, date: str, stale_after_seconds: float = 1800.0) -> bool:
    """搶下今日的執行權；已在跑或今日已跑過則回 False。

    卡住的任務（例如實例在執行中被回收）以 stale_after_seconds 釋放，
    否則一次失敗會讓該任務整天不再重試。
    """
    now = taipei_now().timestamp()
    with _LOCK:
        entry = _STATE.setdefault(job, {})
        if entry.get("done_date") == date:
            return False
        started = entry.get("started_at")
        if entry.get("running") and started and now - started < stale_after_seconds:
            return False
        entry.update(running=True, started_at=now, date=date, error=None)
        return True


def finish(job: str, date: str, ok: bool, detail: str | None = None) -> None:
    with _LOCK:
        entry = _STATE.setdefault(job, {})
        entry.update(running=False, finished_at=taipei_now().timestamp(), error=None if ok else detail)
        if ok:
            entry["done_date"] = date
        entry["detail"] = detail


def run_in_background(job: str, date: str, target) -> None:
    """在背景執行並確保狀態一定被收尾，避免任務卡在 running。"""
    def _wrapped() -> None:
        try:
            detail = target()
            finish(job, date, True, detail if isinstance(detail, str) else None)
        except Exception as exc:  # noqa: BLE001 - 任何失敗都要留痕並允許重試
            finish(job, date, False, f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_wrapped, name=f"daily-job-{job}", daemon=True).start()


def dispatch_workflow(workflow: str, inputs: dict | None = None, ref: str = "main") -> dict:
    """觸發 GitHub Actions workflow_dispatch。

    盤後重評刻意交給 Actions 而非在本行程算：那裡有完整 secrets、30 分鐘 timeout，
    且不會因為 Render 休眠而中途消失。
    """
    token = (os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT") or "").strip()
    repo = os.environ.get("GITHUB_ACTIONS_REPO", "a7662888/Investment_Strategy_Company").strip()
    if not token:
        return {"dispatched": False, "error": "no_token"}
    body = json.dumps({"ref": ref, **({"inputs": inputs} if inputs else {})}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "investment-daily-jobs/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return {"dispatched": response.status in (201, 204), "status": response.status,
                    "workflow": workflow}
    except urllib.error.HTTPError as exc:
        return {"dispatched": False, "status": exc.code, "workflow": workflow,
                "error": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"dispatched": False, "workflow": workflow, "error": f"{type(exc).__name__}: {exc}"}
