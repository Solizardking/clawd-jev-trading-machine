"""CoinGecko market-regime context (read-only, fail-soft).

Mirrors the musebook JEV endpoints: every decision carries market context.
Pulls SOL + BTC 24h stats from CoinGecko's public /coins/markets endpoint
and derives a simple labeled regime from 24h-change thresholds. An optional
COINGECKO_API_KEY is sent as the x-cg-demo-api-key header, exactly like the
musebook worker does.

Fail-soft: never raises. When unreachable, rate-limited, or the key is
missing, returns regime="UNKNOWN" with status="unavailable" and labels it
honestly in the JEV state.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

USER_AGENT = "clawd-jev/0.2"

# Thresholds in percent (24h change). Kept deliberately coarse: this is a
# regime label for the JEV state, not a trading signal by itself.
RISK_OFF_SOL_PCT = -3.0
RISK_OFF_BTC_PCT = -2.0
RISK_ON_SOL_PCT = 3.0
RISK_ON_BTC_PCT = 1.0


def classify(sol_chg_24h, btc_chg_24h) -> str:
    """Pure function: 24h percent changes -> regime label."""
    if sol_chg_24h is None or btc_chg_24h is None:
        return "UNKNOWN"
    try:
        s, b = float(sol_chg_24h), float(btc_chg_24h)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if s <= RISK_OFF_SOL_PCT or b <= RISK_OFF_BTC_PCT:
        return "RISK_OFF"
    if s >= RISK_ON_SOL_PCT and b >= RISK_ON_BTC_PCT:
        return "RISK_ON"
    return "CHOP"


def _unavailable(error: str, key_present: bool) -> dict:
    return {
        "status": "unavailable",
        "regime": "UNKNOWN",
        "sol_price_usd": None,
        "sol_24h_pct": None,
        "btc_price_usd": None,
        "btc_24h_pct": None,
        "sol_volume_24h_usd": None,
        "btc_volume_24h_usd": None,
        "source": "coingecko",
        "key_present": key_present,
        "error": error,
        "fetched_at": None,
    }


def fetch(*, base_url: str = "https://api.coingecko.com",
          timeout: float = 12.0) -> dict:
    """Fetch SOL+BTC markets and classify the regime. Never raises."""
    key = os.environ.get("COINGECKO_API_KEY")
    key_present = bool(key)
    url = (base_url.rstrip("/") + "/api/v3/coins/markets"
           "?vs_currency=usd&ids=solana,bitcoin&price_change_percentage=24h")
    headers = {"accept": "application/json", "user-agent": USER_AGENT}
    if key:
        headers["x-cg-demo-api-key"] = key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, list):
            raise ValueError("unexpected CoinGecko response shape")
        by_id = {c.get("id"): c for c in data if isinstance(c, dict)}
        sol = by_id.get("solana") or {}
        btc = by_id.get("bitcoin") or {}
        sol_chg = sol.get("price_change_percentage_24h")
        btc_chg = btc.get("price_change_percentage_24h")
        return {
            "status": "ok",
            "regime": classify(sol_chg, btc_chg),
            "sol_price_usd": sol.get("current_price"),
            "sol_24h_pct": sol_chg,
            "btc_price_usd": btc.get("current_price"),
            "btc_24h_pct": btc_chg,
            "sol_volume_24h_usd": sol.get("total_volume"),
            "btc_volume_24h_usd": btc.get("total_volume"),
            "source": "coingecko",
            "key_present": key_present,
            "error": None,
            "fetched_at": time.time(),
        }
    except urllib.error.HTTPError as e:
        return _unavailable(f"CoinGecko HTTP {e.code}", key_present)
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {e}", key_present)
