"""pump.fun token-vs-SOL venue: per-token quoting, action space, sim fills.

- token_tag: deterministic mint-prefix tags, collision suffixes.
- get_token_quotes: DexScreener best-liquidity pair parsing, per-token fail-soft.
- Action space: BUY_<TAG>_PUMPFUN / SELL_<TAG>_PUMPFUN admitted only for
  quoted tokens; unknown tags fail closed to BLOCKED (DecisionError).
- Simulator: token fills debit SOL / credit tokens; SELL needs paper
  holdings; stale/missing quotes skip; mark-to-market values token holdings.
- Config: LOBSTER_PUMPFUN_TOKENS parsing + validation.
"""
import os
import time
import unittest
from unittest import mock

from clawd_jev.config import Config, ConfigError
from clawd_jev.decision import (Decision, DecisionError, build_criteria,
                                map_answer, offer_actions)
from clawd_jev.sim import (Portfolio, mark_to_market, parse_token_target,
                           simulate)
from clawd_jev.venues import pumpfun as pumpfun_venue
from clawd_jev.venues.pumpfun import TokenQuote, token_tag

# Public mainnet mints used as fixtures (addresses only, no keys).
CLAWD_MINT = "8cHzQHUS2s2h8TzCmfqPKYiM4dSt4roa3n7MyRLApump"   # tag 8CHZQH
SOL_MINT = "So11111111111111111111111111111111111111112"      # tag SO1111
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"    # tag EPJFWD
WSOL_MINT = "So11111111111111111111111111111111111111112"


def _pairs(mint, symbol="WIF", price_native="0.001", price_usd="0.12",
           liquidity=100_000.0, dex_id="pumpswap", quote_mint=WSOL_MINT):
    def one(addr, px, usd, liq, dex, qm):
        return {"pairAddress": addr,
                "dexId": dex,
                "baseToken": {"address": mint, "symbol": symbol},
                "quoteToken": {"address": qm, "symbol": "SOL" if qm == WSOL_MINT else "USDC"},
                "priceNative": px, "priceUsd": usd,
                "liquidity": {"usd": liq}}
    return [
        one("low-liq", "0.0009", "0.10", 10.0, dex_id, quote_mint),
        one("best", price_native, price_usd, liquidity, dex_id, quote_mint),
    ]


def _tq(mint=CLAWD_MINT, tag="8CHZQH", symbol="WIF", price_sol=0.001,
        price_usd=0.12, ok=True, stale=False):
    return TokenQuote(
        mint=mint, tag=tag, symbol=symbol, price_sol=price_sol,
        price_usd=price_usd,
        fetched_at=time.time() - (9999 if stale else 0),
        ok=ok, error=None if ok else "boom")


def _cfg(**kw):
    base = dict(venues=("pumpfun",), ticket_sol=0.1, fee_bps=25.0,
                quote_stale_s=300.0)
    base.update(kw)
    return Config(**base)


def _decision(target_id):
    return Decision("CLICK", target_id, 0.9, "test", "mock", True)


def _ans(choice, conf=0.8):
    return {"type": "choice", "choice": choice, "confidence": conf,
            "probabilities": {choice: conf}}


class TestTokenTag(unittest.TestCase):
    def test_derives_first_six_upper(self):
        self.assertEqual(token_tag(CLAWD_MINT), "8CHZQH")
        self.assertEqual(token_tag(SOL_MINT), "SO1111")

    def test_collision_gets_numeric_suffix(self):
        m1 = "ABCDEF" + "1" * 38
        m2 = "ABCDEF" + "2" * 38
        self.assertEqual(token_tag(m1), "ABCDEF")
        self.assertEqual(token_tag(m2, taken=("ABCDEF",)), "ABCDEF2")

    def test_invalid_mint_raises(self):
        with self.assertRaises(ValueError):
            token_tag("!!!not-a-mint!!!")


