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
    </tr>`;
  }).join("");
  $("quoteTime").textContent = new Date().toLocaleString();
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
      <ul>${item.reasons.map(reason => `<li>${reason}</li>`).join("")}</ul>
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
      <ul>${item.reasons.map(reason => `<li>${reason}</li>`).join("")}</ul>
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
