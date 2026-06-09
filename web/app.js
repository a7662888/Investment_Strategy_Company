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

// 三家 Agent 最近一次選股的所有推薦代碼(由 discoverToday 更新)
let lastAgentPicks = [];

// 即時報價的股票清單 = 三家 Agent 推薦 ∪ 明日決策中心代號 ∪ 持股(去重)
function quoteSymbols() {
  const set = [];
  const add = (s) => { s = (s || "").trim(); if (s && !set.includes(s)) set.push(s); };
  lastAgentPicks.forEach(add);
  symbols().forEach(add);
  positions().forEach(p => add(p.symbol));
  return set;
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

function getModelConfidence(prob) {
  if (prob === undefined || prob === null) return "未知";
  const dist = Math.abs(prob - 50);
  if (dist >= 10) return "高";
  if (dist >= 5) return "中";
  return "低";
}

// 注意：此處反映的是「大盤風險狀態」，非「模型本身的預測有效性」。
// 模型自身有效性(滾動 AUC 監控)屬 Phase 2 (P2-1)，與大盤燈號脫鉤，尚未上線。
function getMarketState(riskLevel) {
  if (riskLevel === "RED" || riskLevel === "BLACK") {
    return "<span class='neg' style='font-weight: bold;'>避險/轉守 (大盤急停或空頭)</span>";
  }
  return "<span class='pos' style='font-weight: bold;'>正常 (正常交易)</span>";
}

function modelLine(model, riskLevel) {
  if (!model) return "";
  const calibrated = model.calibrated ? ` · 歷史同類勝率 ${(Number(model.calibrated.empirical_up_rate || 0) * 100).toFixed(1)}% (樣本數 ${model.calibrated.sample_count || 0})` : "";
  const confidence = getModelConfidence(model.calibrated_probability_up ?? model.probability_up);
  const marketState = getMarketState(riskLevel);
  const reasons = asArray(model.calibrated_reasons).slice(0, 3).join("；");
  return `
    <p class="modelLine">AI 模型信心：<strong>${confidence}</strong> · 市場狀態：${marketState} · 趨勢 ${model.trend_points ?? "-"} · 動能 ${model.momentum_points ?? "-"}${calibrated}</p>
    ${reasons ? `<p class="modelReason">校準理由：${reasons}</p>` : ""}
  `;
}

function renderShapBars(contributions) {
  if (!contributions || contributions.length === 0) return "";
  const top = contributions.slice(0, 5);
  const maxVal = Math.max(...top.map(c => Math.abs(c.contribution || 0))) || 0.01;
  
  const rows = top.map(c => {
    const val = Number(c.contribution || 0);
    const widthPct = Math.min(100, (Math.abs(val) / maxVal * 100)).toFixed(0) + "%";
    const isPos = val >= 0;
    
    const leftBar = isPos ? "" : `<div style="background: #10b981; height: 8px; border-radius: 4px; width: ${widthPct}; margin-left: auto;"></div>`;
    const rightBar = isPos ? `<div style="background: #ef4444; height: 8px; border-radius: 4px; width: ${widthPct};"></div>` : "";
    const valClass = isPos ? "pos" : "neg";
    const valSign = val >= 0 ? "+" : "";
    
    return `
      <div style="display: grid; grid-template-columns: 100px 1fr 10px 1fr 60px; gap: 8px; align-items: center; font-family: monospace; font-size: 11px; margin-top: 4px;">
        <span style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap; font-weight: bold; color: var(--ink);" title="${c.label}">${c.label}</span>
        <div style="display: flex; align-items: center; justify-content: flex-end;">${leftBar}</div>
        <div style="background: var(--line); width: 2px; height: 12px; margin: 0 auto;"></div>
        <div style="display: flex; align-items: center;">${rightBar}</div>
        <span class="${valClass}" style="text-align: right; font-weight: bold;">${valSign}${val.toFixed(3)}</span>
      </div>
    `;
  }).join("");
  
  return `
    <div class="shap-container" style="margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 8px;">
      <div style="font-size: 12px; font-weight: 700; color: #1e3a8a; display: flex; justify-content: space-between; margin-bottom: 4px;">
        <span>📊 Saabas (TreeSHAP) 特徵貢獻度</span>
        <span style="font-size: 10px; color: var(--muted); font-weight: 400;">(左偏空 綠 | 右偏多 紅)</span>
      </div>
      ${rows}
    </div>
  `;
}

function calibratedModelPanel(model, riskLevel) {
  if (!model || !model.calibrated_probability_up) return "";
  
  const prob = model.calibrated_probability_up;
  const cal = model.calibrated; // prob_bucket, empirical_up_rate, avg_fwd_return, sample_count, horizon_days
  const reasons = model.calibrated_reasons || [];
  const metrics = model.calibrated_evidence || {};

  const isUp = prob >= 50.0;
  const badgeClass = isUp ? "pos" : "neg";
  const predictionText = isUp ? "看漲" : "看跌";
  const confidence = getModelConfidence(prob);
  const marketState = getMarketState(riskLevel);
  
  let calEvidenceHtml = "";
  if (cal && cal.empirical_up_rate !== undefined) {
    const avgFwdReturn = cal.avg_fwd_return;
    const returnClass = avgFwdReturn >= 0 ? "pos" : "neg";
    const upRatePercent = (cal.empirical_up_rate * 100).toFixed(1);
    const avgReturnPercent = (avgFwdReturn * 100).toFixed(2);
    calEvidenceHtml = `
      <div style="font-size: 13px; color: var(--muted); margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 6px;">
        📊 <strong>樣本外前向校準驗證：</strong><br />
        模型信心：<strong>${confidence}</strong><br />
        目前市場狀態：${marketState}<br />
        前向預測天數：<strong>${cal.horizon_days || 5} 天</strong><br />
        所屬機率區間：<strong>${cal.prob_bucket}</strong><br />
        歷史同類情境勝率 (實際上漲率)：<strong class="pos" style="font-weight: bold;">${upRatePercent}%</strong><br />
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
      ${renderShapBars(model.contributions)}
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

function renderLearningPanel(data) {
  const summary = data.model_training || {};
  const results = asArray(data.results);
  const optimizations = asArray(data.optimization);
  const thresholdReviews = asArray(data.threshold_reviews);
  if (!summary.available) {
    $("learningStatus").textContent = "模型 artifact 不可用";
    $("learningPanel").innerHTML = `<p>${summary.summary || "尚無模型訓練資訊"}</p>`;
    return;
  }

  const best = summary.best_bucket || {};
  const topFactors = asArray(summary.top_factors).map(item => `
    <li>
      <strong>${item.label}</strong>
      <span class="${item.direction === "偏多" ? "pos" : "neg"}">${item.direction} ${Number(item.weight || 0).toFixed(3)}</span>
    </li>
  `).join("");
  const process = asArray(summary.thinking_process).map(item => `<li>${item}</li>`).join("");
  const limits = asArray(summary.limitations).map(item => `<li>${item}</li>`).join("");
  const nextSteps = asArray(summary.next_steps).map(item => `<li>${item}</li>`).join("");
  const reviews = results.map(row => {
    const review = row.learning_review || {};
    const findings = asArray(review.findings).map(item => `<li>${item}</li>`).join("");
    const next = asArray(review.next_adjustments).map(item => `<li>${item}</li>`).join("");
    return `
      <article class="learningCard">
        <strong>${row.symbol} · ${row.role}</strong>
        <p>策略報酬 ${pct(row.total_return)}；買進持有 ${pct(review.buy_hold_return)}；差距 ${pct(review.gap_vs_buy_hold)}；交易 ${row.trade_count} 次。</p>
        <ul>${findings}</ul>
        <p class="learningSubtitle">下一輪優化</p>
        <ul>${next}</ul>
      </article>
    `;
  }).join("");
  const optimizationHtml = optimizations.map(item => {
    const best = item.best_variant || {};
    const params = best.params ? Object.entries(best.params).map(([key, value]) => `${key}=${value}`).join("，") : "-";
    return `
      <article class="learningCard">
        <strong>${item.symbol} · ${item.role} 參數競賽</strong>
        <p>目前規則 ${pct(item.baseline_return)}；最佳候選 ${pct(best.total_return)}；改善 ${pct(item.improvement)}。</p>
        <p>最佳參數：${params}</p>
        <p>${item.recommendation || ""}</p>
      </article>
    `;
  }).join("");
  const thresholdHtml = thresholdReviews.map(review => {
    const rows = asArray(review.thresholds).map(item => `
      <tr>
        <td>${item.threshold}%</td>
        <td>${item.signals}</td>
        <td>${item.hit_rate === null || item.hit_rate === undefined ? "-" : pct(item.hit_rate)}</td>
        <td>${pct(item.avg_forward_return)}</td>
      </tr>
    `).join("");
    const best = review.best_threshold;
    return `
      <article class="learningCard">
        <strong>${review.symbol} · 機率門檻審計</strong>
        <p>${review.interpretation || ""}</p>
        ${best ? `<p>本區間最佳門檻：${best.threshold}%；命中率 ${pct(best.hit_rate)}；5日均報酬 ${pct(best.avg_forward_return)}。</p>` : "<p>訊號不足，暫無可靠門檻。</p>"}
        <div class="tableWrap">
          <table>
            <thead><tr><th>門檻</th><th>訊號</th><th>命中率</th><th>5日均報酬</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </article>
    `;
  }).join("");

  $("learningStatus").textContent = `${summary.name} · 樣本外 ${summary.oos_sample_count || "-"} 筆`;
  $("learningPanel").innerHTML = `
    <div class="learningGrid">
      <article class="learningCard">
        <strong>校準模型證據</strong>
        <p>訓練股票 ${summary.train_symbol_count} 檔；訓練樣本 ${summary.train_sample_count}；樣本外 AUC ${Number(summary.oos_auc || 0).toFixed(3)}；基準上漲率 ${(Number(summary.base_rate_up || 0) * 100).toFixed(1)}%。</p>
        <p>最佳校準桶 ${best.lo !== undefined ? `${(best.lo * 100).toFixed(0)}-${(best.hi * 100).toFixed(0)}%` : "-"}：歷史上漲率 ${(Number(best.empirical_up_rate || 0) * 100).toFixed(1)}%，5日均報酬 ${(Number(best.avg_fwd_return || 0) * 100).toFixed(2)}%。</p>
      </article>
      <article class="learningCard">
        <strong>目前主要權重</strong>
        <ul>${topFactors}</ul>
      </article>
      <article class="learningCard">
        <strong>模型怎麼學</strong>
        <ul>${process}</ul>
      </article>
      <article class="learningCard">
        <strong>限制與風險</strong>
        <ul>${limits}</ul>
      </article>
    </div>
    <div class="learningReviews">${reviews}</div>
    <div class="learningReviews">${optimizationHtml}</div>
    <div class="learningReviews">${thresholdHtml}</div>
    <article class="learningCard">
      <strong>下一步總體優化</strong>
      <ul>${nextSteps}</ul>
    </article>
  `;
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
    const qsList = quoteSymbols();
    const qs = (qsList.length ? qsList : symbols()).join(",");
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
    
    // Clear console and hide optimized weights banner
    $("trainConsole").textContent = "🔍 正在啟動在線優化訓練器...\n";
    $("optimizedWeights").style.display = "none";
    $("trainProgress").textContent = "優化狀態：計算特徵中";

    const res = await fetch("/api/train", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        symbols: symbols(),
        roles: roles(),
        start: $("startDate").value,
        end: $("endDate").value,
        initial_cash: 1000000,
        fee: Number($("feeRate").value),
        tax: Number($("taxRate").value),
        slippage: Number($("slippageRate").value)
      })
    });
    const data = await readJson(res);
    const results = asArray(data.results);
    renderLearningPanel(data);
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
    
    // Trigger terminal scrolling output for model training
    const mt = data.model_training;
    if (mt && mt.epoch_logs && mt.epoch_logs.length > 0) {
      $("trainProgress").textContent = "優化狀態：回傳迭代日誌中";
      const logs = mt.epoch_logs;
      const terminal = $("trainConsole");
      terminal.textContent = "🔍 [1/4] 特徵提取完成，共收集 " + (logs.length * 10) + " 個跨股票歷史交易日樣本。\n";
      terminal.textContent += "⚙️ [2/4] 啟動隨機梯度下降 L2 擬合器 (Learning Rate=0.1, L2=0.01, Epochs=500)...\n\n";
      
      let idx = 0;
      function printEpochLog() {
        if (idx < logs.length) {
          const log = logs[idx];
          terminal.textContent += `[Epoch ${log.epoch}/500] BCE損失值 (Loss): ${log.loss.toFixed(6)} | 訓練擬合度 (Accuracy): ${log.accuracy.toFixed(2)}%\n`;
          terminal.scrollTop = terminal.scrollHeight;
          idx++;
          setTimeout(printEpochLog, 50);
        } else {
          terminal.textContent += `\n🎉 [3/4] AI 預測模型在線優化成功！\n`;
          const weights = mt.weights;
          terminal.textContent += `優化權重：Bias = ${weights.bias.toFixed(4)}, RSI = ${weights.rsi.toFixed(4)}, Slope = ${weights.slope.toFixed(4)}, MACD = ${weights.macd_hist.toFixed(4)}\n`;
          terminal.textContent += `[4/4] 權重已持久化存檔。更新後的預測特徵貢獻佔比條已就緒。\n`;
          terminal.scrollTop = terminal.scrollHeight;
          
          $("trainProgress").textContent = "優化狀態：已完成且套用新權重";
          $("optimizedWeights").style.display = "block";
          $("weightsDetail").innerHTML = `RSI權重: <strong>${weights.rsi.toFixed(3)}</strong> | 5日斜率權重: <strong>${weights.slope.toFixed(3)}</strong> | MACD權重: <strong>${weights.macd_hist.toFixed(3)}</strong> | 偏置值: <strong>${weights.bias.toFixed(3)}</strong> | 最終歷史擬合準確率: <strong>${mt.accuracy.toFixed(1)}%</strong>`;
          
          // Re-render recommendations & plans using the new weights!
          recommendToday();
          nextDayPlan();
          saveSnapshot("train");
        }
      }
      setTimeout(printEpochLog, 300);
    } else {
      $("trainConsole").textContent = "無法載入優化訓練過程，請確認股票代號。";
      $("trainProgress").textContent = "優化狀態：失敗";
    }

  } catch (err) {
    $("trainingStatus").textContent = `訓練失敗：${err.message}`;
    $("learningStatus").textContent = "訓練失敗";
    $("learningPanel").innerHTML = `<p>${err.message}</p>`;
    $("trainConsole").textContent = `❌ 錯誤: ${err.message}`;
    $("trainProgress").textContent = "優化狀態：錯誤";
  } finally {
    setBusy("runTraining", false);
  }
}

function updateMarketRiskPanel(m) {
  const panel = $("marketRiskPanel");
  if (!panel) return;
  if (!m) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "block";

  // Reset all lights
  const lights = ["GREEN", "YELLOW", "RED", "BLACK"];
  lights.forEach(color => {
    const el = $(`light${color}`);
    if (el) el.className = "light";
  });

  // Activate corresponding light
  const riskLevel = m.risk_level || "GREEN";
  const activeLight = $(`light${riskLevel}`);
  if (activeLight) {
    activeLight.className = `light active ${riskLevel.toLowerCase()}`;
  }

  // Set text labels
  $("riskLabel").textContent = m.risk_label || "🟢 綠色 · 正常選股";
  $("riskStance").textContent = m.risk_stance || "多頭常態，正常選股";

  // Tomorrow's decision
  const tomorrowDecision = $("tomorrowDecision");
  if (tomorrowDecision) {
    const dateStr = m.date || new Date().toISOString().split("T")[0];
    let decisionText = `${dateStr}：`;
    if (riskLevel === "BLACK") {
      decisionText += "市場急停，停止所有新買進，清空部位或極限避險！";
      tomorrowDecision.style.color = "#111827"; // Black
    } else if (riskLevel === "RED") {
      decisionText += "市場停止新買進，減碼防守！";
      tomorrowDecision.style.color = "var(--red)";
    } else if (riskLevel === "YELLOW") {
      decisionText += "大盤整理中，減半觀察操作！";
      tomorrowDecision.style.color = "var(--amber)";
    } else {
      decisionText += "大盤安全，可正常選股買進！";
      tomorrowDecision.style.color = "var(--green)";
    }
    tomorrowDecision.textContent = decisionText;
  }

  // Decision reasons
  const reasonsDiv = $("decisionReasons");
  if (reasonsDiv) {
    const reasons = asArray(m.decision_reasons);
    reasonsDiv.innerHTML = reasons.length 
      ? `<ul style="margin: 4px 0 0; padding-left: 16px;">${reasons.map(r => `<li>${r}</li>`).join("")}</ul>`
      : "<p style='margin: 4px 0 0; color: var(--muted);'>無特別系統性風險警訊</p>";
  }

  // Position Suggestions
  $("buyExposureSuggest").textContent = m.buy_exposure || "100%";
  if (riskLevel === "BLACK" || riskLevel === "RED") {
    $("buyExposureSuggest").style.color = "var(--red)";
  } else if (riskLevel === "YELLOW") {
    $("buyExposureSuggest").style.color = "var(--amber)";
  } else {
    $("buyExposureSuggest").style.color = "var(--green)";
  }
  
  $("holdExposureSuggest").textContent = m.hold_exposure || "正常續抱";
  $("openGuideSuggest").textContent = m.open_guide || "低接不追高";
}

async function recommendToday() {
  try {
    setBusy("recommendToday", true);
    
    // 清空 A/B/C 三個列表
    const listA = $("candidateListA");
    const listB = $("candidateListB");
    const listC = $("candidateListC");
    if (listA) listA.innerHTML = "<p>分析中...</p>";
    if (listB) listB.innerHTML = "<p>分析中...</p>";
    if (listC) listC.innerHTML = "<p>分析中...</p>";
    
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
    
    // 更新頂部市場風險與總決策面板
    updateMarketRiskPanel(data.market_index);
    
    // 渲染大盤導引狀態舊卡片 (保留相容性，如果有的話)
    const m = data.market_index;
    const mCard = $("marketStatusCard");
    if (m && mCard) {
      mCard.style.display = "block";
      const changeClass = m.change_percent >= 0 ? "pos" : "neg";
      const changePrefix = m.change_percent >= 0 ? "+" : "";
      
      let badgeClass = "consolidation";
      if (m.regime === "強勢多頭") badgeClass = "bullish";
      else if (m.regime === "弱勢空頭") badgeClass = "bearish";
      else if (m.regime === "高波動震盪") badgeClass = "volatility";
      
      mCard.className = `market-status-card ${badgeClass}`;
      mCard.innerHTML = `
        <div style="font-weight: bold; font-size: 14px; display: flex; align-items: center; justify-content: space-between;">
          <span>📈 ${m.name} (${m.symbol}) 收盤：${m.close.toLocaleString()} <span class="${changeClass}" style="font-weight: bold;">${changePrefix}${m.change_percent}%</span></span>
          <span class="market-badge ${badgeClass}">${m.regime}</span>
        </div>
        <div style="margin-top: 6px; font-size: 13px; opacity: 0.95;">
          🎯 <strong>大盤引導決策：</strong>${m.regime_note}
        </div>
      `;
    } else if (mCard) {
      mCard.style.display = "none";
    }

    const candidates = asArray(data.candidates);
    
    // 渲染空輸入時的提示 note
    const noteEl = $("candidateNote");
    if (noteEl) {
      if (symbols().length === 0 && candidates.length > 0) {
        noteEl.style.display = "block";
        noteEl.innerHTML = `<div style="font-size: 13px; color: var(--amber); margin-bottom: 10px; font-weight: bold; background: #fffdf5; border: 1px solid #fef3c7; padding: 10px; border-radius: 6px;">💡 偵測到股票代號輸入為空，系統已自動載入跨產業指標股池，並依今日大盤環境動態篩選出潛力股進行排序：</div>`;
      } else {
        noteEl.style.display = "none";
      }
    }

    // 分組 A, B, C
    const gradeA = [];
    const gradeB = [];
    const gradeC = [];
    
    candidates.forEach(item => {
      const g = item.grade || "B";
      if (g === "A") gradeA.push(item);
      else if (g === "C") gradeC.push(item);
      else gradeB.push(item);
    });

    const riskLevel = m ? m.risk_level : null;
    const renderCard = (item) => `
      <article class="candidate">
        <strong>
          <span>${item.symbol} · ${item.action}</span>
          <span class="${item.score >= 5 ? "pos" : item.score <= -2 ? "neg" : "watch"}">分數 ${item.score}</span>
        </strong>
        <p>截至 ${item.last_date}，收盤 ${item.last_close}</p>
        ${modelLine(item.model, riskLevel)}
        <ul>${asArray(item.reasons).map(reason => `<li>${reason}</li>`).join("")}</ul>
        ${aiPredictorLine(item.ai_predictor)}
        ${calibratedModelPanel(item.model, riskLevel)}
      </article>
    `;

    if (listA) {
      listA.innerHTML = gradeA.length 
        ? gradeA.map(renderCard).join("") 
        : "<p style='color: var(--muted); padding: 8px;'>無 A 級候選股</p>";
    }
    if (listB) {
      listB.innerHTML = gradeB.length 
        ? gradeB.map(renderCard).join("") 
        : "<p style='color: var(--muted); padding: 8px;'>無 B 級觀察股</p>";
    }
    if (listC) {
      listC.innerHTML = gradeC.length 
        ? gradeC.map(renderCard).join("") 
        : "<p style='color: var(--muted); padding: 8px;'>無 C 級禁買股</p>";
    }
    
    // Update portfolio allocation
    updatePortfolioAllocation(data.market_index, candidates);
  } catch (err) {
    const listA = $("candidateListA");
    if (listA) {
      listA.innerHTML = `<p>候選分析失敗：${err.message}</p>`;
    }
  } finally {
    setBusy("recommendToday", false);
  }
}

async function discoverToday() {
  const end = encodeURIComponent($("endDate").value);
  $("discoverStatus").textContent = "三家 Agent 正在並行計算選股中...";
  $("codexList").innerHTML = "<p>計算中</p>";
  $("antigravityList").innerHTML = "<p>計算中</p>";
  $("claudeList").innerHTML = "<p>計算中</p>";
  setBusy("discoverToday", true);

  try {
    const [resCodex, resAnti, resClaude] = await Promise.all([
      fetch(`/api/discover?end=${end}&limit=5`),
      fetch(`/api/antigravity/discover?end=${end}&limit=5`),
      fetch(`/api/claude/discover?end=${end}&limit=5`),
    ]);

    const codexData  = resCodex.ok  ? await resCodex.json()  : {};
    const antiData   = resAnti.ok   ? await resAnti.json()   : [];
    const claudeData = resClaude.ok ? await resClaude.json() : [];

    // Codex returns a discover-style wrapper; Antigravity/Claude return plain arrays
    const codexCands  = asArray(codexData.candidates || codexData);
    const antiCands   = asArray(antiData);
    const claudeCands = asArray(claudeData);

    // 記錄三家所有推薦代碼(供即時報價取用)
    lastAgentPicks = [];
    [...codexCands, ...antiCands, ...claudeCands].forEach(c => {
      const s = (c.symbol || "").trim();
      if (s && !lastAgentPicks.includes(s)) lastAgentPicks.push(s);
    });

    // 預設:三家各取前 2 名(去重),取代過去只填 Codex 的清單
    const executablePick = (c) => c && c.symbol && c.grade !== "C";
    const topSyms = (arr) => asArray(arr).filter(executablePick).slice(0, 2).map(c => c.symbol).filter(Boolean);
    const merged = [];
    [...topSyms(codexCands), ...topSyms(antiCands), ...topSyms(claudeCands)].forEach(s => {
      if (s && !merged.includes(s)) merged.push(s);
    });
    if (merged.length) {
      $("symbolInput").value = merged.join(",");
    }

    $("discoverStatus").textContent =
      `Codex ${codexCands.length}｜Antigravity ${antiCands.length}｜Claude ${claudeCands.length} 檔 · 分數已標準化(校準機率基準 0–10,三家可比)`;

    $("codexList").innerHTML = codexCands.length
      ? codexCands.map(c => renderAgentCard(c, "codex")).join("")
      : "<p>無候選股</p>";
    $("antigravityList").innerHTML = antiCands.length
      ? antiCands.map(c => renderAgentCard(c, "anti")).join("")
      : "<p>無候選股</p>";
    $("claudeList").innerHTML = claudeCands.length
      ? claudeCands.map(c => renderAgentCard(c, "claude")).join("")
      : "<p>無候選股 (目前 regime 建議持守現金)</p>";

    refreshQuotes();   // 報價改用 三家推薦 ∪ 決策中心 ∪ 持股
    recommendToday();
    nextDayPlan();
  } catch (err) {
    $("discoverStatus").textContent = "Agent 選股失敗: " + err.message;
    const errHtml = `<p>${err.message}</p>`;
    $("codexList").innerHTML = $("antigravityList").innerHTML = $("claudeList").innerHTML = errHtml;
  } finally {
    setBusy("discoverToday", false);
  }
}

// 共同錨定校準機率 → 0–10 標準化分(三家同基準,才可跨家比較)
// 50%→5、58%→7、62%→8、70%→10、42%→3,夾在 0–10
function standardizedScore(item) {
  let prob = item.probability_up;
  if (prob === undefined || prob === null) {
    const m = item.model || {};
    prob = (m.calibrated_probability_up !== undefined && m.calibrated_probability_up !== null)
      ? m.calibrated_probability_up : m.probability_up;
  }
  if (prob === undefined || prob === null) return null;
  return Math.max(0, Math.min(10, 5 + (Number(prob) - 50) * 0.25));
}

function renderAgentCard(item, agentType) {
  const nativeVal = item.discovery_score !== undefined ? item.discovery_score : item.score;
  const nativeScore = Number(nativeVal || 0);
  const std = standardizedScore(item);                 // 標準化分(可比較)
  const shown = std !== null ? std : nativeScore;
  const cls = shown >= 6.5 ? "pos" : shown <= 4 ? "neg" : "watch";
  const symbol = item.symbol || "";
  const name = item.name || "";
  const sector = item.sector ? `<span class="pill">${item.sector}</span>` : "";
  const gradePill = item.grade_label ? `<span class="pill">${item.grade_label}</span>` : "";
  const codexPill = item.codex_score !== undefined && item.codex_score !== null
    ? `<span class="pill">Codex v2 ${Number(item.codex_score).toFixed(1)}</span>`
    : "";
  const regimePill = item.regime_label
    ? `<span class="pill" style="background:#e0f2fe;color:#0369a1;border-color:#bae6fd;">${item.regime_label}</span>`
    : "";
  const reasonsHtml = asArray(item.reasons).slice(0, 4).map(r => `<li>${r}</li>`).join("");
  const dateClose = item.last_date
    ? `<p style="margin:4px 0;font-size:12px;color:var(--muted)">截至 ${item.last_date}，收盤 ${item.last_close || "-"}</p>`
    : "";
  const nativeLabel = Number.isInteger(nativeScore) ? nativeScore : nativeScore.toFixed(1);
  const scoreLabel = std !== null ? std.toFixed(1) : nativeLabel;
  const scoreTitle = std !== null ? `標準化分(校準機率基準,三家同尺度可比較)。此家原始分：${nativeLabel}` : "原始分";
  // Check if this symbol is currently selected to show active state
  const currentSyms = $("symbolInput").value.split(",").map(s => s.trim()).filter(Boolean);
  const isSelected = currentSyms.includes(symbol);
  return `<article class="agent-card${isSelected ? " agent-card--selected" : ""}" onclick="toggleSymbol('${symbol}', this)"
    title="點擊即可加入/移除 ${symbol}。可多選">
    <strong>
      <span>${symbol} ${name} ${sector} ${regimePill}</span>
      <span class="${cls}" title="${scoreTitle}">${scoreLabel}分</span>
    </strong>
    <div style="margin:3px 0;font-size:12px">${gradePill}${codexPill}</div>
    ${dateClose}
    <ul style="margin:4px 0 0;padding-left:14px;font-size:12px;color:var(--muted)">${reasonsHtml}</ul>
  </article>`;
}

function toggleSymbol(symbol, cardEl) {
  const input = $("symbolInput");
  let syms = input.value.split(",").map(s => s.trim()).filter(Boolean);
  const idx = syms.indexOf(symbol);
  if (idx === -1) {
    syms.push(symbol);
    if (cardEl && cardEl.classList) cardEl.classList.add("agent-card--selected");
  } else {
    syms.splice(idx, 1);
    if (cardEl && cardEl.classList) cardEl.classList.remove("agent-card--selected");
  }
  input.value = syms.join(",");
  if ($("universeGrid")) {
    loadUniverse();
  }
  
  // Instantly trigger updates
  refreshQuotes();
  recommendToday();
  nextDayPlan();
}

// Keep backward-compat alias for candidate cards rendered by recommendToday
function setSymbolFromAgent(symbol) {
  toggleSymbol(symbol, { classList: { add() {}, remove() {} } });
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
    const m = data.market_index;
    const riskLevel = m ? m.risk_level : null;
    
    // Update top market risk panel if available
    if (m) {
      updateMarketRiskPanel(m);
    }

    $("planList").innerHTML = plans.length ? plans.map(item => {
      const gain = item.held && item.unrealized_gain !== null ? `<span class="pill">未實現 ${pct(item.unrealized_gain)}</span>` : `<span class="pill">未持有</span>`;
      const cls = item.action.includes("買進") || item.action.includes("續抱") || item.action.includes("加碼") ? "pos" : item.action.includes("賣") || item.action.includes("減碼") ? "neg" : "watch";
      return `<article class="candidate">
        <strong>
          <span>${item.symbol} · <span class="${cls}">${item.action}</span></span>
          <span>分數 ${item.score}</span>
        </strong>
        <p>截至 ${item.as_of}，收盤 ${item.last_close} ${gain}</p>
        ${modelLine(item.model, riskLevel)}
        <ul>${asArray(item.reasons).map(reason => `<li>${reason}</li>`).join("")}</ul>
        ${aiPredictorLine(item.ai_predictor)}
        ${calibratedModelPanel(item.model, riskLevel)}
      </article>`;
    }).join("") : "<p>沒有明日計畫資料，請確認股票代號或區間。</p>";
    refreshQuotes();   // 持股/決策中心更新後,報價同步涵蓋
    saveSnapshot("plan");
  } catch (err) {
    $("planList").innerHTML = `<p>明日計畫失敗：${err.message}</p>`;
  } finally {
    setBusy("nextDayPlan", false);
  }
}

async function loadDailyPerformance() {
  try {
    $("dailyPerfStatus").textContent = "計算中…";
    const end = encodeURIComponent($("endDate").value);
    const res = await fetch(`/api/daily-performance?end=${end}`);
    const data = await readJson(res);
    const agents = asArray(data.agents);
    if (!agents.length) {
      $("dailyPerfStatus").textContent = data.error || "無資料";
      $("dailyPerfPanel").innerHTML = "<p>暫無昨日績效資料。</p>";
      return;
    }
    $("dailyPerfStatus").textContent = `選股日 ${data.pick_date} → 評估 ${data.eval_date}`;
    $("dailyPerfPanel").innerHTML = agents.map(a => {
      const avg = a.avg_return;
      const cls = avg === null ? "watch" : avg >= 0 ? "pos" : "neg";
      const avgText = avg === null ? "—" : pct(avg);
      const picks = asArray(a.picks).map(p => {
        const r = p.return;
        const rc = r === null ? "watch" : r >= 0 ? "pos" : "neg";
        return `<li><span>${p.symbol}</span> <span class="${rc}">${r === null ? "—" : pct(r)}</span></li>`;
      }).join("");
      return `<article class="candidate">
        <strong>
          <span>${a.agent}</span>
          <span class="${cls}">平均 ${avgText}</span>
        </strong>
        <p style="font-size:12px;color:var(--muted)">前一交易日選股 ${a.n} 檔的實現報酬</p>
        <ul style="list-style:none;padding-left:0;margin:6px 0 0;font-size:13px">${picks}</ul>
      </article>`;
    }).join("");
  } catch (err) {
    $("dailyPerfStatus").textContent = `失敗：${err.message}`;
    $("dailyPerfPanel").innerHTML = `<p>每日績效載入失敗：${err.message}</p>`;
  }
}

function bindActions() {
  $("refreshQuotes").addEventListener("click", refreshQuotes);
  $("runTraining").addEventListener("click", runTraining);
  $("recommendToday").addEventListener("click", recommendToday);
  $("discoverToday").addEventListener("click", discoverToday);
  $("nextDayPlan").addEventListener("click", nextDayPlan);
}

// Set endDate to today if not already set
if (!$("endDate").value) {
  $("endDate").value = new Date().toISOString().slice(0, 10);
}

bindActions();
refreshQuotes();
loadDailyPerformance();
discoverToday();
recommendToday();
nextDayPlan();

async function loadUniverse() {
  try {
    const res = await fetch("/api/universe");
    const data = await readJson(res);
    const universe = asArray(data);
    $("universeCount").textContent = `${universe.length} 檔週選標的`;
    
    $("universeGrid").innerHTML = universe.map(stock => {
      const isSelected = $("symbolInput").value.split(",").map(s => s.trim()).filter(Boolean).includes(stock.symbol);
      return `
        <div class="universe-card ${isSelected ? 'selected' : ''}" 
             style="
               background: ${isSelected ? 'rgba(0, 240, 255, 0.05)' : 'rgba(255, 255, 255, 0.02)'};
               border: 1px solid ${isSelected ? 'var(--green)' : 'var(--line)'};
               border-left: 4px solid ${isSelected ? 'var(--green)' : 'var(--line)'};
               border-radius: 6px;
               padding: 8px 12px;
               cursor: pointer;
               font-size: 13px;
               transition: all 0.2s ease;
             "
             onclick="toggleSymbol('${stock.symbol}', this)"
             title="點擊以選取/取消選取 ${stock.symbol}"
        >
          <div style="display: flex; justify-content: space-between; font-weight: bold;">
            <span>${stock.name}</span>
            <span style="font-family: monospace; color: var(--muted);">${stock.symbol}</span>
          </div>
          <div style="font-size: 11px; color: var(--muted); margin-top: 4px;">
            ${stock.sector || "一般類股"}
          </div>
        </div>
      `;
    }).join("");
  } catch (err) {
    $("universeCount").textContent = `載入失敗: ${err.message}`;
  }
}

async function loadMarketNews() {
  try {
    const end = encodeURIComponent($("endDate").value);
    const res = await fetch(`/api/news?date=${end}`);
    const data = await readJson(res);
    const news = asArray(data.news);
    
    if (news.length > 0) {
      $("focusNewsPanel").style.display = "block";
      $("focusNewsGrid").innerHTML = news.map(item => {
        let badgeColor = '#666';
        if (item.category === '大盤市場') badgeColor = '#00f0ff';
        if (item.category === '個股焦點') badgeColor = '#00ff66';
        if (item.category === '環境特徵') badgeColor = '#ffb700';
        if (item.category === '社群情緒') badgeColor = '#ff0055';
        
        return `
          <div style="background: rgba(0,0,0,0.2); border: 1px solid var(--line); border-radius: 6px; padding: 10px; font-size: 13px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
              <span class="pill" style="border-color: ${badgeColor}; color: ${badgeColor}; font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: bold;">
                ${item.category}
              </span>
              <span style="font-size: 11px; color: var(--muted); font-family: monospace;">
                ${item.time}
              </span>
            </div>
            <div style="color: var(--text); line-height: 1.4;">
              ${item.title}
            </div>
          </div>
        `;
      }).join("");
    } else {
      $("focusNewsPanel").style.display = "none";
    }
  } catch (err) {
    console.error("Error loading market news:", err);
    $("focusNewsPanel").style.display = "none";
  }
}

// --- 1. Smart Portfolio Allocator ---
function updatePortfolioAllocation(marketIndex, candidates) {
  const panel = $("portfolioAllocationPanel");
  if (!panel) return;
  if (!marketIndex || !candidates || candidates.length === 0) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "block";
  
  const regime = marketIndex.regime || "區間整理";
  const riskLevel = marketIndex.risk_level;
  let stockExposure = 50;
  if (regime.includes("強勢多頭")) stockExposure = 90;
  else if (regime.includes("弱勢空頭")) stockExposure = 20;
  else if (regime.includes("高波動震盪")) stockExposure = 40;
  else if (regime.includes("區間整理")) stockExposure = 60;
  
  if (riskLevel === "BLACK" || riskLevel === "RED") {
    stockExposure = 0;
  } else if (riskLevel === "YELLOW") {
    stockExposure = Math.min(25, Math.round(stockExposure / 2));
  }
  
  $("portfolioRegimeLabel").textContent = `當前狀態：${regime} (${marketIndex.risk_label || "正常"})`;
  $("stockExposureVal").textContent = `${stockExposure}%`;
  $("stockExposureBar").style.width = `${stockExposure}%`;
  $("cashExposureVal").textContent = `${100 - stockExposure}%`;
  $("cashExposureBar").style.width = `${100 - stockExposure}%`;
  $("regimeStanceText").textContent = `分析官觀點：${marketIndex.risk_stance || marketIndex.regime_note || "大盤整理中，建議均衡配置。"}`;
  
  // Top 5 allocation
  const topCands = candidates.slice(0, 5);
  const scores = topCands.map(c => {
    const std = standardizedScore(c);
    return Math.max(0.1, std !== null ? std : (c.score || 1));
  });
  const totalScore = scores.reduce((a, b) => a + b, 0) || 1;
  
  const grid = $("stockAllocationsGrid");
  grid.innerHTML = topCands.map((c, i) => {
    const score = scores[i];
    const weightOfStockPortion = score / totalScore;
    const finalWeight = weightOfStockPortion * stockExposure;
    
    return `
      <div style="font-size: 13px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
          <span><strong>${c.symbol} ${c.name || ""}</strong> <span style="font-size: 11px; color: var(--muted);">(${c.sector || "一般類股"})</span></span>
          <span style="font-family: monospace;">權重：<strong style="color: var(--blue); font-size: 13px;">${finalWeight.toFixed(1)}%</strong> (相對佔比 ${(weightOfStockPortion*100).toFixed(0)}%)</span>
        </div>
        <div style="height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden;">
          <div style="height: 100%; background: var(--blue); width: ${(weightOfStockPortion * 100).toFixed(0)}%;"></div>
        </div>
      </div>
    `;
  }).join("");
}

// --- 2. Historical Simulation Snapshots ---
function saveSnapshot(actionType) {
  try {
    const dateVal = $("endDate").value;
    const regimeText = $("portfolioRegimeLabel").textContent.replace("當前狀態：", "") || "中性震盪";
    const picks = lastAgentPicks.slice(0, 5).join(",") || symbols().slice(0, 3).join(",");
    const pos = $("positionInput").value || "無持倉";
    
    let accuracy = "未優化";
    const banner = $("weightsDetail");
    if (banner && banner.textContent.includes("準確率")) {
      const parts = banner.textContent.split("準確率:");
      if (parts.length > 1) {
        accuracy = parts[1].trim();
      }
    }
    
    const snapshot = {
      timestamp: new Date().toLocaleString(),
      targetDate: dateVal,
      regime: regimeText,
      picks: picks,
      positions: pos,
      accuracy: accuracy
    };
    
    let list = [];
    const saved = localStorage.getItem("quant_snapshots");
    if (saved) {
      list = JSON.parse(saved);
    }
    list.unshift(snapshot); // prepend new ones
    localStorage.setItem("quant_snapshots", JSON.stringify(list));
    renderSnapshots();
  } catch (err) {
    console.error("Failed to save snapshot:", err);
  }
}

function renderSnapshots() {
  const rowsEl = $("snapshotRows");
  if (!rowsEl) return;
  const saved = localStorage.getItem("quant_snapshots");
  if (!saved) {
    rowsEl.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--muted);">尚無覆盤快照。當您執行「明日計畫」或「開始區間訓練」時將自動記錄快照。</td></tr>`;
    return;
  }
  const list = JSON.parse(saved);
  if (list.length === 0) {
    rowsEl.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--muted);">尚無覆盤快照。當您執行「明日計畫」或「開始區間訓練」時將自動記錄快照。</td></tr>`;
    return;
  }
  rowsEl.innerHTML = list.map((item, idx) => `
    <tr>
      <td>${item.timestamp}</td>
      <td><strong>${item.targetDate}</strong></td>
      <td>${item.regime}</td>
      <td><span style="font-family: monospace; font-size: 12px;">${item.picks}</span></td>
      <td title="${item.positions}">${item.positions.length > 30 ? item.positions.slice(0, 30) + "..." : item.positions}</td>
      <td>${item.accuracy}</td>
      <td>
        <button class="danger" onclick="deleteSnapshot(${idx})" style="height: 24px; line-height: 22px; padding: 0 6px; font-size: 11px; border-color: var(--red); color: var(--red); background: #fef2f2;">刪除</button>
      </td>
    </tr>
  `).join("");
}

