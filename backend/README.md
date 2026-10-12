# HAUNT Backend

The single server behind everything ORE HAUNT sells: payment verification
and entitlements, the rolling-24h anomaly slot ledger, and `.skr` wallet
auth. It is also the settlement layer the HAUNT Data API's paid tier (P2)
plugs into. Python 3, **stdlib only** (sqlite3 for state,
ThreadingHTTPServer for HTTP) — no pip dependencies.

## Run

```
python3 server.py [port]        # default 8790
python3 test_all.py             # full test suite (stubbed RPC + prices)
```

## Configuration (environment)

| Var              | Default                                 | Meaning |
|------------------|-----------------------------------------|---------|
| `HAUNT_TREASURY` | *(unset)*                               | Treasury wallet (base58) that receives payments. |
| `HAUNT_RPC`      | `https://api.mainnet-beta.solana.com`   | Solana JSON-RPC endpoint for tx verification. |
| `HAUNT_DB`       | `./haunt.db` next to the code           | SQLite database path (WAL mode). |

## The honest-unset rule

**While `HAUNT_TREASURY` is unset, settlement is impossible by
construction** — not by convention:

- `POST /v1/pay/quote` answers `503 {"error":"payments_not_configured"}`.
- `POST /v1/pay/verify` answers 503 for any signature; nothing is consumed
  and no entitlement can be granted.
- `GET /v1/health` reports `treasury_configured: false`.

