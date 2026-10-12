# HAUNT $0 ORE Data Pipeline

Collects ORE round history (and per-wallet deploy state) from Solana
mainnet for **$0**: public RPC only, account-state reads instead of
transaction scanning, stdlib-only Python. Read-only — no keys, no
signing, no writes to chain.

This supersedes the V3-era `ore-indexer` cron pipeline (which stays
disabled). Measured design rationale lives in
[OPTIMIZATIONS.md](OPTIMIZATIONS.md) — every cost figure there was
measured live, not taken from docs.

## Why it costs nothing

- **Tier 1 (rounds)** reads two accounts per round — the Board and the
  settled Round account — batched at 100 accounts/call. ~2 RPC
  calls/round; a 100-round backfill is **1 call**.
- **Tier 2 (per-wallet)** is captured, not scanned: at the moment a
  round settles, one `getProgramAccounts` snapshot of Miner accounts
  reproduces the round's per-wallet census exactly (it decays within
  minutes, so the poller takes it live). Transaction scanning survives
  only as a burst tool for rounds the snapshot missed.
- The one program that runs this is ~1,300 lines of stdlib Python.
  No dependencies, no API keys, no metered endpoints.

## Quick start

```bash
cd ~/workspace/ore-poller
python3 poller.py --dry-run            # fetch + decode + print; write nothing
python3 poller.py --backfill 437901 438000   # explicit range (idempotent)
python3 poller.py --catchup            # one-shot: collect all settled rounds since watermark
python3 poller.py --snapshot-miners --catchup  # catchup + Miner census for new rounds
python3 poller.py                      # watch mode (HTTP, cadence-aligned to round slots)
python3 poller.py --ws --snapshot-miners     # watch mode, websocket-triggered (recommended)
```

Tier-2 burst scan of one round (writes `data/deploys_v4_<round>.jsonl`):

```bash
python3 scanner.py 437986 --measure    # cost + validate against the Round account, write nothing
python3 scanner.py 437986              # write events
python3 scanner.py 437986 --wallets 20 --snapshot   # strategy comparison + Miner snapshot
```

Config is environment variables: `ORE_RPC_URL` (default public
mainnet-beta), `ORE_PROGRAM_ID` (default the ORE program). A different
RPC (e.g. a free-tier endpoint) drops in without code changes.

## Outputs

| File | Contents |
|---|---|
| `data/rounds_v4.jsonl` | One row per round, **schema-identical to the frozen corpus** (`round, winner, no_entropy, deployed_lamports, total_deployed_sol, miners_per_tile, total_miners, motherlode_raw, slot_hash, expires_at_slot, collected_at`) plus V4 extras (`top_miner, total_vaulted_lamports, total_returned_sol_lamports, rewards_sum_raw, program_era`). Append-ordered by run. |
| `data/miners_v4.jsonl` | Miner snapshots: per-wallet `deployed_lamports[25]`, unclaimed `rewards_sol_lamports` / `rewards_ore_raw`, `lifetime_deployed_lamports`, and `capture_lag_rounds` (0 = taken at the flip; fidelity decays with lag — see OPTIMIZATIONS.md §3). |
| `data/deploys_v4_<round>.jsonl` | Burst-scan deploy events in the frozen deploys-file schema. |
| `state/watermark.json` | Resume pointer; advances only after fsynced writes. |
| `state/journal.jsonl` | Run/gap audit log. Gaps (recycled rounds) are permanent and recorded, never retried forever. |

## How HAUNT / the Data API consume this

Consumers read the JSONL files exactly as they read the frozen corpus
— the round schema is byte-compatible in every shared field, so the
bundle builder and snapshot builder need no changes beyond pointing
at the new files. `program_era` discriminates V4 rows; `top_miner` is
`SpLiT111…` when the round's reward was split (the program's own
sentinel). Miner snapshots join to rounds on `(round, authority)`
and are the exposure/fingerprint input the old deploy-event file used
to provide — with exact totals instead of a ~99% reconstruction.

## Durability model

- **Watch mode** on any always-on host is the primary collector
  (it is the only mode that captures Miner censuses at full fidelity).
- **GitHub Actions** (`.github/workflows/poll.yml`) runs `--catchup`
  every 30 minutes and commits `data/` + `state/` back — the audit
  trail and the host-loss backstop. Rounds are safe for ~17.5 h
  on-chain, so even a dead primary is recoverable twice over.
- Setup for the Action: the repo owner creates the (public) repo,
  adds the workflow file, done — the job needs no secrets
  (`ORE_RPC_URL` secret is optional if a private endpoint is preferred).
  GitHub is **not** authenticated from the build sandbox; repo creation
  is intentionally left to the owner.

## Honesty notes

- The frozen corpus ends at round 431460 (2026-10-07) and V4 account
  retention reaches back only ~1,500 rounds. The rounds in between
  are **not** silently missing — they are gone from RPC, and
  OPTIMIZATIONS.md §7 documents the one real recovery path (Old
  Faithful archives) and its cost.
- Deploy events reconstructed from transactions carry a measured
  ~1% residual vs the Round account (automation strategies can
  override amount/mask on-chain). The Miner snapshot is the exact
  source; the scan is the fallback. Both say so in their outputs.

## Operations (live on the HAUNT host since 2026-10-12)
- `ore-poller.service` (systemd, enabled, Restart=always): `poller.py --ws --snapshot-miners`; falls back to cadence-aligned HTTP polling when the websocket drops — snapshots then land at capture_lag_rounds≈1 instead of 0.
- `ore-poller-catchup.timer`: runs `--catchup --snapshot-miners` every 30 min as backstop; the watermark makes every restart self-healing.
- `ore-scanner.service` (systemd, enabled, Restart=always): `scan_service.py` — Tier-2 continuous deploy-event scanning on top of `scanner.scan_round`. Strictly sequential, one round at a time, on the public endpoint (~100–140 RPC calls and ~50–70 s per round, measured). Rules: a round is scanned only once it is ≥4 rounds behind the Board (signature-index lag); events land per-round in `data/deploys/<round>.jsonl` (temp file + atomic replace, written BEFORE the watermark advances — a crash re-scans exactly one round and duplicates are impossible). State: `state/scanner_watermark.json` (`last_scanned_round`; a fresh start anchors at the oldest round in `rounds_v4.jsonl`), `state/scanner.log` (one line per round/backoff/skip/failure), `state/scanner_failures.jsonl` (a round that errors twice is journaled and skipped — it never wedges the queue). Rate limits (429 / -32429 / "too many requests") trigger journaled exponential backoff 30→60→120→240→300 s and never count as round failures. If the unscanned backlog exceeds 60 rounds the watermark jumps to (eligible − 60) and the skipped range is journaled in the failures file — this valve is suppressed only during the initial fresh-start drain from the oldest collected round, and arms once the backlog first falls to ≤60.
- Canonical units live in `systemd/` (copies installed to /etc/systemd/system). After a VM replacement: re-copy the units, `systemctl daemon-reload && systemctl enable --now ore-poller.service ore-poller-catchup.timer ore-scanner.service`.
- EGRESS PROXY: this sandbox reaches the internet only via its proxy (http_proxy/https_proxy env). The units carry Environment= lines copied from the operator shell. SYMPTOM of a stale/rotated proxy credential: watch.log fills with `SSL: WRONG_VERSION_NUMBER` and the watermark stalls — refresh the Environment lines from a live shell and restart.
