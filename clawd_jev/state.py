"""Market-state builder + rolling tape for the JEV state payload."""
from __future__ import annotations

import json
from collections import deque


class Tape:
    """Rolling window of mid prices; ret_bps is the window move in basis points."""

    def __init__(self, maxlen: int = 20):
        self._m = deque(maxlen=maxlen)

    def add(self, mid) -> None:
        if isinstance(mid, (int, float)) and mid > 0:
            self._m.append(float(mid))

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._m)

    def ret_bps(self):
        if len(self._m) < 2:
            return None
        first = self._m[0]
        return (self._m[-1] - first) / first * 10_000


GOAL = (
    "Paper-trade SOL on Solana venues with a fixed ticket: buy low, sell high, "
    "never exceed risk caps, never chase a moving tape, never trade into a wide "
    "spread. Advisory only - no execution."
)


def build_state(*, cycle: int, quotes: dict, tape: Tape, config, mock: bool,
               regime: dict | None = None, memory: dict | None = None) -> dict:
    market = {}
    for name, q in quotes.items():
        market[name] = {
            "bid": q.bid,
            "ask": q.ask,
            "mid": q.mid,
            "spread_bps": q.spread_bps,
            "ref_price_usd": q.ref_price_usd,
            "ok": q.ok,
            "stale": q.is_stale(config.quote_stale_s),
            "age_s": round(q.age_s(), 1) if q.ok else None,
            "error": q.error,
        }
    return {
        "mode": "paper-dry-run",
        "cycle": cycle,
        "goal": GOAL,
        "policy": {
            "dry_run": True,
            "never_signs": True,
            "never_broadcasts": True,
            "requires_human_approval_for_live": True,
            "one_action_per_cycle": True,
            "fail_closed": "invalid or missing JEV output becomes BLOCKED; no fill",
        },
        "market": market,
        "tape": {"lookback": len(tape), "ret_bps": tape.ret_bps()},
        "regime": regime if regime is not None else {
            "status": "unavailable", "regime": "UNKNOWN",
            "error": "regime context not fetched", "source": "coingecko",
        },
        "memory": memory if memory is not None else {
            "enabled": False, "memories": [],
            "error": "memory recall not performed",
        },
        "risk": {
            "ticket_sol": config.ticket_sol,
            "max_ticket_sol": config.max_ticket_sol,
            "max_slippage_bps": config.max_slippage_bps,
            "fee_bps": config.fee_bps,
            "venues": list(config.venues),
        },
        "mock": bool(mock),
    }


def render(state: dict) -> str:
    return json.dumps(state, separators=(",", ":"))
