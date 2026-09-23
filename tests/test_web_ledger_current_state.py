from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"
INDEX_HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_current_state_arrival_rerenders_an_already_loaded_ledger():
    """The two endpoints load concurrently; either completion order must be safe."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function loadDailyValueState()")
    end = source.index("\n}\n", start) + 3
    function_body = source[start:end]

    state_assignment = function_body.index("dailyValueState = data;")
    ledger_guard = function_body.index("if (ledgerSignals.length)")
    ledger_render = function_body.index("renderLedger(", ledger_guard)

    assert state_assignment < ledger_guard < ledger_render
    assert '.ledger-filter.active' in function_body


def test_intraday_flash_is_hidden_when_not_current_and_open():
    """A saved provisional artifact must not masquerade as a live flash."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function loadIntradayFlash(")
    end = source.index("\n}\n", start) + 3
    function_body = source[start:end]

    assert "marketOpenNow = false" in function_body
    assert "!marketOpenNow" in function_body
    assert "data.date !== taipeiDateString()" in function_body
    assert 'section.style.display = "none"' in function_body


def test_ledger_filters_use_current_state_and_real_holdings():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'data-filter="holdings"' in html
    assert 'data-filter="hold"' not in html
    assert 'data-filter="value-engine"' not in html
    assert "function currentDecisionKind(signal)" in source
    assert "cur.quality_pass === false" in source
    assert "/排除|賣出檢查/.test(decision)" in source
    assert 'filterType === "holdings"' in source
    assert "_heldSet.has" in source


def test_card_leads_with_current_decision_before_historical_price_context():
    source = APP_JS.read_text(encoding="utf-8")
    card_start = source.index('<div class="ledger-card">')
    card_end = source.index('  }).join("");', card_start)
    card = source[card_start:card_end]

    assert card.index("${presentDecision(") < card.index("${freshnessBadge(")
    assert "凍結區間已過時" in source
    assert "此建議已過時" not in source


def test_cloud_sync_reports_load_save_and_conflict_states_clearly():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "已從雲端載入" in source
    assert "已更新雲端" in source
    assert "雲端已有較新版本，已載入最新資料；請確認後再儲存" in source
    assert "不會靜默覆蓋較新的雲端資料" in html
