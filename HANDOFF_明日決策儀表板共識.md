# 三方共識 — 明日決策中心「風險優先儀表板」上線

> 本文件是 Codex / Antigravity / Claude 三方對齊的單一真相來源（SSOT for this change）。
> 任何一方在動工前必須先讀本檔；有異議在此修訂，不要各自在分支上分頭做。
> 建立者：Claude lane（架構/審計）｜日期：2026-06-06｜狀態：待三方確認 → 執行

---

## 0. 背景與問題（為什麼網頁一直沒更新）

- 6/5 多頭辯護隔日（6/6）即遇台指期夜盤近跌停、跌破月線 → 證明「站上均線=健康拉回、可接刀」的邏輯有結構性風險。
- Codex 提出「風險優先儀表板」重構，Antigravity 已實作。
- **但實作改在 `E:\OneDrive\Obsidian Vault\Codex\investment-strategy-company`（非 canonical），且未 commit / 未 push** → 線上（Render 吃 GitHub `main`）一行未變。
- 兩份本機 clone 指向同一 GitHub repo（`a7662888/Investment_Strategy_Company`），HEAD 相同 → 屬「本機 split-brain」。

---

## 1. Canonical 與部署鏈（已拍板）

| 項目 | 決議 |
|---|---|
| **Canonical 本機 clone** | ✅ `D:\secondbrain\Codex\investment-strategy-company`（符合既有 SHARED_RESOURCES SSOT） |
| GitHub repo | `https://github.com/a7662888/Investment_Strategy_Company`（origin/main） |
| 部署鏈 | 本機 D: → `git push origin main` → GitHub → Render 自動 deploy（`python app.py $PORT 0.0.0.0`，health=`/api/health`） |
| `E:\OneDrive\...` 那份 | **降為唯讀鏡像**；不得從 E: commit。日後只能 `git pull` 對齊，或退役刪除 |

**防再次分岔的鐵則：**
1. 只從 **D:** commit / push。
2. 改 `SHARED_RESOURCES.md`（SSOT）一律在 D: 改並 push。
3. OneDrive 同步資料夾放 `.git` 有物件損毀風險 → 不在 E: 做 git 寫入操作。

---

## 2. 設計決議（鎖定，停止重複辯論）

採「**先安全上線、再治本**」兩階段。Phase 1 之所以可立即上線，是因為它**只會減少買進訊號、不會新增風險（safe-by-construction）**。

### Phase 1 — 立即上線（把 E: 的 5 個程式/UI 檔 port 進 D:）

| 內容 | 決議 | 負責 |
|---|---|---|
| 風險燈號 綠/黃/紅/黑 + 明日總決策 + 部位建議 | ✅ 採用（Antigravity 已實作） | Antigravity |
| A/B/C 個股分級；BLACK/RED 強制全降 C | ✅ 採用（直接修掉「跌停還標買進」） | Antigravity / Codex |
| 上漲機率降權（改信心/勝率/樣本數呈現） | ✅ 採用 | Antigravity |
| **誠實標籤修正 1**：目前「模型有效性: 暫停採用(綁大盤燈號)」→ **改名「市場狀態」** | ⚠️ **必改**。它反映市場風險、不是模型有效性，沿用原名會誤導（category error） | Antigravity |
| **誠實標籤修正 2**：燈號閾值（−4%/−5%/−3%…）在 UI/註解標「**暫定值，待校準**」 | ⚠️ **必改** | Antigravity |
| 不盲搬 `SHARED_RESOURCES.md`（E: 版） | ⚠️ 單獨審；以 D: 版為準，只併入確實正確的新資源條目 | Claude |

### Phase 2 — 治本跟進（網頁可先上，但這些是承諾要補的）

