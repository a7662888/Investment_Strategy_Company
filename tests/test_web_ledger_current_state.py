from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"


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
