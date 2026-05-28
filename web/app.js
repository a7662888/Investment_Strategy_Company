const $ = (id) => document.getElementById(id);

function symbols() {
  return $("symbolInput").value.split(",").map(s => s.trim()).filter(Boolean);
}

function roles() {
  const list = [];
  if ($("roleC1").checked) list.push("C-1");
  if ($("roleC2").checked) list.push("C-2");
  return list;
}

function positions() {
  const raw = $("positionInput").value.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
  return raw.map(item => {
    const [symbolPart, costPart] = item.split("@");
    const [symbol, shares] = symbolPart.split(":");
    return {
      symbol: symbol?.trim(),
      shares: Number(shares || 0),
      cost: Number(costPart || 0)
    };
  }).filter(item => item.symbol);
}

function pct(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function modelLine(model) {
  if (!model) return "";
  return `<p class="modelLine">AI 模型：偏多機率 ${model.probability_up}% · 趨勢 ${model.trend_points} · 動能 ${model.momentum_points} · RSI ${model.rsi14} · 波動 ${model.volatility_20}</p>`;
}

function aiPredictorLine(pred) {
  if (!pred) return "";
  
  const isUp = pred.prediction.includes("Uptrend") || pred.prediction.includes("看漲");
  const isDown = pred.prediction.includes("Downtrend") || pred.prediction.includes("看跌");
  const predClass = isUp ? "pos" : isDown ? "neg" : "watch";
  
  const featuresHtml = pred.features.map(f => `
    <div style="margin-top: 5px;">
      <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-muted);">
        <span>${f.name}</span>
        <span>${f.weight}%</span>
      </div>
      <div style="height: 4px; background: rgba(255, 255, 255, 0.05); border-radius: 2px; overflow: hidden; margin-top: 2px;">
        <div style="height: 100%; width: ${f.weight}%; background: linear-gradient(90deg, #0055ff, #00f0ff); border-radius: 2px;"></div>
      </div>
    </div>
  `).join("");

  return `
    <div class="ai-predictor-card" style="
      margin-top: 10px; 
      padding: 10px; 
      background: rgba(0, 240, 255, 0.02); 
      border: 1px solid rgba(0, 240, 255, 0.1); 
      border-radius: 6px;
      text-align: left;
    ">
      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; font-weight: bold; color: #00f0ff; margin-bottom: 6px;">
        <span>🤖 AI 預測模型官 (AI Quant Predictor)</span>
        <span class="${predClass}" style="padding: 2px 6px; border-radius: 4px; background: rgba(255,255,255,0.02);">${pred.prediction} (${pred.probability.toFixed(0)}%)</span>
      </div>
      <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 8px;">
        預估明日價格區間：<strong style="color: var(--text-main); font-family: monospace;">${pred.predicted_range}</strong>
      </div>
      <div style="font-size: 0.75rem; line-height: 1.4; color: var(--text-muted); padding: 6px; background: rgba(0,0,0,0.2); border-radius: 4px; margin-bottom: 8px;">
        <strong>數理理由：</strong>${pred.rationale}
      </div>
      <div style="border-top: 1px dashed rgba(255,255,255,0.05); padding-top: 6px;">
        ${featuresHtml}
      </div>
    </div>
  `;
}

async function refreshQuotes() {
  const qs = symbols().join(",");
  $("quoteTime").textContent = "更新中";
  const res = await fetch(`/api/quote?symbols=${encodeURIComponent(qs)}`);
  const data = await res.json();
  const rows = data.quoteResponse?.result || [];
  $("quoteRows").innerHTML = rows.map(row => {
    const change = row.regularMarketChangePercent ?? 0;
    const cls = change >= 0 ? "pos" : "neg";
    const time = row.regularMarketTime ? new Date(row.regularMarketTime * 1000).toLocaleString() : "";
    return `<tr>
      <td>${row.symbol}</td>
      <td>${row.shortName || ""}</td>
      <td>${row.regularMarketPrice ?? ""}</td>
      <td class="${cls}">${change.toFixed(2)}%</td>
      <td>${time}</td>
      <td>${row.source || ""}</td>
      <td>${row.realtimeStatus || ""}</td>
    </tr>`;
  }).join("");
  $("quoteTime").textContent = `${new Date().toLocaleString()} · ${data.quotePolicy || ""}`;
}

async function runTraining() {
  $("trainingStatus").textContent = "訓練中";
  $("trainingRows").innerHTML = "";
  const res = await fetch("/api/train", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      symbols: symbols(),
      roles: roles(),
      start: $("startDate").value,
      end: $("endDate").value,
      initial_cash: 1000000
    })
  });
  const data = await res.json();
  if (data.error) {
    $("trainingStatus").textContent = data.error;
    return;
  }
  $("trainingRows").innerHTML = data.results.map(row => `
    <tr>
      <td>${row.symbol}</td>
      <td>${row.role}</td>
      <td>${row.start} - ${row.end}</td>
      <td class="${row.total_return >= 0 ? "pos" : "neg"}">${pct(row.total_return)}</td>
      <td class="neg">${pct(row.max_drawdown)}</td>
      <td>${row.trade_count}</td>
      <td><span title="${row.training_note || ""}">${row.model_basis || ""}</span></td>
      <td>${row.future_knowledge_used ? "異常" : "未使用"}</td>
    </tr>
  `).join("");
  $("trainingStatus").textContent = "完成";
}

