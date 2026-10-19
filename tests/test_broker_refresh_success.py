from unittest.mock import patch
import run_broker_positions as runner
import app
from company.model import daily_jobs


def test_required_ledger_prevents_false_workflow_success():
    with patch.object(runner, '_fetch', return_value={'positions': []}), \
         patch.object(runner.broker, 'record_broker_attempt', return_value={'durable': True}), \
         patch.object(runner, '_save_ledger', return_value={'lots': {'durable':True}, 'realized':{'error_code':'READ_FAILED'}}):
        assert runner.main(['--fetch', '--require-ledger']) == 1
    with patch.object(runner, '_fetch', return_value={'positions': []}), \
         patch.object(runner.broker, 'record_broker_attempt', return_value={'durable': True}), \
         patch.object(runner, '_save_ledger', return_value={'lots': {'durable':True}, 'realized':{'durable':True}}):
        assert runner.main(['--fetch', '--require-ledger']) == 0


def test_existing_broker_read_is_followed_without_a_second_dispatch():
    with patch.object(daily_jobs, 'manual_check', return_value=(True, 'ok')), \
         patch.object(daily_jobs, 'latest_workflow_run', return_value={
             'status':'in_progress', 'created_at':'2026-10-19T08:35:00+00:00'}), \
         patch.object(daily_jobs, 'dispatch_workflow') as dispatch:
        payload, status = app.manual_refresh('broker')
        assert status == 202 and payload['started']
        assert payload['started_at'] == '2026-10-19T08:35:00+00:00'
        dispatch.assert_not_called()