class TestGetTokenQuotes(unittest.TestCase):
    def test_parses_best_liquidity_pair(self):
        with mock.patch.object(pumpfun_venue, "_get_json",
                               return_value=_pairs(CLAWD_MINT)):
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT], base_url="https://api.dexscreener.com")
        tq = out["8CHZQH"]
        self.assertTrue(tq.ok)
        self.assertEqual(tq.mint, CLAWD_MINT)
        self.assertEqual(tq.symbol, "WIF")
        self.assertAlmostEqual(tq.price_sol, 0.001)
        self.assertAlmostEqual(tq.price_usd, 0.12)

    def test_fail_soft_per_token(self):
        def flaky(url, timeout):
            if CLAWD_MINT in url:
                raise ConnectionError("down")
            return _pairs(USDC_MINT, symbol="USDC")
        with mock.patch.object(pumpfun_venue, "_get_json", side_effect=flaky):
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT, USDC_MINT], base_url="https://api.dexscreener.com")
        self.assertFalse(out["8CHZQH"].ok)
        self.assertIsNotNone(out["8CHZQH"].error)
        self.assertTrue(out["EPJFWD"].ok)

    def test_empty_mints(self):
        out = pumpfun_venue.get_token_quotes([], base_url="https://x")
        self.assertEqual(out, {})

    def test_dedups_duplicate_mints(self):
        with mock.patch.object(pumpfun_venue, "_get_json",
                               return_value=_pairs(CLAWD_MINT)) as m:
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT, CLAWD_MINT], base_url="https://api.dexscreener.com")
        self.assertEqual(m.call_count, 1)
        self.assertEqual(list(out), ["8CHZQH"])

    def test_no_pairs_is_unavailable(self):
        with mock.patch.object(pumpfun_venue, "_get_json", return_value=[]):
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT], base_url="https://api.dexscreener.com")
        self.assertFalse(out["8CHZQH"].ok)

    def test_non_sol_quoted_pairs_rejected(self):
        with mock.patch.object(pumpfun_venue, "_get_json",
                               return_value=_pairs(CLAWD_MINT,
                                                   quote_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")):
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT], base_url="https://api.dexscreener.com")
        tq = out["8CHZQH"]
        self.assertFalse(tq.ok)
        self.assertIn("no SOL-quoted pair", tq.error)

    def test_pumpfun_family_pair_preferred(self):
        pairs = [
            {"pairAddress": "ray", "dexId": "raydium",
             "baseToken": {"address": CLAWD_MINT, "symbol": "WIF"},
             "quoteToken": {"address": WSOL_MINT, "symbol": "SOL"},
             "priceNative": "0.001", "priceUsd": "0.12",
             "liquidity": {"usd": 1_000_000.0}},
            {"pairAddress": "pump", "dexId": "pumpswap",
             "baseToken": {"address": CLAWD_MINT, "symbol": "WIF"},
             "quoteToken": {"address": WSOL_MINT, "symbol": "SOL"},
             "priceNative": "0.0009", "priceUsd": "0.10",
             "liquidity": {"usd": 10.0}},
        ]
        with mock.patch.object(pumpfun_venue, "_get_json", return_value=pairs):
            out = pumpfun_venue.get_token_quotes(
                [CLAWD_MINT], base_url="https://api.dexscreener.com")
        tq = out["8CHZQH"]
        self.assertTrue(tq.ok)
        self.assertAlmostEqual(tq.price_sol, 0.0009)  # the pumpswap pair

    def test_venue_level_quote_still_unavailable(self):
        q = pumpfun_venue.get_quote()
        self.assertFalse(q.ok)
        self.assertIn("no SOL/USDC spot market", q.error)


