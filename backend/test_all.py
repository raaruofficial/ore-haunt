#!/usr/bin/env python3
"""HAUNT backend test suite (stdlib unittest).

Run:  python3 test_all.py     (exit 0 = all green)

All Solana RPC and Jupiter calls are stubbed: fabricated jsonParsed
transactions are built in-process and `solana.get_transaction` /
`pricing._fetch_prices` are monkeypatched per test. No network, no real
treasury, no real funds anywhere near this file.
"""
import base64
import os
import re
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth
import config
import payments
import pricing
import slots
import solana
from db import DB

TREASURY = '7YttLkHDoV9T9vGYbQMw9NqS3hT5vFzRkP8wXyZaBcD'
PAYER = '9xQeVvSMBjVa41XNKjPHDUaKugWEGbCpUcM9qEHc7VZ'
OTHER = '4Nd1mPzV3kXnWqRtYuHsJfGdBcAeFgHiJkLmNoPqRsT'
NOW = 1_700_000_000.0
FAKE_PRICES = {config.SOL_MINT: {'usdPrice': 200.0},
               config.ORE_MINT: {'usdPrice': 100.0}}


# ---- fabricated transactions -------------------------------------------------

def memo_ix(memo):
    return {'program': 'spl-memo',
            'programId': 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr',
            'parsed': memo}


def sol_transfer_ix(source, destination, lamports):
    return {'program': 'system',
            'programId': '11111111111111111111111111111111',
            'parsed': {'type': 'transfer',
                       'info': {'source': source, 'destination': destination,
                                'lamports': lamports}}}


def token_transfer_ix(source, destination, amount, authority):
    return {'program': 'spl-token',
            'programId': 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
            'parsed': {'type': 'transfer',
                       'info': {'source': source, 'destination': destination,
                                'authority': authority,
                                'amount': str(amount)}}}


def sol_tx(treasury, lamports, memo, payer=PAYER, pre=5_000_000_000):
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': [
                {'pubkey': payer, 'signer': True, 'source': 'transaction'},
                {'pubkey': treasury, 'signer': False, 'source': 'transaction'}],
            'instructions': [memo_ix(memo),
                             sol_transfer_ix(payer, treasury, lamports)]}},
        'meta': {'err': None,
                 'preBalances': [20_000_000_000, pre],
                 'postBalances': [20_000_000_000 - lamports - 5000,
                                  pre + lamports],
                 'preTokenBalances': [], 'postTokenBalances': [],
                 'logMessages': []}}


TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
PAYER_ATA = '8F3kQz7nVbTPayerAta11111111111111111111111'
PAYER_ATA2 = '8F3kQz7nVbTPayerAta22222222222222222222222'
TREASURY_ATA = '9G4lRa8oWcUTreasuryAta1111111111111111111'
TREASURY_ATA2 = '9G4lRa8oWcUTreasuryAta2222222222222222222'
OTHER_ATA = '7E2jPz6mUaSOtherAta11111111111111111111111'
POOL_OWNER = '5PoolWa11et1111111111111111111111111111111'
POOL_ATA = '5PoolAta1111111111111111111111111111111111'
SHUFFLE_DEST = '6ShuffleDest111111111111111111111111111111'


def _tb(index, mint, owner, amount, decimals=11):
    return {'accountIndex': index, 'mint': mint, 'owner': owner,
            'programId': TOKEN_PROGRAM,
            'uiTokenAmount': {'amount': str(amount), 'decimals': decimals}}


def ore_tx(treasury, delta_base, memo, split=None, pre_total=10 ** 12):
    """ORE payment; `split` = fractions of the delta across TWO of the
    treasury's token accounts (the client's split-account case). Carries
    real parsed transfer instructions from the payer's token account to
    each treasury token account, so payer binding exercises the
    instruction path (post-fix), not the balance-diff fallback."""
    fractions = split or [1.0]
    shares = []
    remaining = int(delta_base)
    for i, frac in enumerate(fractions):
        share = int(delta_base * frac) if i < len(fractions) - 1 else remaining
        remaining -= share
        shares.append(share)
    treasury_atas = [TREASURY_ATA, TREASURY_ATA2][:len(shares)]
    keys = [{'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
            {'pubkey': PAYER_ATA, 'signer': False, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'}]
    keys += [{'pubkey': a, 'signer': False, 'source': 'transaction'}
             for a in treasury_atas]
    keys.append({'pubkey': OTHER, 'signer': False, 'source': 'transaction'})
    keys.append({'pubkey': OTHER_ATA, 'signer': False,
                 'source': 'transaction'})
    keys.append({'pubkey': TREASURY_ATA2 + 'BadMint', 'signer': False,
                 'source': 'transaction'})
    idx = {k['pubkey']: i for i, k in enumerate(keys)}
    pre_entries = [_tb(1, config.ORE_MINT, PAYER,
                       pre_total + int(delta_base))]
    post_entries = [_tb(1, config.ORE_MINT, PAYER, pre_total)]
    instructions = [memo_ix(memo)]
    for ata, share in zip(treasury_atas, shares):
        pre_i = pre_total // len(shares)
        pre_entries.append(_tb(idx[ata], config.ORE_MINT, treasury, pre_i))
        post_entries.append(_tb(idx[ata], config.ORE_MINT, treasury,
                                pre_i + share))
        instructions.append(token_transfer_ix(PAYER_ATA, ata, share, PAYER))
    # Noise that MUST be ignored: another owner's account, another mint.
    post_entries.append(_tb(idx[OTHER_ATA], config.ORE_MINT, OTHER,
                            10 ** 13))
    post_entries.append(_tb(idx[TREASURY_ATA2 + 'BadMint'], OTHER, treasury,
                            10 ** 13, decimals=6))
    n = len(keys)
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys, 'instructions': instructions}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10] + [10 ** 9] * (n - 1),
                 'postBalances': [10 ** 10 - 5000] + [10 ** 9] * (n - 1),
                 'preTokenBalances': pre_entries,
                 'postTokenBalances': post_entries,
                 'logMessages': []}}


class BackendCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(self.db_path)
        self.db = DB(self.db_path)
        self._saved = (config.TREASURY, config.SLOT_CAP)
        config.TREASURY = TREASURY
        self.txs = {}
        self._real_get_tx = solana.get_transaction
        solana.get_transaction = lambda sig: self.txs.get(sig)
        self._real_fetch = pricing._fetch_prices
        pricing._fetch_prices = lambda mints: FAKE_PRICES

    def tearDown(self):
        solana.get_transaction = self._real_get_tx
        pricing._fetch_prices = self._real_fetch
        config.TREASURY, config.SLOT_CAP = self._saved
        self.db.close()
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.db_path + suffix)
            except OSError:
                pass

    def entitlement_count(self, wallet):
        row = self.db.query_one(
            'SELECT COUNT(*) AS c FROM entitlements WHERE wallet = ?',
            (wallet,))
        return row['c']


# ---- 1. SOL payments -----------------------------------------------------------

class TestSOLPayments(BackendCase):
    def test_exact_amount_grants(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        self.assertEqual(q['amount'], 1 / 200.0)  # $1 at $200/SOL
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig1'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig1', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(self.entitlement_count(PAYER), 1)
        ent = payments.list_entitlements(self.db, PAYER, now=NOW + 60)
        self.assertEqual(ent['active_tier'], 'day')
        self.assertEqual(ent['entitlements'][0]['expires_at'],
                         NOW + 60 + 86400)

    def test_98_percent_rejected(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9 * 0.98))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'insufficient_amount')
        self.assertEqual(self.entitlement_count(PAYER), 0)
        self.assertIsNone(self.db.query_one(
            'SELECT sig FROM consumed_signatures WHERE sig = ?', ('sig',)))

    def test_wrong_memo_rejected(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, 'HAUNT:not-a-quote')
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'unknown_memo')
        self.assertEqual(self.entitlement_count(PAYER), 0)

    def test_wrong_recipient_rejected(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        # Payment lands at OTHER wallet's position in the account list.
        tx = sol_tx(OTHER, lamports, q['memo'])
        self.txs['sig'] = tx
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'insufficient_amount')
        self.assertEqual(r['actual'], 0)

    def test_expired_quote_rejected(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        late = NOW + config.QUOTE_TTL_MINUTES * 60 + 1
        r = payments.verify_payment(self.db, 'sig', now=late)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'quote_expired')
        self.assertEqual(self.entitlement_count(PAYER), 0)


# ---- 2. ORE payments -----------------------------------------------------------

class TestOREPayments(BackendCase):
    def test_split_across_two_accounts_grants(self):
        q = payments.create_quote(self.db, PAYER, 'monthly', 'ORE', now=NOW)
        self.assertEqual(q['amount'], round(5 / 100.0, 6))  # $5 at $100/ORE
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx(TREASURY, base, q['memo'], split=[0.6, 0.4])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(self.entitlement_count(PAYER), 1)

    def test_99_percent_boundary_passes(self):
        q = payments.create_quote(self.db, PAYER, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        pay = base * 99 // 100  # exactly 99%
        self.txs['sig'] = ore_tx(TREASURY, pay, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)

    def test_98_9_percent_fails(self):
        q = payments.create_quote(self.db, PAYER, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        pay = base * 989 // 1000  # 98.9%
        self.txs['sig'] = ore_tx(TREASURY, pay, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'insufficient_amount')
        self.assertEqual(self.entitlement_count(PAYER), 0)


# ---- 3. Replay ------------------------------------------------------------------

class TestReplay(BackendCase):
    def test_same_signature_twice_grants_once(self):
        q = payments.create_quote(self.db, PAYER, 'lifetime', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        first = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(first['verified'], first)
        second = payments.verify_payment(self.db, 'sig', now=NOW + 120)
        self.assertFalse(second['verified'])
        self.assertEqual(second['error'], 'signature_already_consumed')
        self.assertEqual(self.entitlement_count(PAYER), 1)
        ent = payments.list_entitlements(self.db, PAYER, now=NOW + 120)
        self.assertIsNone(ent['entitlements'][0]['expires_at'])  # lifetime


# ---- 4. Treasury unset ------------------------------------------------------------

class TestTreasuryUnset(BackendCase):
    def test_unset_blocks_everything(self):
        config.TREASURY = None
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        self.assertEqual(q.get('error'), 'payments_not_configured')
        r = payments.verify_payment(self.db, 'anysig', now=NOW)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'payments_not_configured')
        self.assertEqual(self.entitlement_count(PAYER), 0)

    def test_unset_after_quote_grants_nothing(self):
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        config.TREASURY = None  # operator unsets before verification
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(self.entitlement_count(PAYER), 0)


# ---- 4b. Payer binding (null-wallet quotes) -----------------------------------------
#
# The web client has no connected wallet: it quotes anonymously (outside
# the anomaly tier) and the entitlement must bind to whoever actually
# paid, bound from the tx's transfer instructions.

def sol_tx_split_senders(treasury, lamports, memo):
    """Treasury gains the full amount, but TWO senders each cover ~half —
    no single payer reaches the 99% derivation bar."""
    half = lamports // 2
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': [
                {'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
                {'pubkey': OTHER, 'signer': True, 'source': 'transaction'},
                {'pubkey': treasury, 'signer': False, 'source': 'transaction'}],
            'instructions': [{
                'program': 'spl-memo',
                'programId': 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr',
                'parsed': memo}]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10, 10 ** 10, 5_000_000_000],
                 'postBalances': [10 ** 10 - half - 5000,
                                  10 ** 10 - (lamports - half),
                                  5_000_000_000 + lamports],
                 'preTokenBalances': [], 'postTokenBalances': [],
                 'logMessages': []}}


