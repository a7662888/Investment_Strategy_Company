# -*- coding: utf-8 -*-
"""每日投資摘要 Email（雲端排程版，GitHub Actions 每交易日 16:35 台北執行）。

個資零入庫：收件人、SMTP 應用程式密碼、持股全部來自環境變數（Actions 加密 secrets）：
  EMAIL_ADDRESS      寄件人=收件人 Gmail
  SMTP_APP_PASSWORD  Gmail 應用程式密碼
  STOCK_POSITIONS    JSON 陣列，如 [{"symbol":"0056.TW","shares":1000,"cost":53.95}]
資料只讀已部署網站的公開 API；shadow 免責聲明內建。
"""
import json, os, smtplib, ssl, sys, urllib.request
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BASE = os.environ.get("SITE_BASE", "https://investment-strategy-company.onrender.com")

def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "daily-email"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())

def pct(x, digits=2):
    return "—" if x is None else f"{x*100:+.{digits}f}%"

def build_html(positions):
    today = date.today().isoformat()
    plan = api("/api/next-day-plan", {
        "symbols": [p["symbol"] for p in positions],
        "positions": positions, "end": today, "lookback_days": 320})
    m = plan.get("market_index") or {}
    plans = {p["symbol"]: p for p in plan.get("plans", [])}
    led = api("/api/decision-ledger?limit=120&agents=claude-value,claude-etf-subtrack")
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
    for s in sigs:
        oc = s.get("outcomes") or {}
        for h in HORIZONS:
            o = oc.get(h)
            if isinstance(o, dict) and o.get("gross_return") is not None:
                agg.setdefault(h, []).append((s.get("symbol"), o.get("gross_return"), o.get("excess_return")))
    risk = m.get("risk_level") or "?"
    risk_color = {"GREEN": "#137333", "YELLOW": "#b45309", "RED": "#c5221f", "BLACK": "#111"}.get(risk, "#555")
    def holding_advice(sym):
        """持有中該怎麼辦：以價值引擎 action 翻成白話（與網站首頁一致）。"""
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
        pl = plans.get(p["symbol"])
        if pl:
            ug = pl.get("unrealized_gain")
            cls = "#137333" if (ug or 0) >= 0 else "#c5221f"
            rows += (f"<tr><td>{p['symbol']}</td><td>{pl.get('action','—')}</td>"
                     f"<td align='right'>{pl.get('last_close','—')}</td>"
                     f"<td align='right'>{p.get('cost','—')}</td>"
                     f"<td align='right' style='color:{cls};font-weight:600'>{pct(ug)}</td></tr>"
                     f"<tr><td colspan='5' style='font-size:12px;color:#1e3a8a;background:#eff6ff;padding:4px 8px;'>"
                     f"👉 {holding_advice(p['symbol'])}</td></tr>")
        else:
            rows += f"<tr><td>{p['symbol']}</td><td colspan='4' style='color:#c5221f'>今日未取得計畫</td></tr>"
    acc_html = "".join(
        f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：買進區間 {s.get('entry_range','—')}｜參考價 {s.get('reference_price','—')}（{s.get('data_cutoff','')} 凍結）</li>"
        for s in accum) or "<li>目前無 accumulate 標的（市場不便宜時，沒有買進本來就是紀律）</li>"
    avoid_html = "、".join(f"{s.get('name','')}{s.get('symbol','')}" for s in avoid) or "無"
    if agg:
        cells = ""
        for h in HORIZONS:
            arr = agg.get(h)
            if not arr:
                continue
            avg = sum(g for _, g, _ in arr) / len(arr)
            ex = [e for _, _, e in arr if e is not None]
            avg_ex = sum(ex) / len(ex) if ex else None
            win = (sum(1 for e in ex if e > 0) / len(ex)) if ex else None
            ex_color = "#137333" if (avg_ex or 0) >= 0 else "#c5221f"
            cells += (f"<td align='center' style='border:1px solid #ddd;padding:6px'>"
                      f"<div style='font-size:11px;color:#777'>{h}（{len(arr)} 筆）</div>"
                      f"<div style='font-size:15px;font-weight:600'>{pct(avg)}</div>"
                      f"<div style='font-size:11px;color:{ex_color}'>對0050 {pct(avg_ex) if avg_ex is not None else '—'}</div>"
                      f"<div style='font-size:11px;color:#777'>勝率 {f'{win*100:.0f}%' if win is not None else '—'}</div></td>")
        max_days = max(int(h[:-1]) for h in agg)
        mat_block = (f"<h3>📊 成績回顧（復盤）</h3>"
                     f"<table style='border-collapse:collapse;width:100%'><tr>{cells}</tr></table>"
                     f"<p style='font-size:12px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;"
                     f"border-radius:6px;padding:6px 8px;margin-top:8px'>⚠️ 目前最長僅 {max_days} 個交易日，"
                     f"屬雜訊區間、<b>不能當作方法有效的證據</b>；判定標準為 60／120 個交易日。</p>")
    else:
        mat_block = "<p style='color:#777'>📊 成績累積中，凍結後第 1 個交易日起會自動出現在此。</p>"
    return f"""
<div style="font-family:'Microsoft JhengHei',sans-serif;max-width:640px;margin:auto;color:#222">
  <h2>📈 每日投資摘要 <span style="font-size:13px;color:#777">{today}</span></h2>
  <p>大盤風險燈：<b style="color:{risk_color}">{risk}</b>（{(m.get('regime_label') or m.get('regime') or '')}）</p>
  <h3>💼 我的持股</h3>
  <table border="0" cellpadding="6" style="border-collapse:collapse;width:100%;font-size:14px">
    <tr style="background:#f1f5f9"><th align="left">標的</th><th align="left">明日建議</th><th align="right">收盤</th><th align="right">成本</th><th align="right">未實現</th></tr>
    {rows}
  </table>
  <h3>🟢 價值引擎：目前值得留意（accumulate）</h3>
  <ul>{acc_html}</ul>
  <p>🔴 avoid：{avoid_html}</p>
  {mat_block}
  <hr style="border:none;border-top:1px solid #ddd">
  <p style="font-size:12px;color:#888">本信由雲端排程自動產生（shadow 研究模式，未證明優於 0050 前不構成投資建議）。
  下單前請以券商 App 實際報價為準。儀表板：<a href="{BASE}/">{BASE}</a></p>
</div>"""

def main():
    addr = os.environ.get("EMAIL_ADDRESS", "").strip()
    pw = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    positions = json.loads(os.environ.get("STOCK_POSITIONS", "[]"))
    if not addr or not pw:
        print("EMAIL_ADDRESS / SMTP_APP_PASSWORD 未設定", file=sys.stderr)
        sys.exit(1)
    html = build_html(positions)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 每日投資摘要 {date.today().isoformat()}"
    msg["From"] = addr
    msg["To"] = addr
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(addr, pw)
        s.sendmail(addr, [addr], msg.as_string())
    print("daily email sent")

if __name__ == "__main__":
    main()
