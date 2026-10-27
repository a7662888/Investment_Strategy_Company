"""Valuation multiples and share-price levels are independent measures."""
import shutil
import subprocess
from pathlib import Path

import pytest
from company.model.value_daily import _daily_item
from test_decision_reliability_api import evaluation


def test_low_valuation_and_high_price_are_preserved_without_buy_promotion():
    row = evaluation()
    row.update(valuation_pct=10.9, price_pct_252=95.2, pe=15.13,
               valuation_basis="PER(EMS)", distance_from_high_252=-0.037879)
    result = _daily_item(row, True)
    assert result["valuation_pct"] == 10.9
    assert result["price_pct_252"] == 95.2
    assert result["pe"] == 15.13
    assert result["valuation_zone"] == "估值歷史低位（≤P20）"
    assert result["decision"] == "基本面待確認，暫緩新增"


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable")
def test_rendered_valuation_names_the_multiple_and_escapes_its_basis():
    source = Path(__file__).resolve().parents[1] / "web" / "app.js"
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const text=fs.readFileSync(process.argv[1],'utf8');
const start=text.indexOf('function valuationPositionText('),tail=text.slice(start),end=/\r?\n\}\r?(?:\n|$)/.exec(tail);
const context=vm.createContext({escapeHtml:v=>String(v).replaceAll('<','&lt;').replaceAll('>','&gt;')});
vm.runInContext(tail.slice(0,end.index+end[0].length),context);
assert.equal(context.valuationPositionText({valuation_pct:10.9,valuation_basis:'PER(EMS)',pe:15.13}),'PER(EMS) 約 P10.9（15.13 倍）');
assert.equal(context.valuationPositionText({valuation_pct:null}),'—');
assert.ok(!context.valuationPositionText({valuation_pct:10.9,valuation_basis:'<img>',pe:15.13}).includes('<img>'));
'''
    result = subprocess.run([shutil.which("node"), "-e", program, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