def sol_tx_treasury_only(treasury, lamports, memo):
    """Treasury gains the full amount but the only other account moved by
    a bare network fee — the funds' source is not identifiable."""
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': [
                {'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
                {'pubkey': treasury, 'signer': False, 'source': 'transaction'}],
            'instructions': [{
                'program': 'spl-memo',
                'programId': 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr',
                'parsed': memo}]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10, 5_000_000_000],
                 'postBalances': [10 ** 10 - 5000, 5_000_000_000 + lamports],
                 'preTokenBalances': [], 'postTokenBalances': [],
                 'logMessages': []}}


class TestPayerDerivation(BackendCase):
    def test_null_wallet_sol_quote_grants_derived_payer(self):
        q = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        self.assertIsNone(q['wallet'])
        row = self.db.query_one('SELECT wallet FROM quotes'
                                ' WHERE request_id = ?', (q['request_id'],))
        self.assertIsNone(row['wallet'])
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)          # derived, from the tx
        self.assertIsNone(r['claimed_wallet'])
        self.assertEqual(self.entitlement_count(PAYER), 1)

    def test_null_wallet_ore_quote_grants_derived_owner(self):
        q = payments.create_quote(self.db, None, 'monthly', 'ORE', now=NOW)
        self.assertNotIn('error', q)
        self.assertIsNone(q['wallet'])
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx(TREASURY, base, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)
        self.assertEqual(self.entitlement_count(PAYER), 1)

    def test_split_senders_no_derivable_payer_rejected(self):
        # Balance-only fixture (no transfer instructions): the narrow
        # fallback sees TWO signers, each below the bar — binds nobody.
        q = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx_split_senders(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'payer_not_found')
        self.assertEqual(self.entitlement_count(PAYER), 0)
        self.assertEqual(self.entitlement_count(OTHER), 0)
        # Nothing consumed, quote still pending: a proper payment with the
        # same memo still verifies afterwards.
        self.assertIsNone(self.db.query_one(
            'SELECT sig FROM consumed_signatures WHERE sig = ?', ('sig',)))
        row = self.db.query_one('SELECT state FROM quotes'
                                ' WHERE request_id = ?', (q['request_id'],))
        self.assertEqual(row['state'], 'pending')
        self.txs['sig2'] = sol_tx(TREASURY, lamports, q['memo'])
        r2 = payments.verify_payment(self.db, 'sig2', now=NOW + 120)
        self.assertTrue(r2['verified'], r2)
        self.assertEqual(self.entitlement_count(PAYER), 1)

    def test_treasury_only_deltas_no_payer_rejected(self):
        q = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx_treasury_only(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'payer_not_found')
        self.assertEqual(self.entitlement_count(PAYER), 0)
        self.assertIsNone(self.db.query_one(
            'SELECT sig FROM consumed_signatures WHERE sig = ?', ('sig',)))

    def test_claimed_wallet_mismatch_grants_derived_payer(self):
        # Quote CLAIMS wallet OTHER; the transaction shows PAYER paid.
        q = payments.create_quote(self.db, OTHER, 'day', 'SOL', now=NOW)
        self.assertEqual(q['wallet'], OTHER)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)            # the chain is truth
        self.assertEqual(r['claimed_wallet'], OTHER)    # claim reported, unused
        self.assertEqual(self.entitlement_count(PAYER), 1)
        self.assertEqual(self.entitlement_count(OTHER), 0)

    def test_wallet_anomaly_converts_hold_and_stamps_payer(self):
        q = payments.create_quote(self.db, PAYER, 'anomaly', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        self.assertIsNotNone(q['hold_id'])
        hold = self.db.query_one('SELECT wallet, state FROM slot_holds'
                                 ' WHERE id = ?', (q['hold_id'],))
        self.assertEqual(hold['wallet'], PAYER)
        self.assertEqual(hold['state'], 'held')
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['pass_state'], 'active')
        hold = self.db.query_one('SELECT wallet, state FROM slot_holds'
                                 ' WHERE id = ?', (q['hold_id'],))
        self.assertEqual(hold['state'], 'converted')
        self.assertEqual(hold['wallet'], PAYER)        # payer stamped on
        # ...so the one-slot-per-wallet rule now sees this payer.
        again = slots.claim(self.db, PAYER, now=NOW + 120)
        self.assertFalse(again['ok'])
        self.assertEqual(again['error'], 'already_active')


# ---- 4c. Schema migration (pre-amendment databases) ---------------------------------

OLD_SCHEMA = [
    """CREATE TABLE quotes (
        request_id  TEXT PRIMARY KEY,
        wallet      TEXT NOT NULL,
        tier        TEXT NOT NULL,
        currency    TEXT NOT NULL,
        amount      REAL NOT NULL,
        memo        TEXT NOT NULL UNIQUE,
        hold_id     INTEGER,
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        state       TEXT NOT NULL DEFAULT 'pending'
    )""",
    """CREATE TABLE slot_holds (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet          TEXT NOT NULL,
        state           TEXT NOT NULL DEFAULT 'held',
        created_at      REAL NOT NULL,
        expires_at      REAL NOT NULL,
        pass_expires_at REAL
    )""",
]


