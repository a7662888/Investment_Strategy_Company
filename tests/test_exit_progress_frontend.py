"""Execute the actual UI functions in Node, with synthetic private state."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable")
def test_controls_escape_content_and_require_confirmed_current_stage():
    source = Path(__file__).resolve().parents[1] / "web" / "app.js"
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function capture(name) {const start=source.search(new RegExp('^(?:async )?function '+name+'\\(', 'm')); assert.ok(start>=0); const tail=source.slice(start);const end=/\r?\n\}\r?(?:\n|$)/.exec(tail);return tail.slice(0,end.index+end[0].length);}
const context=vm.createContext({positionSyncToken:()=> 'synthetic',asArray:v=>Array.isArray(v)?v:[],escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')});
vm.runInContext(capture('renderExitProgressControls'),context);
const render=(symbol,timing)=> context.renderExitProgressControls(symbol,timing);
assert.equal(render('2356.TW',{executable:false,plan:[]}), '');
const p={plan_id:'a'.repeat(64),version:2,original_shares:1000,completed_fraction:.2,remaining_fraction:.3,status:'active',completed_stages:[{stage:1,completed_at:'2026-10-27'}],expires_at:'2026-11-03'};
let html=render('<img src=x onerror=1>', {executable:true,exit_progress:p,plan:[{stage:2,confirmed_break:false}]});
assert.ok(!html.includes('<img'));
assert.ok(html.includes('第 2 批'));
assert.match(html, /data-exit-action="complete"[^>]*disabled/);
html=render('2356.TW',{executable:true,exit_progress:p,plan:[{stage:2,confirmed_break:true}]});
assert.doesNotMatch(html,/data-exit-action="complete"[^>]*disabled/);
html=render('2356.TW',{executable:false,exit_progress:{...p,status:'completion_pending'},plan:[],completion_report:{stage:2,plan_id:p.plan_id,requires_server_verification:true}});
assert.doesNotMatch(html,/data-exit-action="complete"[^>]*disabled/);
assert.match(html,/data-exit-stage="2"/);
html=render('2356.TW',{executable:false,exit_progress:{...p,status:'completed'},plan:[],fresh_plan_available:false});
assert.ok(!html.includes('data-exit-action="complete"'));
assert.ok(!html.includes('data-exit-action="start"'));
'''
    result = subprocess.run([shutil.which("node"), "-e", program, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable")
def test_history_keeps_completion_control_after_final_holding_disappears():
    source = Path(__file__).resolve().parents[1] / "web" / "app.js"
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function capture(name){const start=source.search(new RegExp('^(?:async )?function '+name+'\\(', 'm'));const tail=source.slice(start);const end=/\r?\n\}\r?(?:\n|$)/.exec(tail);return tail.slice(0,end.index+end[0].length);}
const panel={textContent:'',innerHTML:'',onclick:null};
const doc={version:2,plans:{'2356.TW':{symbol:'2356.TW',plan_id:'a'.repeat(64),status:'active',original_shares:1000,completed_stages:[],stages:[{stage:1,fraction:1}],expires_at:'2026-11-03'}},completion_reports:{'2356.TW':{stage:1,requires_server_verification:true}}};
const context=vm.createContext({$:()=>panel,holdingsRenderVersion:1,positionSyncToken:()=> 'synthetic',asArray:v=>Array.isArray(v)?v:[],escapeHtml:v=>String(v),handleExitProgressClick:()=>{},fetch:async()=>({status:200}),readJson:async()=>doc});
vm.runInContext(capture('renderExitProgressControls')+'\n'+capture('loadExitProgressHistory'),context);
(async()=>{await context.loadExitProgressHistory(1);assert.match(panel.innerHTML,/data-exit-action="complete"/);assert.doesNotMatch(panel.innerHTML,/data-exit-action="complete"[^>]*disabled/);assert.equal(typeof panel.onclick,'function');})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([shutil.which("node"), "-e", program, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
