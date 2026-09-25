"""pump.fun venue - token-vs-SOL quoting (no SOL/USDC spot market exists).

pump.fun is a token-launch protocol: its markets are bonding-curve and Pump
AMM pools where tokens trade *against SOL*. There is no SOL/USDC spot market
on pump.fun, so the venue-level ``get_quote()`` below always reports
unavailable (kept for the SOL/USDC action-space wiring).

The real venue path is token-vs-SOL: for each mint in the configured token
universe (``LOBSTER_PUMPFUN_TOKENS``), ``get_token_quotes()`` fetches an
indicative token price in SOL per token via the keyless DexScreener token
API (best-liquidity pair's ``priceNative``). The engine then offers
``BUY_<TAG>_PUMPFUN`` / ``SELL_<TAG>_PUMPFUN`` per quotable token, admitted
only when that token's quote succeeds. With no tokens configured the venue
stays unavailable, truthfully labeled.

Quotes are indicative mids - pump.fun has no central order book, so there is
no bid/ask spread to report; fills are simulated at the quoted price by the
dry-run simulator. No signing, no broadcast, no live-order path exists.
Read-only by construction.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import Quote

# Large DexScreener responses get truncated by the egress proxy on the
# urllib path (IncompleteRead); curl is reliable there. urllib remains as
# fallback where curl is unavailable.

__all__ = [
    "UNAVAILABLE_REASON", "TokenQuote", "token_tag", "get_quote",
    "get_token_quotes",
]

UNAVAILABLE_REASON = (
    "no SOL/USDC spot market on pump.fun "
    "(bonding-curve and Pump AMM markets are token-vs-SOL)"
)

USER_AGENT = "clawd-jev/0.2"
TAG_LEN = 6
_TAG_RE = re.compile(r"^[A-Z0-9]{1,12}$")
_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Wrapped SOL: the quote leg must be native SOL so priceNative is SOL/token.
WSOL_MINT = "So11111111111111111111111111111111111111112"
# pump.fun-family DEX ids; preferred when several SOL-quoted pairs exist.
PUMPFUN_DEX_IDS = ("pumpswap", "pumpfun")


def valid_mint(mint: str) -> bool:
    """Loose base58 sanity check for a Solana mint address."""
    return isinstance(mint, str) and bool(_MINT_RE.match(mint))


def token_tag(mint: str, taken: tuple = ()) -> str:
    """Deterministic action-space tag from a mint: first 6 chars, uppercased.

    Never returns "SOL" (reserved for the SOL/USDC action ids); collisions
    get a numeric suffix. Deterministic for a given (mint, taken) input.
    """
    base = (mint or "")[:TAG_LEN].upper()
    if not base or not _TAG_RE.match(base):
        raise ValueError(f"cannot derive tag from mint {mint!r}")
    tag = base
    n = 2
    candidate = tag
    while candidate in taken:
        candidate = f"{tag}{n}"
        n += 1
    return candidate


@dataclass
class TokenQuote:
    """Indicative token-vs-SOL quote. price_sol is SOL per whole token."""
    mint: str
    tag: str
    symbol: str | None = None
    price_sol: float | None = None
    price_usd: float | None = None
    fetched_at: float = 0.0
    ok: bool = False
    error: str | None = None

    def age_s(self) -> float:
        return time.time() - self.fetched_at if self.fetched_at else float("inf")

    def is_stale(self, cutoff_s: float) -> bool:
        return (not self.ok) or self.age_s() > cutoff_s

    def to_dict(self) -> dict:
        return {
            "mint": self.mint,
            "tag": self.tag,
            "symbol": self.symbol,
            "price_sol": self.price_sol,
            "price_usd": self.price_usd,
            "ok": self.ok,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TokenQuote":
        d = d or {}
        return cls(
            mint=str(d.get("mint") or ""),
            tag=str(d.get("tag") or ""),
            symbol=d.get("symbol"),
            price_sol=d.get("price_sol"),
            price_usd=d.get("price_usd"),
            fetched_at=time.time(),
            ok=bool(d.get("ok")),
            error=d.get("error"),
        )


def get_quote(*args, **kwargs) -> Quote:
    """Venue-level SOL/USDC quote: always unavailable (no such market)."""
    return Quote(venue="pumpfun", ok=False, error=UNAVAILABLE_REASON)


def _urllib_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def _curl_get(url: str, timeout: float) -> str:
    proc = subprocess.run(
        ["curl", "-fsSL", "--max-time", str(max(1, int(timeout))),
         "-H", f"User-Agent: {USER_AGENT}", "-H", "Accept: application/json",
         url],
        capture_output=True, timeout=timeout + 5)
    if proc.returncode != 0:
        raise ConnectionError(
            f"curl exit {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout.decode("utf-8")


def _get_json(url: str, timeout: float):
    """Fetch and decode JSON, raising ConnectionError on any failure."""
    try:
        if shutil.which("curl"):
            body = _curl_get(url, timeout)
        else:
            body = _urllib_get(url, timeout)
        return json.loads(body)
    except (ConnectionError, json.JSONDecodeError) as e:
        raise ConnectionError(f"GET {url}: {type(e).__name__}: {e}")
    except (subprocess.SubprocessError, OSError, urllib.error.URLError) as e:
        raise ConnectionError(f"GET {url}: {type(e).__name__}: {e}")


def _quote_one(mint: str, tag: str, *, base_url: str, timeout: float) -> TokenQuote:
    """Fetch one token's indicative SOL price. Never raises.

    Picks the best pump.fun-family SOL-quoted pair (falls back to any
    SOL-quoted pair) so the quoted unit is always SOL per token.
    """
    try:
        if not valid_mint(mint):
            raise ValueError(f"invalid mint {mint!r}")
        pairs = _get_json(f"{base_url}/tokens/v1/solana/{mint}", timeout)
        if not isinstance(pairs, list) or not pairs:
            raise ValueError("no pairs returned for mint")

        def _liq(p):
            try:
                return float((p.get("liquidity") or {}).get("usd") or 0)
            except (TypeError, ValueError):
                return 0.0

        def _sol_quoted(p) -> bool:
            try:
                px = float(p.get("priceNative") or 0)
            except (TypeError, ValueError):
                return False
            quote_mint = ((p.get("quoteToken") or {}).get("address") or "")
            return px > 0 and quote_mint == WSOL_MINT

        eligible = [p for p in pairs if _sol_quoted(p)]
        if not eligible:
            raise ValueError("no SOL-quoted pair with a positive native price")

        def _rank(p):
            fam = 1 if (p.get("dexId") or "").lower() in PUMPFUN_DEX_IDS else 0
            return (fam, _liq(p))

        best = max(eligible, key=_rank)
        price_sol = float(best["priceNative"])
        try:
            price_usd = float(best.get("priceUsd") or 0) or None
        except (TypeError, ValueError):
            price_usd = None
        base_token = best.get("baseToken") or {}
        symbol = base_token.get("symbol")
        return TokenQuote(
            mint=mint, tag=tag,
            symbol=str(symbol) if symbol else None,
            price_sol=price_sol, price_usd=price_usd,
            fetched_at=time.time(), ok=True,
        )
    except Exception as e:
        return TokenQuote(mint=mint, tag=tag, ok=False,
                          error=f"{type(e).__name__}: {e}",
                          fetched_at=time.time())


def get_token_quotes(mints, *, base_url: str, timeout: float = 12.0) -> dict:
    """Per-token indicative quotes, keyed by deterministic tag.

    Fail-soft per token: one bad mint never poisons the rest. Duplicate
    mints are collapsed; tag collisions get a numeric suffix.
    """
    out: dict[str, TokenQuote] = {}
    seen_mints: set[str] = set()
    taken: list[str] = []
    bad_n = 0
    for mint in mints or ():
        mint = (mint or "").strip()
        if not mint or mint in seen_mints:
            continue
        seen_mints.add(mint)
        try:
            tag = token_tag(mint, tuple(taken))
        except ValueError as e:
            # Recorded, never offered: key is not a valid action tag and the
            # quote is ok=False, so the action space ignores it.
            key = f"INVALID{bad_n}"
            bad_n += 1
            out[key] = TokenQuote(
                mint=mint, tag=key, ok=False, error=f"ValueError: {e}",
                fetched_at=time.time())
            continue
        taken.append(tag)
        out[tag] = _quote_one(mint, tag, base_url=base_url, timeout=timeout)
    return out
