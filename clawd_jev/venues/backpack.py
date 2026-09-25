"""Backpack Exchange spot venue - PUBLIC market data only. Read-only.

Quotes SOL/USDC from the public order book (no auth):
  GET {base}/api/v1/depth?symbol=SOL_USDC    best bid / best ask
  GET {base}/api/v1/ticker?symbol=SOL_USDC   reference last price

Authenticated trading is NOT enabled (per the backpack skill) and this
module has no order path at all: quotes only, fills are simulated by the
dry-run simulator. Execution is disabled by design - dry-run only.

Never call it a DEX in copy: it is Backpack Exchange.

Fail-soft: never raises; on failure returns Quote(ok=False) with an error.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import Quote

USER_AGENT = "clawd-jev/0.2"
SYMBOL = "SOL_USDC"


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_quote(size_sol: float, *, base_url: str, timeout: float = 12.0) -> Quote:
    try:
        if not isinstance(size_sol, (int, float)) or size_sol <= 0:
            raise ValueError("size_sol must be positive")
        depth = _get_json(f"{base_url}/api/v1/depth?symbol={SYMBOL}", timeout)
        bids = depth.get("bids") or []
        asks = depth.get("asks") or []
        if not bids or not asks:
            raise ValueError("empty order book")
        # Sort order is not guaranteed: best bid = highest bid price,
        # best ask = lowest ask price.
        bid = max(float(b[0]) for b in bids)
        ask = min(float(a[0]) for a in asks)
        if not (bid > 0 and ask > 0):
            raise ValueError("non-positive best bid/ask")
        if ask < bid:
            raise ValueError("crossed book (best ask below best bid)")
        mid = (bid + ask) / 2
        ticker = _get_json(f"{base_url}/api/v1/ticker?symbol={SYMBOL}", timeout)
        ref = float(ticker.get("lastPrice") or 0)
        if not ref > 0:
            raise ValueError("no reference last price")
        return Quote(
            venue="backpack",
            bid=bid,
            ask=ask,
            mid=mid,
            spread_bps=(ask - bid) / mid * 10_000,
            ref_price_usd=ref,
            fetched_at=time.time(),
            ok=True,
        )
    except Exception as e:
        return Quote(venue="backpack", ok=False, error=f"{type(e).__name__}: {e}",
                     fetched_at=time.time())
