#!/usr/bin/env python3
"""
HAUNT $0 data pipeline — Tier 2 continuous deploy-event scanner service.

A paced daemon loop around scanner.scan_round (the scan itself — round-
scoped signature walk, checkpoint/deploy attribution rule, (wallet,tile)
earliest-wins dedupe — lives in scanner.py and is NOT reimplemented here).

Design rules (proven in OPTIMIZATIONS.md, binding here):
  * Lag: a round is scanned only once it is >= LAG_ROUNDS behind the
    current Board round. The signature index lags reality; scans run
    seconds after close see an incomplete sig list (44 vs 96 measured).
  * Pacing: strictly sequential, one round at a time, on the PUBLIC
    endpoint. Steady state ~= scanner's ~100-140 calls/round.
  * Rate limits (HTTP 429 / RPC -32429 / "too many requests"): outer
    exponential backoff 30 -> 60 -> 120 -> 240 -> 300s cap, journaled.
    ore_rpc.rpc already retries internally; this layer is the long
    backstop. A rate limit never counts as a round failure and never
    crashes the service.
  * Failures: a round that errors (non-rate-limit) twice is recorded in
    state/scanner_failures.jsonl and the watermark advances past it —
    one bad round never wedges the queue.
  * Falling behind: if the unscanned backlog exceeds BACKLOG_MAX rounds,
    the watermark jumps to (eligible - BACKLOG_MAX) and the skipped
    range is journaled in the failures file. Suppressed ONLY during the
    initial drain from the oldest collected round on a fresh start
    (complete deploy history from the restart of collection is the
    owner's explicit requirement); armed permanently once the backlog
    first falls to <= BACKLOG_MAX, and armed from boot on restarts.
  * Idempotency: events are written per-round to data/deploys/<round>.jsonl
    via temp file + atomic replace, BEFORE the watermark advances. A
    crash mid-round re-scans exactly that one round and the replace
    overwrites — duplicates are impossible by construction.
  * Single instance: flock on state/scanner.lock (mirrors poller.py).

State:
  state/scanner_watermark.json  {"last_scanned_round": N}
  state/scanner_failures.jsonl  {"round":..,"error":..,"time":..} |
                                {"skipped_range":[a,b],"reason":..,"time":..}
  state/scanner.log             one line per round / backoff / skip / failure
"""

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ore_rpc
import scanner
from ore_rpc import LEDGER

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
STATE_DIR = os.path.join(HERE, "state")
DEPLOYS_DIR = os.path.join(DATA_DIR, "deploys")
ROUNDS_FILE = os.path.join(DATA_DIR, "rounds_v4.jsonl")
WM_FILE = os.path.join(STATE_DIR, "scanner_watermark.json")
FAIL_FILE = os.path.join(STATE_DIR, "scanner_failures.jsonl")
LOG_FILE = os.path.join(STATE_DIR, "scanner.log")

LAG_ROUNDS = 4          # scan only rounds >= this far behind the Board
BACKLOG_MAX = 60        # skip valve threshold (see docstring)
SLEEP_BETWEEN_ROUNDS = 2.0
SLEEP_CAUGHT_UP = 20.0
SLEEP_ON_ERROR = 10.0
BACKOFF_START = 30
BACKOFF_CAP = 300
MAX_ATTEMPTS = 2        # non-rate-limit errors before a round is failed+skipped


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_rate_limit(exc):
    s = str(exc).lower()
    return ("429" in s or "-32429" in s or "too many requests" in s
            or "rate limit" in s or "rate_limited" in s)


# ------------------------------------------------------------------ state

def read_watermark():
    """-> last_scanned_round (int) or None when no state exists yet."""
    try:
        with open(WM_FILE) as f:
            return int(json.load(f)["last_scanned_round"])
    except (OSError, ValueError, KeyError):
        return None


def write_watermark(last_round):
    tmp = WM_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_scanned_round": last_round,
                   "updated_at": now_iso()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, WM_FILE)


