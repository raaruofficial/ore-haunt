#!/usr/bin/env python3
"""
Shared plumbing for the HAUNT $0 ORE data pipeline: stdlib-only Solana RPC,
base58, PDA derivation, and ORE account layouts (V3 + V4-deployed).

Layout provenance (primary sources, verified 2026-10-12):
  * V3 indexer (~/workspace/ore-indexer/indexer.py) — verified vs V3 source.
  * Deployed program commit 48c203bd75db3cc45105ec29d8f8db719e5a2263
    (verify.osec.io: on-chain hash == repo hash for
    oreV3EG1i9BEgiAJ8b177Z2S2rMarzak4NMv1kULvWv, verified 2026-10-02).
    regolith-labs/ore api/src/state/{board,round,miner}.rs at that commit.
  * Live-byte proof 2026-10-12: Board = 40 B, disc 105; Round = 952 B,
    disc 109, field offsets identical to V3 through total_miners, plus a
    trailing top_miner pubkey; winner = (r1^r2^r3^r4) % 25 over slot_hash;
    rewards sum == 10**11 (1 ORE) on sampled settled rounds.

No third-party deps: PDA derivation is sha256 + an ed25519 on-curve test.
"""

import base64
import hashlib
import json
import os
import random
import struct
import time
import urllib.error
import urllib.request

# ------------------------------------------------------------------ config

PROGRAM_ID = os.environ.get("ORE_PROGRAM_ID", "oreV3EG1i9BEgiAJ8b177Z2S2rMarzak4NMv1kULvWv")
DEFAULT_RPC = os.environ.get("ORE_RPC_URL", "https://api.mainnet-beta.solana.com")
LAMPORTS_PER_SOL = 1_000_000_000
ONE_ORE = 10 ** 11  # ORE base units per whole ORE (11 decimals)

# ------------------------------------------------------------------ ledger

class CallLedger:
    """Counts every RPC call so credit claims are auditable."""
    def __init__(self):
        self.counts = {}
        self.total = 0

    def add(self, method):
        self.counts[method] = self.counts.get(method, 0) + 1
        self.total += 1

    def summary(self):
        return {"total": self.total, "by_method": dict(self.counts)}

LEDGER = CallLedger()

# ------------------------------------------------------------------ base58

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58I = {c: i for i, c in enumerate(_B58)}

def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n > 0:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + (s or "")

