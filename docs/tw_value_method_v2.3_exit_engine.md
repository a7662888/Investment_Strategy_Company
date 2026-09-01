# tw_value_method v2.3 — Exit Engine（shadow）

## 目的

對已持有標的提供可解釋的獲利保護／風險退出提醒。它不以「已賺 20%」或單一 RSI
直接觸發賣出；價格上漲本身不是錯，必須結合估值、基本面、趨勢、盈餘品質與持倉集中度。

## v1 計分（100 分）

| 分項 | 上限 | 現行可用輸入 |
|---|---:|---|
| Valuation stretch | 30 | 相對自身歷史 PER／PBR 百分位（依產業子軌） |
| Fundamental deterioration | 30 | 月營收 YoY、季度營收／EPS YoY、毛利率／營益率連降、品質硬篩 |
| Momentum breakdown | 20 | 收盤相對 MA20／MA60、MA20 vs MA60、距 252 日高點回撤 |
| Accounting quality | 10 | 近四季 OCF／淨利、負債比年變化 |
| Position risk | 10 | 以使用者輸入持股計算的單一標的權重；ETF 使用較寬門檻 |

分數區間：0–29 Hold；30–44 Watch profit；45–59 Trim 20–25%；60–74
Trim 30–50%；75 以上 Take profit 50–75%。若品質硬篩失敗且至少兩項獨立基本面
惡化訊號成立，才標記 Thesis broken／Exit。資料覆蓋低於 70/100 時，任何減碼訊號
一律降為觀察。

## 明確未啟用

- 分析師一致預估 EPS revision：現行免費資料層沒有可靠 point-in-time 歷史，加入會造成
  look-ahead 或把缺值當證據。
- ATR trailing stop：現行每日狀態只保留可信任收盤序列，尚未把 high/low 與拆股調整
  納入一致驗證；固定 ATR 倍數也尚未在本系統台股樣本驗證。
- 稅務最佳化：使用者稅務身分與交易成本不在資料模型內。

## 科學依據與限制

- Momentum 具有長期橫斷面實證，因此「創新高」不單獨構成賣出理由；本模型只對趨勢
  破壞計分。Jegadeesh & Titman (2001), DOI: 10.1111/0022-1082.00342。
- Quality 並非單一公認因子；較穩健者集中於 profitability、investment、accounting
  quality 與 payout/dilution，因此本模型只採可追溯的獲利品質與現金轉換，不宣稱所有
  財務比率都有預測力。Hsu et al. (2019), DOI: 10.1080/0015198X.2019.1567194。
- 台灣市場研究支持分析師盈餘預測修正的資訊含量，但本系統尚無合格資料，因此不啟用。
  Finance Research Letters 88 (2026) 109164, DOI: 10.1016/j.frl.2025.109164。

所有 cutoff 都是預先註冊的 shadow 規則，不是已證明的最佳參數。每日私有 audit 會保留
當時分數、分項與資料日期，後續比較 20D／60D／120D／250D 的 continued-hold return、
avoided drawdown、false-positive sell rate、交易成本後機會成本；滿足既有獨立樣本與期間
門檻前，不得宣稱能提高報酬。
