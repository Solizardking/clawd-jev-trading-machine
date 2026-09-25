"""Environment-based configuration. Fail closed on invalid values.

Secret values (TYPESAFE_API_KEY, DFLOW_API_KEY) are never read here and never
stored on the Config object. Modules that need them read the environment at
call time; only key *presence* is recorded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


from .venues.pumpfun import valid_mint as _valid_pumpfun_mint


class ConfigError(Exception):
    """Raised when configuration is missing or out of range."""


ALLOWED_VENUES = ("jupiter", "dflow", "imperial", "pumpfun", "backpack")


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}")


@dataclass(frozen=True)
class Config:
    decision_model: str = "jev-latest"
    typesafe_base_url: str = "https://api.typesafe.ai"
    typesafe_timeout_s: float = 30.0
    typesafe_key_present: bool = False
    venues: tuple = ("jupiter",)
    ticket_sol: float = 0.1
    max_ticket_sol: float = 1.0
    quote_size_sol: float = 0.1
    max_slippage_bps: float = 50.0
    fee_bps: float = 25.0
    paper_start_usdc: float = 1000.0
    quote_stale_s: float = 300.0
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    jupiter_base_url: str = "https://lite-api.jup.ag"
    dflow_base_url: str = "https://quote-api.dflow.net"
    dflow_key_present: bool = False
    backpack_base_url: str = "https://api.backpack.exchange"
    pumpfun_tokens: tuple = ()
    pumpfun_quote_url: str = "https://api.dexscreener.com"
    coingecko_base_url: str = "https://api.coingecko.com"
    coingecko_key_present: bool = False
    supermemory_base_url: str = "https://api.supermemory.ai"
    supermemory_key_present: bool = False
    state_root: Path = Path("runtime")

    @classmethod
    def from_env(cls) -> "Config":
        venues_raw = os.environ.get("LOBSTER_VENUES", "jupiter")
        venues = tuple(v.strip().lower() for v in venues_raw.split(",") if v.strip())
        tokens_raw = os.environ.get("LOBSTER_PUMPFUN_TOKENS", "")
        pumpfun_tokens = tuple(
            t.strip() for t in tokens_raw.split(",") if t.strip())
        cfg = cls(
            decision_model=_str("LOBSTER_DECISION_MODEL", "jev-latest"),
            typesafe_base_url=_str("LOBSTER_TYPESAFE_URL", "https://api.typesafe.ai").rstrip("/"),
            typesafe_timeout_s=_float("LOBSTER_TYPESAFE_TIMEOUT_S", 30.0),
            typesafe_key_present=bool(os.environ.get("TYPESAFE_API_KEY")),
            venues=venues,
            ticket_sol=_float("LOBSTER_TICKET_SOL", 0.1),
            max_ticket_sol=_float("LOBSTER_MAX_TICKET_SOL", 1.0),
            quote_size_sol=_float("LOBSTER_QUOTE_SIZE_SOL", 0.1),
            max_slippage_bps=_float("LOBSTER_MAX_SLIPPAGE_BPS", 50.0),
            fee_bps=_float("LOBSTER_FEE_BPS", 25.0),
            paper_start_usdc=_float("LOBSTER_PAPER_START_USDC", 1000.0),
            quote_stale_s=_float("LOBSTER_QUOTE_STALE_S", 300.0),
            rpc_url=_str("LOBSTER_RPC_URL", "https://api.mainnet-beta.solana.com"),
            jupiter_base_url=_str("LOBSTER_JUPITER_URL", "https://lite-api.jup.ag").rstrip("/"),
            dflow_base_url=_str("LOBSTER_DFLOW_URL", "https://quote-api.dflow.net").rstrip("/"),
            dflow_key_present=bool(os.environ.get("DFLOW_API_KEY")),
            backpack_base_url=_str("LOBSTER_BACKPACK_URL", "https://api.backpack.exchange").rstrip("/"),
            pumpfun_tokens=pumpfun_tokens,
            pumpfun_quote_url=_str("LOBSTER_PUMPFUN_QUOTE_URL", "https://api.dexscreener.com").rstrip("/"),
            coingecko_base_url=_str("COINGECKO_BASE_URL", "https://api.coingecko.com").rstrip("/"),
            coingecko_key_present=bool(os.environ.get("COINGECKO_API_KEY")),
            supermemory_base_url=_str("SUPERMEMORY_BASE_URL", "https://api.supermemory.ai").rstrip("/"),
            supermemory_key_present=bool(os.environ.get("SUPERMEMORY_API_KEY")),
            state_root=Path(_str("LOBSTER_STATE_DIR", "runtime")),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.venues:
            raise ConfigError("LOBSTER_VENUES must name at least one venue")
        for v in self.venues:
            if v not in ALLOWED_VENUES:
                raise ConfigError(f"unknown venue {v!r}; allowed: {', '.join(ALLOWED_VENUES)}")
        for t in self.pumpfun_tokens:
            if not _valid_pumpfun_mint(t):
                raise ConfigError(
                    f"LOBSTER_PUMPFUN_TOKENS has invalid mint {t!r}; "
                    "expected comma-separated base58 Solana mint addresses")
        if len(set(self.pumpfun_tokens)) != len(self.pumpfun_tokens):
            raise ConfigError("LOBSTER_PUMPFUN_TOKENS has duplicate mints")
        if not self.decision_model:
            raise ConfigError("LOBSTER_DECISION_MODEL must not be empty")
        for attr in ("typesafe_base_url", "jupiter_base_url", "dflow_base_url",
                     "backpack_base_url", "pumpfun_quote_url",
                     "coingecko_base_url", "supermemory_base_url", "rpc_url"):
            url = getattr(self, attr)
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ConfigError(f"{attr} must be an http(s) URL, got {url!r}")
        if self.typesafe_timeout_s <= 0:
            raise ConfigError("LOBSTER_TYPESAFE_TIMEOUT_S must be positive")
        if not self.ticket_sol > 0:
            raise ConfigError("LOBSTER_TICKET_SOL must be positive")
        if not self.max_ticket_sol > 0:
            raise ConfigError("LOBSTER_MAX_TICKET_SOL must be positive")
        if self.ticket_sol > self.max_ticket_sol:
            raise ConfigError("LOBSTER_TICKET_SOL must not exceed LOBSTER_MAX_TICKET_SOL")
        if not self.quote_size_sol > 0:
            raise ConfigError("LOBSTER_QUOTE_SIZE_SOL must be positive")
        if not 0 <= self.fee_bps <= 10_000:
            raise ConfigError("LOBSTER_FEE_BPS must be within [0, 10000]")
        if not 1 <= self.max_slippage_bps <= 10_000:
            raise ConfigError("LOBSTER_MAX_SLIPPAGE_BPS must be within [1, 10000]")
        if not self.paper_start_usdc > 0:
            raise ConfigError("LOBSTER_PAPER_START_USDC must be positive")
        if not self.quote_stale_s > 0:
            raise ConfigError("LOBSTER_QUOTE_STALE_S must be positive")

    def redacted_summary(self) -> dict:
        """Safe to print/log: key presence only, never key values."""
        return {
            "decision_model": self.decision_model,
            "typesafe_key_present": self.typesafe_key_present,
            "dflow_key_present": self.dflow_key_present,
            "coingecko_key_present": self.coingecko_key_present,
            "supermemory_key_present": self.supermemory_key_present,
            "venues": list(self.venues),
            "pumpfun_tokens": list(self.pumpfun_tokens),
            "ticket_sol": self.ticket_sol,
            "max_ticket_sol": self.max_ticket_sol,
            "max_slippage_bps": self.max_slippage_bps,
            "fee_bps": self.fee_bps,
            "paper_start_usdc": self.paper_start_usdc,
        }
