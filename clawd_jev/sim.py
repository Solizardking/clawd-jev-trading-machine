"""Dry-run fill simulator + paper portfolio.

Fills are simulated against venue quotes. No signing, no broadcast, no
private keys, no network orders - this module cannot place a trade by
construction: it has no venue write path at all.

Token-vs-SOL dimension: pump.fun token actions (BUY_<TAG>_PUMPFUN /
SELL_<TAG>_PUMPFUN) are simulated against indicative token quotes (SOL per
token). The paper portfolio tracks per-token balances keyed by mint; a SELL
can only fill against tokens the paper portfolio actually holds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_ACTION_RE = re.compile(r"^(BUY|SELL)_([A-Z0-9]{1,12})_PUMPFUN$")


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
    # token-vs-SOL dimension (None for SOL/USDC fills)
    price_sol: float | None = None      # SOL per whole token
    token_mint: str | None = None
    token_symbol: str | None = None
    token_amount: float | None = None

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
            "price_sol": self.price_sol,
            "token_mint": self.token_mint,
            "token_symbol": self.token_symbol,
            "token_amount": round(self.token_amount, 6) if self.token_amount is not None else None,
        }


@dataclass
class Portfolio:
    usdc: float
    sol: float
    start_usdc: float
    tokens: dict = field(default_factory=dict)   # mint -> token amount

    def to_dict(self) -> dict:
        return {
            "usdc": round(self.usdc, 6),
            "sol": round(self.sol, 6),
            "start_usdc": round(self.start_usdc, 6),
            "tokens": {m: round(a, 6) for m, a in self.tokens.items()},
        }


def parse_target(target_id: str) -> tuple[str, str]:
    """'BUY_SOL_JUPITER' -> ('buy', 'jupiter'). Raises ValueError if malformed."""
    parts = (target_id or "").split("_")
    if len(parts) < 3 or parts[0] not in ("BUY", "SELL") or parts[1] != "SOL":
        raise ValueError(f"malformed target id: {target_id!r}")
    return parts[0].lower(), "_".join(parts[2:]).lower()


def parse_token_target(target_id: str) -> tuple[str, str]:
    """'BUY_8CHZQH_PUMPFUN' -> ('buy', '8CHZQH'). Raises ValueError if not a
    pump.fun token action. 'SOL' is never a token tag (reserved for the
    SOL/USDC action ids)."""
    m = _TOKEN_ACTION_RE.match(target_id or "")
    if not m or m.group(2) == "SOL":
        raise ValueError(f"not a pump.fun token target: {target_id!r}")
    return m.group(1).lower(), m.group(2)


def _simulate_token(decision, side: str, tag: str, token_quotes: dict,
                    portfolio: Portfolio, config, cycle: int) -> Fill:
    """Simulated token-vs-SOL fill on pump.fun at the indicative quote."""
    tq = token_quotes.get(tag)
    if tq is None or not tq.ok or tq.is_stale(config.quote_stale_s):
        return Fill(cycle, "pumpfun", side, 0.0, None, 0.0, 0.0,
                    "skipped", f"token tag {tag!r} quote unavailable or stale")
    price = tq.price_sol
    if not isinstance(price, (int, float)) or not price > 0:
        return Fill(cycle, "pumpfun", side, 0.0, None, 0.0, 0.0,
                    "rejected", f"token tag {tag!r} has no positive SOL price")
    ticket = config.ticket_sol
    fee = ticket * config.fee_bps / 10_000
    amount = ticket / price
    sym = tq.symbol or tag
    if side == "buy":
        if portfolio.sol < ticket + fee:
            return Fill(cycle, "pumpfun", side, ticket, None, 0.0, 0.0,
                        "rejected",
                        f"insufficient paper SOL ({portfolio.sol:.4f} < {ticket + fee:.4f})")
        portfolio.sol -= ticket + fee
        portfolio.tokens[tq.mint] = portfolio.tokens.get(tq.mint, 0.0) + amount
        return Fill(cycle, "pumpfun", side, ticket, None, 0.0, 0.0,
                    "filled",
                    f"simulated token-vs-SOL fill at {price:.8f} SOL/{sym} "
                    f"(indicative mid, no order-book spread); fee {fee:.6f} SOL",
                    price_sol=price, token_mint=tq.mint,
                    token_symbol=sym, token_amount=amount)
    have = portfolio.tokens.get(tq.mint, 0.0)
    if have < amount:
        return Fill(cycle, "pumpfun", side, ticket, None, 0.0, 0.0,
                    "rejected",
                    f"insufficient paper {sym} ({have:.6f} < {amount:.6f}); "
                    f"paper SELL needs a prior paper BUY")
    portfolio.tokens[tq.mint] = have - amount
    portfolio.sol += ticket - fee
    return Fill(cycle, "pumpfun", side, ticket, None, 0.0, 0.0,
                "filled",
                f"simulated token-vs-SOL fill at {price:.8f} SOL/{sym} "
                f"(indicative mid, no order-book spread); fee {fee:.6f} SOL",
                price_sol=price, token_mint=tq.mint,
                token_symbol=sym, token_amount=amount)


def simulate(decision, quotes: dict, portfolio: Portfolio, config, cycle: int,
             token_quotes: dict | None = None) -> Fill:
    """Apply one decision to the paper portfolio. Returns a Fill record.

    token_quotes: tag -> TokenQuote for the pump.fun token dimension.
    """
    if decision.operation in ("WAIT", "OPEN_REVIEW", "BLOCKED"):
        return Fill(cycle, None, None, 0.0, None, 0.0, 0.0,
                    "skipped", f"no fill for operation {decision.operation}")
    try:
        side, tag = parse_token_target(decision.target_id)
        return _simulate_token(decision, side, tag, token_quotes or {},
                               portfolio, config, cycle)
    except ValueError:
        pass
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


def mark_to_market(portfolio: Portfolio, mid_usdc, token_quotes=None) -> dict | None:
    """Paper portfolio value at a mid price. None when no usable mid.

    token_quotes: mint -> TokenQuote (ok ones) used to value paper token
    holdings at their indicative SOL price converted at the SOL mid.
    """
    if not isinstance(mid_usdc, (int, float)) or mid_usdc <= 0:
        return None
    tokens_usdc = 0.0
    if token_quotes:
        for mint, amt in (portfolio.tokens or {}).items():
            tq = token_quotes.get(mint)
            px = getattr(tq, "price_sol", None)
            if tq is not None and getattr(tq, "ok", False) and isinstance(px, (int, float)) and px > 0:
                tokens_usdc += amt * px * mid_usdc
    total = portfolio.usdc + portfolio.sol * mid_usdc + tokens_usdc
    pnl = total - portfolio.start_usdc
    return {
        "mid_usdc": round(mid_usdc, 4),
        "total_usdc": round(total, 4),
        "pnl_usdc": round(pnl, 4),
        "pnl_pct": round(pnl / portfolio.start_usdc * 100, 4),
        "tokens_usdc": round(tokens_usdc, 4),
    }
