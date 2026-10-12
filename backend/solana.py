"""Solana JSON-RPC reads + transaction accounting (stdlib urllib only).

The delta semantics deliberately mirror the audited HAUNT client verifier:
- SOL: treasury lamport delta = postBalances[i] - preBalances[i], where i is
  the treasury's index in the transaction's accountKeys.
- ORE: sum over ALL postTokenBalances entries matching (mint, owner) minus
  the same sum over preTokenBalances. A payment split across several of the
  treasury's token accounts counts in full — first-match would undercount.
Amounts are Python ints (arbitrary precision): no float ever touches a
token balance.

Payer identification is a separate, stricter step (instruction_payer_*):
it parses transfer instructions — including inner/CPI instructions — and
binds only a signer whose own transfers INTO the treasury reach the
expected amount. Balance deltas survive only as the amount gate above
and as a narrow, signer-only fallback for payments whose instructions
cannot be resolved (documented on _fallback_payer).
"""
import json
import urllib.request

import config

MEMO_PROGRAM_IDS = {
    'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr',   # Memo v2
    'Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQFxFMNo',    # Memo v1
}


def _rpc(method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                       'method': method, 'params': params}).encode()
    req = urllib.request.Request(
        config.RPC_URL, data=body,
        headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    if payload.get('error'):
        return None
    return payload.get('result')


def get_transaction(sig):
    """Fetch a parsed transaction; None if not found / RPC error."""
    if not sig:
        return None
    try:
        return _rpc('getTransaction',
                    [sig, {'encoding': 'jsonParsed',
                           'maxSupportedTransactionVersion': 0}])
    except Exception:
        return None


def account_keys(tx):
    """Pubkeys (strings) of the transaction message, in balance order."""
    try:
        keys = tx['transaction']['message']['accountKeys']
    except (TypeError, KeyError):
        return []
    out = []
    for k in keys:
        out.append(k.get('pubkey') if isinstance(k, dict) else k)
    return out


def sol_delta(tx, wallet):
    """Lamports gained by `wallet` in this tx (post - pre). 0 if absent."""
    keys = account_keys(tx)
    if wallet not in keys:
        return 0
    i = keys.index(wallet)
    meta = tx.get('meta') or {}
    pre = meta.get('preBalances') or []
    post = meta.get('postBalances') or []
    if i >= len(pre) or i >= len(post):
        return 0
    return int(post[i]) - int(pre[i])


def _token_sum(balances, mint, owner):
    total = 0
    for entry in balances or []:
        if entry.get('mint') == mint and entry.get('owner') == owner:
            total += int(entry['uiTokenAmount']['amount'])
    return total


def ore_delta(tx, treasury=None, mint=None):
    """ORE base units gained by the treasury (default: config treasury/mint).

    Sums ALL matching token accounts, pre subtracted from post — the exact
    semantics of the audited client verifier, in integer base units.
    """
    treasury = config.TREASURY if treasury is None else treasury
    mint = config.ORE_MINT if mint is None else mint
    meta = tx.get('meta') or {}
    post = _token_sum(meta.get('postTokenBalances'), mint, treasury)
    pre = _token_sum(meta.get('preTokenBalances'), mint, treasury)
    return post - pre


SYSTEM_PROGRAM_ID = '11111111111111111111111111111111'
TOKEN_PROGRAM_IDS = {
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',    # SPL Token
    'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',   # Token-2022
}


def signer_set(tx):
    """Pubkeys that signed this tx. accountKeys[0] is the fee payer and
    always a signer, even if a payload omits the flag."""
    out = set()
    try:
        keys = tx['transaction']['message']['accountKeys']
    except (TypeError, KeyError):
        return out
    for i, k in enumerate(keys):
        if isinstance(k, dict):
            if k.get('signer') or i == 0:
                if k.get('pubkey'):
                    out.add(k['pubkey'])
        elif i == 0 and isinstance(k, str):
            out.add(k)
    return out


def all_instructions(tx):
    """Every parsed instruction: top-level message instructions plus all
    inner (CPI) instructions — a payment routed through a program still
    shows its transfers here."""
    out = []
    message = (tx.get('transaction') or {}).get('message') or {}
    out.extend(message.get('instructions') or [])
    meta = tx.get('meta') or {}
    for inner in meta.get('innerInstructions') or []:
        out.extend(inner.get('instructions') or [])
    return out


def _parsed_info(ix):
    parsed = ix.get('parsed')
    if isinstance(parsed, dict):
        info = parsed.get('info')
        if isinstance(info, dict):
            return parsed.get('type'), info
    return None, None


def sol_instruction_contributions(tx, treasury):
    """Lamports sent INTO the treasury per source account, summed over
    every system 'transfer' instruction (top-level and inner) whose
    destination is the treasury."""
    contrib = {}
    for ix in all_instructions(tx):
        if not isinstance(ix, dict):
            continue
        if (ix.get('program') != 'system'
                and ix.get('programId') != SYSTEM_PROGRAM_ID):
            continue
        itype, info = _parsed_info(ix)
        if itype != 'transfer' or info.get('destination') != treasury:
            continue
        source = info.get('source')
        try:
            lamports = int(info.get('lamports'))
        except (TypeError, ValueError):
            continue
        if source and lamports > 0:
            contrib[source] = contrib.get(source, 0) + lamports
    return contrib


def token_account_map(tx):
    """Token account address -> (owner, mint), resolved from the tx's
    pre/post token balances. Both snapshots are consulted (pre first, post
    wins): an account CLOSED by this tx appears only in preTokenBalances,
    an account created by it only in postTokenBalances — either way its
    owner is resolvable."""
    keys = account_keys(tx)
    meta = tx.get('meta') or {}
    out = {}
    for balances in (meta.get('preTokenBalances'),
                     meta.get('postTokenBalances')):
        for entry in balances or []:
            idx = entry.get('accountIndex')
            if not isinstance(idx, int) or idx >= len(keys):
                continue
            owner, mint = entry.get('owner'), entry.get('mint')
            if owner and mint:
                out[keys[idx]] = (owner, mint)
    return out


def ore_instruction_contributions(tx, mint, treasury):
    """ORE base units sent INTO treasury-owned token accounts, summed per
    SOURCE OWNER, over every token 'transfer'/'transferChecked'
    instruction (top-level and inner).

    A contribution counts only when the destination account resolves —
    via the tx's own token balances — to (owner == treasury, mint ==
    mint): tokens landing in an account we cannot prove is the
    treasury's are not payment. Returns (contributions, unresolvable),
    where `unresolvable` is the amount whose destination checked out but
    whose SOURCE account's owner could not be resolved from the balance
    snapshots at all.
    """
    accounts = token_account_map(tx)
    contrib = {}
    unresolvable = 0
    for ix in all_instructions(tx):
        if not isinstance(ix, dict):
            continue
        if (ix.get('program') != 'spl-token'
                and ix.get('programId') not in TOKEN_PROGRAM_IDS):
            continue
        itype, info = _parsed_info(ix)
        if itype not in ('transfer', 'transferChecked'):
            continue
        dest = accounts.get(info.get('destination') or '')
        if dest is None or dest[0] != treasury or dest[1] != mint:
            continue
        if itype == 'transferChecked':
            if info.get('mint') not in (None, mint):
                continue
            token_amount = info.get('tokenAmount') or {}
            raw = token_amount.get('amount')
        else:
            raw = info.get('amount')
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        src = accounts.get(info.get('source') or '')
        if src is None:
            unresolvable += amount
            continue
        contrib[src[0]] = contrib.get(src[0], 0) + amount
    return contrib, unresolvable


def _fallback_payer(tx, currency, treasury, mint, min_magnitude):
    """NARROW balance-diff fallback for payer identification.

    Engaged ONLY when instruction parsing produced no qualifying
    candidate (see instruction_payer_sol / instruction_payer_ore). It
    considers balance deltas of SIGNER accounts only, and binds ONLY when
    exactly one signer has a qualifying negative delta (>= min_magnitude)
    — zero or two-or-more qualifying signers means no binding. This is
    what keeps a co-signed tx from ever binding the wrong signer, and a
    swap-routed payment (value arrives from a non-signer pool) unbindable:
    in both cases the fallback finds either the true lone payer or nobody.
    """
    signers = signer_set(tx)
    qualifying = []
    if currency == 'SOL':
        keys = account_keys(tx)
        meta = tx.get('meta') or {}
        pre = meta.get('preBalances') or []
        post = meta.get('postBalances') or []
        for i, key in enumerate(keys):
            if key == treasury or key not in signers:
                continue
            if i >= len(pre) or i >= len(post):
                continue
            delta = int(post[i]) - int(pre[i])
            if delta < 0 and -delta >= min_magnitude:
                qualifying.append(key)
    else:
        for owner, delta in ore_owner_deltas(tx, mint).items():
            if owner == treasury or owner not in signers:
                continue
            if delta < 0 and -delta >= min_magnitude:
                qualifying.append(owner)
    return qualifying[0] if len(qualifying) == 1 else None


def instruction_payer_sol(tx, treasury, min_magnitude):
    """Identify who PAID in SOL, from transfer instructions.

    The payer is a SIGNER whose own system-transfer instructions into the
    treasury sum to at least `min_magnitude` lamports (the caller passes
    99% of the expected amount). Shuffle volume elsewhere in the tx is
    irrelevant — only transfers INTO the treasury count, so a co-signer
    moving more SOL around cannot steal the binding. Swap-routed payments
    (contributions sourced from a non-signer pool) yield no candidate and
    fail closed via the narrow fallback (which will also find nobody).
    Returns the payer's pubkey or None.
    """
    contrib = sol_instruction_contributions(tx, treasury)
    signers = signer_set(tx)
    best = None
    best_amt = 0
    for source, amount in contrib.items():
        if source in signers and amount >= min_magnitude \
                and amount > best_amt:
            best, best_amt = source, amount
    if best is not None:
        return best
    return _fallback_payer(tx, 'SOL', treasury, None, min_magnitude)


def ore_owner_deltas(tx, mint):
    """Net ORE base-unit delta per token-account OWNER (post - pre).

    Sums every token-balance entry for `mint` by owner, so a payer paying
    from several of their own token accounts shows one combined (negative)
    delta, and the treasury's gain appears under the treasury's owner key.
    """
    meta = tx.get('meta') or {}
    deltas = {}

    def acc(balances, sign):
        for entry in balances or []:
            if entry.get('mint') != mint:
                continue
            owner = entry.get('owner')
            if not owner:
                continue
            deltas[owner] = (deltas.get(owner, 0)
                             + sign * int(entry['uiTokenAmount']['amount']))

    acc(meta.get('postTokenBalances'), +1)
    acc(meta.get('preTokenBalances'), -1)
    return deltas


def instruction_payer_ore(tx, mint, treasury, min_magnitude):
    """Identify who PAID in ORE, from transfer instructions.

    The payer is a SIGNER who owns the source token account(s) whose
    transfers into treasury-owned accounts sum to at least
    `min_magnitude` base units. Contributions are summed per owner, so a
    payer splitting across two of their own accounts still binds (and a
    payment split across two treasury accounts still counts in full). A
    source account closed by the tx resolves through preTokenBalances, so
    closing the source after paying does not break the binding. When the
    only contributions come from a non-signer (e.g. a swap pool routing
    the payment), there is no candidate and the narrow signer-only
    fallback decides — normally finding nobody, failing closed.
    Returns the payer's wallet (token-account owner) or None.
    """
    contrib, _unresolvable = ore_instruction_contributions(
        tx, mint, treasury)
    signers = signer_set(tx)
    best = None
    best_amt = 0
    for owner, amount in contrib.items():
        if owner in signers and amount >= min_magnitude \
                and amount > best_amt:
            best, best_amt = owner, amount
    if best is not None:
        return best
    return _fallback_payer(tx, 'ORE', treasury, mint, min_magnitude)


def _memo_from_instruction(ix):
    if not isinstance(ix, dict):
        return None
    program = ix.get('program')
    program_id = ix.get('programId')
    if program != 'spl-memo' and program_id not in MEMO_PROGRAM_IDS:
        return None
    parsed = ix.get('parsed')
    if isinstance(parsed, str) and parsed:
        return parsed
    data = ix.get('data')
    if isinstance(data, str) and data:
        # Partially-decoded instruction: data is base58-encoded UTF-8.
        try:
            from auth import b58decode
            return b58decode(data).decode('utf-8')
        except Exception:
            return None
    return None


def extract_memos(tx):
    """All memo strings carried by the tx (instructions + log messages)."""
    memos = []

    def add(m):
        if m and m not in memos:
            memos.append(m)

    message = (tx.get('transaction') or {}).get('message') or {}
    for ix in message.get('instructions') or []:
        add(_memo_from_instruction(ix))
    meta = tx.get('meta') or {}
    for inner in meta.get('innerInstructions') or []:
        for ix in inner.get('instructions') or []:
            add(_memo_from_instruction(ix))
    for line in meta.get('logMessages') or []:
        # e.g. 'Program log: Memo (len 24): "HAUNT:abc123"'
        if 'Memo (len' in line:
            first = line.find('"')
            last = line.rfind('"')
            if 0 <= first < last:
                add(line[first + 1:last])
    return memos


def find_memo(tx):
    memos = extract_memos(tx)
    return memos[0] if memos else None
