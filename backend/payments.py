"""Payment quotes + on-chain verification + entitlement grants.

Flow: create_quote() prices a tier in SOL or ORE from the live Jupiter rate
(no rate, no quote) and stores it with a unique memo 'HAUNT:<request_id>'.
For the anomaly tier it first claims a rolling-24h slot hold; the quote's
expires_at IS that hold's expiry (2 minutes — see config).

Anomaly quotes are NOT anonymous: they require a wallet (holds are bound
to an identity and to the source IP — one live hold per wallet, one live
hold per IP, 20 quote attempts per hour per IP) so reservations cannot be
rotated for free. Other tiers stay anonymous-optional.

verify_payment(signature) fetches the transaction once and checks, in
order: treasury configured -> signature not already consumed -> tx exists
and succeeded -> some memo in the tx matches a quote (ALL memos are
considered; a pending, unexpired quote wins over a stale one sharing the
tx) -> the payment is not stale -> the treasury's balance delta is
>= 99% of the quoted amount (exact integer math: actual * 100 >=
expected * 99) -> a payer can be BOUND from the transaction's transfer
instructions (see solana.instruction_payer_*). Only then, in ONE
transaction, it consumes the signature, grants the entitlement — always
to the BOUND payer, never to a client-claimed wallet — and settles the
anomaly slot.

Staleness: non-anomaly quotes die at expires_at. For anomaly quotes
expires_at is the HOLD deadline, not a payment deadline: a payment made
while the buyer was completing checkout still settles if the tx's
blockTime (or, lacking one, the verify time) falls within
QUOTE_TTL_MINUTES of the quote's creation. The slot, however, is
re-decided at verify time: if the hold expired and capacity is gone, the
grant is recorded with note 'refund_due' and the response carries
pass_state 'refund_due' — the payment is never silently swallowed; if
capacity exists, a fresh slot is claimed for the payer inside the same
transaction (pass_state 'active').

Quotes may be anonymous (wallet=None) outside the anomaly tier: the HAUNT
web client has no connected wallet, so the payer is whoever's wallet
executes the Solana Pay link. The chain is the only trustworthy source of
who paid, which is why the entitlement binds to the bound payer even when
a quote carries a (possibly wrong) claimed wallet.
"""
import sqlite3
import time
import uuid

import config
import pricing
import slots
import solana

TIER_PRIORITY = {'anomaly': 0, 'lifetime': 1, 'monthly': 2, 'day': 3}


def _anomaly_rate_gate(db, source_ip, now):
    """Per-IP anomaly-quote attempt bound: at most
    config.ANOMALY_QUOTE_RATE_PER_HOUR attempts in the trailing hour.
    Attempts are recorded (and stale ones pruned) in the same transaction
    as the check, so concurrent requests cannot slip past the count.
    Returns True when this attempt may proceed. With no source IP there
    is nothing to key on (direct module calls) and the gate is open — the
    HTTP layer always supplies the request IP."""
    if source_ip is None:
        return True
    window = now - 3600
    with db.transaction():
        db.execute('DELETE FROM anomaly_quote_attempts'
                   ' WHERE created_at <= ?', (window,))
        count = db.query_one(
            'SELECT COUNT(*) AS c FROM anomaly_quote_attempts'
            ' WHERE ip = ? AND created_at > ?', (source_ip, window))['c']
        if count >= config.ANOMALY_QUOTE_RATE_PER_HOUR:
            return False
        db.execute('INSERT INTO anomaly_quote_attempts (ip, created_at)'
                   ' VALUES (?, ?)', (source_ip, now))
    return True


