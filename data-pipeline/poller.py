#!/usr/bin/env python3
"""
HAUNT $0 data pipeline — Tier 1 spine (ORE round aggregates).

Reads the ORE Board account; when a round settles, fetches its Round
account (batched, <=100/call), decodes + validates it, and appends one
JSONL row in the frozen-corpus schema to data/rounds_v4.jsonl.

Read-only. No keys, no signing, no writes to chain. Stdlib only.

Design notes (measured 2026-10-12, see OPTIMIZATIONS.md):
  * V4-deployed program == V3 program id; Board/Round layouts are
    byte-compatible with the V3 indexer (plus a trailing top_miner).
    Proven on live bytes and by a 2,433/2,433 corpus round-trip.
  * Steady state ~= 2 RPC calls/round: 1 Board poll (cadence-aligned to
    Board start/end slots) + 1 getMultipleAccounts fetch.
  * Round accounts are recycled ~1 day after close (expires_at): missed
    rounds inside that window are backfillable by id; older ones are
    permanent gaps and are journaled, never retried forever.
  * Crash-safe: watermark advances only after the shard write is fsynced;
    startup reconciliation scans the output file; re-runs are no-ops.

Modes:
  (default)       watch loop (HTTP polling, cadence-aligned)
  --ws            watch loop driven by Board accountSubscribe (fallback: HTTP)
  --catchup       one-shot: collect everything settled since watermark, exit
  --backfill F T  collect explicit round range (idempotent), exit
  --dry-run       fetch + decode + print the latest settled round, write nothing
"""

import argparse
import base64
import fcntl
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ore_rpc
from ore_rpc import (BOARD_ADDR, LEDGER, RpcError, account_bytes, get_account,
                     get_multiple_accounts, parse_board, parse_round,
                     round_address, rpc)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(HERE, "data")
DEFAULT_STATE = os.path.join(HERE, "state")
OUT_NAME = "rounds_v4.jsonl"
SAFETY_LAG = 1          # never touch the in-progress round
SETTLE_BUFFER_S = 3.0   # wait a beat after predicted round end
MAX_SLEEP_S = 120.0
MIN_SLEEP_S = 2.0


# ------------------------------------------------------------------ state

def ensure_dirs(data_dir, state_dir):
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)


def out_path(data_dir):
    return os.path.join(data_dir, OUT_NAME)


def existing_ids(data_dir):
    ids = set()
    p = out_path(data_dir)
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line).get("round")
                except json.JSONDecodeError:
                    continue  # torn tail line from a killed run
                if isinstance(r, int):
                    ids.add(r)
    return ids