def oldest_collected_round():
    """Oldest round present in the Tier-1 rounds file (fresh-start anchor)."""
    with open(ROUNDS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                return int(json.loads(line)["round"])
    raise RuntimeError("rounds_v4.jsonl is empty — nothing to anchor to")


def record_failure(entry):
    entry = dict(entry)
    entry["time"] = now_iso()
    with open(FAIL_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ------------------------------------------------------------------ rounds

def write_round_file(round_id, events):
    os.makedirs(DEPLOYS_DIR, exist_ok=True)
    tmp = os.path.join(DEPLOYS_DIR, f".{round_id}.tmp")
    final = os.path.join(DEPLOYS_DIR, f"{round_id}.jsonl")
    with open(tmp, "w") as f:
        for e in events:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)


def board_round(rpc_url):
    acc = ore_rpc.get_account(rpc_url, ore_rpc.BOARD_ADDR)
    return ore_rpc.parse_board(ore_rpc.account_bytes(acc))["round_id"]


def process_round(rpc_url, round_id):
    """Scan one round. Returns True when the round is DONE (scanned and
    written, or failed twice and journaled) — caller advances watermark.
    Rate limits back off and retry inside here, indefinitely."""
    attempts = 0
    backoff = BACKOFF_START
    while True:
        t0 = time.time()
        before = LEDGER.total
        try:
            events, stats = scanner.scan_round(rpc_url, round_id)
        except Exception as e:
            if is_rate_limit(e):
                log(f"BACKOFF round={round_id} reason={e} sleep={backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP)
                continue
            attempts += 1
            log(f"ERROR round={round_id} attempt={attempts}/{MAX_ATTEMPTS} error={e}")
            if attempts >= MAX_ATTEMPTS:
                record_failure({"round": round_id, "error": str(e)})
                log(f"FAILURE round={round_id} journaled; watermark will advance past it")
                return True
            time.sleep(5)
            continue

        # Events first, watermark later (caller) — crash-safe ordering.
        write_round_file(round_id, events)

        # Validation is for the log line only; its failure never fails
        # a round whose events are already written.
        recon_pct = None
        try:
            verdict = scanner.validate_against_round(rpc_url, round_id, events)
            total = verdict.get("round_total_deployed")
            if total:
                recon = sum(e["amount_lamports"] for e in events)
                recon_pct = round(100.0 * recon / total, 2)
        except Exception as e:
            log(f"VALIDATE-SKIP round={round_id} error={e}")

        calls = LEDGER.total - before
        secs = time.time() - t0
        log(f"ROUND round={round_id} events={len(events)} "
            f"wallets={stats.get('wallets')} "
            f"recon_pct={recon_pct if recon_pct is not None else 'n/a'} "
            f"calls={calls} secs={secs:.1f}")
        return True


# ------------------------------------------------------------------ main

def main():
    rpc_url = ore_rpc.DEFAULT_RPC
    os.makedirs(STATE_DIR, exist_ok=True)

    lockf = open(os.path.join(STATE_DIR, "scanner.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("scanner already running (lock held)", flush=True)
        return 1

    last = read_watermark()
    fresh_start = last is None
    if fresh_start:
        anchor = oldest_collected_round()
        last = anchor - 1
        write_watermark(last)
        log(f"START fresh: watermark initialized at {last} "
            f"(first scan = oldest collected round {anchor}); rpc={rpc_url}")
    else:
        log(f"START resume: watermark at {last}; rpc={rpc_url}")

    # Skip valve suppressed only while the initial fresh-start drain is
    # still above the threshold; armed once backlog <= BACKLOG_MAX, and
    # armed from boot whenever this is a resume.
    skip_armed = not fresh_start
    backoff = BACKOFF_START

    while True:
        try:
            board = board_round(rpc_url)
        except Exception as e:
            if is_rate_limit(e):
                log(f"BACKOFF board-fetch reason={e} sleep={backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP)
            else:
                log(f"ERROR board-fetch error={e} sleep={SLEEP_ON_ERROR}s")
                time.sleep(SLEEP_ON_ERROR)
            continue
        backoff = BACKOFF_START

        eligible = board - LAG_ROUNDS
        backlog = eligible - last

        if backlog <= 0:
            time.sleep(SLEEP_CAUGHT_UP)
            continue

        if backlog <= BACKLOG_MAX:
            skip_armed = True
        elif skip_armed:
            jump_to = eligible - BACKLOG_MAX
            record_failure({"skipped_range": [last + 1, jump_to],
                            "reason": f"backlog {backlog} > {BACKLOG_MAX}"})
            log(f"SKIP rounds {last + 1}..{jump_to} journaled "
                f"(backlog {backlog} > {BACKLOG_MAX}); watermark -> {jump_to}")
            last = jump_to
            write_watermark(last)
            continue

        nxt = last + 1
        if process_round(rpc_url, nxt):
            last = nxt
            write_watermark(last)
        time.sleep(SLEEP_BETWEEN_ROUNDS)


if __name__ == "__main__":
    sys.exit(main())
