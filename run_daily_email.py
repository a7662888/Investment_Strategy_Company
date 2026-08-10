# -*- coding: utf-8 -*-
"""每日投資摘要 Email（雲端排程版，GitHub Actions 每交易日 16:35 台北執行）。

收件人與SMTP密碼來自 Actions 加密 secrets；持股優先讀私有 data repo SSOT：
  EMAIL_ADDRESS      寄件人=收件人 Gmail
  SMTP_APP_PASSWORD  Gmail 應用程式密碼
  STOCK_POSITIONS    JSON 陣列；或 sync:<私有同步密鑰> 以共用網頁持股
每日候選由同一個雲端 workflow 先重算，再讀已部署網站 API；寄信輸入另存私有 audit 快照。
"""
import json, os, smtplib, ssl, sys, urllib.request
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from company.model.durable_document import save_document
from company.model.positions import load_positions

BASE = os.environ.get("SITE_BASE", "https://investment-strategy-company.onrender.com")
POSITION_SYNC_PREFIX = "sync:"
TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent

def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "daily-email"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())

def synced_positions(sync_token):
    """Read the private portfolio through the deployed API without exposing data-repo credentials."""
    req = urllib.request.Request(
        BASE + "/api/positions",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {sync_token}",
            "User-Agent": "daily-email-positions",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        doc = json.loads(response.read().decode("utf-8"))
    if int(doc.get("version") or 0) <= 0:
        raise RuntimeError("private positions have not been initialized")
    positions = doc.get("positions")
    if not isinstance(positions, list):
        raise RuntimeError("private positions response is invalid")
    return positions, {
        "source": "positions-api", "version": int(doc.get("version") or 0),
        "updated_at": doc.get("updated_at"),
    }

def resolve_positions_with_meta():
    """Use the durable private portfolio first; legacy secrets are fallback only."""
    position_doc, position_storage = load_positions()
    if position_storage.get("source") in ("github", "local") and position_doc.get("version", 0) > 0:
        return position_doc.get("positions", []), {
            "source": position_storage.get("source"), "version": position_doc.get("version"),
            "updated_at": position_doc.get("updated_at"),
        }

    raw = os.environ.get("STOCK_POSITIONS", "").strip()
    if raw.lower().startswith(POSITION_SYNC_PREFIX):
        sync_token = raw[len(POSITION_SYNC_PREFIX):].strip()
        if not sync_token:
            raise RuntimeError("STOCK_POSITIONS sync token is empty")
        return synced_positions(sync_token)

    fallback_positions = json.loads(raw or "[]")
    return fallback_positions, {"source": "legacy-secret", "version": 0, "updated_at": None}


def resolve_positions():
    return resolve_positions_with_meta()[0]

def pct(x, digits=2):
    return "—" if x is None else f"{x*100:+.{digits}f}%"

def price(x):
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "—"


def fetch_daily_context(positions):
    """Fetch one coherent input bundle; value-state/portfolio failure must stop the email."""
    today = datetime.now(TAIPEI).date().isoformat()
    try:
        market = api(f"/api/market-risk?date={today}")
    except Exception as exc:
        print(f"market risk unavailable: {type(exc).__name__}", file=sys.stderr)
        market = {}
    value_state = api("/api/value-current")
    value_portfolio = api("/api/value-portfolio", {"positions": positions})
    ledger = api("/api/decision-ledger?limit=120&agents=claude-value,claude-etf-subtrack")
    return {"market": market, "value_state": value_state, "value_portfolio": value_portfolio,
            "ledger": ledger}


def require_fresh_analysis(value_state):
    expected = datetime.now(TAIPEI).date().isoformat()
    actual = value_state.get("analysis_date_taipei")
    if not actual and value_state.get("generated_at"):
        generated = datetime.fromisoformat(str(value_state["generated_at"]).replace("Z", "+00:00"))
        actual = generated.astimezone(TAIPEI).date().isoformat()
    if actual != expected:
        raise RuntimeError(f"refusing to send stale analysis: expected {expected}, got {actual or 'unknown'}")


def archive_daily_analysis(context, position_meta):
    """Append one private, timestamped audit snapshot for every email run."""
    now = datetime.now(TAIPEI)
    run_id = os.environ.get("GITHUB_RUN_ID") or now.strftime("local-%H%M%S")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    audit_id = f"{now.date().isoformat()}-{run_id}-{attempt}"
    state = context["value_state"]
    portfolio = context["value_portfolio"]
    doc = {
        "schema_version": 1, "audit_id": audit_id, "created_at": now.isoformat(),
        "analysis_date_taipei": state.get("analysis_date_taipei"),
        "market_as_of": state.get("as_of"), "state_generated_at": state.get("generated_at"),
        "source_commit": os.environ.get("GITHUB_SHA"), "position_source": position_meta,
        "market": context.get("market") or {}, "coverage": state.get("coverage") or {},
        "top_picks": state.get("top_picks") or [], "waiting_list": state.get("waiting_list") or [],
        "etf_candidates": state.get("etf_candidates") or [],
        "portfolio_actions": portfolio.get("actions") or [],
        "method": state.get("method"), "shadow": True,
    }
    remote_path = f"value/daily_audit/{now.date().isoformat()}/{run_id}-{attempt}.json"
    local_path = ROOT / "data" / "daily_audit" / now.date().isoformat() / f"{run_id}-{attempt}.json"
    saved = save_document(doc, local_path, remote_path, f"audit(value): freeze daily email {audit_id}")
    if os.environ.get("GITHUB_DATA_TOKEN") and not saved.get("durable"):
        raise RuntimeError(f"daily audit was not saved durably: {saved}")
    return audit_id, saved


def build_html(positions, context=None, position_meta=None, audit_id=None):
    today = datetime.now(TAIPEI).date().isoformat()
    context = context or fetch_daily_context(positions)
    market = context["market"]
    value_state = context["value_state"]
    value_portfolio = context["value_portfolio"]
    m = market
    portfolio_map = {p["symbol"]: p for p in value_portfolio.get("actions", [])}
    led = context["ledger"]
    sigs = [s for s in led.get("signals", []) if s.get("event_type") == "signal"]
    # 同一標的可能有多版凍結（如 ETF 卡修正參考價後重凍）→ 只取最新，避免舊錯卡的失真報酬混入
    def _rank(e):
        # 同日重凍多版時，只比 data_cutoff 會取到舊版 → 需併比 recorded_at
        return (e.get("data_cutoff") or "", e.get("recorded_at") or "")

    newest = {}
    for s in sigs:
        key = (s.get("agent_id"), s.get("symbol"))
        if key not in newest or _rank(s) > _rank(newest[key]):
            newest[key] = s
    sigs = list(newest.values())
    by_symbol = {s.get("symbol"): s for s in sigs}
    accum = [s for s in sigs if "accumulate" in (s.get("action") or "")]
    avoid = [s for s in sigs if "avoid" in (s.get("action") or "")]
    # 成績回顧：涵蓋所有已成熟期數（先前只找 20D/60D/120D，漏掉已算出的 1D/5D）
    HORIZONS = ("1D", "5D", "20D", "60D", "120D")
    agg = {}
    def _sane(o):
        # 凍結時若寫入錯誤參考價（實際發生過：台積電 ref 誤植 100 vs 實際 2290 → +2140% 假超額），
        # 單筆就能把平均帶偏。5D/20D 報酬不可能達 ±100%，一律視為資料異常剔除。
        g = o.get("gross_return")
        e = o.get("excess_return")
        if g is None or abs(float(g)) > 1.0:
            return False
        return not (e is not None and abs(float(e)) > 1.0)

    def _direction(action):
        value = (action or "").lower()
        if "accumulate" in value or "buy_zone" in value or value == "buy":
            return 1
        if "avoid" in value:
            return -1
        return 0

    for s in sigs:
        direction = _direction(s.get("action"))
        if direction == 0:  # watch / hold 沒有交易方向，不納入績效。
            continue
        oc = s.get("outcomes") or {}
        for h in HORIZONS:
            o = oc.get(h)
            if isinstance(o, dict) and o.get("gross_return") is not None and _sane(o):
                excess = o.get("excess_return")
                adjusted = None if excess is None else float(excess) * direction
                agg.setdefault(h, []).append((s.get("symbol"), adjusted, direction))
    risk = m.get("risk_level") or "?"
    risk_color = {"GREEN": "#137333", "YELLOW": "#b45309", "RED": "#c5221f", "BLACK": "#111"}.get(risk, "#555")
    def holding_advice(sym):
        """持有中該怎麼辦：以價值引擎 action 翻成白話（與網站首頁一致）。"""
        current = portfolio_map.get(sym)
        if current:
            why = "；".join(current.get("reasons") or [])
            return f"<b>{current.get('action','—')}</b>" + (f"：{why}" if why else "")
        s = by_symbol.get(sym)
        if not s:
            return "未納入價值分析，僅供價格參考"
        act = (s.get("action") or "").lower()
        er = s.get("entry_range")
        er_txt = f"（便宜區約 {er[0]}–{er[1]}）" if isinstance(er, list) and len(er) == 2 else ""
        if "accumulate" in act:
            return f"目前在便宜區，可考慮分批加碼{er_txt}"
        if "avoid" in act:
            return "體質轉弱且偏貴，宜檢視是否減碼，別再加碼"
        if "watch" in act:
            return f"續抱可以（尤其領息型），但<b>此價位不建議加碼</b>{er_txt}"
        if "hold" in act:
            return "價格合理，續抱領息即可"
        return "—"

    rows = ""
    for p in positions:
        pa = portfolio_map.get(p["symbol"])
        if pa:
            ug = pa.get("unrealized_gain")
            cls = "#137333" if (ug or 0) >= 0 else "#c5221f"
            rows += (f"<tr><td>{p['symbol']}</td><td>{pa.get('action','—')}</td>"
                     f"<td align='right'>{pa.get('price','—')}</td>"
                     f"<td align='right'>{p.get('cost','—')}</td>"
                     f"<td align='right' style='color:{cls};font-weight:600'>{pct(ug)}</td></tr>"
                     f"<tr><td colspan='5' style='font-size:12px;color:#1e3a8a;background:#eff6ff;padding:4px 8px;'>"
                     f"👉 {holding_advice(p['symbol'])}</td></tr>")
        else:
            rows += f"<tr><td>{p['symbol']}</td><td colspan='4' style='color:#c5221f'>今日未取得價值判斷，請人工檢查</td></tr>"
    daily_picks = value_state.get("top_picks") or []
    daily_waiting = value_state.get("waiting_list") or []
    daily_etfs = value_state.get("etf_candidates") or []
    if daily_picks:
        acc_html = "".join(
            f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：現價 {price(s.get('price'))}｜"
            f"{s.get('valuation_zone','—')}｜{s.get('trend','—')}｜<b>{s.get('decision','—')}</b>（{s.get('as_of','')} 重評）</li>"
            for s in daily_picks)
    else:
        acc_html = "".join(
            f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：舊卡區間 {s.get('entry_range','—')}｜"
            f"參考價 {price(s.get('reference_price'))}（{s.get('data_cutoff','')} 凍結；每日狀態暫時不可用）</li>"
            for s in accum) or "<li>今天沒有同時通過品質、估值與止跌條件的標的；保留現金也是結果。</li>"
    waiting_html = "".join(
        f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：{s.get('decision','—')}｜"
        f"{s.get('valuation_zone','—')}｜ROE {price(s.get('roe_ttm'))}%</li>" for s in daily_waiting)
    waiting_block = (f'<p style="font-size:13px"><b>便宜但尚待止跌／高風險：</b></p>'
                     f'<ul>{waiting_html}</ul>') if waiting_html else ""
    etf_html = "".join(
        f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：現價 {price(s.get('price'))}｜"
        f"{s.get('valuation_zone','—')}｜{s.get('trend','—')}｜<b>{s.get('decision','—')}</b></li>"
        for s in daily_etfs)
    etf_block = (f'<h3>🧺 ETF 子池狀態</h3><ul>{etf_html}</ul>' if etf_html else
                 '<h3>🧺 ETF 子池狀態</h3><p style="color:#777">ETF資料暫時無法取得。</p>')
    avoid_html = "、".join(f"{s.get('name','')}{s.get('symbol','')}" for s in avoid) or "無"
    if agg:
        cells = ""
        for h in HORIZONS:
            arr = agg.get(h)
            if not arr:
                continue
            ex = [e for _, e, _ in arr if e is not None]
            avg_ex = sum(ex) / len(ex) if ex else None
            win = (sum(1 for e in ex if e > 0) / len(ex)) if ex else None
            ex_color = "#137333" if (avg_ex or 0) >= 0 else "#c5221f"
            cells += (f"<td align='center' style='border:1px solid #ddd;padding:6px'>"
                      f"<div style='font-size:11px;color:#777'>{h}（{len(arr)} 筆）</div>"
                      f"<div style='font-size:15px;font-weight:600;color:{ex_color}'>對0050 {pct(avg_ex) if avg_ex is not None else '—'}</div>"
                      f"<div style='font-size:11px;color:#777'>方向正確率 {f'{win*100:.0f}%' if win is not None else '—'}</div></td>")
        max_days = max(int(h[:-1]) for h in agg)
        mat_block = (f"<h3>📊 成績回顧（復盤）</h3>"
                     f"<table style='border-collapse:collapse;width:100%'><tr>{cells}</tr></table>"
                     f"<p style='font-size:12px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;"
                     f"border-radius:6px;padding:6px 8px;margin-top:8px'>⚠️ 目前最長僅 {max_days} 個交易日，"
                     f"屬雜訊區間、<b>不能當作方法有效的證據</b>；買進與避開已依方向計分，watch/hold 不列入；判定標準為 60／120 個交易日。</p>")
    else:
        mat_block = "<p style='color:#777'>📊 成績累積中，凍結後第 1 個交易日起會自動出現在此。</p>"
    position_meta = position_meta or {}
    generated_at = value_state.get("generated_at") or "—"
    market_as_of = value_state.get("as_of") or "—"
    position_label = (f"持股SSOT v{position_meta.get('version', 0)}｜"
                      f"更新 {position_meta.get('updated_at') or '未提供'}｜來源 {position_meta.get('source') or 'unknown'}")
    return f"""
<div style="font-family:'Microsoft JhengHei',sans-serif;max-width:640px;margin:auto;color:#222">
  <h2>📈 每日投資摘要 <span style="font-size:13px;color:#777">{today}</span></h2>
  <p style="font-size:12px;color:#475569;background:#f8fafc;padding:8px;border-radius:6px">本次分析執行：{generated_at}<br>市場資料截至：<b>{market_as_of}</b><br>{position_label}<br>稽核編號：{audit_id or '預覽模式'}</p>
  <p>大盤風險燈：<b style="color:{risk_color}">{risk}</b>（{(m.get('regime_label') or m.get('regime') or '')}）</p>
  <h3>💼 我的持股</h3>
  <table border="0" cellpadding="6" style="border-collapse:collapse;width:100%;font-size:14px">
    <tr style="background:#f1f5f9"><th align="left">標的</th><th align="left">持股判斷</th><th align="right">收盤</th><th align="right">成本</th><th align="right">未實現</th></tr>
    {rows}
  </table>
  <h3>🌱 今日優質股與進場時機</h3>
  <ul>{acc_html}</ul>
  {waiting_block}
  {etf_block}
  <p>🔴 avoid：{avoid_html}</p>
  {mat_block}
  <hr style="border:none;border-top:1px solid #ddd">
  <p style="font-size:12px;color:#888">本信由雲端排程自動產生（shadow 研究模式，未證明優於 0050 前不構成投資建議）。
  下單前請以券商 App 實際報價為準。儀表板：<a href="{BASE}/">{BASE}</a></p>
</div>"""

def main():
    addr = os.environ.get("EMAIL_ADDRESS", "").strip()
    pw = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    if not addr or not pw:
        print("EMAIL_ADDRESS / SMTP_APP_PASSWORD 未設定", file=sys.stderr)
        sys.exit(1)
    positions, position_meta = resolve_positions_with_meta()
    if not positions:
        raise RuntimeError("refusing to send: no positions found in private SSOT or fallback secret")
    context = fetch_daily_context(positions)
    require_fresh_analysis(context["value_state"])
    audit_id, audit_storage = archive_daily_analysis(context, position_meta)
    html = build_html(positions, context=context, position_meta=position_meta, audit_id=audit_id)
    msg = MIMEMultipart("alternative")
    market_as_of = context["value_state"].get("as_of") or "資料日期未知"
    msg["Subject"] = f"📈 每日投資摘要 {datetime.now(TAIPEI).date().isoformat()}（市場截至 {market_as_of}）"
    msg["From"] = addr
    msg["To"] = addr
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(addr, pw)
        s.sendmail(addr, [addr], msg.as_string())
    print(json.dumps({"status": "sent", "position_count": len(positions), "audit_id": audit_id,
                      "audit_storage": audit_storage, "market_as_of": market_as_of}, ensure_ascii=False))

if __name__ == "__main__":
    main()
