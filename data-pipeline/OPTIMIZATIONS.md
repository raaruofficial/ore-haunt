# OPTIMIZATIONS — measured findings for the $0 ORE data pipeline

Every number below was measured live on 2026-10-12 against the public RPC
(`https://api.mainnet-beta.solana.com`) or the official ORE API. Nothing is
estimated from docs; where a figure is an extrapolation it says so.

## 0. The headline

**The cheapest data source is the account state the program itself
maintains — and the single most valuable capture in the whole system
costs 1 RPC call per round**: a `getProgramAccounts` snapshot of Miner
accounts taken the moment a round settles reproduces that round's
per-wallet deploy census with its SOL total matching the Round account
**to the lamport**. Transaction scanning — the expensive pattern this
pipeline replaces — is demoted to a burst-only fallback at ~99% fidelity.

## 1. Ground truth first: the layout proof

V4 did not change the wire format that matters. The deployed program is
still `oreV3EG1i9BEgiAJ8b177Z2S2rMarzak4NMv1kULvWv`, upgraded in place;
verify.osec.io binds the on-chain hash to regolith-labs/ore commit
`48c203bd` (verified 2026-10-02). Source at that commit + live bytes agree:

| Account | Size | Disc | Fields used |
|---|---|---|---|
| Board | 40 B | 105 | round_id, start_slot, end_slot, production_cost_ema |
| Round | 952 B | 109 | id, deployed[25], mass[25], count[25], slot_hash, expires_at, motherlode, rent_payer, rewards[25], total_vaulted, total_returned_sol, total_miners, **top_miner (new in V4, trailing 32 B)** |
| Miner | 752 B | 103 | authority @8, deployed[25] @64, round_id @664, rewards_sol @688, rewards_ore @704, lifetime_deployed @736 |

Proofs, in increasing strength:

1. Live decode of Board + two settled rounds: every internal-consistency
   gate passes; parse consumes exactly 952/952 bytes.
2. **Round-trip**: all **2,433 / 2,433** frozen V3 corpus rows re-encoded
   into account bytes and decoded back to identical records.
3. **Invariant**: on every validated V4 round (100-round backfill +
   samples), `sum(rewards) == 100_000_000_000` = exactly 1 ORE in base
   units — the per-round emission, visible in the raw account.
4. **Cross-check vs the official API**: 11 consecutive rounds compared
   against `api.ore.com/events/reset` — winner, total deployed (to the
   lamport), motherlode, and miner count agree **11 / 11**.

Chain facts measured the same day: round = 240 slots; consecutive-round
blockTime deltas 52 s and 54 s; 5-day average 73.5 s/round (≈1,175
rounds/day); slot ≈ 0.22–0.24 s. Numbering continued unbroken from V3
(corpus ends 431460; live tip was 437987+). Round accounts expire at
`end_slot + 288,000 slots` (≈ +17.5 h), so the live backfill window is
roughly the last ~1,500 rounds.

## 2. Tier 1 — round aggregates

| Operation | Calls (measured) |
|---|---|
| Dry-run (Board + latest Round) | **2** |
| Backfill 5 rounds | **1** (`getMultipleAccounts`) |
| Backfill 100 rounds | **1** |
| Same 100 accounts, naive per-account loop | **100 calls, 42.0 s** |
| Catch-up from 4 rounds behind | 2 (+1 gPA per snapshotted round) |

Steady state ≈ **2 calls/round** (1 Board poll, cadence-aligned to
start/end slots, + 1 batched fetch). At the measured ~1,175 rounds/day
that is ≈ 2,350 calls/day ≈ **70k credits/month at 1 credit/call,
705k at 10**. A push trigger (below) removes the Board poll, leaving
≈ 2 calls/round all-in with snapshots.

## 3. Tier 2 — per-wallet deploys, four strategies costed

Reference round 437986 (159 miners, 9.9396 SOL deployed), all measured:

| Strategy | Calls | Result | Fidelity |
|---|---|---|---|
| **Miner snapshot at the flip** (gPA memcmp `round_id`, §4) | **1** | 162 accounts; Σ deployed **== Round total exactly** | census-grade for SOL; account count ran +2.5% vs `total_miners` (162 vs 158 — snapshot is a superset) |
| Round-scoped tx scan (sigs for Round PDA + getTransaction) | 98 | 219 events, 155/159 wallets | 98.7% of deployed SOL; Δ1.33% |
| Wallet×round intersection (top 20 wallets) | 57–68 | 84 events for those wallets | **identical** to round scan for the same wallets (84/84 after the attribution fix) |
| Program-wide scan (for contrast) | ~8,450 tx/day program-wide | — | the pattern to avoid |

