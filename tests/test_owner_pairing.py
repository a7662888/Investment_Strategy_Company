"""Synthetic authorization only; no broker calls and no real credentials."""
import json
from unittest.mock import patch
import urllib.request
import urllib.error

import pytest
import owner_access as access
from company.model import positions
from test_repair_endpoints import server_url


@pytest.fixture(autouse=True)
def isolated_codes():
    access._CODES.clear()
    access._ATTEMPTS.clear()
    access._GLOBAL_WINDOW = (0, 0)


def test_pair_is_one_use_expires_and_signs_device_access():
    with patch.object(access.time, 'time', return_value=1000):
        code = access.create_pairing('synthetic-owner')['code']
        assert len(code) == 8 and code.isdigit()
        token = access.claim_pairing(code, 'synthetic-owner', 'synthetic-client')['token']
        assert access.valid_device_token(token, 'synthetic-owner')
        assert not access.valid_device_token(token, 'wrong-key')
        with pytest.raises(access.PairingError):
            access.claim_pairing(code, 'synthetic-owner', 'synthetic-client')
        with patch.object(positions, 'expected_sync_token', return_value='synthetic-owner'):
            assert positions.is_authorized('Bearer ' + token)
            assert positions.is_authorized('Bearer synthetic-owner')
            assert not positions.is_authorized('Bearer 無效')
    with patch.object(access.time, 'time', return_value=1000 + access.DEVICE_TTL):
        assert not access.valid_device_token(token, 'synthetic-owner')
    with patch.object(access.time, 'time', return_value=900):
        assert not access.valid_device_token(token, 'synthetic-owner')


def test_pair_expiry_replacement_and_guess_limit():
    with patch.object(access.time, 'time', return_value=1000):
        old = access.create_pairing('synthetic-owner')['code']
        fresh = access.create_pairing('synthetic-owner')['code']
        assert old not in access._CODES
        for _ in range(5):
            with pytest.raises(access.PairingError):
                access.claim_pairing('invalid', 'synthetic-owner', 'same-client')
        with pytest.raises(access.PairingError) as exc:
            access.claim_pairing(fresh, 'synthetic-owner', 'same-client')
        assert exc.value.status == 429
    with patch.object(access.time, 'time', return_value=1301):
        with pytest.raises(access.PairingError):
            access.claim_pairing(fresh, 'synthetic-owner', 'same-client')


def request(base, path, data, auth=None, origin=None):
    headers = {'Content-Type': 'application/json'}
    if auth: headers['Authorization'] = 'Bearer ' + auth
    if origin: headers['Origin'] = origin
    req = urllib.request.Request(base+path, data=json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r), r.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc), exc.headers


def test_http_authorized_create_claim_and_untrusted_origin():
    with patch('company.model.positions.expected_sync_token', return_value='synthetic-owner'), \
         patch('app.positions_sync_enabled', return_value=True), server_url() as base:
        assert request(base, '/api/owner/pairing', {})[0] == 401
        assert request(base, '/api/owner/pairing', {}, 'synthetic-owner', 'https://evil.test')[0] == 403
        status, pair, headers = request(base, '/api/owner/pairing', {}, 'synthetic-owner')
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert request(base, '/api/owner/pairing/claim', [pair['code']])[0] == 400
        status, device, _ = request(base, '/api/owner/pairing/claim', {'code':pair['code']})
        assert status == 200
        assert positions.is_authorized('Bearer ' + device['token'])
        assert request(base, '/api/owner/pairing/claim', {'code':pair['code']})[0] == 400
