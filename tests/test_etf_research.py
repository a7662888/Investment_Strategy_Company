"""Research data invariants; all tests run without external market requests."""
import copy
import io
import json
from pathlib import Path
from unittest.mock import patch
import urllib.request
import urllib.error
import threading
from http.server import ThreadingHTTPServer
import zipfile

import pytest
import etf_research as e
import app


def baseline(name):
    return json.loads((e.ROOT / 'web' / name).read_text(encoding='utf8'))


def local_catalog():
    with patch.object(e, 'read_public', side_effect=OSError('offline')):
        e._CACHE.clear()
        return e.catalog()


def test_offline_official_baselines_and_market_symbols():
    data = local_catalog()
    assert data['basic_status']['status'] == 'baseline_fallback'
    assert data['tpex_status']['status'] == 'baseline_official'
    rows = {r['code']: r for r in data['rows']}
    assert rows['00679B']['quote_symbol'] == '00679B.TWO'
    assert rows['0050']['quote_symbol'] == '0050.TW'
    assert rows['00987D']['active'] and rows['00987D']['market'] == 'bond'
    assert rows['00981A']['performance']['verification'] == 'reported_user_chart'
    for market in ['tw', 'global', 'bond']:
        top = sorted([r for r in data['rows'] if r['market'] == market and r.get('popularity_rank') and r['popularity_rank'] <= 20], key=lambda r: r['popularity_rank'])
        assert len(top) == 20
        assert all(r['ranking_status'] == 'official_weekly' for r in top)
        assert len({r['ranking_as_of'] for r in top}) == 1
        assert [r['holders'] for r in top] == sorted([r['holders'] for r in top], reverse=True)


def test_holder_totals_do_not_double_count_classes_or_weeks():
    records = [{'\ufeff資料日期': '20261002', '證券代號': '0050', '持股分級': '17', '人數': '100'}, {'資料日期': '20261002', '證券代號': '0050', '持股分級': '1', '人數': '60'}, {'資料日期': '20260925', '證券代號': '0056', '持股分級': '17', '人數': '200'}]
    result = e.normalize_holders(records)
    assert result['rows'] == [{'code': '0050', 'holders': 100, 'as_of': '2026-10-02'}]


def test_no_official_holder_source_means_no_official_candidates():
    with patch.object(e, 'basic_document', return_value=baseline('etf-basics.json')), patch.object(e, 'tpex_document', return_value=baseline('etf-tpex.json')), patch.object(e, 'holder_document', return_value={'rows': [], 'status': 'unavailable'}):
        data = e.catalog()
    assert all(g['codes'] == [] for g in data['guides'])


def test_fund_flow_does_not_become_executed_trade():
    previous = {'as_of': '2026-10-02', 'scope': 'stock', 'units': 1000, 'holdings': [{'code': '2330', 'name': 'T', 'shares': 100, 'weight_pct': 10}]}
    current = {'as_of': '2026-10-05', 'scope': 'stock', 'units': 2000, 'holdings': [{'code': '2330', 'name': 'T', 'shares': 200, 'weight_pct': 10}]}
    result = e.compare_holdings(previous, current)
    assert result['rows'][0]['shares_change'] == 100
    assert result['rows'][0]['quantity_per_unit_change_pct'] == 0
    assert result['executed_buy_ratio_pct'] is None
    assert result['increases_share_pct'] is None
    assert e.compare_holdings(None, current)['status'] == 'no_previous_snapshot'
    older = {**previous, 'as_of': '2026-09-01'}
    assert e.compare_holdings(older, current)['status'] == 'incomparable'


def test_public_snapshots_preserve_bonds_foreign_codes_and_derivatives():
    data = baseline('etf-holdings.json')
    for code, pair in data.items():
        current, previous = pair['current'], pair['previous']
        assert current['as_of'] <= '2026-10-06'
        assert len({r['code'] for r in current['holdings']}) == len(current['holdings'])
        assert all(r['asset_type'] not in ['futures', 'options'] for r in current['holdings'])
        assert 0 < sum(r['weight_pct'] for r in current['holdings']) <= 102
        assert current['source'].startswith('https://')
        assert previous['as_of'] < current['as_of']
        assert e.compare_holdings(previous, current)['status'] == 'comparable'
    assert data['00987D']['current']['holdings'][0]['quantity_unit'] == '面額'
    assert any(' US' in r['code'] for r in data['00409A']['current']['holdings'])
    assert len(data['00404A']['current']['derivatives']) == 3


def test_quotes_cache_individual_codes_and_request_limits():
    local_catalog()
    calls = []
    def fetch(symbols):
        calls.append(symbols)
        return {'quoteResponse': {'result': [{'symbol': s, 'regularMarketPrice': 1} for s in symbols]}}
    first = e.quotes('0050,0056', fetch)
    second = e.quotes('0050,00878', fetch)
    assert calls == [['0050.TW', '0056.TW'], ['00878.TW']]
    assert len(second['quoteResponse']['result']) == 2
    assert 'fetchedAt' in first and 'marketSession' in first
    for bad in ['2330', 'https://evil.test/', ','.join(['0050'] + [f'00{x}' for x in range(20)])]:
        with pytest.raises(ValueError): e.validate_codes(bad)


def test_yahoo_tpex_mapping_keeps_logical_symbols():
    item = {'symbol': '00679B.TWO', 'regularMarketPrice': 20, 'regularMarketTime': 1, 'source': 'Yahoo 1m intraday'}
    with patch.object(app, 'load_market_snapshots', return_value={}), patch.object(app, 'fetch_twse_mis_quotes', return_value=[]), patch.object(app, 'fetch_official_daily_quotes', return_value=[]), patch.object(app, 'is_tw_market_session', return_value=True), patch.object(app, 'fetch_yahoo_intraday_quotes', return_value=[item]) as yahoo:
        data = app.fetch_quote(['00679B.TW'], yahoo_symbol_map={'00679B.TW': '00679B.TWO'})
    yahoo.assert_called_once_with(['00679B.TWO'])
    row = data['quoteResponse']['result'][0]
    assert row['symbol'] == '00679B.TW' and row['providerSymbol'] == '00679B.TWO'


def test_public_etf_api_and_bad_inputs():
    data = local_catalog()
    server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with patch.object(e, 'catalog', return_value=data):
            response = urllib.request.urlopen(base + '/api/etf/catalog')
            assert json.load(response)['active_count'] >= 40
            assert "default-src 'self'" in response.headers['Content-Security-Policy']
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(base + '/api/etf/holdings?code=../../positions')
            assert exc.value.code == 400
            payload = e.holdings('00982A')
            assert payload['status'] == 'not_connected' and payload['holdings'] == []
    finally:
        server.shutdown(); server.server_close()