Two traps found and fixed by measurement:

- **Batch contamination.** Automation keepers fire checkpoint(prev
  round) + deploy(next round) in ONE transaction. A parser that trusts
  the signature's account list attributes the deploy leg to the wrong
  round (it produced +45 phantom events before the fix). Rule now
  enforced in `scanner.parse_deploys`: the instruction's own round
  account (`accounts[6]`) must equal the target round's PDA.
- **Signature-index latency.** Scans run seconds after settlement see an
  incomplete signature list (44 ok sigs at +1 min vs 96 later on the
  same round). Burst scans should run ≥ 5 min after close.

Residual tx-scan error (~1%) is structural: automation strategies
(Preferred / Random) can override amount/mask on-chain, so the
instruction records the executor's *request*. The deterministic
Random-mask derivation is published in the program source, so a perfect
reconstructor is possible — it is bounded, optional work; the snapshot
already provides the exact census when captured live.

**Snapshot decay (measured)**: 162 accounts at +0.6 s after the flip →
27 still parked one round later → 8 across four rounds at lag 1–4 →
**0 at +50 rounds**. Miner accounts roll forward on the miner's next
action, and miners must checkpoint before redeploying. Conclusion:
the census capture only works as a live, flip-triggered step inside
the poller (`poller.py --snapshot-miners`). It cannot be recovered
retroactively — for missed rounds, tx scanning is the fallback.

`getProgramAccounts` on the public endpoint with `dataSize` + `memcmp`
filters answered in **0.5–0.7 s** per call in every probe — no premium
endpoint required. A single-wallet lookup (memcmp on `authority`, offset
8) is the same 1 call and returns that wallet's current-round deploys
plus its **unclaimed** `rewards_sol` / `rewards_ore` — a live payout
ledger for tracked wallets, free.

## 4. Push transports

- **Solana websocket (public endpoint): WORKS.** Minimal stdlib client
  (`ws_client.py`; RFC 6455 by hand, CONNECT-tunnel aware for proxied
  egress). `accountSubscribe` on Board: initial notification in 0.14 s,
  then **25 notifications / 70 s** (batched deploy txs rewrite Board).
  Zero polling in steady state — the notification *is* the flip alarm.
- `programSubscribe` unfiltered: **432 notifications / 70 s (6.2/s)** —
  a firehose of every Miner write. Useful filtered (memcmp on
  Miner.round_id), wrong as a default.
- **Official SSE mirror**: `https://api.ore.com/connect` answers
  `Content-Type: text/event-stream` — the project's own live event
  stream exists (see §5 for the dependence caution).

## 5. Public mirrors audit (api.ore.com and friends)

The official app is a Rust/WASM frontend; its backend is
`api.ore.com`. Probed live:

| Endpoint | Returns | Grade |
|---|---|---|
| `/events/reset` | last **12** rounds: winning_square, total_deployed, total_winnings, total_minted, motherlode, rng, top_miner, ts | **Cross-check: excellent** (11/11 agreement, §1). Bootstrap: no — 12 deep |
| `/events/deploys/{authority}` | last **12** deploys for a wallet, fully annotated (per-square amount, mask, round, winner) | **Wallet spot-checks: excellent**; history: no |
| `/events/motherlode` | recent motherlode hits (sample pool: 13,760,000,000,000 raw = 137.6 ORE) | niche cross-check |
| `/connect` | SSE live event stream | live tail, if ever needed |

Caution, stated plainly: this API is undocumented, project-operated,
12 events deep, and carries social/profile fields — it is a *mirror*,
never a foundation. Depending on it for the primary feed would trade a
protocol guarantee (account state) for a company's uptime and goodwill,
and bulk scraping someone else's backend is poor citizenship besides.
Use it the way this doc does: independent cross-checks and spot checks.

**Dune / Flipside**: both operate Solana catalogs with free tiers, and
Dune's dataset-discovery shows decoded/spell table classes for Solana —
but access is API-key gated and credit-capped, and no maintained
ORE-specific decoded table was found. Grade: **ad-hoc cross-check /
research only**, not a $0 live feed.