def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58I[c]
    pad = len(s) - len(s.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body

# ------------------------------------------------------------------ PDA

_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P

def _is_on_curve(b: bytes) -> bool:
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    yy = (y * y) % _P
    u = (yy - 1) % _P
    v = (_D * yy + 1) % _P
    if v == 0:
        return False
    x2 = u * pow(v, _P - 2, _P) % _P
    if x2 == 0:
        return True
    return pow(x2, (_P - 1) // 2, _P) == 1

def find_program_address(seeds, program_id=PROGRAM_ID):
    prog = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256()
        for s in seeds:
            h.update(s)
        h.update(bytes([bump]))
        h.update(prog)
        h.update(b"ProgramDerivedAddress")
        digest = h.digest()
        if not _is_on_curve(digest):
            return b58encode(digest), bump
    raise ValueError("no PDA found")

def board_address(program_id=PROGRAM_ID):
    return find_program_address([b"board"], program_id)[0]

def round_address(round_id, program_id=PROGRAM_ID):
    return find_program_address([b"round", round_id.to_bytes(8, "little")], program_id)[0]

def miner_address(authority_b58, program_id=PROGRAM_ID):
    return find_program_address([b"miner", b58decode(authority_b58)], program_id)[0]

BOARD_ADDR = "BrcSxdp1nXFzou1YyDnQJcPNBNHgoypZmTsyKBSLLXzi"  # == board_address(); asserted in tests

# ------------------------------------------------------------------ RPC

class RpcError(Exception):
    def __init__(self, msg, retryable=True):
        super().__init__(msg)
        self.retryable = retryable

def rpc_call(rpc_url, method, params, timeout=40):
    LEDGER.add(method)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RpcError("HTTP 429 rate limited", retryable=True)
        if 500 <= e.code < 600:
            raise RpcError(f"HTTP {e.code}", retryable=True)
        raise RpcError(f"HTTP {e.code} (fatal)", retryable=False)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RpcError(f"transport error: {e}", retryable=True)
    if "error" in payload:
        err = payload["error"]
        retryable = err.get("code") in (-32005, -32002, -32603, -32000)
        raise RpcError(f"rpc error {err.get('code')}: {err.get('message')}", retryable=retryable)
    return payload["result"]

def rpc(rpc_url, method, params, max_retries=5):
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return rpc_call(rpc_url, method, params)
        except RpcError as e:
            if not e.retryable or attempt == max_retries - 1:
                raise
            time.sleep(delay + random.uniform(0, 0.5))
            delay = delay * 2.5 if "429" in str(e) else delay * 2.0
    raise RpcError("exhausted retries")

def get_account(rpc_url, address, max_retries=5):
    res = rpc(rpc_url, "getAccountInfo",
              [address, {"encoding": "base64", "commitment": "confirmed"}], max_retries)
    return res["value"]

def get_multiple_accounts(rpc_url, addresses, max_retries=5):
    out = []
    for i in range(0, len(addresses), 100):  # RPC cap: 100 accounts/call
        chunk = addresses[i:i + 100]
        res = rpc(rpc_url, "getMultipleAccounts",
                  [chunk, {"encoding": "base64", "commitment": "confirmed"}], max_retries)
        out.extend(res["value"])
    return out

def account_bytes(value):
    """getAccountInfo/getMultipleAccounts value -> raw bytes or None."""
    if value is None:
        return None
    return base64.b64decode(value["data"][0])

# ------------------------------------------------------------------ layouts

BOARD_LEN = 40
BOARD_DISC = 105       # OreAccount::Board
ROUND_LEN = 952
ROUND_DISC = 109       # OreAccount::Round
MINER_LEN = 752
MINER_DISC = 103        # OreAccount::Miner
# Miner field offsets (absolute, incl. 8-byte discriminator)
MINER_OFF_AUTHORITY = 8
MINER_OFF_DEPLOYED = 64
MINER_OFF_ROUND_ID = 664
MINER_OFF_REWARDS_SOL = 688
MINER_OFF_REFINED_ORE = 696
MINER_OFF_REWARDS_ORE = 704
MINER_OFF_LIFETIME_REWARDS_ORE = 728
MINER_OFF_LIFETIME_DEPLOYED = 736

def parse_board(data):
    """-> dict or raises ValueError. Works for V3 and V4-deployed boards."""
    if data is None or len(data) != BOARD_LEN:
        raise ValueError(f"board bad length {None if data is None else len(data)}")
    if data[0] != BOARD_DISC:
        raise ValueError(f"board bad discriminator {data[0]}")
    round_id, start_slot, end_slot, ema = struct.unpack_from("<4Q", data, 8)
    return {"round_id": round_id, "start_slot": start_slot,
            "end_slot": end_slot, "production_cost_ema": ema}

def parse_round(data, expected_id):
    """-> (record, None) or (None, gap_reason). Byte-identical field offsets
    for V3 and the V4-deployed program; V4 adds trailing top_miner."""
    if data is None:
        return None, "account_closed"
    if len(data) != ROUND_LEN:
        return None, f"bad_length_{len(data)}"
    if data[0] != ROUND_DISC:
        return None, f"bad_discriminator_{data[0]}"
    off = 8
    rid, = struct.unpack_from("<Q", data, off); off += 8
    if rid != expected_id:
        return None, f"id_mismatch_{rid}"
    deployed = struct.unpack_from("<25Q", data, off); off += 200
    off += 200  # mass
    miners = struct.unpack_from("<25Q", data, off); off += 200
    slot_hash = data[off:off + 32]; off += 32
    expires_at, = struct.unpack_from("<Q", data, off); off += 8
    motherlode, = struct.unpack_from("<Q", data, off); off += 8
    off += 32  # rent_payer
    rewards = struct.unpack_from("<25Q", data, off); off += 200
    total_vaulted, = struct.unpack_from("<Q", data, off); off += 8
    total_returned, = struct.unpack_from("<Q", data, off); off += 8
    total_miners, = struct.unpack_from("<Q", data, off); off += 8
    top_miner = b58encode(data[off:off + 32]); off += 32
    assert off == ROUND_LEN

    if slot_hash == bytes(32) or slot_hash == bytes([255]) * 32:
        winner, no_entropy = None, True
    else:
        r1, r2, r3, r4 = struct.unpack("<4Q", slot_hash)
        winner, no_entropy = (r1 ^ r2 ^ r3 ^ r4) % 25, False

    # Internal consistency gates — a layout misread must fail loudly here,
    # never flow into the dataset.
    if winner is not None and not (0 <= winner < 25):
        return None, f"winner_out_of_range_{winner}"
    if any(c > total_miners for c in miners):
        return None, "count_exceeds_total_miners"
    if total_miners > 100_000:
        return None, f"implausible_total_miners_{total_miners}"
    total_lamports = sum(deployed)
    if total_lamports > 100_000 * LAMPORTS_PER_SOL:
        return None, f"implausible_total_deployed_{total_lamports}"

    from datetime import datetime, timezone
    return {
        "round": expected_id,
        "winner": winner,
        "no_entropy": no_entropy,
        "deployed_lamports": list(deployed),
        "total_deployed_sol": round(total_lamports / LAMPORTS_PER_SOL, 9),
        "miners_per_tile": list(miners),
        "total_miners": total_miners,
        "motherlode_raw": motherlode,
        "slot_hash": slot_hash.hex(),
        "expires_at_slot": expires_at,
        "top_miner": top_miner,
        "total_vaulted_lamports": total_vaulted,
        "total_returned_sol_lamports": total_returned,
        "rewards_sum_raw": sum(rewards),
        "program_era": "v4",
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }, None

def parse_miner(data):
    """-> dict or raises ValueError. Miner = one authority's CURRENT state
    (rolls forward every round the miner plays; not a historical record)."""
    if data is None or len(data) != MINER_LEN:
        raise ValueError(f"miner bad length {None if data is None else len(data)}")
    if data[0] != MINER_DISC:
        raise ValueError(f"miner bad discriminator {data[0]}")
    deployed = struct.unpack_from("<25Q", data, MINER_OFF_DEPLOYED)
    round_id, = struct.unpack_from("<Q", data, MINER_OFF_ROUND_ID)
    rewards_sol, = struct.unpack_from("<Q", data, MINER_OFF_REWARDS_SOL)
    rewards_ore, = struct.unpack_from("<Q", data, MINER_OFF_REWARDS_ORE)
    lifetime_deployed, = struct.unpack_from("<Q", data, MINER_OFF_LIFETIME_DEPLOYED)
    return {
        "authority": b58encode(data[MINER_OFF_AUTHORITY:MINER_OFF_AUTHORITY + 32]),
        "round_id": round_id,
        "deployed_lamports": list(deployed),
        "rewards_sol_lamports": rewards_sol,
        "rewards_ore_raw": rewards_ore,
        "lifetime_deployed_lamports": lifetime_deployed,
    }