class TestWalletMigration(unittest.TestCase):
    def test_old_schema_db_migrates_to_nullable_wallets(self):
        import sqlite3
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(path)
        conn = sqlite3.connect(path)
        for stmt in OLD_SCHEMA:
            conn.execute(stmt)
        conn.execute("INSERT INTO quotes (request_id, wallet, tier,"
                     " currency, amount, memo, hold_id, created_at,"
                     " expires_at, state) VALUES ('r1', 'w1', 'day',"
                     " 'SOL', 0.005, 'HAUNT:r1', NULL, 1, 2, 'pending')")
        conn.execute("INSERT INTO slot_holds (wallet, state, created_at,"
                     " expires_at) VALUES ('w1', 'held', 1, 2)")
        conn.commit()
        conn.close()
        try:
            db = DB(path)  # opening runs the migration
            try:
                for table in ('quotes', 'slot_holds'):
                    info = db.query_all('PRAGMA table_info(%s)' % table)
                    col = [r for r in info if r[1] == 'wallet'][0]
                    self.assertEqual(col[3], 0, table)  # notnull flag off
                    # Columns added since (source_ip) are present too.
                    self.assertIn('source_ip', [r[1] for r in info], table)
                ent_info = db.query_all('PRAGMA table_info(entitlements)')
                self.assertIn('note', [r[1] for r in ent_info])
                # Rows survive verbatim...
                row = db.query_one("SELECT wallet FROM quotes"
                                   " WHERE request_id = 'r1'")
                self.assertEqual(row['wallet'], 'w1')
                self.assertEqual(db.query_one(
                    'SELECT COUNT(*) AS c FROM slot_holds')['c'], 1)
                # ...and anonymous rows now insert.
                db.execute("INSERT INTO quotes (request_id, wallet, tier,"
                           " currency, amount, memo, created_at, expires_at)"
                           " VALUES ('r2', NULL, 'day', 'SOL', 0.005,"
                           " 'HAUNT:r2', 1, 2)")
                db.execute("INSERT INTO slot_holds (wallet, state,"
                           " created_at, expires_at) VALUES (NULL, 'held',"
                           " 1, 2)")
            finally:
                db.close()
            db2 = DB(path)  # migration is idempotent
            db2.close()
        finally:
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.unlink(path + suffix)
                except OSError:
                    pass


# ---- 5. Slots ---------------------------------------------------------------------

class TestSlots(BackendCase):
    def setUp(self):
        super().setUp()
        config.SLOT_CAP = 3

    def test_cap_and_next_free(self):
        for i in range(3):
            r = slots.claim(self.db, 'wallet%d' % i, now=NOW)
            self.assertTrue(r['ok'], r)
        r = slots.claim(self.db, 'wallet3', now=NOW)
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'sold_out')
        self.assertEqual(r['next_free_at'], NOW + config.SLOT_HOLD_MINUTES * 60)
        st = slots.status(self.db, now=NOW)
        self.assertEqual((st['active'], st['holds'], st['cap']), (0, 3, 3))
        self.assertEqual(st['next_free_at'],
                         NOW + config.SLOT_HOLD_MINUTES * 60)

    def test_hold_expiry_frees_capacity(self):
        for i in range(3):
            slots.claim(self.db, 'wallet%d' % i, now=NOW)
        later = NOW + config.SLOT_HOLD_MINUTES * 60 + 1
        r = slots.claim(self.db, 'wallet3', now=later)
        self.assertTrue(r['ok'], r)

    def test_one_per_wallet(self):
        self.assertTrue(slots.claim(self.db, 'w1', now=NOW)['ok'])
        r = slots.claim(self.db, 'w1', now=NOW)
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'already_holding')

    def test_release_frees(self):
        hold = slots.claim(self.db, 'w1', now=NOW)
        self.assertTrue(slots.release(self.db, hold['hold_id'], now=NOW))
        self.assertFalse(slots.release(self.db, hold['hold_id'], now=NOW))
        st = slots.status(self.db, now=NOW)
        self.assertEqual(st['holds'], 0)

    def test_rolling_window_24h(self):
        hold = slots.claim(self.db, 'w1', now=NOW)
        self.assertTrue(slots.convert(self.db, hold['hold_id'],
                                      NOW + 24 * 3600, now=NOW))
        st = slots.status(self.db, now=NOW + 23 * 3600)
        self.assertEqual(st['active'], 1)   # pass still occupies its slot
        st = slots.status(self.db, now=NOW + 24 * 3600 + 1)
        self.assertEqual(st['active'], 0)   # freed exactly 24h after grant
        r = slots.claim(self.db, 'w1', now=NOW + 3600)
        self.assertFalse(r['ok'])           # active pass blocks a second slot
        self.assertEqual(r['error'], 'already_active')

    def test_anomaly_payment_converts_hold(self):
        q = payments.create_quote(self.db, PAYER, 'anomaly', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        self.assertIsNotNone(q['hold_id'])
        st = slots.status(self.db, now=NOW)
        self.assertEqual((st['active'], st['holds']), (0, 1))
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        st = slots.status(self.db, now=NOW + 60)
        self.assertEqual((st['active'], st['holds']), (1, 0))
        self.assertEqual(r['expires_at'], NOW + 60 + 86400)  # 24h pass


# ---- 6. Concurrency -----------------------------------------------------------------

class TestSlotConcurrency(BackendCase):
    def test_twenty_threads_five_slots(self):
        config.SLOT_CAP = 5
        results, errors = [], []

        def worker(i):
            try:
                results.append(slots.claim(self.db, 'racer%d' % i, now=NOW))
            except Exception as exc:  # noqa: BLE001 - collected and asserted
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        granted = [r for r in results if r.get('ok')]
        self.assertEqual(len(granted), 5)
        self.assertEqual(len({r['hold_id'] for r in granted}), 5)
        st = slots.status(self.db, now=NOW)
        self.assertEqual(st['holds'], 5)


# ---- 7. Auth -------------------------------------------------------------------------

# RFC 8032 section 7.1, TEST 1 (empty message) and TEST 3 (message af82).
RFC1_PK = bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
RFC1_SIG = bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')
RFC3_PK = bytes.fromhex('fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025')
RFC3_SIG = bytes.fromhex('6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a')


class TestAuth(unittest.TestCase):
    def test_rfc8032_vector_empty_message(self):
        self.assertTrue(auth.verify(RFC1_PK, b'', RFC1_SIG))

    def test_rfc8032_vector_nonempty_message(self):
        self.assertTrue(auth.verify(RFC3_PK, bytes.fromhex('af82'), RFC3_SIG))
        self.assertFalse(auth.verify(RFC3_PK, bytes.fromhex('af'), RFC3_SIG))

    def test_tampered_signature_fails(self):
        sig = bytearray(RFC1_SIG)
        sig[40] ^= 1
        self.assertFalse(auth.verify(RFC1_PK, b'', bytes(sig)))


class TestChallengeFlow(BackendCase):
    def setUp(self):
        super().setUp()
        self.seed = bytes(range(1, 33))
        self.pub = auth.pubkey_from_seed(self.seed)
        self.wallet = auth.b58encode(self.pub)

    def test_challenge_single_use(self):
        ch = auth.create_challenge(self.db, self.wallet, now=NOW)
        sig = auth.sign(self.seed, ch['message'].encode())
        r = auth.verify_challenge(self.db, self.wallet, ch['nonce'],
                                  auth.b58encode(sig), now=NOW + 10)
        self.assertTrue(r['ok'], r)
        again = auth.verify_challenge(self.db, self.wallet, ch['nonce'],
                                      auth.b58encode(sig), now=NOW + 20)
        self.assertFalse(again['ok'])
        self.assertEqual(again['error'], 'nonce_already_used')

    def test_challenge_expired_nonce_fails(self):
        ch = auth.create_challenge(self.db, self.wallet, now=NOW)
        sig = auth.sign(self.seed, ch['message'].encode())
        r = auth.verify_challenge(self.db, self.wallet, ch['nonce'],
                                  auth.b58encode(sig),
                                  now=NOW + config.AUTH_TTL_SECONDS + 1)
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'nonce_expired')

    def test_challenge_base64_signature_and_wrong_wallet(self):
        ch = auth.create_challenge(self.db, self.wallet, now=NOW)
        sig = auth.sign(self.seed, ch['message'].encode())
        r = auth.verify_challenge(self.db, OTHER, ch['nonce'],
                                  base64.b64encode(sig).decode(), now=NOW + 10)
        self.assertFalse(r['ok'])  # wallet mismatch (and bad key length path)
        r = auth.verify_challenge(self.db, self.wallet, ch['nonce'],
                                  base64.b64encode(sig).decode(), now=NOW + 10)
        self.assertTrue(r['ok'], r)


