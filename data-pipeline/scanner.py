#!/usr/bin/env python3
"""
HAUNT $0 data pipeline — Tier 2 burst scanner (NOT a daemon).

Recovers per-wallet deploy events for specific rounds, in the frozen
deploys-file schema, and validates them against the Round account (the
ground truth the program itself maintains).

Strategies (costed in OPTIMIZATIONS.md):
  round   — getSignaturesForAddress on the ROUND account (not the whole
            program), one getTransaction per ok signature, parse ORE
            Deploy instructions (discriminant 6; args = per-square
            amount u64 + squares bitmask u32). Deploy ixs touching a
            Round account are that round's deploys *by construction*
            (the program asserts round.id == board.round_id).
  wallet  — for a small tracked-wallet set: their signature lists,
            filtered to the round's slot window, then getTransaction.
  snapshot— getProgramAccounts(memcmp Miner.round_id == N): the Miner
            accounts still parked on round N. Near-free (1 call) but a
            decaying sample: miners roll to N+1 on their next action
            (measured: ~145 mid-round, ~27 one round later, 0 at +50).

Event semantics (from program source, deploy.rs @ 48c203b):
  * ix.accounts = [signer, authority, automation, board, config, miner,
    round, treasury, system, ore_program, ...entropy]. wallet=authority.
  * args.amount is PER SQUARE. A deploy only lands on squares the miner
    has not already covered this round (later repeats are no-ops), so
    reconstruction dedupes (wallet, tile) keeping the earliest event.
  * Automation strategies (Preferred/Random) may override amount/mask
    on-chain; the ix carries the executor's request. Divergence from
    ground truth is measured, not hidden (see --validate output).

Output schema mirrors ~/workspace/ore-intel/data/deploys_2026-10-07.jsonl:
  wallet, signer, amount_lamports (total = per-square x squares),
  amount_sol, squares, round, strategy ("manual" if signer == wallet
  else "automated"), event_ts, slot, block_time, sig,
  amount_per_square_lamports.
"""

import argparse
import json
import os
import struct
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ore_rpc
from ore_rpc import (LEDGER, account_bytes, b58decode, get_account,
                     miner_address, parse_round, round_address, rpc)

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY_DISC = 6
RESET_DISC = 9
CHECKPOINT_DISC = 2
CLAIM_SOL_DISC = 3
CLAIM_ORE_DISC = 4


# ------------------------------------------------------------------ RPC

def get_signatures(rpc_url, address, limit_total=5000):
    """All signatures for an address (newest first), paginated."""
    out, before = [], None
    while len(out) < limit_total:
        params = [address, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        page = rpc(rpc_url, "getSignaturesForAddress", params)
        if not page:
            break
        out.extend(page)
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    return out


def get_tx(rpc_url, sig):
    return rpc(rpc_url, "getTransaction",
               [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
                      "commitment": "confirmed"}])


# ------------------------------------------------------------------ parse

def _all_instructions(tx):
    """Yield (program_id, data_b58, accounts) for top-level + inner ixs."""
    msg = tx["transaction"]["message"]
    for ix in msg["instructions"]:
        yield ix.get("programId"), ix.get("data", ""), ix.get("accounts", [])
    meta = tx.get("meta") or {}
    for group in meta.get("innerInstructions") or []:
        for ix in group.get("instructions", []):
            yield ix.get("programId"), ix.get("data", ""), ix.get("accounts", [])


def _resolve_keys(tx):
    msg = tx["transaction"]["message"]
    keys = []
    for k in msg["accountKeys"]:
        keys.append(k["pubkey"] if isinstance(k, dict) else k)
    meta = tx.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    keys.extend(loaded.get("writable", []))
    keys.extend(loaded.get("readonly", []))
    return keys


