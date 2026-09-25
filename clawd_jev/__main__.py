"""clawd_jev CLI: doctor / paper / live / backtest."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import memory as memory_mod
from . import regime as regime_mod
from .backtest import replay
from .config import Config, ConfigError
from .decision import Decision, DecisionError, build_questions, map_answer, offer_actions
from .mock import MOCK_MODEL, mock_ask
from .sim import Portfolio, mark_to_market, simulate
from .state import Tape, build_state, render
from .store import append_jsonl, new_run_dir, write_json
from .typesafe import JevError, ask as typesafe_ask
from .venues import Quote
from .venues import backpack as backpack_venue
from .venues import dflow as dflow_venue
from .venues import imperial as imperial_venue
from .venues import jupiter as jupiter_venue
from .venues import pumpfun as pumpfun_venue


def load_dotenv(path: str | None) -> None:
    """Minimal KEY=VALUE loader. Never overwrites already-exported vars."""
    p = Path(path) if path else Path(".env")
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


# ---------------------------------------------------------------- doctor ---

def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def fetch_all_quotes(config: Config) -> dict:
    quotes: dict[str, Quote] = {}
    if "jupiter" in config.venues:
        quotes["jupiter"] = jupiter_venue.get_quote(
            config.quote_size_sol, base_url=config.jupiter_base_url, timeout=12.0)
    if "dflow" in config.venues:
        ref = quotes["jupiter"].ref_price_usd if quotes.get("jupiter") and quotes["jupiter"].ok else None
        quotes["dflow"] = dflow_venue.get_quote(
            config.quote_size_sol, base_url=config.dflow_base_url,
            ref_price_usd=ref, timeout=12.0)
    if "imperial" in config.venues:
        quotes["imperial"] = imperial_venue.get_quote()
    if "backpack" in config.venues:
        quotes["backpack"] = backpack_venue.get_quote(
            config.quote_size_sol, base_url=config.backpack_base_url, timeout=12.0)
    if "pumpfun" in config.venues:
        quotes["pumpfun"] = pumpfun_venue.get_quote()
    return quotes


def fetch_pumpfun_tokens(config: Config) -> dict:
    """Token-vs-SOL indicative quotes, keyed by deterministic tag.

    Empty unless the pumpfun venue is enabled AND LOBSTER_PUMPFUN_TOKENS
    names at least one mint. Fail-soft per token.
    """
    if "pumpfun" not in config.venues or not config.pumpfun_tokens:
        return {}
    return pumpfun_venue.get_token_quotes(
        config.pumpfun_tokens, base_url=config.pumpfun_quote_url, timeout=12.0)


def token_info_for(token_quotes: dict) -> dict:
    """Criteria info per pump.fun token action id (ok quotes only)."""
    info = {}
    for tag, tq in token_quotes.items():
        if not tq.ok:
            continue
        for side in ("BUY", "SELL"):
            info[f"{side}_{tag}_PUMPFUN"] = {
                "tag": tag,
                "symbol": tq.symbol,
                "mint": tq.mint,
                "price_sol": tq.price_sol,
                "price_usd": tq.price_usd,
            }
    return info


def offered_actions(quotes: dict, token_quotes: dict | None = None) -> tuple:
    """Dynamic action space: venues with a fresh ok quote, plus pump.fun
    token tags with a fresh ok indicative quote."""
    available = tuple(sorted(n for n, q in quotes.items() if q.ok))
    tags = tuple(sorted(t for t, tq in (token_quotes or {}).items() if tq.ok))
    return offer_actions(available, token_tags=tags)


def cmd_doctor(args, config: Config) -> int:
    checks = [_check("config", "ok", f"valid ({len(config.venues)} venue(s): "
                                     f"{', '.join(config.venues)})")]
    if config.typesafe_key_present:
        checks.append(_check("typesafe", "ok",
                             f"key present; model {config.decision_model}"))
    else:
        checks.append(_check("typesafe", "warn",
                             "TYPESAFE_API_KEY not set - paper needs --dry-run-mock"))

    quotes = fetch_all_quotes(config)
    jq = quotes.get("jupiter")
    if jq is not None:
        if jq.ok:
            checks.append(_check("jupiter", "ok",
                                 f"SOL bid {jq.bid:.4f} / ask {jq.ask:.4f} USDC "
                                 f"(spread {jq.spread_bps:.1f}bps)"))
        else:
            checks.append(_check("jupiter", "warn", f"unreachable: {jq.error}"))
    dq = quotes.get("dflow")
    if dq is not None:
        if dq.ok:
            checks.append(_check("dflow", "ok",
                                 f"SOL bid {dq.bid:.4f} / ask {dq.ask:.4f} USDC"))
        elif not config.dflow_key_present:
            checks.append(_check("dflow", "warn", "DFLOW_API_KEY not set - venue unavailable"))
        else:
            checks.append(_check("dflow", "warn", f"unavailable: {dq.error}"))
    if "imperial" in config.venues:
        checks.append(_check("imperial", "warn", "stub - perps feed not wired"))
    bq = quotes.get("backpack")
    if bq is not None:
        if bq.ok:
            checks.append(_check("backpack", "ok",
                                 f"SOL bid {bq.bid:.4f} / ask {bq.ask:.4f} USDC "
                                 f"(spread {bq.spread_bps:.1f}bps; public book - "
                                 f"execution disabled, dry-run only)"))
        else:
            checks.append(_check("backpack", "warn", f"unreachable: {bq.error}"))
    pumpfun_tokens = {}
    if "pumpfun" in config.venues:
        pumpfun_tokens = fetch_pumpfun_tokens(config)
        if not config.pumpfun_tokens:
            checks.append(_check(
                "pumpfun", "warn",
                "no SOL/USDC spot market on pump.fun; "
                "LOBSTER_PUMPFUN_TOKENS empty - token-vs-SOL venue unavailable"))
        else:
            for tag, tq in pumpfun_tokens.items():
                if tq.ok:
                    usd = f" (~${tq.price_usd:.4f})" if tq.price_usd else ""
                    checks.append(_check(
                        "pumpfun:" + tag, "ok",
                        f"{tq.symbol or tag}: {tq.price_sol:.8f} SOL/token{usd} "
                        f"(indicative mid, dry-run only)"))
                else:
                    checks.append(_check(
                        "pumpfun:" + tag, "warn",
                        f"{tq.mint[:8]}...: {tq.error}"))

    actions = offered_actions(quotes, pumpfun_tokens)
    checks.append(_check("action-space", "ok",
                         f"{len(actions)} offered: {', '.join(actions)}"))

    rg = regime_mod.fetch(base_url=config.coingecko_base_url, timeout=10.0)
    if rg["status"] == "ok":
        checks.append(_check("regime", "ok",
                             f"{rg['regime']} (SOL {rg['sol_24h_pct']:+.2f}%/24h, "
                             f"BTC {rg['btc_24h_pct']:+.2f}%/24h, CoinGecko"
                             f"{' keyed' if rg['key_present'] else ''})"))
    else:
        checks.append(_check("regime", "warn",
                             f"UNKNOWN - {rg['error']} (degrades honestly)"))

    if config.supermemory_key_present:
        checks.append(_check("memory", "ok",
                             f"SUPERMEMORY_API_KEY present; tag {memory_mod.TAG}"))
    else:
        checks.append(_check("memory", "warn",
                             "SUPERMEMORY_API_KEY not set - memory disabled"))

    if args.json:
        print(json.dumps({"version": __version__, "checks": checks,
                          "config": config.redacted_summary()}, indent=2))
    else:
        print(f"clawd-jev {__version__} - doctor")
        for c in checks:
            mark = {"ok": "[ok]", "warn": "[warn]", "fail": "[FAIL]"}[c["status"]]
            print(f"  {mark} {c['name']}: {c['detail']}")
    return 0


# ----------------------------------------------------------------- paper ---


def cmd_paper(args, config: Config) -> int:
    mock = args.dry_run_mock
    if not mock and not config.typesafe_key_present:
        print("error: TYPESAFE_API_KEY not set - use --dry-run-mock or export the key.",
              file=sys.stderr)
        return 2

    run_dir = new_run_dir("paper", config.state_root)
    portfolio = Portfolio(config.paper_start_usdc, 0.0, config.paper_start_usdc)
    tape = Tape()
    ask_fn = mock_ask if mock else typesafe_ask
    label = "MOCK" if mock else config.decision_model
    print(f"clawd-jev {__version__} PAPER [{label}] - dry run, no orders, no signing")

    # CoinGecko regime: fetched once per run (free-tier friendly), reused
    # across cycles, labeled with fetched_at. UNKNOWN when unreachable.
    regime = regime_mod.fetch(base_url=config.coingecko_base_url, timeout=12.0)
    regime_txt = (f"regime {regime['regime']}"
                  if regime["status"] == "ok"
                  else "regime UNKNOWN (CoinGecko unreachable)")
    mem_txt = "memory on" if memory_mod.enabled() else "memory disabled"
    print(f"  {regime_txt} | {mem_txt}")
    last_mid = None

    for cycle in range(1, args.iterations + 1):
        quotes = fetch_all_quotes(config)
        token_quotes = fetch_pumpfun_tokens(config)
        jq = quotes.get("jupiter")
        if jq is not None and jq.mid:
            tape.add(jq.mid)
            last_mid = jq.mid
        # Dynamic action space: venues with a fresh quote this cycle, plus
        # pump.fun token tags with a fresh indicative quote.
        actions = offered_actions(quotes, token_quotes)
        questions = build_questions(config.ticket_sol, actions,
                                    token_info_for(token_quotes))
        allowed = frozenset(actions)
        # Memory recall before the JEV call; labeled honestly when disabled.
        mem = memory_mod.recall(
            f"SOL paper trade cycle {cycle}; {regime_txt}; goal: buy low, "
            f"sell high within ticket/risk caps; mode={'mock' if mock else 'jev'}",
            limit=5)
        state = build_state(cycle=cycle, quotes=quotes, tape=tape,
                            config=config, mock=mock, regime=regime,
                            memory={"enabled": mem["enabled"],
                                    "memories": mem["memories"],
                                    "error": mem["error"]},
                            token_quotes=token_quotes)
        try:
            if mock:
                res = ask_fn(render(state), questions)
            else:
                res = ask_fn(render(state), questions, model=config.decision_model,
                             base_url=config.typesafe_base_url,
                             timeout_s=config.typesafe_timeout_s)
            raw = res["answers"]["action"]
            decision = map_answer(raw, allowed=allowed,
                                  model=res.get("model") or label, mock=mock)
        except (JevError, DecisionError, KeyError, TypeError) as e:
            decision = Decision("BLOCKED", None, 1.0,
                                f"fail-closed: {type(e).__name__}: {e}",
                                model=label, mock=mock)
        fill = simulate(decision, quotes, portfolio, config, cycle,
                        token_quotes=token_quotes)
        sol_mid = jq.mid if jq is not None and jq.ok else None
        mtm = mark_to_market(
            portfolio, sol_mid,
            token_quotes={tq.mint: tq for tq in token_quotes.values() if tq.ok})
        # Memory write after the simulated outcome (no-op when disabled).
        tok_txt = ", ".join(f"{m[:6]}:{a:.4f}"
                            for m, a in portfolio.tokens.items()) or "none"
        mem_write = memory_mod.write(
            f"cycle {cycle}: {decision.operation}"
            f"{' -> ' + decision.target_id if decision.target_id else ''} "
            f"conf={decision.confidence:.2f} fill={fill.status} ({fill.reason}); "
            f"portfolio {portfolio.usdc:.2f} USDC / {portfolio.sol:g} SOL / "
            f"tokens {{{tok_txt}}}; "
            f"{regime_txt}{' [MOCK]' if mock else ''}",
            metadata={"mode": "paper", "mock": mock, "cycle": cycle,
                      "regime": regime["regime"], "decision": decision.operation})
        append_jsonl(run_dir / "decisions.jsonl", {
            "cycle": cycle,
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": "paper",
            "mock": mock,
            "action_space": list(actions),
            "regime": regime["regime"],
            "memory_enabled": mem["enabled"],
            "memory_write_ok": mem_write["ok"],
            "decision": {
                "operation": decision.operation,
                "target_id": decision.target_id,
                "confidence": decision.confidence,
                "rationale": decision.rationale,
                "model": decision.model,
                "mock": decision.mock,
            },
            "quotes": {name: q.to_dict() for name, q in quotes.items()},
            "token_quotes": {tag: tq.to_dict()
                             for tag, tq in token_quotes.items()},
            "fill": fill.to_dict(),
            "portfolio": portfolio.to_dict(),
            "mark_to_market": mtm,
        })
        mid_txt = f"{jq.mid:.4f}" if jq is not None and jq.mid else "n/a"
        print(f"  [{cycle}/{args.iterations}] {decision.operation}"
              f"{' -> ' + decision.target_id if decision.target_id else ''}"
              f" conf={decision.confidence:.2f} fill={fill.status}"
              f" ({fill.reason}) SOLmid={mid_txt} space={len(actions)}")
        if cycle < args.iterations and args.interval_s > 0:
            time.sleep(args.interval_s)

    mtm = mark_to_market(portfolio, last_mid)
    write_json(run_dir / "report.json", {
        "mode": "paper",
        "mock": mock,
        "label": "dry-run - simulated fills only, no orders placed",
        "iterations": args.iterations,
        "portfolio": portfolio.to_dict(),
        "mark_to_market": mtm,
        "config": config.redacted_summary(),
    })
    print(f"done. portfolio: {portfolio.usdc:.2f} USDC / {portfolio.sol:g} SOL"
          + (f" / {len(portfolio.tokens)} token position(s)" if portfolio.tokens else ""))
    print(f"run dir: {run_dir}")
    return 0


# ------------------------------------------------------------------ live ---

LIVE_REFUSAL = (
    "REFUSING: live trading is not wired in this scaffold.\n"
    "clawd-jev never signs, never broadcasts, and holds no private keys by design.\n"
    "Live execution requires explicit user approval of exact terms plus a\n"
    "separately audited venue wiring. Nothing was sent anywhere."
)


def cmd_live(args, config: Config) -> int:
    print(LIVE_REFUSAL, file=sys.stderr)
    return 2


# -------------------------------------------------------------- backtest ---

def cmd_backtest(args, config: Config) -> int:
    try:
        run_dir = replay(args.decisions, config)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"replay complete: {run_dir}")
    print(f"  report.json + report.html (simulated fills only)")
    return 0


# ------------------------------------------------------------------- cli ---

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clawd_jev",
        description="clawd-JEV-trading machine: JEV-on-Solana trader (dry-run first).")
    p.add_argument("--env-file", default=None,
                   help="path to .env file (default: ./.env if present)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="validate config/env; check venue reachability")
    d.add_argument("--json", action="store_true", help="machine-readable output")

    pa = sub.add_parser("paper", help="dry-run loop: JEV decides, fills simulated")
    pa.add_argument("--iterations", type=int, default=3,
                    help="decision cycles to run (default: 3)")
    pa.add_argument("--dry-run-mock", action="store_true",
                    help="use the deterministic MOCK engine (labeled, never real JEV)")
    pa.add_argument("--interval-s", type=float, default=2.0,
                    help="pause between cycles in seconds (default: 2)")

    sub.add_parser("live", help="refuses: live trading is not wired")

    b = sub.add_parser("backtest", help="replay a recorded decisions.jsonl")
    b.add_argument("--decisions", required=True,
                   help="path to decisions.jsonl from a paper run")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(args.env_file)
    try:
        config = Config.from_env()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if args.command == "doctor":
        return cmd_doctor(args, config)
    if args.command == "paper":
        if args.iterations is not None and args.iterations < 1:
            print("error: --iterations must be positive", file=sys.stderr)
            return 2
        return cmd_paper(args, config)
    if args.command == "live":
        return cmd_live(args, config)
    if args.command == "backtest":
        return cmd_backtest(args, config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
