"""Config validation: ranges, venues, and secret hygiene."""
import json
import os
import unittest
from unittest import mock

from clawd_jev.config import Config, ConfigError


def clean_env(**overrides):
    """Env with all LOBSTER_*/key vars stripped, then overrides applied."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LOBSTER_")
           and not k.startswith("COINGECKO_")
           and not k.startswith("SUPERMEMORY_")
           and k not in ("TYPESAFE_API_KEY", "DFLOW_API_KEY")}
    env.update(overrides)
    return env


class TestConfig(unittest.TestCase):
    def test_defaults_valid(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            c = Config.from_env()
            c.validate()
            self.assertEqual(c.venues, ("jupiter",))
            self.assertFalse(c.typesafe_key_present)

    def test_ticket_exceeds_max_raises(self):
        with mock.patch.dict(os.environ,
                             clean_env(LOBSTER_TICKET_SOL="2", LOBSTER_MAX_TICKET_SOL="1"),
                             clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_zero_ticket_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_TICKET_SOL="0"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_negative_fee_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_FEE_BPS="-1"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_fee_over_cap_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_FEE_BPS="10001"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_zero_slippage_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_MAX_SLIPPAGE_BPS="0"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_unknown_venue_raises(self):
        with mock.patch.dict(os.environ,
                             clean_env(LOBSTER_VENUES="jupiter,atlantis"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_empty_venues_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_VENUES=""), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_non_numeric_raises(self):
        with mock.patch.dict(os.environ, clean_env(LOBSTER_TICKET_SOL="abc"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_bad_url_raises(self):
        with mock.patch.dict(os.environ,
                             clean_env(LOBSTER_JUPITER_URL="not-a-url"), clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_redacted_summary_never_contains_secret_values(self):
        with mock.patch.dict(os.environ,
                             clean_env(TYPESAFE_API_KEY="sk-secret-abc123",
                                       DFLOW_API_KEY="df-secret-xyz789"),
                             clear=True):
            c = Config.from_env()
            blob = json.dumps(c.redacted_summary())
            self.assertNotIn("sk-secret-abc123", blob)
            self.assertNotIn("df-secret-xyz789", blob)
            self.assertTrue(c.typesafe_key_present)
            self.assertTrue(c.dflow_key_present)

    def test_key_presence_flags(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            self.assertFalse(Config.from_env().typesafe_key_present)

    def test_regime_memory_presence_flags(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            c = Config.from_env()
            self.assertFalse(c.coingecko_key_present)
            self.assertFalse(c.supermemory_key_present)
        with mock.patch.dict(os.environ,
                             clean_env(COINGECKO_API_KEY="cg-dummy-value-1",
                                       SUPERMEMORY_API_KEY="sm-dummy-value-2"),
                             clear=True):
            c = Config.from_env()
            self.assertTrue(c.coingecko_key_present)
            self.assertTrue(c.supermemory_key_present)
            blob = json.dumps(c.redacted_summary())
            self.assertNotIn("cg-dummy-value-1", blob)
            self.assertNotIn("sm-dummy-value-2", blob)

    def test_bad_coingecko_url_raises(self):
        with mock.patch.dict(os.environ,
                             clean_env(COINGECKO_BASE_URL="not-a-url"),
                             clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
