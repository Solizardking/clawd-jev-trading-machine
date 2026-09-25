"""New venues: backpack (live public book) + pumpfun (truthfully unavailable).

- backpack: quote parsing, admission on success, exclusion on failure.
- pumpfun: always unavailable (no SOL/USDC spot market); wiring admits it
  only if a quote ever succeeded; out-of-table choices still fail closed.
- config: new venues validate; unknown venues still rejected.
"""
import os
import unittest
from unittest import mock

from clawd_jev.config import ALLOWED_VENUES, Config, ConfigError
from clawd_jev.decision import DecisionError, build_criteria, map_answer, offer_actions
from clawd_jev.sim import parse_target
from clawd_jev.venues import backpack as backpack_venue
from clawd_jev.venues import pumpfun as pumpfun_venue


def _ans(choice, conf=0.8):
    return {"type": "choice", "choice": choice, "confidence": conf,
            "probabilities": {choice: conf}}


def _allowed(*venues):
    return frozenset(offer_actions(tuple(venues)))


def _fake_book(url, timeout):
    if "/depth" in url:
        # bids intentionally unsorted: best bid is the max, not bids[0]
        return {"bids": [["0.02", "5.0"], ["120.50", "10.0"], ["0.01", "4.0"]],
                "asks": [["120.62", "3.0"], ["120.60", "8.0"]]}
    if "/ticker" in url:
        return {"lastPrice": "120.55", "symbol": "SOL_USDC"}
    raise AssertionError(f"unexpected url {url}")


class TestBackpackAdapter(unittest.TestCase):
    def test_quote_parses_public_book(self):
        with mock.patch.object(backpack_venue, "_get_json", side_effect=_fake_book):
            q = backpack_venue.get_quote(0.1, base_url="https://api.backpack.exchange")
        self.assertTrue(q.ok)
        self.assertEqual(q.venue, "backpack")
        self.assertAlmostEqual(q.bid, 120.50)
        self.assertAlmostEqual(q.ask, 120.60)
        self.assertAlmostEqual(q.mid, 120.55)
        self.assertAlmostEqual(q.ref_price_usd, 120.55)
        self.assertGreater(q.spread_bps, 0)

    def test_quote_failure_is_fail_soft(self):
        q = backpack_venue.get_quote(0.1, base_url="http://127.0.0.1:1", timeout=2.0)
        self.assertFalse(q.ok)
        self.assertIsNotNone(q.error)

    def test_crossed_book_rejected(self):
        def crossed(url, timeout):
            if "/depth" in url:
                return {"bids": [["120.70", "1.0"]], "asks": [["120.60", "1.0"]]}
            return {"lastPrice": "120.65"}
        with mock.patch.object(backpack_venue, "_get_json", side_effect=crossed):
            q = backpack_venue.get_quote(0.1, base_url="https://api.backpack.exchange")
        self.assertFalse(q.ok)

    def test_admission_on_quote_success(self):
        acts = offer_actions(("backpack",))
        self.assertIn("BUY_SOL_BACKPACK", acts)
        self.assertIn("SELL_SOL_BACKPACK", acts)

    def test_exclusion_on_quote_failure(self):
        self.assertNotIn("BUY_SOL_BACKPACK", offer_actions(()))
        self.assertNotIn("SELL_SOL_BACKPACK", offer_actions(()))

    def test_criteria_mentions_backpack_exchange(self):
        crit = build_criteria(0.1, offer_actions(("backpack",)))
        self.assertIn("Backpack Exchange", crit["BUY_SOL_BACKPACK"])
        self.assertNotIn("DEX", crit["BUY_SOL_BACKPACK"])

    def test_buy_backpack_maps_to_click(self):
        d = map_answer(_ans("BUY_SOL_BACKPACK"), allowed=_allowed("backpack"))
        self.assertEqual(d.operation, "CLICK")
        self.assertEqual(d.target_id, "BUY_SOL_BACKPACK")

    def test_sim_parses_backpack_target(self):
        self.assertEqual(parse_target("BUY_SOL_BACKPACK"), ("buy", "backpack"))
        self.assertEqual(parse_target("SELL_SOL_BACKPACK"), ("sell", "backpack"))


class TestPumpfunAdapter(unittest.TestCase):
    def test_always_unavailable_truthful(self):
        q = pumpfun_venue.get_quote()
        self.assertFalse(q.ok)
        self.assertEqual(q.venue, "pumpfun")
        self.assertIn("no SOL/USDC spot market", q.error)

    def test_unavailable_without_network_or_key(self):
        # No args, no env, no network: still a clean fail-soft Quote.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUMPFUN_API_KEY", None)
            q = pumpfun_venue.get_quote(0.1, base_url="https://example.invalid")
        self.assertFalse(q.ok)

    def test_never_offered_while_unavailable(self):
        self.assertNotIn("BUY_SOL_PUMPFUN", offer_actions(()))
        self.assertNotIn("SELL_SOL_PUMPFUN", offer_actions(()))

    def test_wiring_admits_if_quote_ever_succeeds(self):
        # The action-space builder admits pumpfun like any spot venue; only
        # the (never succeeding) quote keeps it out in practice.
        acts = offer_actions(("pumpfun",))
        self.assertIn("BUY_SOL_PUMPFUN", acts)
        self.assertIn("SELL_SOL_PUMPFUN", acts)

    def test_out_of_table_choice_fails_closed(self):
        with self.assertRaises(DecisionError):
            map_answer(_ans("BUY_SOL_PUMPFUN"), allowed=_allowed("jupiter"))
        with self.assertRaises(DecisionError):
            map_answer(_ans("SELL_SOL_BACKPACK"), allowed=_allowed("jupiter"))


class TestVenueConfig(unittest.TestCase):
    def test_allowed_venues_include_new(self):
        for v in ("jupiter", "dflow", "imperial", "pumpfun", "backpack"):
            self.assertIn(v, ALLOWED_VENUES)

    def test_venues_env_parses_new(self):
        with mock.patch.dict(os.environ, {"LOBSTER_VENUES": "jupiter,backpack,pumpfun"}):
            cfg = Config.from_env()
        self.assertEqual(cfg.venues, ("jupiter", "backpack", "pumpfun"))

    def test_backpack_url_override(self):
        with mock.patch.dict(os.environ,
                             {"LOBSTER_BACKPACK_URL": "https://example.com/bp"}):
            cfg = Config.from_env()
        self.assertEqual(cfg.backpack_base_url, "https://example.com/bp")

    def test_backpack_url_must_be_http(self):
        with mock.patch.dict(os.environ, {"LOBSTER_BACKPACK_URL": "notaurl"}):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_unknown_venue_still_rejected(self):
        with mock.patch.dict(os.environ, {"LOBSTER_VENUES": "atlantis"}):
            with self.assertRaises(ConfigError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