# ---- 8. Pricing honesty ---------------------------------------------------------------

class TestPricingHonesty(BackendCase):
    def test_price_failure_refuses_quote(self):
        def boom(mints):
            raise RuntimeError('network down')
        pricing._fetch_prices = boom
        self.assertIsNone(pricing.sol_usd())
        self.assertIsNone(pricing.ore_usd())
        q = payments.create_quote(self.db, PAYER, 'day', 'SOL', now=NOW)
        self.assertEqual(q.get('error'), 'price_unavailable')
        q = payments.create_quote(self.db, PAYER, 'day', 'ORE', now=NOW)
        self.assertEqual(q.get('error'), 'price_unavailable')
        self.assertEqual(self.entitlement_count(PAYER), 0)

    def test_no_fallback_price_in_source(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'pricing.py')).read()
        # Anti-patterns a fallback price would take (prose mentions of the
        # word in the docstring are fine; code patterns are not):
        self.assertIsNone(re.search(r'return\s+\d', src))       # return <number>
        self.assertIsNone(re.search(r'\bor\s+\d+\.?\d*', src))  # price or 180
        self.assertIsNone(re.search(r'=\s*1[0-9]{2}(\.\d+)?\b', src))  # =$1xx literal


# ---- 4d. Instruction-derived payer binding (auditor probes F, G, D, E) --------------
#
# Post-fix, binding reads transfer INSTRUCTIONS, not balance diffs. These
# fixtures are expressed in instruction terms on purpose.

def sol_tx_cosigned(treasury, lamports, memo):
    """Probe G: the attacker (OTHER) is a co-signer and shuffles 3x the
    payment amount elsewhere in the same tx — the largest balance loser
    by far. The ONLY transfer into the treasury is the victim's (PAYER).
    Balance-diff binding would steal the entitlement for the attacker;
    instruction binding must bind the victim."""
    shuffle = lamports * 3
    keys = [{'pubkey': OTHER, 'signer': True, 'source': 'transaction'},
            {'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'},
            {'pubkey': SHUFFLE_DEST, 'signer': False,
             'source': 'transaction'}]
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys,
            'instructions': [
                memo_ix(memo),
                sol_transfer_ix(OTHER, SHUFFLE_DEST, shuffle),
                sol_transfer_ix(PAYER, treasury, lamports)]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10, 10 ** 10, 5_000_000_000, 0],
                 'postBalances': [10 ** 10 - shuffle - 5000,
                                  10 ** 10 - lamports,
                                  5_000_000_000 + lamports, shuffle],
                 'preTokenBalances': [], 'postTokenBalances': [],
                 'logMessages': []}}


