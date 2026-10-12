#!/usr/bin/env python3
"""HAUNT backend HTTP service (stdlib ThreadingHTTPServer).

Run:  python3 server.py [port]        (default 8790)

One server behind everything HAUNT sells: payment quotes + on-chain
verification + entitlements, the rolling-24h anomaly slot ledger, and
.skr wallet auth challenges. With HAUNT_TREASURY unset the pay routes
answer 503 and nothing can settle — the honest disabled state.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import auth
import config
import payments
import slots
from db import DB

DB_INSTANCE = DB(config.DB_PATH)

# quote errors -> HTTP status (success is 200)
_QUOTE_STATUS = {'payments_not_configured': 503, 'sold_out': 409,
                 'already_holding': 409, 'already_active': 409,
                 'ip_hold_limit': 409, 'wallet_required': 400,
                 'rate_limited': 429, 'price_unavailable': 502}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    # -- plumbing -----------------------------------------------------------
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('content-length') or 0)
        raw = self.rfile.read(length) if length else b''
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    # -- routes ---------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/v1/health':
            return self._send(200, {
                'ok': True,
                'treasury_configured': bool(config.TREASURY),
                'snapshot': config.SNAPSHOT})
        if path == '/v1/slots/status':
            return self._send(200, slots.status(DB_INSTANCE))
        if path.startswith('/v1/entitlements/'):
            wallet = unquote(path[len('/v1/entitlements/'):])
            if not wallet or '/' in wallet:
                return self._send(400, {'error': 'bad_wallet'})
            return self._send(200,
                              payments.list_entitlements(DB_INSTANCE, wallet))
        return self._send(404, {'error': 'not_found'})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        if body is None:
            return self._send(400, {'error': 'bad_json'})

        if path == '/v1/pay/quote':
            # wallet is OPTIONAL for most tiers: the HAUNT web client has
            # no connected wallet and quotes anonymously; the entitlement
            # later binds to the payer bound from the payment transaction
            # itself. The ANOMALY tier requires a wallet — its slot hold
            # must be bound to an identity and to the source IP.
            missing = [f for f in ('tier', 'currency') if not body.get(f)]
            if missing:
                return self._send(400, {'error': 'missing_fields',
                                        'fields': missing})
            result = payments.create_quote(
                DB_INSTANCE, body.get('wallet') or None,
                body['tier'], body['currency'],
                source_ip=self.client_address[0])
            if 'error' in result:
                return self._send(_QUOTE_STATUS.get(result['error'], 400),
                                  result)
            return self._send(200, result)

        if path == '/v1/pay/verify':
            signature = body.get('signature')
            if not signature:
                # Verification is signature-based; a memo-only body cannot
                # settle anything here.
                return self._send(400, {'verified': False,
                                        'error': 'signature_required'})
            result = payments.verify_payment(DB_INSTANCE, signature)
            if result.get('verified'):
                return self._send(200, result)
            code = (503 if result.get('error') == 'payments_not_configured'
                    else 400)
            return self._send(code, result)

        if path == '/v1/auth/challenge':
            if not body.get('wallet'):
                return self._send(400, {'error': 'missing_fields',
                                        'fields': ['wallet']})
            return self._send(200, auth.create_challenge(
                DB_INSTANCE, body['wallet']))

        if path == '/v1/auth/verify':
            missing = [f for f in ('wallet', 'nonce', 'signature')
                       if not body.get(f)]
            if missing:
                return self._send(400, {'error': 'missing_fields',
                                        'fields': missing})
            result = auth.verify_challenge(DB_INSTANCE, body['wallet'],
                                           body['nonce'], body['signature'])
            return self._send(200 if result['ok'] else 401, result)

        if path == '/v1/slots/claim':
            if not body.get('wallet'):
                return self._send(400, {'error': 'missing_fields',
                                        'fields': ['wallet']})
            result = slots.claim(DB_INSTANCE, body['wallet'],
                                 source_ip=self.client_address[0])
            if result.get('ok'):
                return self._send(200, result)
            return self._send(409, result)

        if path == '/v1/slots/release':
            if body.get('hold_id') is None:
                return self._send(400, {'error': 'missing_fields',
                                        'fields': ['hold_id']})
            if slots.release(DB_INSTANCE, body['hold_id']):
                return self._send(200, {'ok': True})
            return self._send(404, {'ok': False,
                                    'error': 'hold_not_found'})

        return self._send(404, {'error': 'not_found'})


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    state = ('treasury configured' if config.TREASURY
             else 'treasury UNSET — payments inert (honest disabled state)')
    print('HAUNT backend on http://127.0.0.1:{} — {}'.format(port, state))
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
