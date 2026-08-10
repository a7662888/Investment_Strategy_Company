# One Person Investment Strategy Company

研究與模擬用途的一人投資策略公司儀表板。正式決策採單一價值引擎；其他模型只可提供補充證據或反方風險，不另發競賽名單。

## 共享資源

Antigravity、Claude Code、Codex 都應先讀 [`SHARED_RESOURCES.md`](SHARED_RESOURCES.md)，再新增部署、tunnel、資料來源或長期狀態檔。

## 功能

- 母池100每月依流動性與產業上限更新。
- 100/100基本面輪替更新，依品質硬篩、三年估值位階與20/60日趨勢產生每日價值狀態。
- 支援持股追加、續抱、減碼與賣出檢查；不自動下單。
- Decision Ledger凍結歷史判斷，以0050為基準累積1D/5D/20D/60D/120D成果。
- 驗證與系統狀態頁揭露資料覆蓋、部署版本與方向化績效。
- 正式ETF子池與個股母池共用同一套價值引擎；ETF不套用個股ROE／毛利率硬篩。

## 每日自動化

- GitHub Actions於交易日台北時間16:35在雲端執行，不需要開啟網站或本機。
- 同一工作先重算母池100與ETF子池的當日狀態，再讀私有持股SSOT並寄信，避免候選與持股版本錯開。
- 若當日價值狀態沒有成功更新，郵件會直接失敗而不寄送舊內容。
- 每封郵件使用的候選、持股動作、資料截止日與版本，會以時間戳寫入私有`value/daily_audit/`，供日後回測與稽核。

## 交易原則

- 不做當沖建議。
- 每日交易結束後更新價值狀態與持股檢查。
- 操盤手只能使用指定日期以前的資料。
- 持股資料僅供模擬，不接券商、不自動下單。

## 本機執行

## 線上網址

```text
https://investment-strategy-company.onrender.com
```

健康檢查：

```text
https://investment-strategy-company.onrender.com/api/health
```

```powershell
py app.py 8765
```

開啟：

```text
http://127.0.0.1:8765
```

同一 Wi-Fi 手機測試：

```powershell
py app.py 8765 0.0.0.0
```

手機開啟電腦區網 IP，例如：

```text
http://192.168.1.103:8765
```

## 部署

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/a7662888/Investment_Strategy_Company)

GitHub repo：

```text
https://github.com/a7662888/Investment_Strategy_Company
```

在 Render 建立 Web Service：

- Build command：留空
- Start command：

```bash
python app.py $PORT 0.0.0.0
```

- Health check path：`/api/health`

部署完成後，手機可用 Render 提供的 HTTPS 網址。

### Cloudflare Tunnel

本機長期開機時可使用 Cloudflare Tunnel 產生外部網址。正式固定網址建議綁定自己的 Cloudflare 帳號與網域，並加 Cloudflare Access 保護。

## 風險聲明

本工具只供研究、模擬與流程訓練使用，不構成投資建議、投資顧問服務、代操或保證獲利系統。使用者需自行負擔任何投資決策風險。
