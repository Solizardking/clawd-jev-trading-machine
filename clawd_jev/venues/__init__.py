"""Venue quote type shared by all venue adapters."""
from __future__ import annotations

import time
from dataclasses import dataclass

__all__ = ["Quote", "SOL_MINT", "USDC_MINT"]

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class Quote:
    venue: str
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    spread_bps: float | None = None
    ref_price_usd: float | None = None
    fetched_at: float = 0.0
    ok: bool = False
    error: str | None = None

    def age_s(self) -> float:
        return time.time() - self.fetched_at if self.fetched_at else float("inf")

    def is_stale(self, cutoff_s: float) -> bool:
        return (not self.ok) or self.age_s() > cutoff_s

    def to_dict(self) -> dict:
        return {
            "venue": self.venue,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread_bps": self.spread_bps,
            "ref_price_usd": self.ref_price_usd,
            "ok": self.ok,
            "error": self.error,
        }
