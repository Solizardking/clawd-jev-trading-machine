"""pump.fun venue - NOT QUOTABLE for SOL/USDC. Unavailable by construction.

pump.fun is a token-launch protocol: its markets are bonding-curve and Pump
AMM pools where tokens trade *against SOL*. There is no SOL/USDC spot market
on pump.fun, so no SOL/USDC bid/ask quote can be produced - with or without
a key. (The fun-block swap API is POST-only for specific token mints and
returns an unsigned transaction, not a SOL/USDC quote feed.)

This adapter therefore always reports unavailable with a truthful error, so
the venue is never admitted to the dynamic action space. It is fully wired
(config, action space, doctor, simulator) so that only get_quote() needs to
change if a SOL/USDC quote path ever exists.

No network calls, no keys, no signing - read-only by construction.
"""
from __future__ import annotations

from . import Quote

UNAVAILABLE_REASON = (
    "no SOL/USDC spot market on pump.fun "
    "(bonding-curve and Pump AMM markets are token-vs-SOL)"
)


def get_quote(*args, **kwargs) -> Quote:
    return Quote(venue="pumpfun", ok=False, error=UNAVAILABLE_REASON)
