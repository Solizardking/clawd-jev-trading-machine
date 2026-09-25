"""DFlow spot venue - quote-only GET /order with x-api-key. Read-only.

No userPublicKey is sent, so /order is quote-only: no signing material, no
submission. DFLOW_API_KEY is read from the environment at call time and never
logged or persisted.

Fail-soft: never raises; on failure returns Quote(ok=False) with an error.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import SOL_MINT, USDC_MINT, Quote

USER_AGENT = "clawd-jev/0.2"


def _order(base_url: str, key: str, input_mint: str, output_mint: str,
           amount: int, timeout: float) -> dict:
    qs = urllib.parse.urlencode({
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
    })
    req = urllib.request.Request(
        f"{base_url}/order?{qs}",
        headers={"accept": "application/json", "x-api-key": key, "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_quote(size_sol: float, *, base_url: str, ref_price_usd=None,
              timeout: float = 12.0) -> Quote:
    key = os.environ.get("DFLOW_API_KEY")
    if not key:
        return Quote(venue="dflow", ok=False, error="DFLOW_API_KEY not set",
                     fetched_at=time.time())
    if not isinstance(ref_price_usd, (int, float)) or ref_price_usd <= 0:
        return Quote(venue="dflow", ok=False,
                     error="no reference price to size the USDC leg",
                     fetched_at=time.time())
    try:
        lamports = int(round(size_sol * 1e9))
        usdc_micro = int(round(size_sol * ref_price_usd * 1e6))
        if lamports <= 0 or usdc_micro <= 0:
            raise ValueError("size_sol must be positive")
        sell = _order(base_url, key, SOL_MINT, USDC_MINT, lamports, timeout)
        buy = _order(base_url, key, USDC_MINT, SOL_MINT, usdc_micro, timeout)
        s_out = float(sell.get("outAmount") or (sell.get("order") or {}).get("outAmount") or 0)
        s_in = float(sell.get("inAmount") or (sell.get("order") or {}).get("inAmount") or 0)
        b_out = float(buy.get("outAmount") or (buy.get("order") or {}).get("outAmount") or 0)
        b_in = float(buy.get("inAmount") or (buy.get("order") or {}).get("inAmount") or 0)
        bid = (s_out / 1e6) / (s_in / 1e9)
        ask = (b_in / 1e6) / (b_out / 1e9)
        if not (bid > 0 and ask > 0):
            raise ValueError("non-positive bid/ask proxy")
        mid = (bid + ask) / 2
        if abs(bid - ask) / mid > 0.001:
            raise ValueError("crossed bid/ask proxy beyond 10 bps")
        lo, hi = (bid, ask) if bid <= ask else (ask, bid)
        return Quote(
            venue="dflow",
            bid=lo,
            ask=hi,
            mid=mid,
            spread_bps=max(0.0, (hi - lo) / mid * 10_000),
            ref_price_usd=float(ref_price_usd),
            fetched_at=time.time(),
            ok=True,
        )
    except urllib.error.HTTPError as e:
        return Quote(venue="dflow", ok=False, error=f"DFlow HTTP {e.code}",
                     fetched_at=time.time())
    except Exception as e:
        return Quote(venue="dflow", ok=False, error=f"{type(e).__name__}: {e}",
                     fetched_at=time.time())
