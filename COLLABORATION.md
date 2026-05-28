# 三方協作守則 — Antigravity · Claude Code · Codex

本檔定義三個 AI 代理在同一個 GitHub repo 上**互不覆蓋、可非同步溝通**的規則。
狀態與資源清單看 [`SHARED_RESOURCES.md`](SHARED_RESOURCES.md);彼此留言/交接看 [`AGENT_LOG.md`](AGENT_LOG.md)。

## 1. 分工(lane)

| 代理 | 負責 | 主要路徑 |
|---|---|---|
| **Antigravity** | UI、產品流程、手機體驗、部署 UX | 前端外觀、`web/` 視覺、mobile |
| **Claude Code** | 策略規則、長文件、審計報告、交接文字 | `company/`、`STRATEGY_ENGINE.md`、`run_*.py`、`tests/`、`config/`、`requirements-strategy.txt` |
| **Codex** | 本機實作、測試、資料抓取、GitHub push、部署接線 | `app.py`、`web/`、`Procfile`、`render.yaml`、`runtime.txt`、root `requirements.txt` |

## 2. 檔案歸屬(動別人的檔案前先看這裡)

| 檔案/目錄 | 擁有者 | 別人可否動 |
|---|---|---|
| `app.py`、`web/`、`Procfile`、`render.yaml`、`runtime.txt`、root `requirements.txt`、`DEPLOYMENT.md` | Codex/Antigravity | ❌ 非擁有者勿直接改;要改走 PR/分支或在 `AGENT_LOG.md` 提出 |
| `company/`、`STRATEGY_ENGINE.md`、`run_*.py`、`tests/`、`config/`、`requirements-strategy.txt` | Claude | ❌ 同上 |
| `README.md` | Codex(現行)| 只增不改語意,大改先在 log 提 |
| `SHARED_RESOURCES.md`、`COLLABORATION.md`、`AGENT_LOG.md`、`.gitignore` | **共享** | ✅ **只增不刪**(additive),保留他人段落 |
| `DISCLAIMER.md` | 共享 | 只增不改語意 |

## 3. Git 紀律(避免互相蓋掉)

1. **push 前一定 `git fetch` + `git rebase origin/main`**;遠端常被別的代理推進。
2. **只動自己 lane 的檔案**;共享檔(第 2 表)一律 additive,保留別人內容。
3. **明確路徑 `git add`**,不要 `git add -A`(避免誤推 `tools/`、`data_cache/`、`reports/`、`.env`)。
4. 相依分開:Codex 的 `app.py` 維持純標準函式庫(root `requirements.txt`);Claude 的引擎相依放 `requirements-strategy.txt`。
5. commit message 前綴標明 lane:`ui(antigravity):`、`feat(claude-lane):`、`feat(codex):`、`docs(shared):`。
6. 要動到別人 lane 的檔案 → **開分支或 PR**,並在 `AGENT_LOG.md` 留言請擁有者審,不直接推 `main`。

## 4. 怎麼溝通

- **要交辦 / 提問 / 回報** → 在 [`AGENT_LOG.md`](AGENT_LOG.md) 最上方新增一筆(append,不刪舊的)。
- **共享狀態變更**(新部署 URL、tunnel、port、資料源、可引用程式庫)→ 更新 [`SHARED_RESOURCES.md`](SHARED_RESOURCES.md)。
- **可被引用的成果** → 登記在 `SHARED_RESOURCES.md` 的 *Shared Code Resources*,讓別人重用而非重做。

## 5. 衝突發生時

- rebase 衝突在**自己 lane 的檔案** → 自己解。
- 衝突在**別人 lane 的檔案** → 保留對方版本(`--theirs`/`--ours` 視情況),把你的需求改成在 `AGENT_LOG.md` 留言或開 PR,不要硬蓋。
- 衝突在**共享檔** → 兩邊內容都保留(union),刪任何一方段落前先在 log 確認。
