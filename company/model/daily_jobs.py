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


def latest_completed_session(now: datetime) -> str | None:
    """最新已完成的交易日（14:00 後算當日；週末、國定與連續假日依證交所休市表往回找）。"""
    try:
        from company.model.daily_history import latest_completed_market_date

        return latest_completed_market_date(now)
    except Exception:  # noqa: BLE001
        return None


def next_trading_session(now: datetime) -> str | None:
    """下一個要開盤的交易日：今日為交易日且尚未收盤則為今日，否則往後找。"""
    day = now.astimezone(TAIPEI).date()
    if not (is_trading_weekday(now) and _minutes(now) < MARKET_CLOSE_MINUTES):
        day += timedelta(days=1)
    for _ in range(40):
        moment = datetime.combine(day, datetime.min.time(), TAIPEI).replace(hour=9)
        if is_trading_weekday(moment):
            return day.isoformat()
        day += timedelta(days=1)
    return None


def postclose_due(state: dict | None, now: datetime) -> tuple[bool, str]:
    """盤後母池重評是否該跑：以「最新已完成交易日」是否已重評為準，不限今天是否開盤。

    業主 2026-10-10：前一個交易日沒跑到就要補，假日或連假進網頁也一樣。
    例：週三之後沒進網頁、週六才進來 → 若週五的盤後重評沒完成，週六補跑週五。
    盤後流程只能算「最新一個」交易日（中間漏掉的單日快照無法事後重建）。
    """
    expected = latest_completed_session(now)
    if expected is None:
        return False, "交易日曆不可用，暫不判斷"
    done = (state or {}).get("as_of")
    if done and done >= expected:
        return False, f"最新已完成交易日 {expected} 已重評"
    if is_trading_weekday(now) and MARKET_OPEN_MINUTES <= _minutes(now) < POSTCLOSE_MINUTES:
        # 盤中不補跑：盤後流程的永豐快照在盤中會抓到未收盤價格並存成收盤紀錄。
        # 14:00 後當日的盤後重評會一併涵蓋（漏掉的那一天單日快照無法事後重建）。
        return False, f"{expected} 尚未重評；盤中不補跑，14:00 後與今日盤後重評一併處理"
    return True, f"最新已完成交易日 {expected} 尚未重評（目前資料停在 {done or '無紀錄'}）"


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
    """交易日：06:00 後每日一次。非交易日：最新盤後狀態就緒後，產生一次「下一交易日預覽」。"""
    if not is_trading_weekday(now):
        expected = latest_completed_session(now)
        target = next_trading_session(now)
        if not state_as_of or not expected or state_as_of < expected:
            return False, f"{non_trading_reason(now)}；等待 {expected or '最新交易日'} 盤後重評完成後再產生預覽"
        if (brief or {}).get("target_session") == target and (brief or {}).get("state_as_of") == state_as_of:
            return False, f"{non_trading_reason(now)}；下一交易日 {target} 預覽已產生"
        return True, f"{non_trading_reason(now)}；產生下一交易日 {target} 的盤前預覽"
    return _premarket_trading_day(brief, now, state_as_of)


def _premarket_trading_day(brief: dict | None, now: datetime,
                           state_as_of: str | None = None) -> tuple[bool, str]:
    """盤前簡報是否該自動產生：每個交易日最多成功一次。

    原本在底層狀態前進時會自動重做（2026-09-22 簡報停在 09-18 的案例），
    現改為「一天一次＋手動更新」；狀態落後時於說明中提示，由業主決定是否重做。
    """
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
# 業主 2026-10-10：手動鍵要能隨時按。只保留防連點；資料保護（盤中不存未收盤價）
# 放在資料來源端（session_unsettled），不再以時段或「今日已完成」拒絕。
MANUAL_COOLDOWN_SECONDS = {"premarket": 30, "intraday": 30, "postclose": 30, "broker": 30}
MANUAL_JOBS = tuple(MANUAL_COOLDOWN_SECONDS)
# 永豐庫存讀取（帳務查詢）走既有 workflow，不在網站行程內登入券商。
BROKER_WORKFLOW = "broker-research-refresh.yml"
_MANUAL: dict[str, float] = {}


