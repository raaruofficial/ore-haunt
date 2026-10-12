"""Anomaly slot system — rolling 24-hour enforced scarcity.

A slot is occupied by either a live HOLD (reserved, unpaid; expires after
config.SLOT_HOLD_MINUTES) or an ACTIVE PASS (converted on payment; occupies
until pass_expires_at, i.e. 24h after grant for the anomaly tier). The cap
applies to the sum: active_passes + live_holds <= config.SLOT_CAP — at any
instant, which is exactly a rolling 24h window for passes.

claim() runs its whole check-and-insert in ONE transaction, so concurrent
claims cannot overshoot the cap. One slot per wallet: a wallet with a live
hold or an active pass cannot claim another. When the caller supplies a
source IP (the HTTP layer always does), one LIVE HOLD per IP is enforced
on top of that — a single source cannot park holds under a pile of fresh
wallets while real buyers are turned away.
"""
import time

import config


def _expire_stale(db, now):
    db.execute(
        "UPDATE slot_holds SET state = 'expired'"
        " WHERE state = 'held' AND expires_at <= ?", (now,))


def _counts(db, now):
    active = db.query_one(
        "SELECT COUNT(*) AS c FROM slot_holds"
        " WHERE state = 'converted' AND pass_expires_at > ?", (now,))['c']
    holds = db.query_one(
        "SELECT COUNT(*) AS c FROM slot_holds"
        " WHERE state = 'held' AND expires_at > ?", (now,))['c']
    return active, holds


def _next_free(db, now):
    row = db.query_one(
        "SELECT MIN(x) AS nf FROM ("
        "  SELECT pass_expires_at AS x FROM slot_holds"
        "   WHERE state = 'converted' AND pass_expires_at > ?"
        "  UNION ALL"
        "  SELECT expires_at AS x FROM slot_holds"
        "   WHERE state = 'held' AND expires_at > ?)", (now, now))
    return row['nf'] if row else None


def status(db, now=None):
    """{active, holds, cap, next_free_at} — next_free_at set only when full."""
    now = time.time() if now is None else now
    with db.transaction():
        _expire_stale(db, now)
        active, holds = _counts(db, now)
        full = (active + holds) >= config.SLOT_CAP
        return {'active': active, 'holds': holds, 'cap': config.SLOT_CAP,
                'next_free_at': _next_free(db, now) if full else None}


def claim(db, wallet, now=None, source_ip=None):
    """Atomically reserve a slot for `wallet`.

    `wallet` may be None: an anonymous hold, claimed through a null-wallet
    payment quote (the web client has no connected wallet — the payer only
    exists once the payment transaction does). The cap still applies; the
    one-per-wallet check is skipped (there is no wallet to check) and the
    payer is stamped onto the hold when the payment converts it.

    `source_ip`, when given, is recorded on the hold and bounds the
    source to ONE live hold at a time ('ip_hold_limit') — wallet rotation
    from a single address cannot multiply reservations.

    Returns {'ok': True, 'hold_id', 'expires_at'} or
    {'ok': False, 'error': 'sold_out'|'already_holding'|'already_active'|
     'ip_hold_limit', 'next_free_at': ...}.
    """
    now = time.time() if now is None else now
    with db.transaction():
        _expire_stale(db, now)
        if wallet is not None:
            existing = db.query_one(
                "SELECT id, state FROM slot_holds WHERE wallet = ?"
                " AND ((state = 'held' AND expires_at > ?)"
                "   OR (state = 'converted' AND pass_expires_at > ?))"
                " ORDER BY id DESC LIMIT 1", (wallet, now, now))
            if existing is not None:
                err = ('already_active' if existing['state'] == 'converted'
                       else 'already_holding')
                return {'ok': False, 'error': err,
                        'next_free_at': _next_free(db, now)}
        if source_ip is not None:
            ip_hold = db.query_one(
                "SELECT MIN(expires_at) AS nf FROM slot_holds"
                " WHERE source_ip = ? AND state = 'held'"
                " AND expires_at > ?", (source_ip, now))
            if ip_hold is not None and ip_hold['nf'] is not None:
                return {'ok': False, 'error': 'ip_hold_limit',
                        'next_free_at': ip_hold['nf']}
        active, holds = _counts(db, now)
        if active + holds >= config.SLOT_CAP:
            return {'ok': False, 'error': 'sold_out',
                    'next_free_at': _next_free(db, now)}
        expires = now + config.SLOT_HOLD_MINUTES * 60
        cur = db.execute(
            "INSERT INTO slot_holds (wallet, state, created_at, expires_at,"
            " pass_expires_at, source_ip) VALUES (?, 'held', ?, ?, NULL, ?)",
            (wallet, now, expires, source_ip))
        return {'ok': True, 'hold_id': cur.lastrowid, 'expires_at': expires}


def release(db, hold_id, now=None):
    """Free a live hold. True if a hold was actually released."""
    with db.transaction():
        cur = db.execute(
            "UPDATE slot_holds SET state = 'released'"
            " WHERE id = ? AND state = 'held'", (hold_id,))
        return cur.rowcount == 1


def convert(db, hold_id, pass_expires_at, now=None):
    """Turn a live hold into an active pass expiring at pass_expires_at."""
    with db.transaction():
        cur = db.execute(
            "UPDATE slot_holds SET state = 'converted', pass_expires_at = ?"
            " WHERE id = ? AND state = 'held'", (pass_expires_at, hold_id))
        return cur.rowcount == 1
