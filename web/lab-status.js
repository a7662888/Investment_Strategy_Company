const $ = id => document.getElementById(id);
const HORIZONS = ["1D", "5D", "20D", "60D", "120D"];

async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function pct(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value) * 100;
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function statCard(label, value, note = "") {
  return `<div class="status-card"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ""}</div>`;
}

function isSaneOutcome(outcome) {
  if (!outcome || outcome.reference_price_valid === false) return false;
  if (outcome.gross_return === null || outcome.gross_return === undefined || outcome.excess_return === null || outcome.excess_return === undefined) return false;
  const gross = Number(outcome.gross_return);
  const excess = Number(outcome.excess_return);
  return Number.isFinite(gross) && Math.abs(gross) <= 1 && Number.isFinite(excess) && Math.abs(excess) <= 1;
}

function actionClass(action) {
  const value = String(action || "").toLowerCase();
  if (value.includes("accumulate") || value.includes("buy_zone") || value === "buy") return "buy";
  if (value.includes("avoid")) return "avoid";
  return "non-trade";
}

function uniqueSignals(signals) {
  const selected = new Map();
  for (const signal of signals) {
    const key = signal.signal_id || signal.event_id || `${signal.agent_id}|${signal.symbol}|${signal.data_cutoff}|${signal.recorded_at}`;
    selected.set(key, signal);
  }
  return [...selected.values()];
}

function aggregate(signals, kind, horizon) {
  const values = [];
  for (const signal of signals) {
    if (actionClass(signal.action) !== kind) continue;
    const outcome = (signal.outcomes || {})[horizon];
    if (!isSaneOutcome(outcome)) continue;
    const excess = Number(outcome.excess_return);
    values.push(kind === "avoid" ? -excess : excess);
  }
  if (!values.length) return null;
  return {
    n: values.length,
    mean: values.reduce((sum, value) => sum + value, 0) / values.length,
    winRate: values.filter(value => value > 0).length / values.length,
  };
}

function performanceTable(signals, kind, title, explanation) {
  const rows = HORIZONS.map(horizon => ({ horizon, result: aggregate(signals, kind, horizon) }));
  const matured = rows.filter(row => row.result);
  if (!matured.length) return `<div><h3>${title}</h3><p>${explanation}：尚無成熟樣本。</p></div>`;
  return `<div class="performance-block">
    <h3>${title}</h3><p>${explanation}</p>
    <div class="tableWrap"><table><thead><tr><th>期限</th><th>有效樣本</th><th>平均對0050超額</th><th>方向正確率</th></tr></thead>
    <tbody>${matured.map(({ horizon, result }) => `<tr><td>${horizon}</td><td>${result.n}</td><td class="${result.mean >= 0 ? "pos" : "neg"}">${pct(result.mean)}</td><td>${(result.winRate * 100).toFixed(1)}%</td></tr>`).join("")}</tbody></table></div>
  </div>`;
}

async function loadStatus() {
  $("pageStatus").textContent = "更新中…";
  try {
    const [value, ledger, pool, health, dataStatus, version] = await Promise.all([
      getJson("/api/value-current"),
      getJson("/api/decision-ledger?limit=500&agents=claude-value,claude-etf-subtrack"),
      getJson("/api/mother-pool"),
      getJson("/api/health/ready"),
      getJson("/api/data-status"),
      getJson("/api/version"),
    ]);

    const coverage = value.coverage || {};
    $("valueAsOf").textContent = `截至 ${value.as_of || "—"}`;
    $("valueCoverage").innerHTML = [
      statCard("母池", coverage.mother_pool ?? 0, "流動性合格股票"),
      statCard("品質覆蓋", `${coverage.quality_covered ?? 0}/${coverage.mother_pool ?? 0}`, "完整基本面"),
      statCard("今日候選", (value.top_picks || []).length, "非自動下單"),
      statCard("等待名單", (value.waiting_list || []).length, "便宜但時機未到"),
    ].join("");
    const picks = value.top_picks || [];
    $("valuePicks").innerHTML = picks.length ? picks.map(item => `<article class="candidate">
      <strong><span>${item.name || ""} ${item.symbol}</span><span>${item.decision || item.action || "—"}</span></strong>
      <p>現價 ${item.price ?? "—"}｜${item.valuation_zone || "—"}｜${item.trend || "—"}｜排名分 ${item.rank_score ?? "—"}</p>
      <p>${(item.reasons || []).join("；") || "—"}</p>
    </article>`).join("") : "<p>今天沒有同時通過品質、估值與時機條件的標的；保留現金也是決策。</p>";

    const signals = uniqueSignals((ledger.signals || []).filter(signal => signal.event_type === "signal"));
    $("performanceStatus").textContent = `${signals.length} 個獨立凍結訊號`;
    $("performanceTables").innerHTML = performanceTable(signals, "buy", "買進／累積訊號", "正超額代表買進後勝過0050") +
      performanceTable(signals, "avoid", "避開訊號", "反向計分；標的落後0050才算判斷正確");

    $("poolAsOf").textContent = `截至 ${pool.as_of || "—"}`;
    $("poolSummary").innerHTML = [
      statCard("母池檔數", pool.n ?? 0),
      statCard("原始候選", pool.candidates_evaluated ?? "—"),
      statCard("產業上限", pool.per_sector_cap ?? "—"),
      statCard("篩選基礎", "60日成交額", "確保可交易性，不冒充品質分"),
    ].join("");
    $("poolList").innerHTML = (pool.stocks || []).map(item => `<span>${item.pool_rank || "—"}. ${item.name} (${item.symbol})</span>`).join("");

    const durable = Boolean(ledger.storage && ledger.storage.durable);
    const fundamentals = value.fundamentals || {};
    $("healthStatus").textContent = health.ready && durable ? "正常" : "需注意";
    $("healthGrid").innerHTML = [
      statCard("服務", health.ready ? "Ready" : "Not ready"),
      statCard("部署版本", (version.render_git_commit || "local").slice(0, 8), version.app_version || ""),
      statCard("Ledger", durable ? "Durable" : "非持久", ledger.storage?.source || "—"),
      statCard("基本面", `${fundamentals.coverage?.complete ?? coverage.quality_covered ?? 0}/100`, fundamentals.load?.source || "—"),
      statCard("價格快取", dataStatus.latest_cache_date || "線上來源", `${dataStatus.cache_files_count ?? 0} 個本機檔`),
      statCard("狀態時間", new Date(dataStatus.timestamp || Date.now()).toLocaleString("zh-TW")),
    ].join("");
    const warnings = [...(health.warnings || []), ...(dataStatus.stale_alerts || [])];
    $("healthWarnings").innerHTML = warnings.length
      ? warnings.map(item => `<div class="status-warning">⚠️ ${item}</div>`).join("")
      : '<div class="status-ok">✓ 未發現阻斷性警告</div>';

    $("pageStatus").textContent = "已更新";
  } catch (error) {
    $("pageStatus").textContent = "載入失敗";
    $("healthWarnings").innerHTML = `<div class="status-warning">${error.message}</div>`;
  }
}

$("refreshStatus").addEventListener("click", loadStatus);
loadStatus();