async function recommendToday() {
  $("candidateList").innerHTML = "<p>分析中</p>";
  const res = await fetch("/api/recommend", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      symbols: symbols(),
      end: $("endDate").value,
      limit: 5,
      lookback_days: 320
    })
  });
  const data = await res.json();
  if (data.error) {
    $("candidateList").innerHTML = `<p>${data.error}</p>`;
    return;
  }
  $("candidateList").innerHTML = data.candidates.map(item => `
    <article class="candidate">
      <strong>
        <span>${item.symbol} · ${item.action}</span>
        <span class="${item.score >= 5 ? "pos" : item.score <= -2 ? "neg" : "watch"}">分數 ${item.score}</span>
      </strong>
      <p>截至 ${item.last_date}，收盤 ${item.last_close}</p>
      ${modelLine(item.model)}
      <ul>${item.reasons.map(reason => `<li>${reason}</li>`).join("")}</ul>
      ${aiPredictorLine(item.ai_predictor)}
    </article>
  `).join("");
}

async function nextDayPlan() {
  $("planList").innerHTML = "<p>產生明日計畫中</p>";
  const res = await fetch("/api/next-day-plan", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      symbols: symbols(),
      positions: positions(),
      end: $("endDate").value,
      lookback_days: 320
    })
  });
  const data = await res.json();
  if (data.error) {
    $("planList").innerHTML = `<p>${data.error}</p>`;
    return;
  }
  $("planList").innerHTML = data.plans.map(item => {
    const gain = item.held && item.unrealized_gain !== null ? `<span class="pill">未實現 ${pct(item.unrealized_gain)}</span>` : `<span class="pill">未持有</span>`;
    const cls = item.action.includes("買進") || item.action.includes("續抱") ? "pos" : item.action.includes("賣") || item.action.includes("減碼") ? "neg" : "watch";
    return `<article class="candidate">
      <strong>
        <span>${item.symbol} · <span class="${cls}">${item.action}</span></span>
        <span>分數 ${item.score}</span>
      </strong>
      <p>截至 ${item.as_of}，收盤 ${item.last_close} ${gain}</p>
      ${modelLine(item.model)}
      <ul>${item.reasons.map(reason => `<li>${reason}</li>`).join("")}</ul>
      ${aiPredictorLine(item.ai_predictor)}
    </article>`;
  }).join("");
}

$("refreshQuotes").addEventListener("click", refreshQuotes);
$("runTraining").addEventListener("click", runTraining);
$("recommendToday").addEventListener("click", recommendToday);
$("nextDayPlan").addEventListener("click", nextDayPlan);

refreshQuotes();
recommendToday();
nextDayPlan();