def ore_tx_swap_routed(treasury, delta_base, memo):
    """Probe F: a swap pool delivers the ORE. The pool's token account
    (owner = POOL_OWNER, NOT a signer) transfers into the treasury; the
    initiating user (OTHER, signer) never loses any ORE. Nobody may be
    bound — the payment is not server-bindable and must fail closed."""
    keys = [{'pubkey': OTHER, 'signer': True, 'source': 'transaction'},
            {'pubkey': POOL_OWNER, 'signer': False, 'source': 'transaction'},
            {'pubkey': POOL_ATA, 'signer': False, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'},
            {'pubkey': TREASURY_ATA, 'signer': False,
             'source': 'transaction'},
            {'pubkey': OTHER_ATA, 'signer': False, 'source': 'transaction'}]
    idx = {k['pubkey']: i for i, k in enumerate(keys)}
    pre = [_tb(idx[POOL_ATA], config.ORE_MINT, POOL_OWNER,
               10 ** 12 + delta_base),
           _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury, 10 ** 12),
           _tb(idx[OTHER_ATA], config.ORE_MINT, OTHER, 10 ** 12)]
    post = [_tb(idx[POOL_ATA], config.ORE_MINT, POOL_OWNER, 10 ** 12),
            _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury,
                10 ** 12 + delta_base),
            _tb(idx[OTHER_ATA], config.ORE_MINT, OTHER, 10 ** 12)]
    n = len(keys)
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys,
            'instructions': [
                memo_ix(memo),
                token_transfer_ix(POOL_ATA, TREASURY_ATA, delta_base,
                                  POOL_OWNER)]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10] + [10 ** 9] * (n - 1),
                 'postBalances': [10 ** 10 - 5000] + [10 ** 9] * (n - 1),
                 'preTokenBalances': pre, 'postTokenBalances': post,
                 'logMessages': []}}


def ore_tx_two_sources(treasury, delta_base, memo):
    """Probe D (source side): the payer pays from TWO of their own token
    accounts, ~55/45 — neither transfer alone reaches the 99% bar; summed
    per owner they do. Must bind the payer."""
    first = delta_base * 55 // 100
    second = delta_base - first
    keys = [{'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
            {'pubkey': PAYER_ATA, 'signer': False, 'source': 'transaction'},
            {'pubkey': PAYER_ATA2, 'signer': False, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'},
            {'pubkey': TREASURY_ATA, 'signer': False,
             'source': 'transaction'}]
    idx = {k['pubkey']: i for i, k in enumerate(keys)}
    pre = [_tb(idx[PAYER_ATA], config.ORE_MINT, PAYER, 10 ** 12),
           _tb(idx[PAYER_ATA2], config.ORE_MINT, PAYER, 10 ** 12),
           _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury, 10 ** 12)]
    post = [_tb(idx[PAYER_ATA], config.ORE_MINT, PAYER, 10 ** 12 - first),
            _tb(idx[PAYER_ATA2], config.ORE_MINT, PAYER, 10 ** 12 - second),
            _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury,
                10 ** 12 + delta_base)]
    n = len(keys)
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys,
            'instructions': [
                memo_ix(memo),
                token_transfer_ix(PAYER_ATA, TREASURY_ATA, first, PAYER),
                token_transfer_ix(PAYER_ATA2, TREASURY_ATA, second, PAYER)]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10] + [10 ** 9] * (n - 1),
                 'postBalances': [10 ** 10 - 5000] + [10 ** 9] * (n - 1),
                 'preTokenBalances': pre, 'postTokenBalances': post,
                 'logMessages': []}}


def ore_tx_closed_source(treasury, delta_base, memo):
    """Probe E: the payer's source token account is CLOSED by the tx —
    it appears in preTokenBalances only. Its owner must still resolve
    (through the pre snapshot) and bind."""
    keys = [{'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
            {'pubkey': PAYER_ATA, 'signer': False, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'},
            {'pubkey': TREASURY_ATA, 'signer': False,
             'source': 'transaction'}]
    idx = {k['pubkey']: i for i, k in enumerate(keys)}
    pre = [_tb(idx[PAYER_ATA], config.ORE_MINT, PAYER, delta_base),
           _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury, 10 ** 12)]
    post = [_tb(idx[TREASURY_ATA], config.ORE_MINT, treasury,
                10 ** 12 + delta_base)]  # source account is gone
    n = len(keys)
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys,
            'instructions': [
                memo_ix(memo),
                token_transfer_ix(PAYER_ATA, TREASURY_ATA, delta_base,
                                  PAYER)]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10] + [10 ** 9] * (n - 1),
                 'postBalances': [10 ** 10 - 5000] + [10 ** 9] * (n - 1),
                 'preTokenBalances': pre, 'postTokenBalances': post,
                 'logMessages': []}}


MYSTERY_ATA = '4MysteryAta11111111111111111111111111111111'