def parse_deploys(tx, round_id):
    """-> list of deploy events in this tx (frozen-file schema).
    Attribution rule: the ix's own round account (accounts[6]) must be
    THIS round's PDA. Automation keepers batch checkpoint(prev) +
    deploy(next) into one tx; without this check the deploy leg is
    misattributed to the checkpoint's round (measured: it happened)."""
    keys = _resolve_keys(tx)
    fee_payer = keys[0] if keys else None
    target_round = round_address(round_id)
    events = []
    for prog, data_b58, accounts in _all_instructions(tx):
        if prog != ore_rpc.PROGRAM_ID or not data_b58:
            continue
        try:
            raw = b58decode(data_b58)  # jsonParsed encodes ix data as base58
        except Exception:
            continue
        if len(raw) != 13 or raw[0] != DEPLOY_DISC:
            continue
        amount_per_square, = struct.unpack_from("<Q", raw, 1)
        mask, = struct.unpack_from("<I", raw, 9)
        squares = [i for i in range(25) if (mask >> i) & 1]
        if not squares or len(accounts) < 2:
            continue
        signer = _key(keys, accounts[0])
        wallet = _key(keys, accounts[1])
        if not wallet:
            continue
        if len(accounts) > 6 and _key(keys, accounts[6]) != target_round:
            continue  # deploy leg of a checkpoint+deploy batch for another round
        events.append({
            "wallet": wallet,
            "signer": signer or fee_payer,
            "amount_lamports": amount_per_square * len(squares),
            "amount_sol": amount_per_square * len(squares) / 1e9,
            "squares": squares,
            "round": round_id,
            "strategy": "manual" if signer == wallet else "automated",
            "event_ts": tx.get("blockTime"),
            "slot": tx["slot"],
            "block_time": (datetime.fromtimestamp(tx["blockTime"], timezone.utc)
                           .isoformat() if tx.get("blockTime") else None),
            "sig": tx["transaction"]["signatures"][0],
            "amount_per_square_lamports": amount_per_square,
        })
    return events


def _key(keys, ref):
    if isinstance(ref, int):
        return keys[ref] if 0 <= ref < len(keys) else None
    return ref  # jsonParsed may already give pubkey strings


# ------------------------------------------------------------------ scan

def scan_round(rpc_url, round_id):
    """Round-scoped scan. Returns (events, stats)."""
    raddr = round_address(round_id)
    sigs = get_signatures(rpc_url, raddr)
    ok = [s for s in sigs if not s["err"]]
    stats = {"signatures": len(sigs), "signatures_ok": len(ok),
             "txs_fetched": 0, "txs_null": 0}
    raw_events = []
    for s in reversed(ok):  # signature lists are newest-first; go oldest-first
        tx = get_tx(rpc_url, s["signature"])
        stats["txs_fetched"] += 1
        if tx is None:
            stats["txs_null"] += 1
            continue
        raw_events.extend(parse_deploys(tx, round_id))
    # Order chronologically: slot asc, then signature-list order reversed
    # (list is newest-first), so "earliest deploy wins" dedupe is correct.
    raw_events.sort(key=lambda e: (e["slot"],))
    seen, events = set(), []
    for e in raw_events:
        fresh = [t for t in e["squares"] if (e["wallet"], t) not in seen]
        for t in fresh:
            seen.add((e["wallet"], t))
        if not fresh:
            continue
        if len(fresh) != len(e["squares"]):
            e = dict(e, squares=fresh,
                     amount_lamports=e["amount_per_square_lamports"] * len(fresh),
                     amount_sol=e["amount_per_square_lamports"] * len(fresh) / 1e9)
        events.append(e)
    stats["events_raw"] = len(raw_events)
    stats["events"] = len(events)
    stats["wallets"] = len({e["wallet"] for e in events})
    return events, stats


def validate_against_round(rpc_url, round_id, events):
    """Compare reconstruction with the Round account ground truth."""
    acc = get_account(rpc_url, round_address(round_id))
    rec, gap = parse_round(account_bytes(acc), round_id)
    if gap:
        return {"gap": gap}
    dep = [0] * 25
    cnt = [0] * 25
    for e in events:
        for t in e["squares"]:
            dep[t] += e["amount_per_square_lamports"]
            cnt[t] += 1
    exact_dep = sum(1 for t in range(25) if dep[t] == rec["deployed_lamports"][t])
    exact_cnt = sum(1 for t in range(25) if cnt[t] == rec["miners_per_tile"][t])
    return {
        "round_total_deployed": sum(rec["deployed_lamports"]),
        "recon_total_deployed": sum(dep),
        "deployed_exact_tiles": f"{exact_dep}/25",
        "count_exact_tiles": f"{exact_cnt}/25",
        "total_abs_delta_lamports": sum(abs(dep[t] - rec["deployed_lamports"][t])
                                        for t in range(25)),
        "round_total_miners": rec["total_miners"],
        "recon_unique_wallets": len({e["wallet"] for e in events}),
    }


