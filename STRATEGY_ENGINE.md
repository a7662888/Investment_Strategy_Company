# 策略引擎 + 操盤手 + 審計資源庫(Claude lane)

> 依 [`SHARED_RESOURCES.md`](SHARED_RESOURCES.md) 分工,本文件與 `company/` 為 **Claude Code** 負責的
> 策略規則、審計、長文件部分,**供 Codex 的 `app.py` 與 Antigravity 的 UI 引用**,不取代它們。

## 這個資源庫提供什麼

`company/` 是一個可被任何前端(`app.py`、Streamlit、未來 API)呼叫的純 Python 策略/審計庫:

| 模組 | 內容 | 對應 lane 用途 |
|---|---|---|
| `company/operator/` | 兩派操盤手(`TrendOperator` 趨勢、`ValueChipOperator` 價值籌碼)逐日決策、操盤日誌、定期復盤 | app.py 可呼叫產生「明日建議 + 理由」 |
| `company/operator/recommend.py` | 收盤後掃描 watchlist → 買進/獲利了結/停損/續抱/觀望 分類 + 理由 | 直接餵前端的推薦清單 |
| `company/strategies/` | 組合版 C-1 價值流 / C-2 動能流 | 多股組合策略 |
| `company/sandbox/` | PIT 回測引擎、台股成本模型、組合熔斷 | 模擬訓練、回測 |
| `company/audit/` | 硬指標(Sharpe/MDD/Calmar…)、規則化審計旗標、單筆交易貢獻度 | 審計報告 |
| `company/allocator/` | 市場 regime 分類、依 regime 配置 | 策略配置 |
| `company/validation/` | walk-forward 樣本外、成本加倍壓測 | 防過擬合驗證 |
| `company/data/` | PIT 資料介面 + 合成資料 + 單股 FinMind 載入(可被 Codex 的 ingestion 取代) | 資料來源(介面為主) |
| `company/roles/*.md` | 六角色的 prompt 規格(B/A/C-1/C-2/D/E) | 策略規則文件 |

## 核心可信原則(六道防線)

① Point-in-Time(只用 ≤T 資料,結構性防未來函數) ② 確定性硬指標(審計不憑感覺)
③ Walk-Forward 樣本外(抓過擬合) ④ 台股交易成本全計入 ⑤ regime 配置 ⑥ 訊號用 Python、LLM 只在路口。

## app.py 怎麼引用(範例)

```python
import pandas as pd
from company.data import single_stock as ss
from company.operator.recommend import recommend_one
from company.operator.trend import TrendOperator
from company.operator.value_chip import ValueChipOperator
from company.sandbox.costs import TaiwanCostModel
from company.sandbox.circuit_breaker import CircuitBreaker

data = ss.load("2327", "2020-01-01", "2026-05-28")   # Codex 的 ingestion 亦可替換此處
costs, breaker = TaiwanCostModel(), CircuitBreaker(halt_drawdown=0.20, cooldown_days=20)
end = pd.Timestamp.today().normalize()

for op in (TrendOperator(), ValueChipOperator()):
    rec = recommend_one(data, op, costs, 1_000_000, breaker, end)
    print(rec.symbol, rec.operator, rec.label, rec.reason, f"未實現{rec.unrealized_pct:+.1%}")
```

## 執行(本機驗證)

```bash
pip install -r requirements-strategy.txt
python run_operator.py                 # 單股逐日操盤(2327 真實資料)→ reports/operator_2327_*
python run_contest.py                  # 組合紅藍對抗(合成資料)
python tests/test_no_lookahead.py      # 防線① 守門測試
```

## 實測重點(2327,2020-01~2026-05)

C-1 價值籌碼 +89%(勝過買進持有 +58%);C-2 趨勢跟隨 +57% 但 MDD −55%。
重點不在數字,而在**每日決策都有理由、錯誤(追高/殺低/whipsaw/錯過大波段)可量化、可被持續優化**。

## 分工備註

- 本資源庫不含前端 UI(屬 Antigravity lane)與部署接線(屬 Codex lane)。
- `company/data/single_stock.py` 的 FinMind 載入可由 Codex 的 ingestion 取代;操盤手只依賴 `StockView` 的 PIT 介面。
- 新功能請放各自命名空間,避免覆蓋他人工作。