def manual_note(job: str, now: datetime, state: dict | None = None) -> str:
    """按下去會發生什麼（給按鈕下方與確認視窗）。不阻擋執行。"""
    if job == "intraday":
        if in_market_session(now):
            return "重新產生盤中快訊並刷新即時價"
        return "非盤中時段：以最後成交價產生參考版本，不代表盤中即時"
    if job == "premarket":
        if not is_trading_weekday(now):
            return f"非交易日：產生下一交易日 {next_trading_session(now)} 的盤前預覽"
        return "重新產生今日盤前簡報（隔夜行情與新聞重抓）"
    if job == "postclose":
        expected = latest_completed_session(now)
        done = (state or {}).get("as_of")
        parts = []
        if session_unsettled(now):
            parts.append("盤中執行：快照、分 K、ETF 每日資料會略過今日未收盤部分，只更新前一交易日的分析")
        if expected and done and done >= expected:
            parts.append(f"{expected} 已重評過：會重新計算並再寄一次每日 Email")
        else:
            parts.append(f"補跑 {expected or '最新交易日'} 的盤後重評（約 10–20 分鐘）")
        return "；".join(parts)
    if job == "broker":
        return "讀取永豐最新庫存、逐筆紀錄與已實現損益（約 2–10 分鐘）"
    return ""


def manual_check(job: str, now: datetime, state: dict | None = None) -> tuple[bool, str]:
    """手動更新：隨時允許，只擋未知項目與 30 秒內重複點擊。回傳 (允許, 說明)。"""
    if job not in MANUAL_COOLDOWN_SECONDS:
        return False, "未知的更新項目"
    with _LOCK:
        last = _MANUAL.get(job)
    elapsed = now.timestamp() - last if last else None
    cooldown = MANUAL_COOLDOWN_SECONDS[job]
    if elapsed is not None and elapsed < cooldown:
        return False, f"剛按過，請 {int(cooldown - elapsed)} 秒後再試"
    return True, manual_note(job, now, state)


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


def recent_workflow_run(workflow: str, since: datetime) -> dict | None:
    """since 之後是否已有該 workflow 的成功、排隊或執行中的 run（避免補跑時重複寄信）。

    查詢失敗回 None（視為沒有），由行程內 claim 擋同一實例的重複觸發。
    """
    token = (os.environ.get("GITHUB_DATA_TOKEN") or os.environ.get("GITHUB_PAT") or "").strip()
    repo = os.environ.get("GITHUB_ACTIONS_REPO", "a7662888/Investment_Strategy_Company").strip()
    if not token:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=10",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "investment-daily-jobs/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            runs = json.loads(response.read().decode("utf-8")).get("workflow_runs") or []
    except Exception:  # noqa: BLE001
        return None
    for run in runs:
        try:
            created = datetime.fromisoformat(str(run.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        active = run.get("status") in ("queued", "in_progress", "waiting", "pending", "requested")
        if created >= since and (active or run.get("conclusion") == "success"):
            return {"status": run.get("status"), "conclusion": run.get("conclusion"),
                    "created_at": run.get("created_at")}
    return None


def session_close(day: str) -> datetime:
    """某交易日的盤後定稿時刻（14:00 台北），作為「這個交易日的盤後流程」的起算點。"""
    return datetime.fromisoformat(day).replace(hour=14, tzinfo=TAIPEI)


def session_unsettled(now: datetime | None = None) -> bool:
    """今日交易時段已開始但尚未定稿（交易日 09:00–14:00）。

    手動「盤後重評」允許隨時按（業主 2026-10-10），所以「盤中不得把未收盤價存成收盤紀錄」
    的保護必須放在資料來源端：快照、分 K、ETF 每日資料在此時段一律略過當日。
    """
    now = now or taipei_now()
    return is_trading_weekday(now) and MARKET_OPEN_MINUTES <= _minutes(now) < POSTCLOSE_MINUTES
