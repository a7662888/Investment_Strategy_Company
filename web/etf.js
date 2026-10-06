'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {catalog: null, market: 'tw', selected: null, quotes: new Map(), quoteBusy: false, quoteQueued: false, catalogBusy: false, holdingsVersion: 0, holdingsBusy: false};
  const formatter = new Intl.NumberFormat('zh-TW', {maximumFractionDigits: 2});
  const officialHosts = ['twse.com.tw', 'tpex.org.tw', 'tdcc.com.tw', 'ezmoney.com.tw', 'fhtrust.com.tw', 'alliancebernstein.com', 'yuantaetfs.com', 'youtube.com', 'tw.stock.yahoo.com'];
  const finite = (n) => typeof n === 'number' && Number.isFinite(n);
  const fmt = (n, decimals = 2) => finite(n) ? n.toLocaleString('zh-TW', {minimumFractionDigits: decimals, maximumFractionDigits: decimals}) : 'NA';
  const count = (n) => finite(n) ? formatter.format(n) : 'NA';
  const pct = (n) => finite(n) ? `${fmt(n)}%` : 'NA';
  const feeText = (value) => finite(value) ? pct(value) : typeof value === 'string' && value.trim() ? value.trim() : 'NA · 待核對';
  const signed = (n, unit = '') => finite(n) ? `${n > 0 ? '+' : ''}${fmt(n)}${unit}` : 'NA';
  const datumDate = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : 'NA';
  const clock = (value) => {
    if (value === null || value === undefined || value === '') return 'NA';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei', hour12: false}) : 'NA';
  };
  const today = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  const ageDays = (s) => datumDate(s) === 'NA' ? null : Math.floor((Date.parse(`${today()}T00:00:00Z`) - Date.parse(`${s}T00:00:00Z`)) / 86400000);
  const node = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined && value !== null) element.textContent = String(value);
    if (className) element.className = className;
    return element;
  };
  const append = (parent, ...children) => { children.forEach((child) => parent.appendChild(child)); return parent; };
  const note = (message, className = 'fineprint') => node('p', message, className);
  const external = (label, raw) => {
    try {
      const url = new URL(raw);
      if (url.protocol !== 'https:' || url.username || url.password || !officialHosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) throw new Error('unsafe link');
      const link = node('a', label);
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      return link;
    } catch { return node('span', `${label}（無有效官方 HTTPS 連結）`); }
  };
  const metric = (label, value) => append(node('div', null, 'datum'), node('span', label), node('strong', value));
  const changeClass = (value) => finite(value) ? value > 0 ? 'up' : value < 0 ? 'down' : '' : '';
  async function getJSON(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(90000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.error && !data.rows && !data.status) throw new Error('資料暫時無法取得');
    return data;
  }
  function rankedRows() {
    return (state.catalog?.rows || []).filter((row) => row.market === state.market && row.ranking_status === 'official_weekly' && finite(row.holders) && finite(row.popularity_rank)).sort((a, b) => a.popularity_rank - b.popularity_rank).slice(0, 20);
  }
  function chooseFund(code, scroll = false) {
    state.selected = code;
    renderDetail();
    if (scroll) $('fundDetail').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    refreshQuotes();
  }
  function fundButton(row, compact = false) {
    const button = node('button', compact ? `${row.code} ${row.name}` : row.code, compact ? '' : 'fund-button');
    button.type = 'button';
    if (!compact) { append(button, node('span', ` ${row.name}`), node('small', `${row.category || '未分類'}${row.active ? ' · 主動式' : ''}`)); }
    button.addEventListener('click', () => chooseFund(row.code, true));
    return button;
  }
  function quoteDescription(quote) {
    if (!quote) return {text: '尚未取得', time: 'NA'};
    const timestamp = quote.regularMarketTime;
    const validTime = finite(timestamp) && timestamp > 0 && timestamp * 1000 <= Date.now() + 60000;
    if (!validTime) return {text: '時間無法核對 · 數值 NA', time: 'NA', invalid: true};
    const date = new Date(timestamp * 1000);
    const dateParts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit'}).format(date);
    const age = (Date.now() - date.getTime()) / 60000;
    const prefix = dateParts !== today() ? '歷史行情' : state.marketSession && age > 15 ? '行情延遲逾 15 分鐘' : '依來源時間';
    return {text: `${prefix} · ${quote.source || '來源未提供'}`, time: clock(date), status: quote.realtimeStatus || '即時性未確認'};
  }
  function renderRanking() {
    const body = $('rankingRows');
    body.replaceChildren();
    const rows = rankedRows();
    if (!rows.length) {
      const cell = node('td', '未取得此類可比較的同一期官方受益人資料；不以圖表舊值代替即時排行。', 'loading-cell');
      cell.colSpan = 6;
      body.appendChild(append(node('tr'), cell));
      return;
    }
    rows.forEach((row) => {
      const quote = state.quotes.get(row.code);
      const description = quoteDescription(quote);
      const price = description.invalid ? null : quote?.regularMarketPrice;
      const change = description.invalid ? null : quote?.regularMarketChangePercent;
      const nameCell = append(node('td'), fundButton(row));
      const holderCell = append(node('td', count(row.holders), 'numeric'), node('small', datumDate(row.ranking_as_of), 'subtext'));
      const sourceCell = append(node('td'), node('span', description.time), node('small', description.text, 'subtext'));
      if (description.status) sourceCell.title = description.status;
      append(body, append(node('tr'), node('td', row.popularity_rank, 'rank-number'), nameCell, holderCell, node('td', fmt(price), 'numeric'), node('td', signed(change, '%'), `numeric ${changeClass(change)}`), sourceCell));
    });
  }
  function renderDetail() {
    const row = state.catalog?.rows.find((item) => item.code === state.selected);
    if (!row) return;
    const panel = $('fundDetail');
    panel.replaceChildren();
    const quoteSymbol = /^00\d{2,4}[ABD]?\.(TW|TWO)$/.test(row.quote_symbol || '') ? row.quote_symbol : `${row.code}.TW`;
    const links = append(node('div', null, 'detail-links'), external('官方商品頁 ↗', row.official_url), external('奇摩股市 ↗', row.yahoo_url || `https://tw.stock.yahoo.com/quote/${encodeURIComponent(quoteSymbol)}`));
    append(panel, append(node('div', null, 'detail-head'), append(node('div'), node('h3', `${row.code} ${row.name}`), node('span', row.fund_type || '基金類型 NA', 'subtext')), links));
    const quote = state.quotes.get(row.code);
    const description = quoteDescription(quote);
    const grid = node('div', null, 'detail-grid');
    append(grid, metric('分類研究方向', row.category || 'NA'), metric('上市日期', datumDate(row.listing_date)), metric('經理費（商品規格）', feeText(row.management_fee)), metric('保管費（商品規格）', feeText(row.custody_fee)), metric('參考指標 / 追蹤指數', row.benchmark || 'NA'), metric('發行受益權單位', count(row.units)), metric('最新取得市價（元）', description.invalid ? 'NA' : fmt(quote?.regularMarketPrice)), metric('官方含息總報酬', 'NA · 未串接'));
    append(panel, grid, note(`基本資料基準日：${datumDate(row.basic_as_of)}；公告日：NA（來源未提供）。${row.fee_status || '費用未取得，請核對公開說明書。'} 行情：${description.time}，${description.text}。`, 'detail-note'));
    if (row.basic_source) append(panel, external('官方基本資料來源 ↗', row.basic_source));
    if (row.fee_source) append(panel, note(`費用規格基準日：${datumDate(row.fee_as_of)}；取得時間：${clock(row.fee_fetched_at)}；商品規格不等於實際總費用率。`), external('官方費用規格來源 ↗', row.fee_source));
    const chart = row.chart_snapshot;
    if (chart) {
      const heading = node('h4', `歷史圖表快照 · ${datumDate(chart.ranking_as_of)}（使用者圖表轉錄，未官方重算）`, 'holdings-title');
      const chartGrid = node('div', null, 'detail-grid');
      append(chartGrid, metric('圖表基金規模（億元）', fmt(chart.aum_billion_twd)), metric('圖表台積電權重', pct(chart.tsmc_weight_pct)), metric('圖表期間報酬', pct(chart.performance?.return_pct)), metric('圖表除息日', datumDate(chart.dividend?.ex_date)), metric('圖表每單位配息（元）', fmt(chart.dividend?.cash_per_unit)), metric('圖表實際配息率', pct(chart.dividend?.trailing_cash_yield_pct)), metric('圖表簡單年化配息試算', pct(chart.dividend?.simple_annualized_pct)), metric('圖表發放日', datumDate(chart.dividend?.payment_date)));
      append(panel, heading, chartGrid);
      const start = chart.performance?.start_date === 'listing_date' ? `自上市日起（${datumDate(row.listing_date)}）` : datumDate(chart.performance?.start_date);
      append(panel, note(`圖表績效期間：${start} 至 ${datumDate(chart.performance?.as_of)}。${chart.performance?.basis || '績效計算基礎未提供。'} 配息資料尚未核對最新投信公告；簡單年化值不是保證收益，配息不等於總報酬。`, 'detail-note'));
    }
    append(panel, note(row.recommendation_status || '分類研究候選；費用與同期間績效尚待核對，無買賣判定。'));
  }
  function renderGuides() {
    $('guideCards').replaceChildren();
    (state.catalog.guides || []).forEach((guide) => {
      const card = node('article', null, 'guide-card');
      append(card, node('h3', guide.category), note(guide.purpose, 'guide-purpose'), note(guide.criteria, ''), note(`風險｜${guide.risk}`, 'risk'));
      const list = node('div', null, 'candidate-list');
      (guide.codes || []).forEach((code) => { const row = state.catalog.rows.find((item) => item.code === code); if (row) list.appendChild(fundButton(row, true)); });
      if (!list.childElementCount) list.appendChild(node('span', '尚無可比較的受益人資料', 'fineprint'));
      append(card, list, note('同類受益人排序的研究入口；不是預期報酬排序，也未完成適合度評估。', 'candidate-note'));
      $('guideCards').appendChild(card);
    });
  }
  function renderSources() {
    $('sourceList').replaceChildren();
    [['TWSE 上市 ETF 基本資料', state.catalog.basic_status?.source], ['TPEx 上櫃 ETF 官方清單', state.catalog.tpex_status?.source || 'https://info.tpex.org.tw/api/etfFilter'], ['集保 ETF 受益人週資料', state.catalog.holder_status?.source]].forEach(([label, url]) => $('sourceList').appendChild(append(node('li'), external(label, url))));
    $('sourceList').appendChild(node('li', '主動 ETF 持股採投信官方 PCF / 資產明細；個別來源於持股區標示。'));
    $('sourceList').appendChild(node('li', '行情沿用永豐金 Shioaji 行情快照優先，其次 TWSE／TPEx 與 Yahoo 備援。僅呈現 ETF 公開市場行情，並未讀取私人持股。Yahoo 資料必須附有效行情時間，否則顯示 NA；延遲與歷史資料會降級標示。'));
    const video = state.catalog.video || {};
    $('videoSource').replaceChildren(external(video.title || '使用者提供 YouTube 影片', video.url));
    const transcriptStatus = video.transcript_status || '未提供';
    const transcriptVerified = /complete|verified|transcribed/.test(transcriptStatus) && !/unavailable|pending/.test(transcriptStatus);
    $('videoSource').appendChild(note(transcriptVerified ? '影片轉錄已取得；影片觀點不直接轉為 ETF 買賣判定。' : '影片完整轉錄尚未取得；目前僅記錄已取得的標題與章節，不宣稱已完整閱讀內容。'));
    if (Array.isArray(video.chapters)) $('videoSource').appendChild(note(video.chapters.map((item) => `${item.at} ${item.topic}`).join(' / ')));
    $('chartSources').replaceChildren();
    (state.catalog.sources || []).forEach((source) => $('chartSources').appendChild(node('li', `${source.title}｜${datumDate(source.as_of)}｜${source.status || '使用者提供圖表'}`)));
    $('disclosure').textContent = state.catalog.disclosure || '分類候選供研究；未取得資料顯示 NA，不推測補值。';
  }
  async function refreshCatalog() {
    if (state.catalogBusy) return;
    state.catalogBusy = true;
    $('refreshCatalog').disabled = true;
    $('pageStatus').textContent = '正在讀取官方基本資料與集保週資料…';
    try {
      const data = await getJSON('/api/etf/catalog');
      if (!Array.isArray(data.rows)) throw new Error('資料格式不符');
      state.catalog = data;
      state.catalogLoadedAt = Date.now();
      $('catalogCount').textContent = `${data.rows.length} 檔`;
      $('rankingDate').textContent = datumDate(data.holder_status?.as_of);
      $('activeCoverage').textContent = `${data.holdings_supported_count ?? 'NA'} / ${data.active_count ?? 'NA'} 檔`;
      $('rankingBasis').textContent = data.ranking_basis || '集保受益人數週資料，非盤中即時人氣。';
      $('scopeNote').textContent = data.scope || '';
      const staleDays = ageDays(data.holder_status?.as_of);
      const officialLive = data.holder_status?.status === 'live_official_weekly';
      const basicLive = data.basic_status?.status === 'live_official';
      const tpexLive = data.tpex_status?.status === 'live_official';
      $('pageStatus').classList.toggle('warning', !officialLive || !basicLive || !tpexLive || staleDays === null || staleDays > 10);
      $('pageStatus').textContent = `受益人：${officialLive ? '官方週資料' : '官方快照備援 / 注意基準日'}${staleDays !== null && staleDays > 10 ? ` · 已距今 ${staleDays} 天，資料降級` : ''}；TWSE 基本資料：${basicLive ? '官方更新' : '快照備援'}；TPEx 清單：${tpexLive ? '官方更新' : '快照備援'}（來源未提供基準日，個別顯示 NA）。本頁取得：${clock(data.generated_at)}。手動更新遵循來源快取：受益人 6 小時、基本資料 30 分鐘。`;
      const previousActive = $('activeSelect').value;
      $('activeSelect').replaceChildren();
      const activeRows = data.rows.filter((row) => row.active).sort((a, b) => a.code.localeCompare(b.code));
      activeRows.forEach((row) => { const option = node('option', `${row.code} ${row.name}${row.holdings_supported ? '' : ' · 尚未串接'}`); option.value = row.code; $('activeSelect').appendChild(option); });
      if (activeRows.some((row) => row.code === previousActive)) $('activeSelect').value = previousActive;
      else if (activeRows.some((row) => row.code === '00981A')) $('activeSelect').value = '00981A';
      if (!state.selected || !data.rows.some((row) => row.code === state.selected)) state.selected = rankedRows()[0]?.code || data.rows[0]?.code;
      renderRanking(); renderDetail(); renderGuides(); renderSources();
      refreshQuotes();
      if (!state.holdingsBusy) refreshHoldings();
    } catch (error) {
      $('pageStatus').classList.add('warning');
      $('pageStatus').textContent = `排行讀取失敗（${error.message}）${state.catalog ? '；保留先前資料，請核對基準日。' : '；暫無資料，不以零代替。'}`;
    } finally { state.catalogBusy = false; $('refreshCatalog').disabled = false; }
  }
  async function refreshQuotes() {
    if (document.hidden || !state.catalog) return;
    if (state.quoteBusy) { state.quoteQueued = true; return; }
    const codes = [...new Set([...rankedRows().map((row) => row.code), state.selected, $('activeSelect').value].filter(Boolean))];
    if (!codes.length) return;
    state.quoteBusy = true;
    try {
      let received = 0;
      for (let index = 0; index < codes.length; index += 20) {
        if (document.hidden) break;
        const batch = codes.slice(index, index + 20);
        const data = await getJSON(`/api/etf/quotes?codes=${encodeURIComponent(batch.join(','))}`);
        if (!Array.isArray(data.quoteResponse?.result)) throw new Error('行情格式不符');
        batch.forEach((code) => state.quotes.delete(code));
        data.quoteResponse.result.forEach((quote) => {
          const code = String(quote.symbol || '').replace(/\.(TW|TWO)$/i, '');
          if (batch.includes(code)) { state.quotes.set(code, quote); received++; }
        });
        state.marketSession = Boolean(data.marketSession);
        state.quoteFetched = data.fetchedAt;
      }
      $('quoteStatus').textContent = `本次取得 ${received} / ${codes.length} 檔行情 · 取得時間 ${clock(state.quoteFetched)}。每 60 秒重讀；資料時間與即時性依來源，非保證即時報價。`;
      renderRanking(); renderDetail();
    } catch (error) {
      $('quoteStatus').textContent = `行情更新失敗（${error.message}）；保留上次已取得行情，請核對各筆時間。`;
    } finally {
      state.quoteBusy = false;
      if (state.quoteQueued) { state.quoteQueued = false; refreshQuotes(); }
    }
  }
  function holdingsTable(holdings, comparison) {
    const changes = new Map((comparison?.rows || []).map((row) => [row.code, row]));
    const current = new Map(holdings.map((row) => [row.code, row]));
    const allCodes = [...new Set([...current.keys(), ...changes.keys()])].sort((a, b) => (current.get(b)?.weight_pct || 0) - (current.get(a)?.weight_pct || 0));
    const table = node('table', null, 'holdings-table');
    const caption = node('caption', '完整已揭露現貨部位與可比較快照的異動', 'visually-hidden');
    const header = node('tr');
    ['證券 / 成分', '持有權重', '前期權重', '權重變動（百分點）', '目前數量', '數量變動', '每基金單位數量變動（%）', '揭露狀態'].forEach((label, index) => header.appendChild(node('th', label, index > 0 && index < 7 ? 'numeric' : '')));
    header.querySelectorAll('th').forEach((th) => th.scope = 'col');
    const body = node('tbody');
    allCodes.forEach((code) => {
      const holding = current.get(code);
      const change = changes.get(code);
      const label = append(node('td', `${code} ${holding?.name || change?.name || ''}`), node('small', holding?.asset_type === 'bond' ? '債券 · 面額' : holding?.quantity_unit || change?.quantity_unit || '數量單位 NA', 'subtext'));
      append(body, append(node('tr'), label, node('td', holding ? pct(holding.weight_pct) : change?.change === '不再揭露' ? '0.00%（未列）' : 'NA', 'numeric'), node('td', pct(change?.previous_weight_pct), 'numeric'), node('td', signed(change?.weight_change_pp), `numeric ${changeClass(change?.weight_change_pp)}`), node('td', holding ? count(holding.shares) : 'NA（未列）', 'numeric'), node('td', signed(change?.shares_change), `numeric ${changeClass(change?.shares_change)}`), node('td', signed(change?.quantity_per_unit_change_pct, '%'), `numeric ${changeClass(change?.quantity_per_unit_change_pct)}`), node('td', change?.change || '前期資料不足')));
    });
    append(table, caption, append(node('thead'), header), body);
    const wrapper = node('div', null, 'table-scroll panel');
    wrapper.tabIndex = 0;
    wrapper.setAttribute('aria-label', '完整持股表，可左右捲動');
    return append(wrapper, note('← 左右滑動，查看全部持股欄位 →', 'table-hint'), table);
  }
  function renderHoldings(data) {
    const panel = $('holdingsPanel');
    panel.replaceChildren();
    if (!Array.isArray(data.holdings) || !data.holdings.length) {
      append(panel, note(data.note || '未取得有效官方持股，不推測補值。', 'holdings-empty panel'));
      if (data.source) append(panel, external('前往官方商品資料 ↗', data.source));
      return;
    }
    const comparison = data.comparison || {};
    const metrics = node('div', null, 'holdings-metrics');
    append(metrics, metric('基金每單位淨值（元）', fmt(data.nav)), metric('基金淨資產（億元）', finite(data.aum_twd) ? fmt(data.aum_twd / 1e8) : 'NA'), metric('前十大已揭露權重', pct(data.top10_weight_pct)), metric('完整已揭露現貨部位', `${data.holdings.length} 項`));
    append(panel, metrics);
    append(panel, note(`持股基準日：${datumDate(data.as_of)}｜PCF 適用日：${datumDate(data.effective_date)}｜公告日：NA（來源未提供）｜取得時間：${clock(data.fetched_at)}。`), external('投信官方持股原始資料 ↗', data.source), note(`揭露範圍：${data.scope || 'NA'}。受益權單位數：${count(data.units)}。`));
    const comparisonBox = node('div', null, 'comparison-note');
    append(comparisonBox, node('strong', '權重異動占比 · 不等於實際買賣比例'));
    if (comparison.status === 'comparable') {
      append(comparisonBox, note(`比較基準：${datumDate(comparison.previous_as_of)} → ${datumDate(comparison.as_of)}。增加權重占異動 ${pct(comparison.increases_share_pct)}；減少權重占異動 ${pct(comparison.decreases_share_pct)}。增加合計 ${fmt(comparison.increases_pp)} 百分點；減少絕對值合計 ${fmt(comparison.decreases_pp)} 百分點。`));
      append(comparisonBox, note('分母：本表證券權重增加百分點合計＋減少百分點絕對值合計。實際買入／賣出比例：NA（未揭露逐筆成交）。'));
    }
    append(comparisonBox, note(comparison.note || '無可比較前期完整快照。'), note('每基金單位數量變動＝（本期證券數量／本期基金單位數）÷（前期證券數量／前期基金單位數）－1；前期為零或資料不足顯示 NA。數量變動未排除分割、公司行動及其他調整。'), note('「新進揭露／不再揭露」只描述完整快照差異；不等於已確認買入／出清。'));
    append(panel, comparisonBox, node('h3', '完整已揭露現貨部位與異動', 'holdings-title'), holdingsTable(data.holdings, comparison));
    const derivatives = Array.isArray(data.derivatives) ? data.derivatives : [];
    append(panel, node('h3', '期貨等衍生部位 · 與現貨分開', 'holdings-title'));
    if (derivatives.length) {
      const table = node('table');
      const header = node('tr');
      ['代號 / 名稱', '權重', '數量 / 單位'].forEach((label) => { const th = node('th', label); th.scope = 'col'; header.appendChild(th); });
      const body = node('tbody');
      derivatives.forEach((row) => append(body, append(node('tr'), node('td', `${row.code} ${row.name}`), node('td', pct(row.weight_pct), 'numeric'), node('td', `${count(row.shares)} ${row.quantity_unit || ''}`, 'numeric'))));
      append(table, append(node('thead'), header), body);
      append(panel, append(node('div', null, 'table-scroll panel'), table));
    } else append(panel, note('本快照未列衍生部位；不推論未揭露部位為零。'));
  }
  async function refreshHoldings() {
    const code = $('activeSelect').value;
    if (!code || !state.catalog || document.hidden) return;
    const version = ++state.holdingsVersion;
    state.holdingsBusy = true;
    $('refreshHoldings').disabled = true;
    $('holdingsStatus').classList.remove('warning');
    $('holdingsStatus').textContent = `${code} 官方持股讀取中…`;
    $('holdingsPanel').replaceChildren();
    try {
      const data = await getJSON(`/api/etf/holdings?code=${encodeURIComponent(code)}`);
      if (version !== state.holdingsVersion || code !== $('activeSelect').value) return;
      state.holdingsLoadedAt = Date.now();
      const unavailable = ['not_connected', 'unavailable'].includes(data.status);
      const old = data.freshness === 'stale' || data.status === 'baseline_official';
      $('holdingsStatus').classList.toggle('warning', unavailable || old);
      $('holdingsStatus').textContent = unavailable ? `${code} · ${data.status === 'not_connected' ? '此投信尚未串接' : '官方資料讀取失敗'} · ${data.note || '沒有有效資料'}` : `${code} · ${data.status === 'baseline_official' ? '官方快照備援，最新讀取失敗' : '投信官方快照'} · 基準日 ${datumDate(data.as_of)}${data.freshness === 'stale' ? ` · 距今 ${data.age_days ?? 'NA'} 天，資料已降級` : ''}${data.note ? ` · ${data.note}` : ''}。每 30 分鐘重讀，非盤中交易流。`;
      renderHoldings(data);
    } catch (error) {
      if (version === state.holdingsVersion) {
        $('holdingsStatus').classList.add('warning');
        $('holdingsStatus').textContent = `${code} 持股讀取失敗（${error.message}）；不使用其他基金成分代填。`;
      }
    } finally {
      if (version === state.holdingsVersion) { state.holdingsBusy = false; $('refreshHoldings').disabled = false; }
    }
  }
  const tabs = [...document.querySelectorAll('[data-market]')];
  function selectTab(tab) {
    state.market = tab.dataset.market;
    tabs.forEach((button) => { button.setAttribute('aria-selected', String(button === tab)); button.tabIndex = button === tab ? 0 : -1; });
    $('rankingPanel').setAttribute('aria-labelledby', tab.id);
    renderRanking();
    refreshQuotes();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', (event) => {
      let next = null;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== null) { event.preventDefault(); tabs[next].focus(); selectTab(tabs[next]); }
    });
  });
  $('refreshCatalog').addEventListener('click', refreshCatalog);
  $('refreshHoldings').addEventListener('click', refreshHoldings);
  $('activeSelect').addEventListener('change', () => { chooseFund($('activeSelect').value); refreshHoldings(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    refreshQuotes();
    if (!state.catalogLoadedAt || Date.now() - state.catalogLoadedAt >= 1800000) refreshCatalog();
    else if (!state.holdingsBusy && (!state.holdingsLoadedAt || Date.now() - state.holdingsLoadedAt >= 1800000)) refreshHoldings();
  });
  setInterval(() => { if (!document.hidden) refreshQuotes(); }, 60000);
  setInterval(() => { if (!document.hidden) refreshCatalog(); }, 1800000);
  setInterval(() => { if (!document.hidden && !state.holdingsBusy) refreshHoldings(); }, 1800000);
  refreshCatalog();
})();