class TestTokenActionSpace(unittest.TestCase):
    def test_token_tags_admitted(self):
        acts = offer_actions((), token_tags=("8CHZQH",))
        self.assertIn("BUY_8CHZQH_PUMPFUN", acts)
        self.assertIn("SELL_8CHZQH_PUMPFUN", acts)

    def test_no_tokens_no_token_actions(self):
        acts = offer_actions(())
        self.assertFalse([a for a in acts if a.endswith("_PUMPFUN") and "_SOL_" not in a])

    def test_malformed_and_sol_tags_ignored(self):
        acts = offer_actions((), token_tags=("bad-tag!", "SOL", ""))
        self.assertFalse([a for a in acts if "PUMPFUN" in a and "_SOL_" not in a])

    def test_offered_token_action_maps_to_click(self):
        acts = offer_actions((), token_tags=("8CHZQH",))
        d = map_answer(_ans("BUY_8CHZQH_PUMPFUN"), allowed=frozenset(acts))
        self.assertEqual(d.operation, "CLICK")
        self.assertEqual(d.target_id, "BUY_8CHZQH_PUMPFUN")

    def test_unknown_token_action_fails_closed(self):
        acts = offer_actions((), token_tags=("8CHZQH",))
        with self.assertRaises(DecisionError):
            map_answer(_ans("BUY_DEADBE_PUMPFUN"), allowed=frozenset(acts))

    def test_criteria_mentions_symbol_and_mint(self):
        acts = offer_actions((), token_tags=("8CHZQH",))
        info = {"BUY_8CHZQH_PUMPFUN": {"tag": "8CHZQH", "symbol": "WIF",
                                      "mint": CLAWD_MINT, "price_sol": 0.001,
                                      "price_usd": 0.12},
                "SELL_8CHZQH_PUMPFUN": {"tag": "8CHZQH", "symbol": "WIF",
                                       "mint": CLAWD_MINT, "price_sol": 0.001,
                                       "price_usd": 0.12}}
        crit = build_criteria(0.1, acts, token_info=info)
        self.assertIn("WIF", crit["BUY_8CHZQH_PUMPFUN"])
        self.assertIn(CLAWD_MINT, crit["BUY_8CHZQH_PUMPFUN"])
        self.assertIn("SOL/token", crit["SELL_8CHZQH_PUMPFUN"])

    def test_criteria_without_token_info_fails_closed(self):
        acts = offer_actions((), token_tags=("8CHZQH",))
        with self.assertRaises(DecisionError):
            build_criteria(0.1, acts)


class TestParseTokenTarget(unittest.TestCase):
    def test_token_target(self):
        self.assertEqual(parse_token_target("BUY_8CHZQH_PUMPFUN"), ("buy", "8CHZQH"))
        self.assertEqual(parse_token_target("SELL_EPJFWD_PUMPFUN"), ("sell", "EPJFWD"))

    def test_sol_action_is_not_token_target(self):
        with self.assertRaises(ValueError):
            parse_token_target("BUY_SOL_PUMPFUN")

    def test_garbage_raises(self):
        for bad in ("BUY_8CHZQH_JUPITER", "WAIT", "", None, "BUY__PUMPFUN"):
            with self.assertRaises(ValueError):
                parse_token_target(bad)