window.deleteSnapshot = function(idx) {
  const saved = localStorage.getItem("quant_snapshots");
  if (saved) {
    const list = JSON.parse(saved);
    list.splice(idx, 1);
    localStorage.setItem("quant_snapshots", JSON.stringify(list));
    renderSnapshots();
  }
};

// --- 3. Bind Sliders and History Buttons ---
$("feeRate").addEventListener("input", (e) => {
  $("feeRateVal").textContent = (e.target.value * 100).toFixed(4) + "%";
});
$("taxRate").addEventListener("input", (e) => {
  $("taxRateVal").textContent = (e.target.value * 100).toFixed(2) + "%";
});
$("slippageRate").addEventListener("input", (e) => {
  $("slippageRateVal").textContent = (e.target.value * 100).toFixed(2) + "%";
});

$("clearSnapshots").addEventListener("click", () => {
  if (confirm("確定要清除所有歷史快照日誌嗎？")) {
    localStorage.removeItem("quant_snapshots");
    renderSnapshots();
  }
});

$("exportSnapshots").addEventListener("click", () => {
  const saved = localStorage.getItem("quant_snapshots");
  if (!saved || JSON.parse(saved).length === 0) {
    alert("沒有任何日誌可以匯出。");
    return;
  }
  const blob = new Blob([saved], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `solopreneur_quant_snapshots_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

$("importSnapshotsBtn").addEventListener("click", () => {
  $("importSnapshotsFile").click();
});

$("importSnapshotsFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(event) {
    try {
      const list = JSON.parse(event.target.result);
      if (!Array.isArray(list)) {
        alert("錯誤的檔案格式：應為快照陣列 JSON。");
        return;
      }
      localStorage.setItem("quant_snapshots", JSON.stringify(list));
      renderSnapshots();
      alert(`成功匯入 ${list.length} 筆歷史快照日誌！`);
    } catch (err) {
      alert("載入檔案失敗：" + err.message);
    }
  };
  reader.readAsText(file);
});

$("replayOptimizeSnapshots").addEventListener("click", async () => {
  const saved = localStorage.getItem("quant_snapshots");
  if (!saved || JSON.parse(saved).length === 0) {
    alert("沒有歷史日誌快照可供覆盤。請先執行「明日計畫」或「開始區間訓練」以產生快照。");
    return;
  }
  
  const panel = $("replayResultsPanel");
  panel.style.display = "block";
  panel.innerHTML = `
    <div style="text-align: center; padding: 25px; color: var(--muted);">
      <span class="loading-spinner" style="display: inline-block; width: 22px; height: 22px; border: 3px solid var(--blue); border-radius: 50%; border-top-color: transparent; animation: spin 1s linear infinite; margin-right: 10px; vertical-align: middle;"></span>
      正在與後端資料庫對齊歷史價格，進行一鍵覆盤與因子權重校準，請稍候...
    </div>
    <style>
      @keyframes spin { to { transform: rotate(360deg); } }
    </style>
  `;
  
  try {
    const response = await fetch("/api/replay-snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshots: JSON.parse(saved) })
    });
    
    if (!response.ok) {
      throw new Error(`伺服器錯誤: ${response.status}`);
    }
    
    const data = await response.json();
    renderReplayResults(data);
  } catch (err) {
    panel.innerHTML = `<div style="color: var(--red); padding: 12px; border: 1px solid var(--red); background: #fef2f2; border-radius: 6px; font-size: 13px;">覆盤失敗：${err.message}</div>`;
  }
});

function renderReplayResults(data) {
  const panel = $("replayResultsPanel");
  if (!panel) return;
  
  const sum = data.summary;
  const opt = data.optimization;
  const regimes = data.regime_breakdown;
  const details = data.details;
  
  const fmtPctSign = (val) => (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
  
  let html = `
    <h3 style="margin-top: 0; color: #1e293b; display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #f1f5f9; padding-bottom: 10px; font-size: 15px;">
      <span style="display: flex; align-items: center; gap: 6px;">🎯 歷史快照一鍵覆盤與優化報告</span>
      <button onclick="document.getElementById('replayResultsPanel').style.display='none'" style="height: 24px; padding: 0 8px; font-size: 11px; background: #e2e8f0; border: none; color: #475569; border-radius: 4px; cursor: pointer;">關閉報告</button>
    </h3>
    
    <!-- 1. Scorecard Grid -->
    <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 20px;">
      <div style="background: #f8fafc; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; font-weight: 500;">評估推薦總股次</div>
        <div style="font-size: 20px; font-weight: bold; color: #1e293b; margin-top: 4px;">${sum.total_picks} 次</div>
      </div>
      <div style="background: #f8fafc; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; font-weight: 500;">樣本外 5D 勝率</div>
        <div style="font-size: 20px; font-weight: bold; margin-top: 4px;" class="${sum.win_rate >= 50 ? 'pos' : 'neg'}">${sum.win_rate.toFixed(1)}%</div>
      </div>
      <div style="background: #f8fafc; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; font-weight: 500;">平均 5D 報酬率</div>
        <div style="font-size: 20px; font-weight: bold; margin-top: 4px;" class="${sum.avg_return >= 0 ? 'pos' : 'neg'}">${fmtPctSign(sum.avg_return)}</div>
      </div>
      <div style="background: #f8fafc; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; font-weight: 500;">平均潛在最大漲幅</div>
        <div style="font-size: 20px; font-weight: bold; margin-top: 4px;" class="pos">+${(sum.avg_max_profit * 100).toFixed(2)}%</div>
      </div>
      <div style="background: #f8fafc; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; font-weight: 500;">平均最大潛在回撤</div>
        <div style="font-size: 20px; font-weight: bold; margin-top: 4px;" class="neg">${(sum.avg_mdd * 100).toFixed(2)}%</div>
      </div>
    </div>
    
    <!-- 2. Regime Breakdown & Diagnostics -->
    <h4 style="margin: 0 0 10px 0; color: #334155; font-size: 13px;">🚦 大盤狀態 (Regime) 表現分析與門檻診斷</h4>
    <div style="display: grid; grid-template-columns: 1fr; gap: 8px; margin-bottom: 20px;">
  `;
  
  if (regimes.length === 0) {
    html += `<div style="padding: 10px; background: #f8fafc; border-radius: 6px; color: #64748b; font-size: 12px; text-align: center;">無大盤狀態分類數據。</div>`;
  } else {
    regimes.forEach(r => {
      let bg = "#f8fafc", border = "#e2e8f0", color = "#475569";
      if (r.level === "warning") {
        bg = "#fffbeb"; border = "#fef3c7"; color = "var(--amber)";
      } else if (r.level === "success") {
        bg = "#f0fdf4"; border = "#bbf7d0"; color = "var(--red)";
      } else if (r.level === "info") {
        bg = "#eff6ff"; border = "#bfdbfe"; color = "var(--blue)";
      }
      
      html += `
        <div style="background: ${bg}; border: 1px solid ${border}; border-radius: 6px; padding: 10px 12px; display: flex; align-items: flex-start; gap: 12px; font-size: 12px;">
          <div style="font-weight: bold; min-width: 90px; color: ${color}; font-size: 13px;">${r.regime}</div>
          <div style="flex: 1; line-height: 1.5;">
            <div style="margin-bottom: 4px; color: #1e293b;">
              樣本數：<strong>${r.count}</strong> | 
              勝率：<strong class="${r.win_rate >= 50 ? 'pos' : 'neg'}">${r.win_rate.toFixed(1)}%</strong> | 
              平均報酬：<strong class="${r.avg_return >= 0 ? 'pos' : 'neg'}">${fmtPctSign(r.avg_return)}</strong> | 
              平均回撤：<strong class="neg">${(r.avg_mdd * 100).toFixed(2)}%</strong>
            </div>
            <div style="color: #475569; font-weight: 500;">💡 診斷：${r.diagnostic}</div>
          </div>
        </div>
      `;
    });
  }
  
  html += `
    </div>
    
    <!-- 3. Weights Tuning Optimizer -->
    <div style="display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; margin-bottom: 20px; border-top: 1px solid #e2e8f0; padding-top: 20px;">
      <div>
        <h4 style="margin: 0 0 10px 0; color: #334155; font-size: 13px;">🎛️ AI 預測模型因子權重校準提案</h4>
        <p style="margin: 0 0 12px 0; font-size: 12px; color: #64748b; line-height: 1.4;">
          分析推薦股在基準日的指標因子與後續 5 日漲跌。優化器將進行在線梯度下降優化，藉此降低選股偽陽性機率（優化特定股池的勝率，而非全局隨機樣本）。
        </p>
  `;
  
  if (!opt.available) {
    html += `
        <div style="padding: 15px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center; color: #64748b; font-size: 12px;">
          樣本數不足（需有歷史推薦個股），無法進行因子權重擬合優化。
        </div>
      </div>
    `;
  } else {
    const cw = opt.current_weights;
    const ow = opt.optimized_weights;
    
    html += `
        <table style="width: 100%; font-size: 12px; border-collapse: collapse; text-align: left;">
          <thead>
            <tr style="border-bottom: 1px solid #e2e8f0; color: #64748b;">
              <th style="padding: 6px 0;">因子名稱</th>
              <th>當前配置</th>
              <th>覆盤校準值</th>
              <th>變化趨勢</th>
            </tr>
          </thead>
          <tbody>
            <tr style="border-bottom: 1px dotted #e2e8f0;">
              <td style="padding: 6px 0; font-weight: 500;">偏誤 (Bias)</td>
              <td>${cw.bias.toFixed(3)}</td>
              <td style="font-weight: bold; color: #1e293b;">${ow.bias.toFixed(3)}</td>
              <td style="color: ${ow.bias > cw.bias ? 'var(--red)' : ow.bias < cw.bias ? 'var(--green)' : '#64748b'};">${ow.bias > cw.bias ? '📈 上調' : ow.bias < cw.bias ? '📉 下調' : '➖ 不變'}</td>
            </tr>
            <tr style="border-bottom: 1px dotted #e2e8f0;">
              <td style="padding: 6px 0; font-weight: 500;">超賣程度 (RSI 因子)</td>
              <td>${cw.rsi.toFixed(3)}</td>
              <td style="font-weight: bold; color: #1e293b;">${ow.rsi.toFixed(3)}</td>
              <td style="color: ${ow.rsi > cw.rsi ? 'var(--red)' : ow.rsi < cw.rsi ? 'var(--green)' : '#64748b'};">${ow.rsi > cw.rsi ? '📈 加重' : ow.rsi < cw.rsi ? '📉 減輕' : '➖ 不變'}</td>
            </tr>
            <tr style="border-bottom: 1px dotted #e2e8f0;">
              <td style="padding: 6px 0; font-weight: 500;">近5日趨勢 (Slope 因子)</td>
              <td>${cw.slope.toFixed(3)}</td>
              <td style="font-weight: bold; color: #1e293b;">${ow.slope.toFixed(3)}</td>
              <td style="color: ${ow.slope > cw.slope ? 'var(--red)' : ow.slope < cw.slope ? 'var(--green)' : '#64748b'};">${ow.slope > cw.slope ? '📈 加重' : ow.slope < cw.slope ? '📉 減輕' : '➖ 不變'}</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
              <td style="padding: 6px 0; font-weight: 500;">柱狀動能 (MACD 因子)</td>
              <td>${cw.macd_hist.toFixed(3)}</td>
              <td style="font-weight: bold; color: #1e293b;">${ow.macd_hist.toFixed(3)}</td>
              <td style="color: ${ow.macd_hist > cw.macd_hist ? 'var(--red)' : ow.macd_hist < cw.macd_hist ? 'var(--green)' : '#64748b'};">${ow.macd_hist > cw.macd_hist ? '📈 加重' : ow.macd_hist < cw.macd_hist ? '📉 減輕' : '➖ 不變'}</td>
            </tr>
          </tbody>
        </table>
        
        <div style="margin-top: 15px; display: flex; align-items: center; justify-content: space-between;">
          <span style="font-size: 11px; color: #64748b;">基於 <strong>${opt.sample_count}</strong> 組推薦個股樣本進行在線優化</span>
          <button id="applyOptimizedWeightsBtn" class="primary" style="height: 28px; line-height: 26px; padding: 0 12px; font-size: 12px; background-color: #10b981; border-color: #10b981; color: white;">套用優化權重</button>
        </div>
      </div>
    `;
  }
  
  html += `
      <!-- 4. Epoch Convergence logs -->
      <div>
        <h4 style="margin: 0 0 10px 0; color: #334155; font-size: 13px;">📈 收斂進度與優化成效</h4>
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; height: 140px; overflow-y: auto; font-family: monospace; font-size: 11px; line-height: 1.5; color: #475569;">
  `;
  
  if (!opt.available || !opt.epoch_logs || opt.epoch_logs.length === 0) {
    html += `<div>無優化日誌。請增加歷史快照日誌。</div>`;
  } else {
    html += `
      <div style="font-weight: bold; color: #1e293b; margin-bottom: 4px;">梯度下降收斂紀錄 (Gradient Descent Run):</div>
    `;
    opt.epoch_logs.forEach(log => {
      html += `<div>[Epoch ${log.epoch.toString().padStart(3)}] Loss: ${log.loss.toFixed(4)} | Acc: ${log.accuracy.toFixed(1)}%</div>`;
    });
    html += `
      <div style="border-top: 1px dashed #cbd5e1; margin-top: 6px; padding-top: 6px; font-weight: bold; color: #166534;">
        校準後預測勝率提升至: ${opt.optimized_accuracy.toFixed(1)}%
      </div>
    `;
  }
  
  html += `
        </div>
      </div>
    </div>
    
    <!-- 5. Detailed Pick Replay Table -->
    <h4 style="margin: 20px 0 10px 0; color: #334155; font-size: 13px; border-top: 1px solid #e2e8f0; padding-top: 20px;">📋 歷次推薦個股樣本外持有 5 日明細表</h4>
    <div style="max-height: 250px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 6px;">
      <table style="width: 100%; font-size: 12px; border-collapse: collapse; text-align: left;">
        <thead>
          <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0; color: #64748b;">
            <th style="padding: 8px 10px;">決策基準日</th>
            <th>大盤狀態</th>
            <th>股票名稱</th>
            <th>進場價 (T+1 O)</th>
            <th>出場價 (T+5 C)</th>
            <th>5D 漲跌幅</th>
            <th>5D 最大回撤</th>
            <th>5D 最大潛在漲幅</th>
            <th>方向</th>
          </tr>
        </thead>
        <tbody>
  `;
  
  if (details.length === 0) {
    html += `<tr><td colspan="9" style="text-align: center; padding: 20px; color: #64748b;">尚無個股明細，請增加歷史日誌。</td></tr>`;
  } else {
    details.forEach(d => {
      const isWin = d.win > 0.5;
      html += `
        <tr style="border-bottom: 1px solid #f1f5f9;">
          <td style="padding: 8px 10px; font-weight: 500;">${d.target_date}</td>
          <td>${d.regime}</td>
          <td><strong>${d.name}</strong> <span style="color: #64748b; font-size: 10px;">(${d.symbol})</span></td>
          <td>$${d.entry_price.toFixed(1)}</td>
          <td>$${d.exit_price.toFixed(1)}</td>
          <td style="font-weight: bold;" class="${d.return_5d >= 0 ? 'pos' : 'neg'}">${fmtPctSign(d.return_5d)}</td>
          <td class="neg">${(d.max_drawdown * 100).toFixed(2)}%</td>
          <td class="pos">+${(d.max_profit * 100).toFixed(2)}%</td>
          <td><span style="padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: bold; background: ${isWin ? '#fef2f2' : '#f0fdf4'}; color: ${isWin ? 'var(--red)' : 'var(--green)'};">${isWin ? '獲利' : '虧損'}</span></td>
        </tr>
      `;
    });
  }
  
  html += `
        </tbody>
      </table>
    </div>
  `;
  
  panel.innerHTML = html;
  
  const applyBtn = $("applyOptimizedWeightsBtn");
  if (applyBtn) {
    applyBtn.addEventListener("click", async () => {
      if (!confirm("確定要將今日校準優化後的因子權重套用到實體預測模型中嗎？這將會即時影響「明日計畫」及新推薦個股的評估機率。")) {
        return;
      }
      
      try {
        const res = await fetch("/api/save-weights", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ weights: opt.optimized_weights })
        });
        
        if (!res.ok) {
          throw new Error("發送保存權重請求失敗");
        }
        
        const resData = await res.json();
        alert(resData.message);
        
        panel.style.display = "none";
        
        const optimizedWeightsBlock = $("optimizedWeights");
        const weightsDetailText = $("weightsDetail");
        if (optimizedWeightsBlock && weightsDetailText) {
          optimizedWeightsBlock.style.display = "block";
          const w = opt.optimized_weights;
          weightsDetailText.textContent = `偏誤(Bias): ${w.bias.toFixed(3)}, RSI: ${w.rsi.toFixed(3)}, 斜率(Slope): ${w.slope.toFixed(3)}, MACD柱體: ${w.macd_hist.toFixed(3)} (準確率: ${opt.optimized_accuracy.toFixed(1)}%)`;
        }
      } catch (err) {
        alert("套用優化權重失敗：" + err.message);
      }
    });
  }
}


// --- 4. Initialization Runs ---
loadUniverse();
loadMarketNews();
renderSnapshots();
$("endDate").addEventListener("change", loadMarketNews);

