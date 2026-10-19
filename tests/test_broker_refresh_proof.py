import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch
import urllib.request

import pytest
import app
from test_repair_endpoints import server_url


def test_ledger_api_preserves_cloud_storage_evidence():
    docs = [({'as_of':'2026-10-19T15:53:36Z','lots':{}}, {'durable':True}),
            ({'as_of':'2026-10-19T15:53:36Z','trades':[]}, {'durable':True})]
    with patch('company.model.positions.expected_sync_token', return_value='synthetic'), \
         patch('company.model.positions.is_authorized', return_value=True), \
         patch.object(app, 'load_private_ledger_with_storage', side_effect=docs), \
         patch.object(app, 'decisions_before', return_value={}), server_url() as base:
        doc = json.load(urllib.request.urlopen(base+'/api/broker-ledger'))
        assert doc['storage']['lots']['durable'] is True
        assert doc['storage']['realized']['durable'] is True


@pytest.mark.parametrize('scenario', ['fresh_while_queued','stale_ledger','local_ledger','invalid_since'])
def test_refresh_finishes_from_durable_data_even_when_workflow_status_lags(scenario):
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),scenario=process.argv[2];
function capture(name) {
 const start=source.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));
 const tail=source.slice(start),end=/\r?\n\}\r?(?:\n|$)/.exec(tail);
 assert.ok(start>=0&&end);return tail.slice(0,end.index+end[0].length);
}
const since='2026-10-19T15:53:18Z',at='2026-10-19T15:53:36Z';
const inventory={status:'ok',as_of:at,last_attempt_at:at,storage:{durable:true}};
const ledger={lots_as_of:at,realized_as_of:at,storage:{lots:{durable:true},realized:{durable:true}}};
if(scenario==='stale_ledger')ledger.realized_as_of='2026-10-19T06:00:00Z';
if(scenario==='local_ledger')ledger.storage.realized.durable=false;
const pending={since,requestedAt:scenario==='invalid_since'?'invalid':since,startedAt:Date.now()};
const note={textContent:''},calls=[],document={hidden:false};let reloaded=0;
const c=vm.createContext({Date,Number,Promise,document,manualPending:{broker:pending},lastDailyRefresh:null,
 REFRESH_ITEMS:[{job:'broker',title:'broker'}],positionSyncToken:()=> 'synthetic',
 $:()=>note,persistManualPending(){},refreshRefreshPanel(){},renderRefreshPanel(){},
 requireOwnerReconnect(){throw Error('unexpected auth failure')},
 reloadBrokerViews:async()=>{reloaded++},
 waitWorkflowPoll:async()=>{document.hidden=true},
 fetch:async(path,options)=>{assert.ok(!options.method);calls.push(path);return{ok:true,status:200,json:async()=>
 path==='/api/broker-positions'?inventory:path==='/api/broker-ledger'?ledger:{run:{status:'queued'}}};}
});
vm.runInContext(['brokerRefreshProof','probeBrokerRefresh','trackWorkflow'].map(capture).join('\n'),c);
c.trackWorkflow('broker').then(()=>{
 if(scenario==='fresh_while_queued'){
  assert.equal(c.manualPending.broker,undefined);assert.equal(reloaded,1);
  assert.ok(note.textContent.includes('新庫存已驗證'));
  assert.ok(!calls.some(x=>x.includes('manual-status')),'data evidence must not wait for stale workflow metadata');
 }else{assert.equal(c.manualPending.broker,pending);assert.equal(reloaded,0)}
}).catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([shutil.which('node'), '-e', script,
                             str(Path(app.__file__).parent/'web'/'app.js'), scenario],
                            capture_output=True, text=True, encoding='utf8', timeout=10)
    assert result.returncode == 0, result.stderr
