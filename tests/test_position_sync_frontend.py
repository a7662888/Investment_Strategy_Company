"""Offline Node VM regressions for private portfolio synchronization races."""
from pathlib import Path
import shutil
import subprocess

import pytest


APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"
NODE = shutil.which("node")
SCENARIOS = (
    "cloud_out_of_order",
    "cloud_version_regression",
    "cloud_uninitialized_does_not_upload",
    "cloud_draft_edit",
    "cloud_token_change",
    "cloud_read_before_save",
    "cloud_save_version_regression",
    "cloud_conflict_preserves_draft",
    "broker_out_of_order",
    "broker_token_change",
    "broker_old_failure",
    "broker_failure_preserves_manual",
)


VM_TEST = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const scenario = process.argv[2];

// Execute declarations only: never evaluate application boot or event bindings.
function capture(name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(start >= 0, `missing function ${name}`);
  const tail = source.slice(start);
  const end = /\r?\n\}\r?(?:\n|$)/.exec(tail);
  assert.ok(end, `missing end of ${name}`);
  return tail.slice(0, end.index + end[0].length);
}

function harness() {
  const store = new Map([
    ["investment_strategy_positions_sync_token", "synthetic-test-token"],
    ["investment_strategy_positions", "1111.TW:1@10"],
  ]);
  const elements = Object.fromEntries([
    "homePositionInput", "positionInput", "homePositionCloudStatus",
    "brokerPositionPanel", "brokerPositionAdopt", "myHoldingsPanel",
  ].map(id => [id, {value: "", textContent: "", innerHTML: "", disabled: true, style: {}}]));
  elements.homePositionInput.value = "1111.TW:1@10";
  elements.positionInput.value = "1111.TW:1@10";
  const requests = [];
  const context = vm.createContext({
    console: {warn() {}},
    $: id => elements[id] || null,
    document: {querySelector: () => null},
    localStorage: {
      getItem: key => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: key => store.delete(key),
    },
    asArray: value => Array.isArray(value) ? value : [],
    parsePositionsRaw: () => [{symbol: "1111.TW", shares: 1, cost: 10}],
    updatePositionSaveStatus() {},
    renderMyHoldings() {},
    renderLedger() {},
    syncFailureMessage: () => "synthetic sync failure",
    readJson: async response => {
      const data = await response.json();
      if (!response.ok) throw new Error("synthetic response failure");
      return data;
    },
    fetch: (url, options) => {
      assert.ok(["/api/positions", "/api/broker-positions"].includes(url));
      return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
    },
  });
  const functions = [
    "escapeHtml", "positionSyncToken", "setPositionCloudStatus", "positionsToRaw",
    "applyCloudPositions", "loadCloudPositions", "saveCloudPositions", "loadBrokerInventory",
  ];
  vm.runInContext(`
    const POSITION_STORAGE_KEY = "investment_strategy_positions";
    const POSITION_SYNC_TOKEN_KEY = "investment_strategy_positions_sync_token";
    let positionCloudVersion = 0, positionSyncGeneration = 0, brokerSyncGeneration = 0;
    let holdingsRenderVersion = 0, brokerInventory = null, brokerPositionsEnabled = false;
    let _heldSet = null;
    const ledgerSignals = [];
    ${functions.map(capture).join("\n")}
    globalThis.inspect = () => ({version: positionCloudVersion,
      enabled: brokerPositionsEnabled, broker: brokerInventory});
    globalThis.seedVersion = value => { positionCloudVersion = value; };
  `, context, {timeout: 1000});
  return {context, elements, store, requests};
}

const position = symbol => ({symbol, shares: 1, cost: 10});
const cloud = (version, symbol = "2222.TW", enabled = false) => ({
  version, positions: [position(symbol)], broker_enabled: enabled,
  storage: {durable: true}, updated_at: "2026-10-02T08:30:00Z",
});
const broker = symbol => ({status: "ok", positions: [position(symbol)],
  storage: {durable: true}, as_of: "2026-10-02T08:30:00Z"});
function respond(request, data, status = 200) {
  request.resolve({ok: status >= 200 && status < 300, status, json: async () => data});
}

