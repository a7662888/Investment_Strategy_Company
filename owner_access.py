"""One-use owner pairing codes and expiring device credentials; no account data."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

PAIR_TTL = 300
DEVICE_TTL = 30 * 86400
_LOCK = threading.Lock()
_CODES: dict[str, tuple[float, str]] = {}
_ATTEMPTS: dict[str, tuple[int, int]] = {}
_GLOBAL_WINDOW = (0, 0)


class PairingError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), b'owner-device-v1', hashlib.sha256).digest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def create_pairing(secret: str) -> dict:
    now = time.time()
    with _LOCK:
        for code in list(_CODES):
            if _CODES[code][0] <= now:
                del _CODES[code]
        # A newly requested code invalidates earlier codes from this owner.
        owner = hashlib.sha256(_key(secret)).hexdigest()
        for code in list(_CODES):
            if _CODES[code][1] == owner:
                del _CODES[code]
        if len(_CODES) >= 4:
            raise PairingError('目前配對請求過多，請稍後再試', 429)
        code = ''.join(str(secrets.randbelow(10)) for _ in range(8))
        _CODES[code] = (now + PAIR_TTL, owner)
    return {'code': code, 'expires_in': PAIR_TTL}


def claim_pairing(code: object, secret: str, client: str) -> dict:
    global _GLOBAL_WINDOW
    now = time.time()
    window = int(now // 60)
    # Keep only an HMAC fingerprint of the transport address, never an account ID.
    identity = hmac.new(_key(secret), str(client).encode(), hashlib.sha256).hexdigest()
    with _LOCK:
        for key in list(_ATTEMPTS):
            if _ATTEMPTS[key][0] != window:
                del _ATTEMPTS[key]
        if _GLOBAL_WINDOW[0] != window:
            _GLOBAL_WINDOW = (window, 0)
        count = _ATTEMPTS.get(identity, (window, 0))[1]
        if count >= 5 or _GLOBAL_WINDOW[1] >= 30 or len(_ATTEMPTS) >= 128:
            raise PairingError('嘗試次數過多，請一分鐘後再試', 429)
        _ATTEMPTS[identity] = (window, count + 1)
        _GLOBAL_WINDOW = (window, _GLOBAL_WINDOW[1] + 1)
        value = str(code or '').strip()
        pair = _CODES.get(value)
        owner = hashlib.sha256(_key(secret)).hexdigest()
        if not pair or pair[0] <= now or not hmac.compare_digest(pair[1], owner):
            raise PairingError('連接碼無效、已使用或已過期；請在已連接的裝置重新產生')
        del _CODES[value]  # Consume under lock before issuing any credential.
    payload = _encode(json.dumps({'iat': int(now), 'exp': int(now) + DEVICE_TTL,
                                  'nonce': secrets.token_hex(16)}, separators=(',', ':')).encode())
    signature = _encode(hmac.new(_key(secret), payload.encode(), hashlib.sha256).digest())
    return {'token': 'device1.' + payload + '.' + signature, 'expires_in': DEVICE_TTL}


def valid_device_token(token: str, secret: str) -> bool:
    if not isinstance(token, str) or len(token) > 512:
        return False
    try:
        version, payload, signature = token.split('.')
        if version != 'device1':
            return False
        expected = _encode(hmac.new(_key(secret), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        doc = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        now = time.time()
        issued, expires = doc['iat'], doc['exp']
        return (type(issued) is int and type(expires) is int and issued <= now + 30
                and expires - issued == DEVICE_TTL and now < expires
                and isinstance(doc.get('nonce'), str) and len(doc['nonce']) == 32)
    except (ValueError, KeyError, TypeError, UnicodeError):
        return False
