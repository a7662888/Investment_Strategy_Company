# -*- coding: utf-8 -*-
"""永豐 1 分 K → 每日彙整（不保存逐分資料）。

單位與時間戳依官方文件範例驗算（2026-10-10）：
- ts 為「台北牆上時間直接當 UTC 編碼」的奈秒：範例 1779094860000000000
  以 UTC 解讀為 2026-05-18 09:01，與文件 polars 轉換結果一致。
- 股票 Volume 單位為張、Amount 為元：範例 2330 首分鐘 Amount 57.09 億 ÷
  (2565 張 × 1000) ≈ 2225.7，落在該分鐘 2225–2235 區間。
每日仍以「均價落在當日高低之間」做執行期檢查，不符的日子捨棄。
"""
from __future__ import annotations

from datetime import datetime, timezone

SESSION_START = "09:00"
SESSION_END = "13:30"


def parse_kbars(kbars) -> list[tuple]:
    raw = kbars.dict() if hasattr(kbars, "dict") else dict(kbars or {})
    ts = raw.get("ts") or []
    cols = [raw.get(k) or [] for k in ("Open", "High", "Low", "Close", "Volume", "Amount")]
    rows = []
    for i, stamp in enumerate(ts):
        try:
            wall = datetime.fromtimestamp(int(stamp) / 1e9, timezone.utc).replace(tzinfo=None)
            values = [float(c[i]) for c in cols]
        except (TypeError, ValueError, IndexError, OverflowError, OSError):
            continue
        rows.append((wall, *values))
    return rows


def _vwap(bars) -> float | None:
    volume = sum(b[5] for b in bars)
    return sum(b[6] for b in bars) / (volume * 1000) if volume else None


def aggregate_days(bars: list[tuple]) -> dict[str, dict]:
    by_day: dict[str, list] = {}
    for bar in bars:
        hhmm = bar[0].strftime("%H:%M")
        if SESSION_START <= hhmm <= SESSION_END:
            by_day.setdefault(bar[0].strftime("%Y-%m-%d"), []).append(bar)
    out = {}
    for day, rows in by_day.items():
        rows.sort(key=lambda b: b[0])
        high = max(b[2] for b in rows)
        low = min(b[3] for b in rows)
        vwap = _vwap(rows)
        if vwap is None or not (low * 0.995 <= vwap <= high * 1.005):
            continue   # 單位或資料異常：寧缺勿用
        first = [b for b in rows if b[0].strftime("%H:%M") <= "09:15"]
        last = [b for b in rows if b[0].strftime("%H:%M") > "13:00"]
        out[day] = {
            "open": rows[0][1], "high": high, "low": low, "close": rows[-1][4],
            "volume_lots": int(sum(b[5] for b in rows)), "amount": round(sum(b[6] for b in rows)),
            "vwap": round(vwap, 4),
            "vwap_first15": round(_vwap(first), 4) if _vwap(first) else None,
            "vwap_last30": round(_vwap(last), 4) if _vwap(last) else None,
            "low_time": min(rows, key=lambda b: b[3])[0].strftime("%H:%M"),
            "high_time": max(rows, key=lambda b: b[2])[0].strftime("%H:%M"),
            "bars": len(rows),
        }
    return out


# 儲存格式：每檔一段 CSV 字串。durable_document 以 indent=2 寫檔，若每日存成 dict／list，
# 30 檔 × 250 日會膨脹到 1 MB 以上（帳本曾因超過 1 MB 出事故）；字串不會被逐值換行。
FIELDS = ("open", "high", "low", "close", "volume_lots", "amount", "vwap",
          "vwap_first15", "vwap_last30", "low_time", "high_time", "bars")
_TEXT = {"low_time", "high_time"}


def encode_days(days: dict[str, dict]) -> str:
    lines = []
    for day, row in sorted(days.items()):
        values = ["" if row.get(f) is None else (row[f] if f in _TEXT else f"{row[f]:.12g}") for f in FIELDS]
        lines.append(",".join([day, *map(str, values)]))
    return "\n".join(lines)


def decode_days(text: str) -> dict[str, dict]:
    out = {}
    for line in (text or "").splitlines():
        parts = line.split(",")
        if len(parts) != len(FIELDS) + 1:
            continue
        row = {}
        for field, value in zip(FIELDS, parts[1:]):
            row[field] = None if value == "" else (value if field in _TEXT else float(value))
        out[parts[0]] = row
    return out