class TestTokenSim(unittest.TestCase):
    def test_buy_fill_math(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        tq = {"8CHZQH": _tq()}
        f = simulate(_decision("BUY_8CHZQH_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes=tq)
        self.assertEqual(f.status, "filled")
        self.assertEqual(f.venue, "pumpfun")
        self.assertEqual(f.side, "buy")
        self.assertAlmostEqual(f.price_sol, 0.001)
        self.assertAlmostEqual(f.token_amount, 100.0)   # 0.1 SOL / 0.001
        self.assertEqual(f.token_mint, CLAWD_MINT)
        self.assertAlmostEqual(pf.sol, 10.0 - 0.1 - 0.00025)  # ticket + 25bps fee
        self.assertAlmostEqual(pf.tokens[CLAWD_MINT], 100.0)
        self.assertIsNone(f.price_usdc)

    def test_buy_insufficient_sol_rejected(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 0.05, 1000.0)
        f = simulate(_decision("BUY_8CHZQH_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes={"8CHZQH": _tq()})
        self.assertEqual(f.status, "rejected")
        self.assertIn("insufficient paper SOL", f.reason)
        self.assertEqual(pf.tokens, {})

    def test_sell_without_holdings_rejected(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        f = simulate(_decision("SELL_8CHZQH_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes={"8CHZQH": _tq()})
        self.assertEqual(f.status, "rejected")
        self.assertIn("insufficient paper", f.reason)

    def test_sell_after_buy_fills(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        tq = {"8CHZQH": _tq()}
        simulate(_decision("BUY_8CHZQH_PUMPFUN"), {}, pf, cfg, 1, token_quotes=tq)
        sol_after_buy = pf.sol
        f = simulate(_decision("SELL_8CHZQH_PUMPFUN"), {}, pf, cfg, 2,
                     token_quotes=tq)
        self.assertEqual(f.status, "filled")
        self.assertAlmostEqual(pf.tokens[CLAWD_MINT], 0.0)
        self.assertAlmostEqual(pf.sol, sol_after_buy + 0.1 - 0.00025)

    def test_stale_quote_skipped(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        f = simulate(_decision("BUY_8CHZQH_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes={"8CHZQH": _tq(stale=True)})
        self.assertEqual(f.status, "skipped")

    def test_unknown_tag_skipped_fail_closed(self):
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        f = simulate(_decision("BUY_DEADBE_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes={"8CHZQH": _tq()})
        self.assertEqual(f.status, "skipped")
        self.assertIn("unavailable", f.reason)
        self.assertEqual(pf.tokens, {})

    def test_sol_pumpfun_action_keeps_old_path(self):
        # BUY_SOL_PUMPFUN is a SOL/USDC action (never quoted); it must NOT
        # take the token path.
        cfg = _cfg()
        pf = Portfolio(1000.0, 10.0, 1000.0)
        f = simulate(_decision("BUY_SOL_PUMPFUN"), {}, pf, cfg, 1,
                     token_quotes={"8CHZQH": _tq()})
        self.assertEqual(f.status, "skipped")
        self.assertIn("quote unavailable", f.reason)


class TestTokenMTM(unittest.TestCase):
    def test_token_holdings_valued(self):
        pf = Portfolio(1000.0, 5.0, 1000.0, tokens={CLAWD_MINT: 100.0})
        mtm = mark_to_market(pf, 120.0,
                             token_quotes={CLAWD_MINT: _tq(price_sol=0.001)})
        # 100 tokens * 0.001 SOL * $120 = $12
        self.assertAlmostEqual(mtm["tokens_usdc"], 12.0)
        self.assertAlmostEqual(mtm["total_usdc"], 1000.0 + 5.0 * 120.0 + 12.0)

    def test_no_tokens_backward_compatible(self):
        pf = Portfolio(1000.0, 5.0, 1000.0)
        mtm = mark_to_market(pf, 120.0)
        self.assertAlmostEqual(mtm["total_usdc"], 1600.0)
        self.assertEqual(mtm["tokens_usdc"], 0.0)


class TestPumpfunTokenConfig(unittest.TestCase):
    def test_tokens_env_parses(self):
        with mock.patch.dict(os.environ, {"LOBSTER_PUMPFUN_TOKENS":
                                           f"{CLAWD_MINT}, {USDC_MINT}"}):
            cfg = Config.from_env()
        self.assertEqual(cfg.pumpfun_tokens, (CLAWD_MINT, USDC_MINT))

    def test_tokens_default_empty(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOBSTER_PUMPFUN_TOKENS", None)
            cfg = Config.from_env()
        self.assertEqual(cfg.pumpfun_tokens, ())

    def test_invalid_mint_rejected(self):
        with mock.patch.dict(os.environ, {"LOBSTER_PUMPFUN_TOKENS": "not-a-mint"}):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_duplicate_mints_rejected(self):
        with mock.patch.dict(os.environ,
                             {"LOBSTER_PUMPFUN_TOKENS": f"{CLAWD_MINT},{CLAWD_MINT}"}):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_quote_url_override_and_validation(self):
        with mock.patch.dict(os.environ,
                             {"LOBSTER_PUMPFUN_QUOTE_URL": "https://example.com/q"}):
            cfg = Config.from_env()
        self.assertEqual(cfg.pumpfun_quote_url, "https://example.com/q")
        with mock.patch.dict(os.environ, {"LOBSTER_PUMPFUN_QUOTE_URL": "notaurl"}):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_redacted_summary_lists_tokens(self):
        with mock.patch.dict(os.environ,
                             {"LOBSTER_PUMPFUN_TOKENS": CLAWD_MINT}):
            cfg = Config.from_env()
        self.assertEqual(cfg.redacted_summary()["pumpfun_tokens"], [CLAWD_MINT])


if __name__ == "__main__":
    unittest.main()