Setting a treasury is the operator's deliberate act; there is no fallback
treasury and no fallback price anywhere (if the Jupiter price fetch fails,
quotes refuse with `price_unavailable` rather than inventing a rate —
the same rule the HAUNT client's `orePrice()` follows).

## Schema note

`quotes.wallet` and `slot_holds.wallet` are nullable (anonymous quotes).
Databases created before that amendment are migrated in place on open:
the two tables are rebuilt from their own stored DDL with just the
wallet column's `NOT NULL` removed — rows, defaults and the memo UNIQUE
constraint survive verbatim. Later columns (`quotes.source_ip`,
`slot_holds.source_ip`, `entitlements.note`) are added when missing.
ALL migrations run inside a single transaction on open, so a failed or
interrupted migration changes nothing.

## Endpoints

| Route | Body / Params | Behavior |
|---|---|---|
| `GET /v1/health` | — | `{ok, treasury_configured, snapshot:'backend-1'}` |
| `POST /v1/pay/quote` | `{tier, currency, wallet?}` | Prices the tier (SOL or ORE, live Jupiter v3 rate; ORE rounded to 6dp like the client), stores a quote with memo `HAUNT:<request_id>`, 30-min TTL. **Wallet is optional for most tiers** — the HAUNT web client has no connected wallet and quotes anonymously; the entitlement later binds to the payer bound from the payment transaction. **The anomaly tier requires a wallet** (`400 wallet_required` without one): its slot hold is bound to the wallet AND the source IP (one live hold each), and anomaly quotes are rate-limited to 20 attempts/hour/IP (`429 rate_limited`). Anomaly tier first claims a slot hold and the quote expires with the hold (2 min). Sold out → 409 with `next_free_at`. |
| `POST /v1/pay/verify` | `{signature}` | Verifies the on-chain payment (see below) and grants the entitlement — to the payer bound from the tx — atomically. Anomaly responses include `pass_state`: `'active'`, or `'refund_due'` when the slot could not be honored (see Slots). Memo-only bodies get `400 signature_required`. |
| `GET /v1/entitlements/{wallet}` | — | Active entitlements + `active_tier` by priority anomaly > lifetime > monthly > day. |
| `POST /v1/auth/challenge` | `{wallet}` | `{nonce, message}` with message `haunt-auth:{nonce}:{wallet}`. |
| `POST /v1/auth/verify` | `{wallet, nonce, signature}` | Ed25519 verify (pure Python, RFC 8032). Single-use nonce, 5-min TTL. Signature may be base58 or base64. |
| `POST /v1/slots/claim` | `{wallet}` | Atomic slot claim; 409 when sold out or the wallet already holds one. |
| `POST /v1/slots/release` | `{hold_id}` | Frees a live hold. |
| `GET /v1/slots/status` | — | `{active, holds, cap, next_free_at}`. |

Bad input → 400 with a specific `error`; unknown routes → 404.

## Verification semantics (mirror the audited HAUNT client)

- The tx is located by quote memo (memo-program instruction, parsed or
  base58, or the memo log line). **All** memos in the tx are considered:
  a pending, unstale quote wins even when a stale or consumed quote's
  memo appears earlier in the same tx.
- SOL: treasury lamport delta = post − pre at the treasury's accountKeys
  index.
- ORE: sum over **all** `postTokenBalances` matching (ORE mint, treasury
  owner) minus the matching pre sum — a payment split across several of the
  treasury's token accounts counts in full. Integer base units throughout.
- A payment passes the AMOUNT gate at ≥ 99% of the quoted amount
  (`actual*100 >= expected*99`, exact integer math).
- **Payer binding (instruction-derived)**: the entitlement binds to
  whoever actually paid, read from the transaction's transfer
  instructions (top-level and inner/CPI) — never to a client-claimed
  wallet and never from balance-diff heuristics. SOL: a **signer** whose
  own system `transfer`s into the treasury sum to ≥ 99% of expected.
  ORE: a **signer** who owns the source token account(s) whose
  `transfer`/`transferChecked`s into treasury-owned token accounts sum
  (per owner) to ≥ 99%; destination accounts must resolve — via the tx's
  own token-balance snapshots — to the treasury and the ORE mint, and a
  source account closed by the tx resolves through `preTokenBalances`.
  Consequences: a co-signer shuffling larger amounts elsewhere cannot
  steal the binding (only transfers INTO the treasury count), and a
  swap-routed payment (value arrives from a non-signer pool) has no
  bindable payer — verification rejects with `payer_not_found`, nothing
  granted, signature NOT consumed. One narrow fallback remains: if
  instruction parsing yields no candidate at all, balance deltas of
  SIGNER accounts only are consulted, and binding happens only when
  **exactly one** signer has a qualifying negative delta. When a quote
  carries a wallet and it differs from the bound payer, the bound payer
  wins; the response reports both (`wallet` = bound, `claimed_wallet` =
  the quote's).
- Signature consumption, the entitlement grant, and the anomaly slot
  settlement happen in **one SQLite transaction**: replays are rejected
  with no second grant, and a failed check consumes nothing.

## Slots (rolling 24h)

Cap = 100 (config `SLOT_CAP`) counting active passes + live holds at any
instant. Holds expire after **2 minutes** unpaid (config
`SLOT_HOLD_MINUTES`) — long enough to finish an already-open wallet
payment, short as a griefing window; passes occupy their slot until
`pass_expires_at` (grant + 24h for the anomaly tier) — a slot frees
exactly 24h after purchase, never at a calendar boundary. Claims are
single-transaction check-and-insert, so concurrent claims cannot
overshoot.

Holds are cost/identity-bound, because a free anonymous hold is a free
denial-of-service on a scarce inventory:

- An anomaly **quote requires a wallet** — one live hold per wallet.
- **One live hold per source IP** (the server passes the request IP into
  every claim), so wallet rotation from one address cannot multiply
  reservations.
- **20 anomaly-quote attempts per hour per IP** (`429` past that), so
  hold churn through the 2-minute TTL is capped too.
- The HAUNT web client has no wallet yet, so its anomaly quotes receive
  `wallet_required` and it falls back to its local flow (the honest
  pre-binding behavior) until Seed Vault wallet binding lands client-side.

**Verify-time re-check + `refund_due`:** an anomaly quote's `expires_at`
is the hold's deadline, not the payment's — a payment whose tx blockTime
(or verify time) falls within 30 minutes of the quote's creation still
settles. The slot is then re-decided inside the grant transaction: the
hold converts if still live; if it lapsed, a fresh slot is claimed for
the payer when capacity remains; if capacity is gone, the grant is still
recorded — the buyer did pay — with `note = 'refund_due'` on the
entitlement and `pass_state: 'refund_due'` in the verify response, so
operations can refund or honor it manually. Nothing is silently lost.

## Who plugs in

- **ORE HAUNT client** — ships with `PAYMENTS.backend = null` (gated,
  behavior-identical when off). Set it to this server's base URL and the
  activation flow runs end-to-end: at pay time `payNow()`/`payNowOre()`
  take a quote from `POST /v1/pay/quote` and put the returned
  `HAUNT:<request_id>` memo in the Solana Pay link (an in-flight guard
  disables the pay buttons during the quote fetch, so a double-tap can't
  fire two quotes); at verify time `verifyPay()` forwards each discovered
  transaction signature to `POST /v1/pay/verify` and accepts a
  `{verified:true}` through its normal success path. Any backend failure
  falls back to the existing on-device verification — **except** a
  sold-out anomaly quote, which shows the sold-out state instead of
  falling back (a local payment would bypass the slot cap). The client's
  anomaly quotes currently end at `wallet_required` (it has no wallet to
  bind the hold to) and take that same local fallback — by design, until
  client-side Seed Vault binding exists.
- **HAUNT Data API (P2)** — its `settle()` gate is this server's
  quote/verify pair: API calls will be quoted and settled against the same
  consumed-signature ledger, so a signature can never buy both an API call
  and an entitlement.
- **Chat transport (future)** — auth challenges here are the identity
  rail a live chat backend will authenticate against.
