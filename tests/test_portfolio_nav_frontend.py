"""以合成資料執行實際淨值 UI，驗證缺值、授權競態與圖表門檻。"""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable")
def test_nav_rendering_and_private_response_races():
    source = Path(__file__).resolve().parents[1] / "web" / "app.js"
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function capture(name) {
 const start=source.search(new RegExp('^(?:async )?function '+name+'\\(', 'm'));
 assert.ok(start>=0); const tail=source.slice(start), end=/\r?\n\}\r?(?:\n|$)/.exec(tail);
 return tail.slice(0,end.index+end[0].length);
}
const panel={textContent:'',innerHTML:''}, requests=[];
let token='synthetic-pairing-token';
const c=vm.createContext({
 $:()=>panel,positionSyncToken:()=>token,asArray:v=>Array.isArray(v)?v:[],
 requireOwnerReconnect:()=>{token='';c.clearPortfolioNav('重新配對');},
 readJson:async r=>{if(!r.ok)throw Error('private detail must not render');return r.json();},
 fetch:(url,options)=>{assert.equal(url,'/api/portfolio-nav');assert.equal(options.cache,'no-store');assert.equal(options.headers.Authorization,`Bearer ${token}`);return new Promise(resolve=>requests.push(resolve));}
});
vm.runInContext(`let portfolioNavGeneration=0;${['escapeHtml','portfolioNavMarkup','clearPortfolioNav','loadPortfolioNav'].map(capture).join('\n')}`,c);
const rows=Array.from({length:25},(_,i)=>({date:`2026-10-${String(i+1).padStart(2,'0')}`,return_start:'2026-10-01',return_status:i?'ok':'baseline',market_value:10000+i,total_cost:9000,cumulative_return:i*.001,benchmark_return:i*.002,cashflow_benchmark_return:i*.003,cashflow_benchmark_value:20000+i}));
const doc={coverage_start:'2026-10-01',last_success_at:'2026-11-01T08:40:00Z',scope:'券商持股部位（不含券商現金及手動持股）',rows,benchmark:{total_return_supported:true,reason:'合成含息序列',cashflow_basis:'合成資料：配息再投入、不借款'}};
const stat=(html,label)=>{
 const entry=[...html.matchAll(/<dt>(.*?)<\/dt><dd>(.*?)<\/dd>/g)].find(match=>match[1]===label);
 assert.ok(entry,`missing stat: ${label}`);return entry[2];
};
for (const days of [19,20]) {
 const boundary=c.portfolioNavMarkup({...doc,rows:rows.slice(0,days)});
 for (const [label,value] of [['我的持股累積報酬','+1.90%'],['0050 同期含息報酬','+3.80%'],['同金流買 0050 投入資本損益率（非時間加權）','+5.70%']]) {
  assert.equal(stat(boundary,label),days<20?'累積中':value);
 }
 assert.equal(stat(boundary,'紀錄期間'),`2026-10-01 ～ 2026-10-${days}`);
 assert.equal(stat(boundary,'本段報酬計算期間'),`2026-10-01 ～ 2026-10-${days}`);
 assert.equal(stat(boundary,'持股市值'),days<20?'10,018 元':'10,019 元');
 assert.equal(stat(boundary,'持股成本'),'9,000 元');
 assert.equal(stat(boundary,'同金流買 0050 模擬市值'),days<20?'20,018 元':'20,019 元');
 if (days<20) {
  assert.match(boundary,/累積中，目前 19 天/);assert.doesNotMatch(boundary,/<svg|[+-]\d+\.\d+%/);
 } else {
  assert.equal((boundary.match(/<path /g)||[]).length,3);assert.doesNotMatch(boundary,/累積中/);
 }
}
let html;
html=c.portfolioNavMarkup(doc);assert.equal((html.match(/<path /g)||[]).length,3);
assert.match(html,/\+2\.40%/);assert.match(html,/10,024 元/);
assert.match(html,/20,024 元/);assert.match(html,/投入資本損益率（非時間加權）/);
assert.match(html,/模擬假設：合成資料：配息再投入、不借款/);assert.match(html,/資料範圍：券商持股部位/);
const reset=rows.map((row,i)=>i<12?row:{...row,return_status:i===12?'reset_base':'ok',return_start:'2026-10-13'});
html=c.portfolioNavMarkup({...doc,rows:reset,last_attempt:{ok:false}});
assert.match(html,/2026-10-13 ～ 2026-10-25/);assert.match(html,/重新起算 1 次/);
assert.match(html,/最近一次更新未成功/);assert.equal((html.match(/<path d="[^"]*M[^"]*M/g)||[]).length,3);
html=c.portfolioNavMarkup({...doc,benchmark:{total_return_supported:false,reason:'<img src=x onerror=1>'}});
assert.equal((html.match(/<path /g)||[]).length,1);assert.ok(!html.includes('<img'));
const missing=rows.map((row,i)=>({...row,cumulative_return:i===12?null:row.cumulative_return,benchmark_return:null,cashflow_benchmark_return:Infinity}));
html=c.portfolioNavMarkup({...doc,rows:missing});
assert.doesNotMatch(html,/NaN|Infinity/);assert.match(html,/資料不足/);
assert.match(html,/<path d="[^"]*M[^"]*M/); // 缺失日保留斷線，不能跨缺值插補。
const allMissing=rows.map(row=>({...row,cumulative_return:null,benchmark_return:null,cashflow_benchmark_return:null}));
assert.doesNotMatch(c.portfolioNavMarkup({...doc,rows:allMissing}),/<svg/);
const respond=(index,data,status=200)=>requests[index]({status,ok:status===200,json:async()=>data});
(async()=>{
 token='';await c.loadPortfolioNav();assert.equal(requests.length,0);assert.match(panel.textContent,/配對/);
 token='synthetic-pairing-token';let old=c.loadPortfolioNav();token='replacement';respond(0,doc);await old;assert.equal(panel.innerHTML,'');
 let first=c.loadPortfolioNav(),latest=c.loadPortfolioNav();respond(2,{...doc,coverage_start:'LATEST'});await latest;respond(1,{...doc,coverage_start:'OLD'});await first;
 assert.match(panel.innerHTML,/LATEST/);assert.doesNotMatch(panel.innerHTML,/OLD/);
 const expired=c.loadPortfolioNav();respond(3,{},401);await expired;assert.match(panel.textContent,/重新配對/);
 token='new';const failed=c.loadPortfolioNav();respond(4,{},500);await failed;assert.doesNotMatch(panel.textContent,/private detail/);assert.match(panel.textContent,/失敗/);
})().catch(error=>{console.error(error);process.exit(1)});
'''
    result = subprocess.run([shutil.which("node"), "-e", program, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
