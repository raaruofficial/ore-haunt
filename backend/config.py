"""HAUNT backend configuration.

Everything is read at call time (never bound at import) so tests and ops can
override via environment or by patching this module.

HONESTY INVARIANT: TREASURY defaults to None. While it is None the payment
surface is inert by construction: quotes are refused and no signature can be
verified into an entitlement. There is no code path around this.
"""
import os

# Treasury wallet that receives HAUNT payments (base58). None = unset.
TREASURY = os.environ.get('HAUNT_TREASURY') or None

# Solana JSON-RPC endpoint used to fetch transactions for verification.
RPC_URL = os.environ.get('HAUNT_RPC', 'https://api.mainnet-beta.solana.com')

# SQLite database location.
DB_PATH = os.environ.get(
    'HAUNT_DB',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'haunt.db'))

# Jupiter Price API (v3, keyless lite endpoint; v2 was retired).
# Response shape: {"<mint>": {"usdPrice": <float>, ...}, ...}
JUPITER_PRICE_URL = 'https://lite-api.jup.ag/price/v3'

SOL_MINT = 'So11111111111111111111111111111111111111112'  # wrapped SOL
ORE_MINT = 'oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp'
ORE_DECIMALS = 11

# Tier catalogue: USD list price and entitlement duration in days
# (days=None means the entitlement never expires).
TIERS = {
    'day':      {'usd': 1,  'days': 1},
    'monthly':  {'usd': 5,  'days': 30},
    'lifetime': {'usd': 50, 'days': None},
    'anomaly':  {'usd': 7,  'days': 1},
}

CURRENCIES = ('SOL', 'ORE')

# Anomaly slot system (rolling 24h scarcity).
SLOT_CAP = 100            # max simultaneously active anomaly passes + holds
SLOT_HOLD_MINUTES = 2     # an unpaid claim reserves a slot this long.
                          # Deliberately short: 2 minutes is long enough to
                          # complete a wallet payment that is already open,
                          # and it shrinks the griefing window in which a
                          # hold can deny a slot to a real buyer (a 10-min
                          # TTL let anonymous holds be renewed forever).
ANOMALY_QUOTE_RATE_PER_HOUR = 20  # anomaly-quote attempts per source IP

QUOTE_TTL_MINUTES = 30    # payment quotes expire after this (anomaly quotes
                          # expire with their slot hold instead — see payments.py)

AUTH_TTL_SECONDS = 300    # .skr auth challenges live 5 minutes, single-use

SNAPSHOT = 'backend-1'