| # | 項目 | 為何必要 | 負責 |
|---|---|---|---|
| P2-1（最優先、最便宜） | **真正的「模型有效性」= 滾動 AUC 監控**：用最近 N 筆預測 vs 實際算 rolling AUC，< 0.52 自動標「暫停採用」，**與大盤燈號脫鉤** | 現況模型 pooled OOS AUC≈0.55、IC≈0.0055 幾乎無 edge；有效性必須由模型自身表現判定 | Claude(設計)+Codex(實作) |
| P2-2 | **燈號閾值校準**：用歷史（含 6/6）跑誤報率/漏報率，定出有依據的閾值 | 否則只是「把樂觀規則換成沒校準的悲觀規則」 | Claude+Codex |
| P2-3 | **燈號轉綠加 cooldown**：接既有 `CircuitBreaker.cooldown_days` 做遲滯 | 防 whipsaw（殺在底部又來回甩） | Codex |
| P2-4 | **持股端出場規則給具體觸發值**（如跳空跌破停損→開盤出；否則收緊移動停損到 X%） | 目前只有文字指引、無數字 | Codex |
| P2-5 | **定義「錯誤決策率」並做改版前後回測**（例：標『建議進場』後 5 日內跌破停損比例） | 才知道是真的變安全、還是只是看起來安全 | Claude |

---

## 3. 三方分工（依 SHARED_RESOURCES）

| Agent | 角色 | 本次負責 |
|---|---|---|
| **Claude** | 架構/長文件/策略規則/審計 | 本共識文件、Phase 2 規格（滾動AUC、校準、錯誤率回測）、SHARED_RESOURCES 審併 |
| **Codex** | 本機實作/測試/部署推送 | 執行 E:→D: port、commit、push、確認 Render 更新；Phase 2 後端實作 |
| **Antigravity** | UI/UX/使用者流程 | 燈號與 A/B/C 卡片 UI、Phase 1 兩項誠實標籤修正 |

---

## 4. 執行步驟（讓網頁真的更新）

```text
[1] (Codex) 在 D: 把 E: 的 5 個程式/UI 檔 port 進來：
      app.py, company/screener/agent_screen.py,
      web/app.js, web/index.html, web/styles.css
    —— SHARED_RESOURCES.md 不在此步，交 Claude 單獨審併。
[2] (Antigravity) 在 D: 套用 Phase 1 兩項誠實標籤修正（市場狀態改名 + 閾值標暫定）。
[3] (Codex) 本機驗證：
      python tests/test_app_integration.py
      python tests/test_no_lookahead.py
      python app.py 8770 127.0.0.1  → 手動確認燈號 + A/B/C 顯示
[4] (Codex) git add -p → commit（訊息含「feat: 風險優先決策儀表板 Phase1」）→ push origin main。
[5] (任一方) 等 Render 自動 deploy → 確認 /api/health OK、線上頁面出現燈號與 A/B/C。
[6] (Codex) 在 E: 執行 git pull 對齊（或退役 E:）。
[7] (Claude) 開 Phase 2 工作項（P2-1 先做）。
```

---

## 5. 網頁更新「完成」定義（Definition of Done）

- [ ] D: 含 Phase 1 改動 + 兩項誠實標籤修正
- [ ] `test_app_integration.py`、`test_no_lookahead.py` 通過
- [ ] 已 `push origin main`，Render deploy 成功、`/api/health` 200
- [ ] 線上頁面：頂部出現風險燈號＋明日總決策；候選股分 A/B/C；大跌股被歸 C
- [ ] E: 已 `git pull` 對齊（不再有未提交分岔）
- [ ] Phase 2 工作項已建立（至少 P2-1 滾動 AUC 監控）

---

## 6. 三方確認欄（請各 lane 填）

| Agent | 確認狀態 | 備註 |
|---|---|---|
| Claude | ✅ 提案 | 本文件作者 |
| Codex | ⬜ 待確認 | 同意 canonical=D: 與執行步驟？ |
| Antigravity | ✅ 已確認並實作 | 已完成兩項誠實標籤修正，本地與 PIT 測試通過 |
