"""Wallet authentication: pure-Python Ed25519 (RFC 8032) + base58.

verify() implements RFC 8032 section 5.1.7 verification with affine point
arithmetic — slow by cryptographic standards (~10ms) but dependency-free
and exact. sign() exists so tests and future tooling can produce real
signatures; the server itself only verifies.

Challenge flow: the server issues a nonce with a canonical message
'haunt-auth:{nonce}:{wallet}'; the wallet signs it; verification is
single-use and time-boxed (config.AUTH_TTL_SECONDS). This is the Seed Vault
signMessage binding the client roadmap requires before real funds flow.
"""
import base64
import binascii
import hashlib
import time
import uuid

import config

# ---- Ed25519 field / curve constants (RFC 8032) ---------------------------
_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    return x


_BY = (4 * pow(5, _Q - 2, _Q)) % _Q
_BX = _xrecover(_BY)
if _BX & 1:
    _BX = _Q - _BX
_B = (_BX, _BY)
_IDENTITY = (0, 1)


def _point_add(p, q):
    x1, y1 = p
    x2, y2 = q
    t = _D * x1 * x2 * y1 * y2 % _Q
    x3 = (x1 * y2 + x2 * y1) * pow(1 + t, _Q - 2, _Q) % _Q
    y3 = (y1 * y2 + x1 * x2) * pow(1 - t, _Q - 2, _Q) % _Q
    return (x3, y3)


def _point_mul(p, e):
    result = _IDENTITY
    addend = p
    while e > 0:
        if e & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        e >>= 1
    return result


def _decode_point(data):
    if len(data) != 32:
        raise ValueError('point must be 32 bytes')
    y = int.from_bytes(data, 'little') & ((1 << 255) - 1)
    sign = (data[31] >> 7) & 1
    if y >= _Q:
        raise ValueError('y out of range')
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _Q != 0:
        raise ValueError('point not on curve')
    return (x, y)


def _encode_point(p):
    x, y = p
    data = bytearray(y.to_bytes(32, 'little'))
    if x & 1:
        data[31] |= 0x80
    return bytes(data)


def _sha512(data):
    return hashlib.sha512(data).digest()


def verify(public_key, message, signature):
    """RFC 8032 verification: True iff `signature` signs `message`."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        a_point = _decode_point(public_key)
        r_point = _decode_point(signature[:32])
    except Exception:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= _L:
        return False
    h = int.from_bytes(_sha512(signature[:32] + public_key + message),
                       'little') % _L
    return _point_mul(_B, s) == _point_add(r_point, _point_mul(a_point, h))


def _clamp(h):
    a = int.from_bytes(h, 'little')
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a


def pubkey_from_seed(seed):
    if len(seed) != 32:
        raise ValueError('seed must be 32 bytes')
    digest = _sha512(seed)
    return _encode_point(_point_mul(_B, _clamp(digest[:32])))


def sign(seed, message):
    """RFC 8032 signing (used by tests/tooling; the server only verifies)."""
    if len(seed) != 32:
        raise ValueError('seed must be 32 bytes')
    digest = _sha512(seed)
    a = _clamp(digest[:32])
    prefix = digest[32:]
    a_enc = _encode_point(_point_mul(_B, a))
    r = int.from_bytes(_sha512(prefix + message), 'little') % _L
    r_enc = _encode_point(_point_mul(_B, r))
    k = int.from_bytes(_sha512(r_enc + a_enc + message), 'little') % _L
    s = (r + k * a) % _L
    return r_enc + s.to_bytes(32, 'little')


# ---- base58 ----------------------------------------------------------------
_B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58encode(data):
    if not data:
        return ''
    zeros = 0
    for byte in data:
        if byte == 0:
            zeros += 1
        else:
            break
    n = int.from_bytes(data, 'big')
    chars = []
    while n > 0:
        n, rem = divmod(n, 58)
        chars.append(_B58_ALPHABET[rem])
    return '1' * zeros + ''.join(reversed(chars))


def b58decode(text):
    if not isinstance(text, str) or not text:
        raise ValueError('empty base58')
    n = 0
    for ch in text:
        if ch not in _B58_INDEX:
            raise ValueError('invalid base58 character')
        n = n * 58 + _B58_INDEX[ch]
    zeros = len(text) - len(text.lstrip('1'))
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\x00' * zeros + body


# ---- challenge flow ----------------------------------------------------------

def _decode_signature(value):
    """Accept a base58 or base64 signature; return 64 bytes or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = b58decode(value)
        if len(raw) == 64:
            return raw
    except Exception:
        pass
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) == 64:
            return raw
    except (binascii.Error, ValueError):
        pass
    return None


def challenge_message(nonce, wallet):
    return 'haunt-auth:{}:{}'.format(nonce, wallet)


def create_challenge(db, wallet, now=None):
    now = time.time() if now is None else now
    nonce = uuid.uuid4().hex + uuid.uuid4().hex
    with db.transaction():
        db.execute(
            'INSERT INTO auth_challenges (nonce, wallet, created_at, used)'
            ' VALUES (?, ?, ?, 0)', (nonce, wallet, now))
    return {'nonce': nonce, 'message': challenge_message(nonce, wallet)}


def verify_challenge(db, wallet, nonce, signature, now=None):
    """Single-use, TTL-bound signature check. Returns {'ok', 'error'}."""
    now = time.time() if now is None else now
    if not wallet or not nonce:
        return {'ok': False, 'error': 'missing_fields'}
    sig = _decode_signature(signature)
    if sig is None:
        return {'ok': False, 'error': 'bad_signature_encoding'}
    try:
        pubkey = b58decode(wallet)
    except Exception:
        return {'ok': False, 'error': 'bad_wallet'}
    if len(pubkey) != 32:
        return {'ok': False, 'error': 'bad_wallet'}
    with db.transaction():
        row = db.query_one(
            'SELECT wallet, created_at, used FROM auth_challenges'
            ' WHERE nonce = ?', (nonce,))
        if row is None:
            return {'ok': False, 'error': 'unknown_nonce'}
        if row['wallet'] != wallet:
            return {'ok': False, 'error': 'wallet_mismatch'}
        if row['used']:
            return {'ok': False, 'error': 'nonce_already_used'}
        if now - row['created_at'] > config.AUTH_TTL_SECONDS:
            return {'ok': False, 'error': 'nonce_expired'}
        message = challenge_message(nonce, wallet).encode('utf-8')
        if not verify(pubkey, message, sig):
            return {'ok': False, 'error': 'bad_signature'}
        cur = db.execute(
            'UPDATE auth_challenges SET used = 1 WHERE nonce = ? AND used = 0',
            (nonce,))
        if cur.rowcount != 1:
            return {'ok': False, 'error': 'nonce_already_used'}
    return {'ok': True, 'error': None}
