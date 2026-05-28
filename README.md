# One Person Investment Strategy Company

研究與模擬用途的一人投資策略公司儀表板。

## 共享資源

Antigravity、Claude Code、Codex 都應先讀 [`SHARED_RESOURCES.md`](SHARED_RESOURCES.md)，再新增部署、tunnel、資料來源或長期狀態檔。

## 功能

- 台股即時/近期報價查詢。
- 收盤後產生明日研究計畫。
- 支援明日買進候選、續抱通知、獲利了結/減碼提醒。
- 可選擇股票與訓練區間做 blind simulation。
- C-1 保守價值流與 C-2 動能流策略比較。

## 交易原則

- 不做當沖建議。
- 每日交易結束後，只產生隔日研究計畫。
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
