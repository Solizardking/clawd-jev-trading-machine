"""Dry-run fill simulator + paper portfolio.

Fills are simulated against venue quotes. No signing, no broadcast, no
private keys, no network orders - this module cannot place a trade by
construction: it has no venue write path at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fill:
    cycle: int
    venue: str | None
    side: str | None            # "buy" | "sell" | None
    size_sol: float
    price_usdc: float | None
    notional_usdc: float
    fee_usdc: float
    status: str                # filled | rejected | skipped
    reason: str

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "venue": self.venue,
            "side": self.side,
            "size_sol": self.size_sol,
            "price_usdc": self.price_usdc,
            "notional_usdc": round(self.notional_usdc, 6),
            "fee_usdc": round(self.fee_usdc, 6),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class Portfolio:
    usdc: float
    sol: float
    start_usdc: float

    def to_dict(self) -> dict:
        return {
            "usdc": round(self.usdc, 6),
            "sol": round(self.sol, 6),
            "start_usdc": round(self.start_usdc, 6),
        }


def parse_target(target_id: str) -> tuple[str, str]:
    """'BUY_SOL_JUPITER' -> ('buy', 'jupiter'). Raises ValueError if malformed."""
    parts = (target_id or "").split("_")
    if len(parts) < 3 or parts[0] not in ("BUY", "SELL") or parts[1] != "SOL":
        raise ValueError(f"malformed target id: {target_id!r}")
    return parts[0].lower(), "_".join(parts[2:]).lower()


def simulate(decision, quotes: dict, portfolio: Portfolio, config, cycle: int) -> Fill:
    """Apply one decision to the paper portfolio. Returns a Fill record."""
    if decision.operation in ("WAIT", "OPEN_REVIEW", "BLOCKED"):
        return Fill(cycle, None, None, 0.0, None, 0.0, 0.0,
                    "skipped", f"no fill for operation {decision.operation}")
    try:
        side, venue = parse_target(decision.target_id)
    except ValueError as e:
        return Fill(cycle, None, None, 0.0, None, 0.0, 0.0, "rejected", str(e))
    if venue not in config.venues:
        return Fill(cycle, venue, side, 0.0, None, 0.0, 0.0,
                    "skipped", f"venue {venue!r} not enabled")
    q = quotes.get(venue)
    if q is None or not q.ok or q.is_stale(config.quote_stale_s):
        return Fill(cycle, venue, side, 0.0, None, 0.0, 0.0,
                    "skipped", f"venue {venue!r} quote unavailable or stale")
    if q.spread_bps is not None and q.spread_bps > config.max_slippage_bps:
        return Fill(cycle, venue, side, 0.0, None, 0.0, 0.0, "rejected",
                    f"spread {q.spread_bps:.1f}bps exceeds tolerance "
                    f"{config.max_slippage_bps:g}bps")
    size = config.ticket_sol
    if side == "buy":
        price, notional = q.ask, size * q.ask
        fee = notional * config.fee_bps / 10_000
        if portfolio.usdc < notional + fee:
            return Fill(cycle, venue, side, size, price, notional, fee, "rejected",
                        f"insufficient paper USDC ({portfolio.usdc:.2f} < {notional + fee:.2f})")
        portfolio.usdc -= notional + fee
        portfolio.sol += size
    else:
        price, notional = q.bid, size * q.bid
        fee = notional * config.fee_bps / 10_000
        if portfolio.sol < size:
            return Fill(cycle, venue, side, size, price, notional, fee, "rejected",
                        f"insufficient paper SOL ({portfolio.sol:g} < {size:g})")
        portfolio.sol -= size
        portfolio.usdc += notional - fee
    return Fill(cycle, venue, side, size, price, notional, fee,
                "filled", "simulated fill at quoted price")


def mark_to_market(portfolio: Portfolio, mid_usdc) -> dict | None:
    """Paper portfolio value at a mid price. None when no usable mid."""
    if not isinstance(mid_usdc, (int, float)) or mid_usdc <= 0:
        return None
    total = portfolio.usdc + portfolio.sol * mid_usdc
    pnl = total - portfolio.start_usdc
    return {
        "mid_usdc": round(mid_usdc, 4),
        "total_usdc": round(total, 4),
        "pnl_usdc": round(pnl, 4),
        "pnl_pct": round(pnl / portfolio.start_usdc * 100, 4),
    }