async function run() {
  const h = harness();
  const {context: c, elements: e, requests: q, store} = h;
  // Most cloud tests isolate the cloud race; broker tests use the real captured loader.
  if (scenario.startsWith("cloud_")) c.loadBrokerInventory = async () => {};
  const originalManual = store.get("investment_strategy_positions");

  if (scenario === "cloud_out_of_order") {
    const old = c.loadCloudPositions(), latest = c.loadCloudPositions();
    respond(q[1], cloud(2, "2222.TW", true));
    assert.equal(await latest, true);
    respond(q[0], cloud(1, "1111.TW", false));
    assert.equal(await old, false);
    assert.equal(c.inspect().version, 2);
    assert.equal(c.inspect().enabled, true);
    assert.equal(e.homePositionInput.value, "2222.TW:1@10");
  } else if (scenario === "cloud_version_regression") {
    c.seedVersion(5);
    for (const version of [4, -1, 1.5, "invalid", Number.MAX_SAFE_INTEGER + 1]) {
      const pending = c.loadCloudPositions();
      respond(q.at(-1), cloud(version));
      assert.equal(await pending, false);
      assert.equal(c.inspect().version, 5);
      assert.equal(store.get("investment_strategy_positions"), originalManual);
    }
  } else if (scenario === "cloud_uninitialized_does_not_upload") {
    const pending = c.loadCloudPositions();
    respond(q[0], cloud(0));
    await pending;
    assert.equal(q.length, 1, "first synchronization must not upload local holdings");
    assert.equal(c.inspect().version, 0);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
    assert.equal(e.homePositionInput.value, originalManual);
  } else if (scenario === "cloud_draft_edit") {
    const pending = c.loadCloudPositions();
    e.homePositionInput.value = "3333.TW:2@20";
    respond(q[0], cloud(2));
    assert.equal(await pending, false);
    assert.equal(e.homePositionInput.value, "3333.TW:2@20");
    assert.equal(c.inspect().version, 0);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
  } else if (scenario === "cloud_token_change") {
    const pending = c.loadCloudPositions();
    store.set("investment_strategy_positions_sync_token", "different-test-token");
    respond(q[0], cloud(2));
    assert.equal(await pending, false);
    assert.equal(c.inspect().version, 0);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
  } else if (scenario === "cloud_read_before_save") {
    c.seedVersion(1);
    const old = c.loadCloudPositions();
    const saved = c.saveCloudPositions([position("3333.TW")]);
    assert.equal(JSON.parse(q[1].options.body).version, 1);
    respond(q[1], cloud(2, "3333.TW"));
    assert.equal(await saved, true);
    const savedStatus = e.homePositionCloudStatus.textContent;
    respond(q[0], cloud(1));
    assert.equal(await old, false);
    assert.equal(c.inspect().version, 2);
    assert.equal(e.homePositionCloudStatus.textContent, savedStatus);
  } else if (scenario === "cloud_save_version_regression") {
    c.seedVersion(5);
    for (const version of [4, 5, 1.5, "invalid"]) {
      const pending = c.saveCloudPositions([position("3333.TW")]);
      respond(q.at(-1), cloud(version));
      assert.equal(await pending, false);
      assert.equal(c.inspect().version, 5);
    }
  } else if (scenario === "cloud_conflict_preserves_draft") {
    c.seedVersion(1);
    e.homePositionInput.value = "3333.TW:2@20";
    const pending = c.saveCloudPositions([position("3333.TW")]);
    respond(q[0], {error: "synthetic version conflict"}, 409);
    assert.equal(await pending, false);
    assert.equal(q.length, 1, "a conflict must not trigger a draft-overwriting GET");
    assert.equal(e.homePositionInput.value, "3333.TW:2@20");
    assert.equal(c.inspect().version, 1);
  } else if (scenario === "broker_out_of_order" || scenario === "broker_old_failure") {
    c.seedVersion(7);
    const old = c.loadBrokerInventory(), latest = c.loadBrokerInventory();
    respond(q[1], broker("2222.TW"));
    await latest;
    const latestHtml = e.brokerPositionPanel.innerHTML;
    if (scenario === "broker_old_failure") q[0].reject(new Error("synthetic old failure"));
    else respond(q[0], broker("1111.TW"));
    await old;
    assert.equal(c.inspect().broker.positions[0].symbol, "2222.TW");
    assert.equal(e.brokerPositionPanel.innerHTML, latestHtml);
    assert.equal(e.brokerPositionAdopt.disabled, false);
    assert.equal(c.inspect().version, 7, "broker loading must not change manual cloud metadata");
    assert.equal(c.inspect().enabled, false);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
    assert.equal(e.homePositionInput.value, originalManual);
  } else if (scenario === "broker_token_change") {
    const pending = c.loadBrokerInventory();
    store.set("investment_strategy_positions_sync_token", "different-test-token");
    respond(q[0], broker("2222.TW"));
    await pending;
    assert.equal(c.inspect().broker, null);
    assert.equal(e.brokerPositionAdopt.disabled, true);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
  } else if (scenario === "broker_failure_preserves_manual") {
    const loaded = c.loadBrokerInventory();
    respond(q[0], broker("2222.TW"));
    await loaded;
    const failed = c.loadBrokerInventory();
    q[1].reject(new Error("synthetic broker failure"));
    await failed;
    assert.equal(c.inspect().broker, null);
    assert.equal(e.brokerPositionAdopt.disabled, true);
    assert.equal(store.get("investment_strategy_positions"), originalManual);
  } else {
    throw new Error(`unknown scenario ${scenario}`);
  }
}

run().then(() => console.log(`PASS ${scenario}`)).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_position_sync_frontend_races(scenario):
    assert NODE is not None, "Node.js is required for offline frontend race tests"
    result = subprocess.run(
        [NODE, "-e", VM_TEST, str(APP_JS), scenario],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == f"PASS {scenario}", "VM scenario did not finish"