def ore_tx_mystery_source(treasury, delta_base, memo):
    """Fallback fixture: the transfer's source account appears in NO
    balance entry (owner unresolvable from instructions), but the tx's
    only signer shows the full ORE loss on their real account. The
    narrow fallback must bind that signer."""
    keys = [{'pubkey': PAYER, 'signer': True, 'source': 'transaction'},
            {'pubkey': PAYER_ATA, 'signer': False, 'source': 'transaction'},
            {'pubkey': treasury, 'signer': False, 'source': 'transaction'},
            {'pubkey': TREASURY_ATA, 'signer': False,
             'source': 'transaction'},
            {'pubkey': MYSTERY_ATA, 'signer': False,
             'source': 'transaction'}]
    idx = {k['pubkey']: i for i, k in enumerate(keys)}
    pre = [_tb(idx[PAYER_ATA], config.ORE_MINT, PAYER, 10 ** 12),
           _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury, 10 ** 12)]
    post = [_tb(idx[PAYER_ATA], config.ORE_MINT, PAYER,
                10 ** 12 - delta_base),
            _tb(idx[TREASURY_ATA], config.ORE_MINT, treasury,
                10 ** 12 + delta_base)]
    n = len(keys)
    return {
        'transaction': {'signatures': ['fakesig'], 'message': {
            'accountKeys': keys,
            'instructions': [
                memo_ix(memo),
                token_transfer_ix(MYSTERY_ATA, TREASURY_ATA, delta_base,
                                  PAYER)]}},
        'meta': {'err': None,
                 'preBalances': [10 ** 10] + [10 ** 9] * (n - 1),
                 'postBalances': [10 ** 10 - 5000] + [10 ** 9] * (n - 1),
                 'preTokenBalances': pre, 'postTokenBalances': post,
                 'logMessages': []}}


class TestInstructionBinding(BackendCase):
    def test_probe_g_cosigned_sol_binds_victim(self):
        q = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx_cosigned(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)          # the victim who paid
        self.assertEqual(self.entitlement_count(PAYER), 1)
        self.assertEqual(self.entitlement_count(OTHER), 0)  # not the shuffler

    def test_probe_f_swap_routed_ore_fails_closed(self):
        q = payments.create_quote(self.db, None, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx_swap_routed(TREASURY, base, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'payer_not_found')
        self.assertEqual(self.entitlement_count(POOL_OWNER), 0)
        self.assertEqual(self.entitlement_count(OTHER), 0)
        self.assertIsNone(self.db.query_one(
            'SELECT sig FROM consumed_signatures WHERE sig = ?', ('sig',)))

    def test_probe_d_two_source_accounts_sum_per_owner(self):
        q = payments.create_quote(self.db, None, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx_two_sources(TREASURY, base, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)
        self.assertEqual(self.entitlement_count(PAYER), 1)

    def test_probe_e_closed_source_account_binds(self):
        q = payments.create_quote(self.db, None, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx_closed_source(TREASURY, base, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)

    def test_fallback_binds_lone_signer_when_source_unresolvable(self):
        q = payments.create_quote(self.db, None, 'monthly', 'ORE', now=NOW)
        base = int(round(q['amount'] * 10 ** 11))
        self.txs['sig'] = ore_tx_mystery_source(TREASURY, base, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 60)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['wallet'], PAYER)


# ---- 4e. Multi-memo transactions (auditor probe K) -----------------------------------

def with_memos(tx, memos):
    """Rebuild a fabricated tx carrying exactly these memos, in order,
    ahead of its original non-memo instructions."""
    msg = tx['transaction']['message']
    rest = [ix for ix in msg['instructions']
            if ix.get('program') != 'spl-memo']
    msg['instructions'] = [memo_ix(m) for m in memos] + rest
    return tx


class TestMultiMemo(BackendCase):
    def test_probe_k_stale_memo_first_valid_second_grants(self):
        old = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        new = payments.create_quote(self.db, None, 'day', 'SOL',
                                    now=NOW + 1800)
        lamports = int(round(new['amount'] * 10 ** 9))
        tx = with_memos(sol_tx(TREASURY, lamports, new['memo']),
                        [old['memo'], new['memo']])
        self.txs['sig'] = tx
        # Verify after the OLD quote expired but inside the new one's TTL.
        r = payments.verify_payment(self.db, 'sig', now=NOW + 1860)
        self.assertTrue(r['verified'], r)
        self.assertEqual(self.entitlement_count(PAYER), 1)
        row = self.db.query_one('SELECT state FROM quotes'
                                ' WHERE request_id = ?', (new['request_id'],))
        self.assertEqual(row['state'], 'consumed')
        row = self.db.query_one('SELECT state FROM quotes'
                                ' WHERE request_id = ?', (old['request_id'],))
        self.assertEqual(row['state'], 'pending')  # untouched, just stale

    def test_probe_k_consumed_memo_first_valid_second_grants(self):
        qa = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW)
        lamports = int(round(qa['amount'] * 10 ** 9))
        self.txs['sigA'] = sol_tx(TREASURY, lamports, qa['memo'])
        ra = payments.verify_payment(self.db, 'sigA', now=NOW + 60)
        self.assertTrue(ra['verified'], ra)
        qb = payments.create_quote(self.db, None, 'monthly', 'SOL', now=NOW)
        lamports_b = int(round(qb['amount'] * 10 ** 9))
        tx = with_memos(sol_tx(TREASURY, lamports_b, qb['memo']),
                        [qa['memo'], qb['memo']])
        self.txs['sigB'] = tx
        rb = payments.verify_payment(self.db, 'sigB', now=NOW + 120)
        self.assertTrue(rb['verified'], rb)
        self.assertEqual(rb['tier'], 'monthly')
        self.assertEqual(self.entitlement_count(PAYER), 2)


# ---- 4f. Anomaly quote bounds (auditor probe H) ---------------------------------------

class TestAnomalyBounds(BackendCase):
    IP = '10.1.1.1'

    def test_hold_ttl_is_two_minutes(self):
        self.assertEqual(config.SLOT_HOLD_MINUTES, 2)
        r = slots.claim(self.db, 'w-ttl', now=NOW)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['expires_at'] - NOW, 120)

    def test_anonymous_anomaly_quote_refused(self):
        q = payments.create_quote(self.db, None, 'anomaly', 'SOL', now=NOW,
                                  source_ip=self.IP)
        self.assertEqual(q.get('error'), 'wallet_required')
        st = slots.status(self.db, now=NOW)
        self.assertEqual(st['holds'], 0)  # no hold was burned

    def test_per_ip_one_live_hold(self):
        q1 = payments.create_quote(self.db, 'w-one', 'anomaly', 'SOL',
                                   now=NOW, source_ip=self.IP)
        self.assertNotIn('error', q1)
        q2 = payments.create_quote(self.db, 'w-two', 'anomaly', 'SOL',
                                   now=NOW, source_ip=self.IP)
        self.assertEqual(q2.get('error'), 'ip_hold_limit')
        q3 = payments.create_quote(self.db, 'w-two', 'anomaly', 'SOL',
                                   now=NOW, source_ip='10.2.2.2')
        self.assertNotIn('error', q3)  # a different source is unaffected

    def test_same_wallet_second_live_hold_rejected(self):
        q1 = payments.create_quote(self.db, 'w-one', 'anomaly', 'SOL',
                                   now=NOW, source_ip=self.IP)
        self.assertNotIn('error', q1)
        # Bypass the IP bound with a second address: the wallet bound is
        # what must bite.
        q2 = payments.create_quote(self.db, 'w-one', 'anomaly', 'SOL',
                                   now=NOW, source_ip='10.2.2.2')
        self.assertEqual(q2.get('error'), 'already_holding')

    def test_anomaly_quote_rate_limit_20_per_hour(self):
        ip = '10.9.9.9'
        for i in range(config.ANOMALY_QUOTE_RATE_PER_HOUR):
            q = payments.create_quote(self.db, 'rl-w%d' % i, 'anomaly',
                                      'SOL', now=NOW, source_ip=ip)
            self.assertNotIn('error', q, 'attempt %d' % i)
            # Free the per-IP hold so the next attempt tests the RATE
            # gate, not the hold gate.
            self.assertTrue(slots.release(self.db, q['hold_id'], now=NOW))
        q = payments.create_quote(self.db, 'rl-w-final', 'anomaly', 'SOL',
                                  now=NOW, source_ip=ip)
        self.assertEqual(q.get('error'), 'rate_limited')
        # The gate is anomaly-only and per-IP.
        q = payments.create_quote(self.db, None, 'day', 'SOL', now=NOW,
                                  source_ip=ip)
        self.assertNotIn('error', q)
        q = payments.create_quote(self.db, 'rl-other', 'anomaly', 'SOL',
                                  now=NOW, source_ip='10.8.8.8')
        self.assertNotIn('error', q)

    def test_cap_still_real_across_distinct_wallets_and_ips(self):
        config.SLOT_CAP = 3
        for i in range(3):
            q = payments.create_quote(self.db, 'cap-w%d' % i, 'anomaly',
                                      'SOL', now=NOW,
                                      source_ip='10.3.3.%d' % i)
            self.assertNotIn('error', q)
        q4 = payments.create_quote(self.db, 'cap-w3', 'anomaly', 'SOL',
                                   now=NOW, source_ip='10.3.3.3')
        self.assertEqual(q4.get('error'), 'sold_out')
        self.assertIsNotNone(q4.get('next_free_at'))


