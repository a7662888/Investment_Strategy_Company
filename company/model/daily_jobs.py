# -*- coding: utf-8 -*-
"""造訪觸發的每日任務排程（每個交易日各限一次）。

為什麼不在網站行程內直接算：盤後母池重評要跑 100 檔 rescreen，Render 免費方案
會在閒置後休眠並殺掉背景執行緒，重工作跑到一半消失。而這件事在 GitHub Actions
上本來就每天可靠地跑。因此本模組的職責只有兩件：

1. 判斷「今天這個任務是否已經做過」——真相來自產物本身（current-state 的
   analysis_date_taipei、快訊文件的 date），而不是另存一份可能與現實脫節的旗標。
2. 若尚未做過且時窗正確，觸發對應的 workflow（盤後）或就地產生（盤中快訊，很輕）。

交易日判斷採證交所公告的休市日表（company.data.market_calendar）。該表抓取
失敗時 fail-open 只擋週末：誤判休市會讓當日完全不更新，代價遠大於在休市日
多觸發一次（該次算出的結果與前一交易日相同，不污染資料）。
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
PREMARKET_JOB = "premarket_brief"

# 盤前簡報自台北 06:00 起可產生。刻意不設上限：若當天開盤前無人造訪，
# 稍晚補跑仍然有效——隔夜海外收盤是既成事實，不會因為台股已開盤而改變。
PREMARKET_FROM_MINUTES = 6 * 60

# The daily workflow orders snapshot, current-state, rescreen and outcomes.
# Dispatching a second rescreen would race its fresh inputs.
POSTCLOSE_WORKFLOWS = ("email-daily.yml",)

# 單一 Render 實例，行程內鎖即足夠；實例重啟後以產物日期重新判斷，不會重複做完的事。
_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def is_trading_weekday(now: datetime) -> bool:
    """是否為交易日（週一～週五且非證交所公告休市日）。

    休市日表抓取失敗時 fail-open，只擋週末：誤判休市會讓當日完全不更新，
    代價遠大於在休市日多觸發一次（該次算出的結果與前一交易日相同）。
    """
    if now.weekday() >= 5:
        return False
    try:
        from company.data.market_calendar import is_holiday

        return not is_holiday(now.astimezone(TAIPEI).date())
    except Exception:  # noqa: BLE001
        return True


def non_trading_reason(now: datetime) -> str:
    if now.weekday() >= 5:
        return "非交易日（週末）"
    try:
        from company.data.market_calendar import holiday_name

        name = holiday_name(now.astimezone(TAIPEI).date())
    except Exception:  # noqa: BLE001
        name = None
    return f"非交易日（{name}）" if name else "非交易日"


def in_market_session(now: datetime) -> bool:
    return is_trading_weekday(now) and MARKET_OPEN_MINUTES <= _minutes(now) <= MARKET_CLOSE_MINUTES


def is_after_close(now: datetime) -> bool:
    return is_trading_weekday(now) and _minutes(now) >= POSTCLOSE_MINUTES


def postclose_due(state: dict | None, now: datetime) -> tuple[bool, str]:
    """盤後母池重評是否該跑。state 為 current-state 文件。"""
    if not is_trading_weekday(now):
        return False, non_trading_reason(now)
    if not is_after_close(now):
        return False, f"尚未到盤後（台北 {now:%H:%M}，14:00 後才重評）"
    today = now.date().isoformat()
    done = (state or {}).get("analysis_date_taipei")
    if done == today:
        return False, f"今日（{today}）已完成盤後重評"
    return True, f"今日尚未重評（最後一次 {done or '無紀錄'}）"


# 盤中快訊自動產生的最早時間：09:00 一開盤時多數標的尚未成交，報價仍是昨收。
INTRADAY_FROM_MINUTES = 9 * 60 + 5


def intraday_due(flash: dict | None, now: datetime) -> tuple[bool, str]:
    """盤中研究快訊是否該自動產生：每個交易日最多成功一次（業主 2026-10-10 定案）。

    盤中想看較新的位置，改用「手動更新」（manual_check）；自動輪替不再重算，
    避免整天反覆寫入，也讓「今天的快訊」有單一、可對照的版本。
    """
    if not is_trading_weekday(now):
        return False, non_trading_reason(now)
    if not in_market_session(now) or _minutes(now) < INTRADAY_FROM_MINUTES:
        return False, f"非盤中時段（台北 {now:%H:%M}，09:05–13:30 才自動產生）"
    today = now.date().isoformat()
    done = (flash or {}).get("date")
    if done == today:
        return False, f"今日（{today}）快訊已產生；需要較新資料請按手動更新"
    return True, f"今日尚未產生快訊（最後一次 {done or '無紀錄'}）"


def premarket_due(brief: dict | None, now: datetime,
                  state_as_of: str | None = None) -> tuple[bool, str]:
    """盤前簡報是否該自動產生：每個交易日最多成功一次。

    原本在底層狀態前進時會自動重做（2026-09-22 簡報停在 09-18 的案例），
    現改為「一天一次＋手動更新」；狀態落後時於說明中提示，由業主決定是否重做。
    """
    if not is_trading_weekday(now):
        return False, non_trading_reason(now)
    if _minutes(now) < PREMARKET_FROM_MINUTES:
        return False, f"尚未到產生時間（台北 {now:%H:%M}，06:00 後產生）"
    today = now.date().isoformat()
    done = (brief or {}).get("date")
    if done == today:
        brief_state = (brief or {}).get("state_as_of")
        if state_as_of and brief_state and brief_state != state_as_of:
            return False, (f"今日盤前簡報已產生（依據 {brief_state}，最新狀態 {state_as_of}）；"
                           "需要重做請按手動更新")
        return False, f"今日（{today}）盤前簡報已產生"
    return True, f"今日尚未產生盤前簡報（最後一次 {done or '無紀錄'}）"


# ---- 手動更新（業主一鍵）----
# 冷卻時間防止連點與誤觸；盤中最短，因為那是最常需要即時資料的時段。
MANUAL_COOLDOWN_SECONDS = {"premarket": 300, "intraday": 120, "postclose": 600, "broker": 900}
MANUAL_JOBS = tuple(MANUAL_COOLDOWN_SECONDS)
# 永豐庫存讀取（帳務查詢）走既有 workflow，不在網站行程內登入券商。
BROKER_WORKFLOW = "broker-research-refresh.yml"
_MANUAL: dict[str, float] = {}


def manual_check(job: str, now: datetime, state: dict | None = None) -> tuple[bool, str]:
    """手動更新是否允許。回傳 (允許, 理由)。不改變自動任務的「今日已做」判斷。"""
    if job not in MANUAL_COOLDOWN_SECONDS:
        return False, "未知的更新項目"
    if job != "broker" and not is_trading_weekday(now):
        return False, non_trading_reason(now)
    if job == "intraday" and not in_market_session(now):
        return False, f"非盤中時段（台北 {now:%H:%M}）；盤中 09:00–13:30 才能更新盤中資訊"
    if job == "premarket" and _minutes(now) < PREMARKET_FROM_MINUTES:
        return False, "06:00 後才能產生盤前簡報"
    if job == "postclose":
        if not is_after_close(now):
            return False, f"尚未到盤後（台北 {now:%H:%M}，14:00 後）"
        if (state or {}).get("analysis_date_taipei") == now.date().isoformat():
            # 盤後流程包含寄 Email 與凍結帳本；已完成時再跑會重寄、重凍，不提供一鍵重做。
            return False, "今日盤後重評已完成；為避免重複寄信與重複凍結，不提供一鍵重做"
    with _LOCK:
        last = _MANUAL.get(job)
    elapsed = now.timestamp() - last if last else None
    cooldown = MANUAL_COOLDOWN_SECONDS[job]
    if elapsed is not None and elapsed < cooldown:
        return False, f"剛更新過，請 {int(cooldown - elapsed)} 秒後再試"
    return True, "允許手動更新"


def mark_manual(job: str, now: datetime) -> None:
    with _LOCK:
        _MANUAL[job] = now.timestamp()


def manual_state() -> dict:
    with _LOCK:
        return {job: datetime.fromtimestamp(ts, TAIPEI).isoformat() for job, ts in _MANUAL.items()}


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
