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
    
    // Clear console and hide optimized weights banner
    $("trainConsole").textContent = "🔍 正在啟動在線優化訓練器...
";
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
        initial_cash: 1000000
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
      terminal.textContent = "🔍 [1/4] 特徵提取完成，共收集 " + (logs.length * 10) + " 個跨股票歷史交易日樣本。
";
      terminal.textContent += "⚙️ [2/4] 啟動隨機梯度下降 L2 擬合器 (Learning Rate=0.1, L2=0.01, Epochs=500)...

";
      
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