# ---- 4g. Verify-time slot re-check (refund_due) ----------------------------------------

class TestRefundDue(BackendCase):
    def test_expired_hold_capacity_gone_records_refund_due(self):
        config.SLOT_CAP = 1
        q = payments.create_quote(self.db, PAYER, 'anomaly', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        # The hold (2-min TTL) lapses; someone else takes the only slot
        # and converts it into an active pass.
        hold = slots.claim(self.db, OTHER, now=NOW + 150)
        self.assertTrue(hold['ok'], hold)
        self.assertTrue(slots.convert(self.db, hold['hold_id'],
                                      NOW + 150 + 86400, now=NOW + 150))
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        r = payments.verify_payment(self.db, 'sig', now=NOW + 200)
        self.assertTrue(r['verified'], r)          # the payment is real...
        self.assertEqual(r['pass_state'], 'refund_due')  # ...but no slot
        ent = self.db.query_one('SELECT note FROM entitlements'
                                ' WHERE wallet = ?', (PAYER,))
        self.assertEqual(ent['note'], 'refund_due')
        # The signature is consumed: the payment can't be re-presented.
        again = payments.verify_payment(self.db, 'sig', now=NOW + 300)
        self.assertEqual(again['error'], 'signature_already_consumed')
        st = slots.status(self.db, now=NOW + 200)
        self.assertEqual(st['active'], 1)  # still the other wallet's pass

    def test_expired_hold_capacity_free_reclaims_active(self):
        q = payments.create_quote(self.db, PAYER, 'anomaly', 'SOL', now=NOW)
        self.assertNotIn('error', q)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        # Hold expired at NOW+120; capacity is wide open at NOW+200.
        r = payments.verify_payment(self.db, 'sig', now=NOW + 200)
        self.assertTrue(r['verified'], r)
        self.assertEqual(r['pass_state'], 'active')
        ent = self.db.query_one('SELECT note FROM entitlements'
                                ' WHERE wallet = ?', (PAYER,))
        self.assertIsNone(ent['note'])
        st = slots.status(self.db, now=NOW + 200)
        self.assertEqual(st['active'], 1)

    def test_anomaly_payment_too_old_rejected(self):
        q = payments.create_quote(self.db, PAYER, 'anomaly', 'SOL', now=NOW)
        lamports = int(round(q['amount'] * 10 ** 9))
        self.txs['sig'] = sol_tx(TREASURY, lamports, q['memo'])
        late = NOW + config.QUOTE_TTL_MINUTES * 60 + 1
        r = payments.verify_payment(self.db, 'sig', now=late)
        self.assertFalse(r['verified'])
        self.assertEqual(r['error'], 'quote_expired')
        self.assertEqual(self.entitlement_count(PAYER), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
