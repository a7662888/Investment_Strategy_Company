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
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

async function readJson(res) {
  const data = await res.json();
  if (!res.ok || data.error) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function setBusy(buttonId, busy) {
  const button = $(buttonId);
  if (button) button.disabled = busy;
}

function modelLine(model) {
  if (!model) return "";
  const calibrated = model.calibrated ? ` · 校準桶 ${model.calibrated.prob_bucket}：歷史上漲率 ${(Number(model.calibrated.empirical_up_rate || 0) * 100).toFixed(1)}%，5日均報酬 ${(Number(model.calibrated.avg_fwd_return || 0) * 100).toFixed(1)}%` : "";
  const reasons = asArray(model.calibrated_reasons).slice(0, 3).join("；");
  return `
    <p class="modelLine">AI 模型：未校準偏多 ${model.probability_up ?? "-"}% · 校準偏多 ${model.calibrated_probability_up ?? "-"}% · 趨勢 ${model.trend_points ?? "-"} · 動能 ${model.momentum_points ?? "-"}${calibrated}</p>
    ${reasons ? `<p class="modelReason">校準理由：${reasons}</p>` : ""}
  `;
}

function calibratedModelPanel(model) {
  if (!model || !model.calibrated_probability_up) return "";
  
  const prob = model.calibrated_probability_up;
  const cal = model.calibrated; // prob_bucket, empirical_up_rate, avg_fwd_return, sample_count, horizon_days
  const reasons = model.calibrated_reasons || [];
  const metrics = model.calibrated_evidence || {};

  const isUp = prob >= 50.0;
  const badgeClass = isUp ? "pos" : "neg";
  const predictionText = isUp ? "看漲" : "看跌";
  
  let calEvidenceHtml = "";
  if (cal && cal.empirical_up_rate !== undefined) {
    const avgFwdReturn = cal.avg_fwd_return;
    const returnClass = avgFwdReturn >= 0 ? "pos" : "neg";
    const upRatePercent = (cal.empirical_up_rate * 100).toFixed(1);
    const avgReturnPercent = (avgFwdReturn * 100).toFixed(2);
    calEvidenceHtml = `
      <div style="font-size: 13px; color: var(--muted); margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 6px;">
        📊 <strong>樣本外前向校準驗證：</strong><br />
        前向預測天數：<strong>${cal.horizon_days || 5} 天</strong><br />
        所屬機率區間：<strong>${cal.prob_bucket}</strong><br />
        歷史同區間實際上漲率：<strong class="pos" style="font-weight: bold;">${upRatePercent}%</strong><br />
        平均持有報酬率：<strong class="${returnClass}" style="font-weight: bold;">${avgReturnPercent}%</strong> 
        (歷史樣本數：${cal.sample_count})
      </div>
    `;
  }

  const reasonsHtml = reasons.map(r => {
    const cls = r.includes("偏多") ? "pos" : r.includes("偏空") ? "neg" : "";
    return `<li style="font-size: 13px; margin-top: 2px;">
      <span class="${cls}">${r}</span>
    </li>`;
  }).join("");

  return `
    <div class="calibrated-model-card" style="
      margin-top: 10px; 
      padding: 10px; 
      background: #faf5ff; 
      border: 1px solid #e9d5ff; 
      border-radius: 6px;
      text-align: left;
    ">
      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 700; color: #6d28d9; margin-bottom: 6px;">
        <span>🎯 離線訓練校準模型 (logit_v1)</span>
        <span class="${badgeClass}">${predictionText} (${prob.toFixed(1)}%)</span>
      </div>
      
      <div style="font-size: 13px; color: var(--muted); margin-bottom: 6px;">
        <strong>主要因子貢獻 (前 4 項)：</strong>
        <ul style="margin: 4px 0 0 15px; padding: 0; list-style-type: disc;">
          ${reasonsHtml}
        </ul>
      </div>
      
      ${calEvidenceHtml}
    </div>
  `;
}

function renderStatusBadge(source, status) {
  if (!source) return "<td></td><td></td>";
  
  let sourceHtml = "";
  let statusHtml = "";
  
  const isTwse = source.includes("TWSE MIS") || source.includes("TPEx");
  const isYahoo1m = source.includes("Yahoo 1m") || (status && status.includes("分鐘線"));
  
  if (isTwse) {
    sourceHtml = `<span class="pill" style="border-color: var(--green); color: var(--green); font-weight: bold; background: #e6f4ea;">${source}</span>`;
    statusHtml = `<span style="color: var(--green); font-weight: bold;">${status || "即時盤中"}</span>`;
  } else if (isYahoo1m) {
    sourceHtml = `<span class="pill" style="border-color: var(--amber); color: var(--amber); background: #fef7e0;">${source}</span>`;
    statusHtml = `<span style="color: var(--amber);">${status || "盤中分鐘線"}</span>`;
  } else {
    sourceHtml = `<span class="pill" style="color: var(--muted); background: #f1f3f4;">${source}</span>`;
    statusHtml = `<span style="color: var(--muted);">${status || "延遲或日線備援"}</span>`;
  }
  
  return `<td>${sourceHtml}</td><td>${statusHtml}</td>`;
}

function aiPredictorLine(pred) {
  if (!pred) return "";

  const prediction = String(pred.prediction || "中性觀察");
  const probability = Number(pred.probability || 0);
  const isUp = prediction.includes("Uptrend") || prediction.includes("看漲");
  const isDown = prediction.includes("Downtrend") || prediction.includes("看跌");
  const predClass = isUp ? "pos" : isDown ? "neg" : "watch";
  const featuresHtml = asArray(pred.features).map(f => {
    const weight = Math.max(0, Math.min(100, Number(f.weight || 0)));
    return `
      <div class="aiFeature">
        <div class="aiFeatureHead">
          <span>${f.name || "factor"}</span>
          <span>${weight.toFixed(0)}%</span>
        </div>
        <div class="aiFeatureBar"><div style="width: ${weight}%;"></div></div>
      </div>
    `;
  }).join("");

  return `
    <div class="ai-predictor-card">
      <div class="aiPredictorHead">
        <span>AI 預測模型官</span>
        <span class="${predClass}">${prediction} (${probability.toFixed(0)}%)</span>
      </div>
      <p>預估明日價格區間：<strong>${pred.predicted_range || "-"}</strong></p>
      <p><strong>數理理由：</strong>${pred.rationale || "尚無補充理由"}</p>
      ${featuresHtml}
    </div>
  `;
}

async function refreshQuotes() {
  try {
    setBusy("refreshQuotes", true);
    const qs = symbols().join(",");
    $("quoteTime").textContent = "更新中";
    const res = await fetch(`/api/quote?symbols=${encodeURIComponent(qs)}`);
    const data = await readJson(res);
    const rows = asArray(data.quoteResponse?.result);
    $("quoteRows").innerHTML = rows.map(row => {
      const change = Number(row.regularMarketChangePercent ?? 0);
      const cls = change >= 0 ? "pos" : "neg";
      const time = row.regularMarketTime ? new Date(row.regularMarketTime * 1000).toLocaleString() : "";
      return `<tr>
        <td>${row.symbol}</td>
        <td>${row.shortName || ""}</td>
        <td>${row.regularMarketPrice ?? ""}</td>
        <td class="${cls}">${change.toFixed(2)}%</td>
        <td>${time}</td>
        ${renderStatusBadge(row.source, row.realtimeStatus)}
      </tr>`;
    }).join("");
    $("quoteTime").textContent = `${new Date().toLocaleString()} · ${data.quotePolicy || ""}`;
  } catch (err) {
    $("quoteTime").textContent = `報價失敗：${err.message}`;
  } finally {
    setBusy("refreshQuotes", false);
  }
}

async function runTraining() {
  try {
    setBusy("runTraining", true);
    $("trainingStatus").textContent = "訓練中，完整區間可能需要 30-90 秒";
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
    const data = await readJson(res);
    const results = asArray(data.results);
    $("trainingRows").innerHTML = results.map(row => `
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
    $("trainingStatus").textContent = results.length ? "完成" : "完成，但沒有可顯示結果";
  } catch (err) {
    $("trainingStatus").textContent = `訓練失敗：${err.message}`;
  } finally {
    setBusy("runTraining", false);
  }
}

async function recommendToday() {
  try {
    setBusy("recommendToday", true);
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
    const data = await readJson(res);
    const candidates = asArray(data.candidates);
    $("candidateList").innerHTML = candidates.length ? candidates.map(item => `
      <article class="candidate">
        <strong>
          <span>${item.symbol} · ${item.action}</span>
          <span class="${item.score >= 5 ? "pos" : item.score <= -2 ? "neg" : "watch"}">分數 ${item.score}</span>
        </strong>
        <p>截至 ${item.last_date}，收盤 ${item.last_close}</p>
        ${modelLine(item.model)}
        <ul>${asArray(item.reasons).map(reason => `<li>${reason}</li>`).join("")}</ul>
        ${aiPredictorLine(item.ai_predictor)}
        ${calibratedModelPanel(item.model)}
      </article>
    `).join("") : "<p>沒有候選資料，請縮短區間或確認股票代號。</p>";
  } catch (err) {
    $("candidateList").innerHTML = `<p>候選分析失敗：${err.message}</p>`;
  } finally {
    setBusy("recommendToday", false);
  }
}

async function nextDayPlan() {
  try {
    setBusy("nextDayPlan", true);
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
    const data = await readJson(res);
    const plans = asArray(data.plans);
    $("planList").innerHTML = plans.length ? plans.map(item => {
      const gain = item.held && item.unrealized_gain !== null ? `<span class="pill">未實現 ${pct(item.unrealized_gain)}</span>` : `<span class="pill">未持有</span>`;
      const cls = item.action.includes("買進") || item.action.includes("續抱") ? "pos" : item.action.includes("賣") || item.action.includes("減碼") ? "neg" : "watch";
      return `<article class="candidate">
        <strong>
          <span>${item.symbol} · <span class="${cls}">${item.action}</span></span>
          <span>分數 ${item.score}</span>
        </strong>
        <p>截至 ${item.as_of}，收盤 ${item.last_close} ${gain}</p>
        ${modelLine(item.model)}
        <ul>${asArray(item.reasons).map(reason => `<li>${reason}</li>`).join("")}</ul>
        ${aiPredictorLine(item.ai_predictor)}
        ${calibratedModelPanel(item.model)}
      </article>`;
    }).join("") : "<p>沒有明日計畫資料，請確認股票代號或區間。</p>";
  } catch (err) {
    $("planList").innerHTML = `<p>明日計畫失敗：${err.message}</p>`;
  } finally {
    setBusy("nextDayPlan", false);
  }
}

function bindActions() {
  $("refreshQuotes").addEventListener("click", refreshQuotes);
  $("runTraining").addEventListener("click", runTraining);
  $("recommendToday").addEventListener("click", recommendToday);
  $("nextDayPlan").addEventListener("click", nextDayPlan);
}

bindActions();
refreshQuotes();
recommendToday();
nextDayPlan();
