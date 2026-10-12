"""Live USD prices from the Jupiter Price API.

Deliberately mirrors the HAUNT client's orePrice() honesty rule: there is
NO fallback price anywhere in this module. If the price cannot be fetched,
the functions return None and quote creation refuses with an honest error
rather than inventing a rate.
"""
import json
import urllib.request

import config


def _fetch_prices(mints):
    """Raw Jupiter price response for the given mints (dict keyed by mint)."""
    url = config.JUPITER_PRICE_URL + '?ids=' + ','.join(mints)
    req = urllib.request.Request(
        url, headers={'accept': 'application/json',
                      'user-agent': 'haunt-backend/1'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _price_of(mint):
    try:
        data = _fetch_prices([mint])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(mint)
    if entry is None and isinstance(data.get('data'), dict):
        entry = data['data'].get(mint)  # tolerate the retired v2 envelope
    if not isinstance(entry, dict):
        return None
    for key in ('usdPrice', 'price'):
        value = entry.get(key)
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def sol_usd():
    """USD price of 1 SOL, or None when unavailable."""
    return _price_of(config.SOL_MINT)


def ore_usd():
    """USD price of 1 ORE, or None when unavailable."""
    return _price_of(config.ORE_MINT)