def snapshot_miners(rpc_url, round_id):
    """gPA memcmp on Miner.round_id — decaying sample, 1 RPC call."""
    res = rpc(rpc_url, "getProgramAccounts",
              [ore_rpc.PROGRAM_ID,
               {"encoding": "base64", "commitment": "confirmed",
                "filters": [{"dataSize": ore_rpc.MINER_LEN},
                            {"memcmp": {"offset": ore_rpc.MINER_OFF_ROUND_ID,
                                        "bytes": ore_rpc.b58encode(
                                            round_id.to_bytes(8, "little"))}}]}])
    miners = []
    for entry in res:
        data = account_bytes(entry["account"])
        try:
            miners.append(ore_rpc.parse_miner(data))
        except ValueError:
            continue
    return miners


def scan_wallets(rpc_url, round_id, wallets, slot_lo, slot_hi):
    """Wallet-intersection strategy: only the given wallets' events."""
    events, stats = [], {"sig_list_calls": 0, "sigs_seen": 0, "txs_fetched": 0}
    for w in wallets:
        sigs = get_signatures(rpc_url, w, limit_total=1000)
        stats["sig_list_calls"] += 1
        stats["sigs_seen"] += len(sigs)
        for s in sigs:
            if s["err"] or not (slot_lo <= s["slot"] <= slot_hi):
                continue
            tx = get_tx(rpc_url, s["signature"])
            stats["txs_fetched"] += 1
            if tx:
                events.extend(e for e in parse_deploys(tx, round_id)
                              if e["wallet"] == w)
    return events, stats


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="HAUNT $0 ORE Tier-2 burst scanner")
    ap.add_argument("round", type=int)
    ap.add_argument("--rpc", default=ore_rpc.DEFAULT_RPC)
    ap.add_argument("--out", default=None,
                    help="write events JSONL here (default: data/deploys_v4_<round>.jsonl)")
    ap.add_argument("--measure", action="store_true",
                    help="scan + validate, print costs, write nothing")
    ap.add_argument("--wallets", type=int, default=0, metavar="N",
                    help="also cost the wallet-intersection strategy for the top N wallets")
    ap.add_argument("--snapshot", action="store_true",
                    help="also run the gPA Miner snapshot and compare coverage")
    args = ap.parse_args()

    before = LEDGER.total
    events, stats = scan_round(args.rpc, args.round)
    verdict = validate_against_round(args.rpc, args.round, events)
    print(f"round {args.round} round-scoped scan: {json.dumps(stats)}")
    print(f"validation vs Round account: {json.dumps(verdict)}")
    print(f"RPC calls for scan+validate: {LEDGER.total - before} "
          f"{LEDGER.summary()['by_method']}")

    if events:
        slots = [e["slot"] for e in events]
        slot_lo, slot_hi = min(slots), max(slots)
    if args.wallets and events:
        # tracked-wallet stand-ins: most frequent wallets in THIS round
        from collections import Counter
        top = [w for w, _ in Counter(e["wallet"] for e in events).most_common(args.wallets)]
        b2 = LEDGER.total
        wevents, wstats = scan_wallets(args.rpc, args.round, top, slot_lo, slot_hi)
        mine = [e for e in events if e["wallet"] in set(top)]
        print(f"wallet-intersection (top {args.wallets}): {json.dumps(wstats)}; "
              f"recovered {len(wevents)} events vs {len(mine)} from round scan; "
              f"calls {LEDGER.total - b2}")

    if args.snapshot:
        b3 = LEDGER.total
        miners = snapshot_miners(args.rpc, args.round)
        dep_round = sum(sum(m["deployed_lamports"]) for m in miners)
        print(f"miner snapshot: {len(miners)} miners parked on round {args.round}, "
              f"deployed captured {dep_round} lamports; calls {LEDGER.total - b3}")

    if not args.measure:
        out = args.out or os.path.join(HERE, "data", f"deploys_v4_{args.round}.jsonl")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            for e in events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
        print(f"wrote {len(events)} events -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
