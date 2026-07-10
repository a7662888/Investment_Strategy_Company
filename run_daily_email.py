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
    accum = [s for s in sigs if "accumulate" in (s.get("action") or "")]
    avoid = [s for s in sigs if "avoid" in (s.get("action") or "")]
    matured = []
    for s in sigs:
        oc = s.get("outcomes") or {}
        for h in ("20D", "60D", "120D"):
            o = oc.get(h)
            if isinstance(o, dict) and o.get("gross_return") is not None:
                matured.append((s.get("symbol"), h, o.get("gross_return"), o.get("excess_return")))
    risk = m.get("risk_level") or "?"
    risk_color = {"GREEN": "#137333", "YELLOW": "#b45309", "RED": "#c5221f", "BLACK": "#111"}.get(risk, "#555")
    rows = ""
    for p in positions:
        pl = plans.get(p["symbol"])
        if pl:
            ug = pl.get("unrealized_gain")
            cls = "#137333" if (ug or 0) >= 0 else "#c5221f"
            rows += (f"<tr><td>{p['symbol']}</td><td>{pl.get('action','—')}</td>"
                     f"<td align='right'>{pl.get('last_close','—')}</td>"
                     f"<td align='right'>{p.get('cost','—')}</td>"
                     f"<td align='right' style='color:{cls};font-weight:600'>{pct(ug)}</td></tr>")
        else:
            rows += f"<tr><td>{p['symbol']}</td><td colspan='4' style='color:#c5221f'>今日未取得計畫</td></tr>"
    acc_html = "".join(
        f"<li><b>{s.get('name','')} {s.get('symbol','')}</b>：買進區間 {s.get('entry_range','—')}｜參考價 {s.get('reference_price','—')}（{s.get('data_cutoff','')} 凍結）</li>"
        for s in accum) or "<li>目前無 accumulate 標的（市場不便宜時，沒有買進本來就是紀律）</li>"
    avoid_html = "、".join(f"{s.get('name','')}{s.get('symbol','')}" for s in avoid) or "無"
    if matured:
        mat_html = "".join(f"<li>{sym} {h}：報酬 {pct(g)}｜對 0050 超額 {pct(e)}</li>" for sym, h, g, e in matured[:12])
        mat_block = f"<h3>📊 已成熟成績（≥20 交易日）</h3><ul>{mat_html}</ul>"
    else:
        mat_block = "<p style='color:#777'>📊 20/60/120 日成績尚在累積，到期會自動出現在此。</p>"
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
