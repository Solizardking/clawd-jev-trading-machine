"""Imperial perps venue - STUB, not wired in this scaffold.

The real client structure will live here: Phoenix-routed 1x SOL perps in
observe mode (read the book, never place orders). Until then every call
reports unavailable so all code paths stay honest.

Tracked as an open decision: wire the perps feed, then extend the action
space with LONG_PERP / SHORT_PERP.
"""
from __future__ import annotations

from . import Quote

WIRED = False


def available() -> bool:
    return False


def get_quote(*args, **kwargs) -> Quote:
    return Quote(venue="imperial", ok=False,
                 error="imperial perps feed not wired (stub)")