def create_quote(db, wallet, tier, currency, now=None, source_ip=None):
    now = time.time() if now is None else now
    if wallet is not None and not isinstance(wallet, str):
        return {'error': 'bad_wallet'}
    if wallet == '':
        wallet = None  # empty string = anonymous quote
    if tier not in config.TIERS:
        return {'error': 'unknown_tier'}
    if currency not in config.CURRENCIES:
        return {'error': 'unknown_currency'}
    if not config.TREASURY:
        return {'error': 'payments_not_configured'}
    if tier == 'anomaly':
        # Holds are a scarce, abusable resource: an anomaly quote must be
        # bound to a wallet (one live hold per wallet) and is rate-bound
        # per source IP. Anonymous anomaly quotes are refused outright.
        if wallet is None:
            return {'error': 'wallet_required'}
        if not _anomaly_rate_gate(db, source_ip, now):
            return {'error': 'rate_limited'}
    usd = config.TIERS[tier]['usd']
    if currency == 'SOL':
        price = pricing.sol_usd()
        if not price:
            return {'error': 'price_unavailable'}
        amount = usd / price
    else:
        price = pricing.ore_usd()
        if not price:
            return {'error': 'price_unavailable'}
        amount = round(usd / price, 6)  # client quotes ORE at 6dp
    hold_id = None
    expires = now + config.QUOTE_TTL_MINUTES * 60
    if tier == 'anomaly':
        claim = slots.claim(db, wallet, now=now, source_ip=source_ip)
        if not claim.get('ok'):
            return {'error': claim.get('error', 'sold_out'),
                    'next_free_at': claim.get('next_free_at')}
        hold_id = claim['hold_id']
        expires = claim['expires_at']  # quote expiry = hold expiry
    request_id = uuid.uuid4().hex
    memo = 'HAUNT:' + request_id
    with db.transaction():
        db.execute(
            'INSERT INTO quotes (request_id, wallet, tier, currency,'
            ' amount, memo, hold_id, created_at, expires_at, state,'
            ' source_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (request_id, wallet, tier, currency, amount, memo, hold_id,
             now, expires, 'pending', source_ip))
    return {'request_id': request_id, 'wallet': wallet, 'tier': tier,
            'currency': currency, 'amount': amount, 'memo': memo,
            'treasury': config.TREASURY, 'expires_at': expires,
            'hold_id': hold_id}


def _expected_base_units(quote):
    if quote['currency'] == 'SOL':
        return int(round(quote['amount'] * 1_000_000_000))
    return int(round(quote['amount'] * (10 ** config.ORE_DECIMALS)))


def _quote_stale(quote, tx, now):
    """Has this quote's payment window closed?

    Non-anomaly: the quote dies at expires_at, full stop. Anomaly:
    expires_at is the slot HOLD's deadline, not the payment's — a buyer
    who paid while completing checkout must still settle. The payment
    itself must be fresh: the tx's blockTime when present (else the
    verify time) has to fall within QUOTE_TTL_MINUTES of the quote's
    creation (with a 5-minute clock-skew allowance before it).
    """
    if quote['tier'] != 'anomaly':
        return now > quote['expires_at']
    paid_at = tx.get('blockTime') or now
    earliest = quote['created_at'] - 300
    latest = quote['created_at'] + config.QUOTE_TTL_MINUTES * 60
    return not (earliest <= paid_at <= latest)


def _settle_anomaly_slot(db, quote, payer, pass_expires_at, source_ip, now):
    """Decide the slot for a verified anomaly payment. Runs INSIDE the
    grant transaction. Returns True when the payer ends up holding an
    active pass:

    - the quote's own hold is still live -> convert it (stamping payer);
    - the hold expired/was released, but the payer has no live hold/pass
      and capacity remains -> claim a fresh slot for the payer, already
      converted;
    - otherwise (capacity gone, or the payer already occupies a slot) ->
      False: the caller records the grant as refund_due instead of
      pretending a pass exists.
    """
    if quote['hold_id']:
        hold = db.query_one('SELECT state, expires_at FROM slot_holds'
                            ' WHERE id = ?', (quote['hold_id'],))
        if hold is not None and hold['state'] == 'converted':
            return True  # already a pass (defensive; quotes are 1-use)
        if hold is not None and hold['state'] == 'held' \
                and hold['expires_at'] > now:
            cur = db.execute(
                "UPDATE slot_holds SET state = 'converted',"
                " pass_expires_at = ?, wallet = ? WHERE id = ?"
                " AND state = 'held'",
                (pass_expires_at, payer, quote['hold_id']))
            if cur.rowcount == 1:
                return True
    slots._expire_stale(db, now)
    existing = db.query_one(
        "SELECT id FROM slot_holds WHERE wallet = ?"
        " AND ((state = 'held' AND expires_at > ?)"
        "   OR (state = 'converted' AND pass_expires_at > ?)) LIMIT 1",
        (payer, now, now))
    if existing is not None:
        return False  # payer already occupies a slot
    active, holds = slots._counts(db, now)
    if active + holds >= config.SLOT_CAP:
        return False  # capacity gone
    db.execute(
        "INSERT INTO slot_holds (wallet, state, created_at, expires_at,"
        " pass_expires_at, source_ip) VALUES (?, 'converted', ?, ?, ?, ?)",
        (payer, now, now, pass_expires_at, source_ip))
    return True


