# Shared Resources

Canonical shared resource file for Antigravity, Claude Code, and Codex working on this app.

協作守則見 [`COLLABORATION.md`](COLLABORATION.md);三方非同步留言/交接見 [`AGENT_LOG.md`](AGENT_LOG.md)。

## Canonical Repository

- GitHub: https://github.com/a7662888/Investment_Strategy_Company
- Local path: `C:\Users\User\OneDrive\應用程式\remotely-save\Obsidian Vault\secondbrain\Codex\investment-strategy-company`
- Branch: `main`

## Runtime

- Local command: `py app.py 8770 127.0.0.1`
- LAN command: `py app.py 8770 0.0.0.0`
- Render start command: `python app.py $PORT 0.0.0.0`
- Render health check: `/api/health`
- Local URL: `http://127.0.0.1:8770`
- Render URL: `https://investment-strategy-company.onrender.com`
- Render health check: `https://investment-strategy-company.onrender.com/api/health`
- Temporary tunnel URL: `https://unix-legendary-douglas-anticipated.trycloudflare.com`
- Separate detected tunnel: `D:\secondbrain\投資策略顧問公司` uses `http://localhost:8501`; do not modify it from this repo unless explicitly requested.

## Do Not Commit

- `cloudflared*.log`
- `data/web_cache/`
- `.env`
- local process logs
- `tools/`(cloudflared 等可攜執行檔)
- `data_cache/`(單股 FinMind 快取,可重生)
- `reports/`(產出的審計/復盤報告,可重生)

## Project Rules

- This is a research and simulation dashboard, not an investment advisory or trading automation product.
- It does not connect to broker APIs or place live orders.
- Recommendations are after-close next-session research plans.
- Holdings are simulation inputs only.

## Agent Coordination

- Antigravity: UI, product flow, mobile experience, deployment UX.
- Claude Code: strategy rules, long-form docs, audit reports, handoff text.
- Codex: local implementation, tests, data ingestion, GitHub push, deployment wiring.

## Shared Code Resources

可互相引用的成果,避免重做。新增資源請登記於此。

### 舊版台股預測報告儀表板(設計參考)

- 原始位置:`D:\Temp\taiwan_stock_prediction_report_dashboard.html`
- repo 參考副本:`docs/design-references/taiwan_stock_prediction_report_dashboard.html`
- 可借用內容:
  - 模型權重 UI(momentum/reversal/volatility/liquidity/gap)。
  - 可列印/匯出 HTML/JSON 報告。
  - 分數長條圖與 top/bottom 摘要。
  - Proxy 契約說明(TWSE/TPEx/券商 API/正式資料源)。
- 不建議直接搬入主 app:
  - 單檔 HTML 太大,與目前 Render API 架構不同。
  - 有瀏覽器直連 TWSE/TPEx 的 CORS 風險。
  - 目前 app.py 已有 RSI/MACD 與明日計畫;應抽取其「報告產生器」與「權重 UI」概念,不要整頁替換。

### 策略引擎 + 操盤手 + 審計庫(Claude lane)

- 位置:`company/`(純 Python,可被 `app.py` / UI / 任何前端 import)。完整說明見 [`STRATEGY_ENGINE.md`](STRATEGY_ENGINE.md)。
- 相依:`pip install -r requirements-strategy.txt`(刻意與 root `requirements.txt` 分開,不影響 Render 的純 stdlib `app.py`)。
- 主要可用介面:
  - `company.operator.recommend.recommend_one(...)` → 回傳某股某操盤手的**明日建議**(買進/獲利了結/停損/續抱/觀望)+ 理由 + 未實現損益%。
  - `company.operator.trend.TrendOperator` / `company.operator.value_chip.ValueChipOperator` → 兩派操盤手(逐日決策、含理由)。
  - `company.operator.journal.JournalEngine` → 單股逐日操盤(T 決策/T+1 開盤成交、含成本與熔斷),產出操盤日誌。
  - `company.operator.review.review_report(...)` → 月/季復盤 + 錯誤偵測(追高/殺低/whipsaw/錯過大波段)。
  - `company.audit.metrics` / `company.audit.attribution` → 硬指標與單筆交易貢獻度(審計用)。
  - `company.sandbox`(回測引擎/成本/熔斷)、`company.validation`(walk-forward/成本壓測)、`company.allocator`(regime 分類)。
- 資料:`company.data.single_stock.load(symbol, start, end)`(FinMind 單股,自動快取);**Codex 的 ingestion 可替換此處**,操盤手只依賴 `StockView` 的 PIT 介面。
- 給前端最短用法見 STRATEGY_ENGINE.md 的「app.py 怎麼引用」範例。

> 建議:`app.py` 的「明日建議」直接呼叫 `recommend_one`,即可拿到帶理由的買/賣/續抱清單,不需重寫策略。

## Update This File When

- A new public deployment URL is created.
- A persistent Cloudflare tunnel or Render URL replaces the temporary URL.
- A new local port is used.
- A new data provider or cache directory is added.
- A new canonical repo or branch is selected.
