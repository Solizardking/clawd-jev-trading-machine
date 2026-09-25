"""Jupiter spot venue - keyless public Lite API, read-only.

Endpoints (same as the jev-trader-solana feed):
  GET {base}/price/v3?ids=<mints>          reference price
  GET {base}/swap/v1/quote?...             routed executable quotes

The bid proxy is the effective price of selling `size_sol` SOL for USDC; the
ask proxy is the effective price of buying it back with USDC. No wallet, no
signing, no submission - this module only READS.

Fail-soft: never raises; on failure returns Quote(ok=False) with an error.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import SOL_MINT, USDC_MINT, Quote

USER_AGENT = "clawd-jev/0.2"


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_quote(size_sol: float, *, base_url: str, timeout: float = 12.0) -> Quote:
    try:
        lamports = int(round(size_sol * 1e9))
        if lamports <= 0:
            raise ValueError("size_sol must be positive")
        price = _get_json(f"{base_url}/price/v3?ids={SOL_MINT},{USDC_MINT}", timeout)
        ref = (price.get(SOL_MINT) or {}).get("usdPrice")
        if not isinstance(ref, (int, float)) or ref <= 0:
            raise ValueError("no SOL reference price")
        usdc_micro = int(round(size_sol * ref * 1e6))

        sell = _get_json(
            f"{base_url}/swap/v1/quote?inputMint={SOL_MINT}&outputMint={USDC_MINT}"
            f"&amount={lamports}&slippageBps=50",
            timeout,
        )
        buy = _get_json(
            f"{base_url}/swap/v1/quote?inputMint={USDC_MINT}&outputMint={SOL_MINT}"
            f"&amount={usdc_micro}&slippageBps=50",
            timeout,
        )
        s_out, s_in = float(sell["outAmount"]), float(sell["inAmount"])
        b_out, b_in = float(buy["outAmount"]), float(buy["inAmount"])
        bid = (s_out / 1e6) / (s_in / 1e9)    # USDC per SOL received selling
        ask = (b_in / 1e6) / (b_out / 1e9)    # USDC per SOL paid buying
        if not (bid > 0 and ask > 0):
            raise ValueError("non-positive bid/ask proxy")
        mid = (bid + ask) / 2
        # Two independently-routed quotes can microscopically cross; tolerate
        # under 10 bps, fail beyond it.
        if abs(bid - ask) / mid > 0.001:
            raise ValueError("crossed bid/ask proxy beyond 10 bps")
        lo, hi = (bid, ask) if bid <= ask else (ask, bid)
        return Quote(
            venue="jupiter",
            bid=lo,
            ask=hi,
            mid=mid,
            spread_bps=max(0.0, (hi - lo) / mid * 10_000),
            ref_price_usd=float(ref),
            fetched_at=time.time(),
            ok=True,
        )
    except Exception as e:
        return Quote(venue="jupiter", ok=False, error=f"{type(e).__name__}: {e}",
                     fetched_at=time.time())