def verify_payment(db, signature, now=None):
    now = time.time() if now is None else now
    if not config.TREASURY:
        return {'verified': False, 'error': 'payments_not_configured'}
    if not signature or not isinstance(signature, str):
        return {'verified': False, 'error': 'missing_signature'}
    if db.query_one('SELECT sig FROM consumed_signatures WHERE sig = ?',
                    (signature,)):
        return {'verified': False, 'error': 'signature_already_consumed'}
    tx = solana.get_transaction(signature)
    if not tx:
        return {'verified': False, 'error': 'tx_not_found'}
    if (tx.get('meta') or {}).get('err'):
        return {'verified': False, 'error': 'tx_failed'}
    # A tx can carry several memos (wallets and aggregators add their
    # own). Consider ALL of them: a pending, unstale quote anywhere in
    # the tx settles it; stale/consumed quotes sharing the tx must not
    # poison a good one.
    candidates = []
    for memo in solana.extract_memos(tx):
        row = db.query_one('SELECT * FROM quotes WHERE memo = ?', (memo,))
        if row is not None:
            candidates.append(row)
    if not candidates:
        return {'verified': False, 'error': 'unknown_memo'}
    quote = None
    for cand in candidates:
        if cand['state'] == 'pending' and not _quote_stale(cand, tx, now):
            quote = cand
            break
    if quote is None:
        first = candidates[0]
        if first['state'] != 'pending':
            return {'verified': False, 'error': 'quote_not_pending'}
        return {'verified': False, 'error': 'quote_expired'}
    expected = _expected_base_units(quote)
    if expected <= 0:
        return {'verified': False, 'error': 'bad_quote_amount'}
    if quote['currency'] == 'SOL':
        actual = solana.sol_delta(tx, config.TREASURY)
    else:
        actual = solana.ore_delta(tx, config.TREASURY, config.ORE_MINT)
    if actual * 100 < expected * 99:
        return {'verified': False, 'error': 'insufficient_amount',
                'expected': expected, 'actual': actual}
    # Bind WHO paid from the transaction's transfer instructions. The
    # entitlement binds to the bound payer — a claimed wallet on the
    # quote is advisory only. Same 99% bar as the treasury check, kept
    # in exact integer math: the payer's contribution magnitude m must
    # satisfy m * 100 >= expected * 99, so the smallest qualifying
    # magnitude is ceil(expected * 99 / 100).
    min_payer_mag = -(-expected * 99 // 100)
    if quote['currency'] == 'SOL':
        payer = solana.instruction_payer_sol(tx, config.TREASURY,
                                             min_payer_mag)
    else:
        payer = solana.instruction_payer_ore(tx, config.ORE_MINT,
                                             config.TREASURY,
                                             min_payer_mag)
    if payer is None:
        return {'verified': False, 'error': 'payer_not_found',
                'expected': expected, 'actual': actual}
    days = config.TIERS[quote['tier']]['days']
    expires_at = None if days is None else now + days * 86400
    pass_state = None
    note = None
    try:
        with db.transaction():
            db.execute(
                'INSERT INTO consumed_signatures (sig, request_id,'
                ' consumed_at) VALUES (?, ?, ?)',
                (signature, quote['request_id'], now))
            if quote['tier'] == 'anomaly':
                settled = _settle_anomaly_slot(
                    db, quote, payer, expires_at, quote['source_ip'], now)
                pass_state = 'active' if settled else 'refund_due'
                note = None if settled else 'refund_due'
            db.execute(
                'INSERT INTO entitlements (wallet, tier, granted_at,'
                ' expires_at, source_sig, note) VALUES (?, ?, ?, ?, ?, ?)',
                (payer, quote['tier'], now, expires_at, signature, note))
            db.execute(
                "UPDATE quotes SET state = 'consumed' WHERE request_id = ?",
                (quote['request_id'],))
    except sqlite3.IntegrityError:
        return {'verified': False, 'error': 'signature_already_consumed'}
    result = {'verified': True, 'tier': quote['tier'],
              'wallet': payer, 'claimed_wallet': quote['wallet'],
              'expires_at': expires_at}
    if pass_state is not None:
        result['pass_state'] = pass_state
    return result


def list_entitlements(db, wallet, now=None):
    now = time.time() if now is None else now
    rows = db.query_all(
        'SELECT tier, granted_at, expires_at, source_sig, note'
        ' FROM entitlements'
        ' WHERE wallet = ? AND (expires_at IS NULL OR expires_at > ?)'
        ' ORDER BY granted_at', (wallet, now))
    entitlements = [{'tier': r['tier'], 'granted_at': r['granted_at'],
                     'expires_at': r['expires_at'],
                     'source_sig': r['source_sig'],
                     'note': r['note']} for r in rows]
    active_tier = None
    if entitlements:
        active_tier = min(entitlements,
                          key=lambda e: TIER_PRIORITY[e['tier']])['tier']
    return {'wallet': wallet, 'active_tier': active_tier,
            'entitlements': entitlements}