## 6. Claim payouts — reconstructible, with one hard limit

Proven on a real winner's transactions: a combined Checkpoint+ClaimORE
transaction moved **+305,353,562,724 raw ORE (3.0535 ORE)** to the
wallet, readable exactly from pre/post token-balance deltas; ClaimSOL
is the same shape in lamports. The limit is attribution: claims pay
out the Miner account's accumulated `rewards_ore`, which aggregates
**many rounds** (that sample was ~3 ORE — far more than one round's
winnings), and the per-round step (Checkpoint, disc 2) moves no wallet
funds at all. So: per-wallet *realized* payout totals are exact from
claim txs; per-round payout attribution from claims alone is
impossible — it needs the Miner-account reward balances observed
around each checkpoint, which the snapshot layer provides while the
miner is parked on that round. Unclaimed winnings are invisible to
claim scanning by definition; they are exactly what
`Miner.rewards_sol / rewards_ore` shows.

## 7. Deep-history artillery (the gap and the pre-corpus past)

The frozen corpus ends at round 431460 (2026-10-07); the live retention
window reaches back only ~1,500 rounds. Rounds 431461–~437900 and
everything before 427910 are **gone from RPC** — Round accounts were
closed and rent reclaimed. The one real path back is Project Old
Faithful (rpcpool/yellowstone-faithful): full-history archives as
per-epoch CAR files (100s of GB per epoch) distributed via Filecoin,
with indexes (slot→cid, tx→cid, **gsfa: address→signatures**, sig-exists,
slot→blocktime) and a `faithful-cli` server that speaks RPC —
including `getSignaturesForAddress` — over the archive. A backfill of
the gap (≈ slots 454.6M–456.1M, ~2–3 epochs) means: pull those epoch
CARs + gsfa indexes (budget ~0.3–1 TB and hours of transfer/indexing),
stand up the local faithful RPC, and point this repo's scanner at it —
the extraction code does not change. Verdict: **real, heavy, and
worth it exactly once** if the Oct 7–12 gap ever matters analytically;
not part of the running pipeline.

## 8. Recommended production configuration

1. **Watch-mode poller on any always-on $0 host** (owner's VM / this
   environment): `poller.py --ws --snapshot-miners` — websocket flip
   trigger (0 polls), 1 batched fetch + 1 snapshot per round.
   ≈ 2 calls/round ≈ 2,350/day ≈ **70k credits/month @1 credit,
   705k @10** — and on the public endpoint there is no credit meter
   at all, just rate politeness.
2. **GitHub Actions catch-up every 15–30 min** (`.github/workflows/
   poll.yml`) as the resilience layer: survives host loss, idempotent,
   commits data + state as an audit trail. Snapshots taken there are
   stale (documented in each record's `capture_lag_rounds`); rounds
   themselves are never lost inside the ~17.5 h retention window.
3. **Tier-2 deep scans burst-only**, on anomaly-flagged rounds (~20%
   of rounds in the frozen window), ≥ 5 min after close: ~98
   calls/round. At ~235 flagged rounds/day ≈ 23k calls/day if run
   daily; batching weekly keeps it trivially inside any free tier.
4. **api.ore.com as CI cross-check** (winner agreement on overlap),
   never as a data source of record.

Expected all-in live cost at the recommended settings: **$0** — public
RPC + free compute, with the credit math above showing 7–14× headroom
even if a metered free tier is substituted.

## 9. Session call ledger (auditable)

All live chain work for this build, tallied from the tools' own
counters (probe scripts) and `ore_rpc.LEDGER` prints (poller/scanner):

| Phase | RPC calls | of which getTransaction |
|---|---|---|
| Layout probes (Board/Round/Miner, gPA) | ~85 | 0 |
| Deploy/claim parsing probes | ~270 | ~265 |
| Scanner measurements (2 rounds × strategies) | ~430 | ~390 |
| Flip/snapshot probe | 55 | 46 |
| WS feasibility | 0 (2 WS subscriptions, ~457 notifications) | — |
| Poller dry-run / backfills / catch-up | 110 | 0 |
| **Total** | **~950** | **~700** |

Plus ~20 HTTPS calls (GitHub raw/API, api.ore.com, ore.supply) that are
not RPC credits at all.