def read_watermark(state_dir):
    p = os.path.join(state_dir, "watermark.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f).get("last_round")


def write_watermark(state_dir, last_round):
    p = os.path.join(state_dir, "watermark.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_round": last_round,
                   "updated_at": datetime.now(timezone.utc).isoformat()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, p)


def append_records(data_dir, records):
    if not records:
        return
    with open(out_path(data_dir), "a") as f:
        for rec in records:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def journal(state_dir, entry):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(os.path.join(state_dir, "journal.jsonl"), "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ------------------------------------------------------------------ core

def fetch_board(rpc_url):
    return parse_board(account_bytes(get_account(rpc_url, BOARD_ADDR)))


def collect_range(rpc_url, lo, hi, data_dir, state_dir, have_ids, dry=False):
    """Collect settled rounds [lo, hi]. Returns stats dict.
    Watermark handling is the caller's job (except gaps are journaled here)."""
    stats = {"collected": 0, "gaps": [], "already": 0}
    ids = [i for i in range(lo, hi + 1)]
    todo = [i for i in ids if i not in have_ids]
    stats["already"] = len(ids) - len(todo)
    for start in range(0, len(todo), 100):
        chunk = todo[start:start + 100]
        addrs = [round_address(i) for i in chunk]
        accounts = get_multiple_accounts(rpc_url, addrs)
        records, retry = [], []
        for rid, acc in zip(chunk, accounts):
            rec, gap = parse_round(account_bytes(acc), rid)
            if gap is None:
                records.append(rec)
            elif gap == "account_closed":
                stats["gaps"].append({"round": rid, "reason": gap})
            else:
                retry.append(rid)
        if retry:  # one retry pass for transient parse faults
            addrs = [round_address(i) for i in retry]
            accounts = get_multiple_accounts(rpc_url, addrs)
            for rid, acc in zip(retry, accounts):
                rec, gap = parse_round(account_bytes(acc), rid)
                if gap is None:
                    records.append(rec)
                else:
                    stats["gaps"].append({"round": rid, "reason": gap})
        records.sort(key=lambda r: r["round"])
        if not dry:
            append_records(data_dir, records)
            for r in records:
                have_ids.add(r["round"])
        stats["collected"] += len(records)
    if stats["gaps"] and not dry:
        journal(state_dir, {"event": "gaps", "gaps": stats["gaps"][:50],
                            "gap_count": len(stats["gaps"])})
    return stats


def snapshot_miners_for(rpc_url, round_ids, data_dir, board_round):
    """Tier-2 live capture: Miner accounts still parked on each round.
    Exact per-wallet census if taken within ~1 round of settlement
    (measured: full census at +0.6s, ~17% one round later, 0 at +50).
    Records carry their capture lag so consumers can judge fidelity."""
    import scanner
    path = os.path.join(data_dir, "miners_v4.jsonl")
    written = 0
    for rid in round_ids:
        try:
            miners = scanner.snapshot_miners(rpc_url, rid)
        except RpcError:
            continue
        with open(path, "a") as f:
            for m in miners:
                m = dict(m, round=rid, captured_at_board_round=board_round,
                         capture_lag_rounds=board_round - rid,
                         collected_at=datetime.now(timezone.utc).isoformat())
                f.write(json.dumps(m, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        written += len(miners)
    return written


def catchup(rpc_url, data_dir, state_dir, have_ids, dry=False, want_snapshots=False):
    """Collect every settled round after the watermark. Returns board info."""
    board = fetch_board(rpc_url)
    hi = board["round_id"] - SAFETY_LAG
    wm = read_watermark(state_dir)
    if wm is not None:
        lo = wm + 1
    elif have_ids:
        # File ahead of a lost watermark: resume after the file's max id.
        # (collect_range also skips ids already present, so overlap is safe.)
        lo = max(have_ids) + 1
    else:
        lo = hi  # first ever run: start at the tip; history via --backfill
    if lo <= hi:
        stats = collect_range(rpc_url, lo, hi, data_dir, state_dir, have_ids, dry=dry)
        if want_snapshots and not dry and stats["collected"]:
            stats["snapshots"] = snapshot_miners_for(
                rpc_url, list(range(lo, hi + 1)), data_dir, board["round_id"])
        if not dry:
            write_watermark(state_dir, hi)
        return board, stats
    return board, {"collected": 0, "gaps": [], "already": 0}


# ------------------------------------------------------------------ modes

def mode_dry_run(rpc_url):
    before = LEDGER.total
    board = fetch_board(rpc_url)
    rid = board["round_id"] - SAFETY_LAG
    acc = get_account(rpc_url, round_address(rid))
    rec, gap = parse_round(account_bytes(acc), rid)
    print(f"board: round_id={board['round_id']} start_slot={board['start_slot']} "
          f"end_slot={board['end_slot']} ema={board['production_cost_ema']}")
    if gap:
        print(f"round {rid}: GAP {gap}")
        return 2
    tot = sum(rec["deployed_lamports"])
    print(f"latest settled round {rid}: winner={rec['winner']} "
          f"total={rec['total_deployed_sol']} SOL total_miners={rec['total_miners']} "
          f"motherlode_raw={rec['motherlode_raw']} top_miner={rec['top_miner']}")
    print(f"  per-tile sum check: sum(deployed)={tot} == total_deployed_sol*1e9 "
          f"({tot == round(rec['total_deployed_sol'] * 1e9)})")
    print(f"  miners/tile sum (multiplicity)={sum(rec['miners_per_tile'])} "
          f"max={max(rec['miners_per_tile'])} <= total_miners={rec['total_miners']}")
    print(f"  rewards_sum_raw={rec['rewards_sum_raw']} (1 ORE = {ore_rpc.ONE_ORE})")
    print(f"RPC calls used: {LEDGER.total - before} {LEDGER.summary()['by_method']}")
    return 0


def mode_backfill(rpc_url, lo, hi, data_dir, state_dir):
    have = existing_ids(data_dir)
    stats = collect_range(rpc_url, lo, hi, data_dir, state_dir, have)
    print(f"backfill {lo}..{hi}: {stats['collected']} collected, "
          f"{stats['already']} already present, {len(stats['gaps'])} gaps")
    if stats["gaps"]:
        print("gaps:", stats["gaps"][:20])
    print(f"RPC calls used: {LEDGER.total} {LEDGER.summary()['by_method']}")
    return 0


def mode_catchup(rpc_url, data_dir, state_dir, want_snapshots=False):
    have = existing_ids(data_dir)
    board, stats = catchup(rpc_url, data_dir, state_dir, have,
                           want_snapshots=want_snapshots)
    print(f"catchup: board round {board['round_id']}; {stats['collected']} collected, "
          f"{len(stats['gaps'])} gaps, {stats.get('snapshots', 0)} miner snapshots")
    print(f"RPC calls used: {LEDGER.total} {LEDGER.summary()['by_method']}")
    return 0


def mode_watch(rpc_url, data_dir, state_dir, use_ws=False, want_snapshots=False):
    have = existing_ids(data_dir)
    slot_secs = 0.24  # measured Oct 2026 (~0.22-0.24 s/slot); adapted online
    last_board = None
    last_change_wall = time.time()
    if use_ws:
        try:
            import ws_client
            return _watch_ws(rpc_url, data_dir, state_dir, have, ws_client,
                             want_snapshots)
        except Exception as e:
            print(f"ws transport unavailable ({e}); falling back to HTTP polling",
                  file=sys.stderr)
    print("watching (HTTP, cadence-aligned). Ctrl-C to stop.")
    while True:
        try:
            board = fetch_board(rpc_url)
        except RpcError as e:
            print(f"board fetch failed: {e}; retry in 15s", file=sys.stderr)
            time.sleep(15)
            continue
        now = time.time()
        if last_board and board["round_id"] != last_board["round_id"]:
            # adapt slot-time estimate from observed round progression
            d_rounds = board["round_id"] - last_board["round_id"]
            d_slots = board["start_slot"] - last_board["start_slot"]
            if d_rounds > 0 and d_slots > 0:
                per_round_slots = d_slots / d_rounds
                est = (now - last_change_wall) / d_slots
                if 0.05 < est < 1.0:
                    slot_secs = 0.7 * slot_secs + 0.3 * est
                print(f"round {last_board['round_id']} -> {board['round_id']} "
                      f"({per_round_slots:.0f} slots/round, ~{slot_secs:.3f} s/slot)")
            last_change_wall = now
        last_board = board
        try:
            _, stats = catchup(rpc_url, data_dir, state_dir, have,
                               want_snapshots=want_snapshots)
            if stats["collected"]:
                print(f"collected {stats['collected']} round(s); "
                      f"calls so far {LEDGER.total}", flush=True)
        except RpcError as e:
            print(f"catchup failed: {e}", file=sys.stderr)
        # sleep until just after the current round should end
        slots_left = max(0, board["end_slot"] - board["start_slot"])
        # we don't know the current slot without another call; estimate from
        # elapsed wall time since the round was first seen.
        elapsed = time.time() - last_change_wall
        remain = slots_left * slot_secs - elapsed + SETTLE_BUFFER_S
        time.sleep(min(MAX_SLEEP_S, max(MIN_SLEEP_S, remain)))


def _watch_ws(rpc_url, data_dir, state_dir, have, ws_client, want_snapshots=False):
    ws_url = rpc_url.replace("https://", "wss://").replace("http://", "ws://")
    print(f"watching via websocket {ws_url} (accountSubscribe on Board)")
    client = ws_client.WsClient(ws_url)
    sub = client.subscribe_account(BOARD_ADDR)
    print(f"subscribed: {sub}")
    while True:
        note = client.next_notification(timeout=180)
        if note is None:
            # silence for 3 min is abnormal (rounds are ~1 min): re-sync once
            print("ws silent 180s; HTTP catchup + resubscribe", file=sys.stderr)
            board, stats = catchup(rpc_url, data_dir, state_dir, have,
                                   want_snapshots=want_snapshots)
            print(f"catchup after silence: {stats['collected']} collected")
            continue
        board, stats = catchup(rpc_url, data_dir, state_dir, have,
                               want_snapshots=want_snapshots)
        if stats["collected"]:
            print(f"[ws] board round {board['round_id']}; "
                  f"collected {stats['collected']}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="HAUNT $0 ORE Tier-1 poller (read-only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--catchup", action="store_true")
    ap.add_argument("--backfill", nargs=2, type=int, metavar=("FROM", "TO"))
    ap.add_argument("--ws", action="store_true", help="websocket transport for watch mode")
    ap.add_argument("--snapshot-miners", action="store_true",
                    help="at each newly settled round, capture Miner accounts (Tier-2 census, +1 call/round)")
    ap.add_argument("--rpc", default=ore_rpc.DEFAULT_RPC)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--state-dir", default=DEFAULT_STATE)
    args = ap.parse_args()

    if args.dry_run:
        return mode_dry_run(args.rpc)

    ensure_dirs(args.data_dir, args.state_dir)
    lockf = open(os.path.join(args.state_dir, "poller.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another poller is running; exiting", file=sys.stderr)
        return 2

    if args.backfill:
        return mode_backfill(args.rpc, args.backfill[0], args.backfill[1],
                             args.data_dir, args.state_dir)
    if args.catchup:
        return mode_catchup(args.rpc, args.data_dir, args.state_dir,
                            want_snapshots=args.snapshot_miners)
    return mode_watch(args.rpc, args.data_dir, args.state_dir, use_ws=args.ws,
                      want_snapshots=args.snapshot_miners)


if __name__ == "__main__":
    sys.exit(main())
