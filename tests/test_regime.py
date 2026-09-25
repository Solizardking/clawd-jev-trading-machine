"""CoinGecko regime: classification thresholds + fail-soft fetch."""
import os
import unittest
from unittest import mock

from clawd_jev import regime


class TestClassify(unittest.TestCase):
    def test_risk_on(self):
        self.assertEqual(regime.classify(5.0, 2.0), "RISK_ON")

    def test_risk_off_sol(self):
        self.assertEqual(regime.classify(-4.0, 0.5), "RISK_OFF")

    def test_risk_off_btc(self):
        self.assertEqual(regime.classify(1.0, -3.0), "RISK_OFF")

    def test_chop(self):
        self.assertEqual(regime.classify(1.0, 0.5), "CHOP")
        self.assertEqual(regime.classify(-1.0, 1.5), "CHOP")

    def test_missing_data_unknown(self):
        self.assertEqual(regime.classify(None, 2.0), "UNKNOWN")
        self.assertEqual(regime.classify(2.0, None), "UNKNOWN")
        self.assertEqual(regime.classify("n/a", 1.0), "UNKNOWN")

    def test_boundary_values(self):
        self.assertEqual(regime.classify(3.0, 1.0), "RISK_ON")
        self.assertEqual(regime.classify(-3.0, 0.0), "RISK_OFF")
        self.assertEqual(regime.classify(0.0, -2.0), "RISK_OFF")


class TestFetchFailSoft(unittest.TestCase):
    def _clean(self):
        return {k: v for k, v in os.environ.items()
                if not k.startswith("COINGECKO_")}

    def test_unreachable_returns_unknown(self):
        # Connection-refused localhost: no network dependency, fails fast.
        with mock.patch.dict(os.environ, self._clean(), clear=True):
            r = regime.fetch(base_url="http://127.0.0.1:1", timeout=3.0)
        self.assertEqual(r["status"], "unavailable")
        self.assertEqual(r["regime"], "UNKNOWN")
        self.assertIsNone(r["sol_24h_pct"])
        self.assertTrue(r["error"])
        self.assertFalse(r["key_present"])

    def test_key_presence_labeled_not_leaked(self):
        with mock.patch.dict(os.environ,
                             {**self._clean(), "COINGECKO_API_KEY": "sekret"},
                             clear=True):
            r = regime.fetch(base_url="http://127.0.0.1:1", timeout=3.0)
        self.assertTrue(r["key_present"])
        # the key value must never appear in the returned dict
        self.assertNotIn("sekret", repr(r))


if __name__ == "__main__":
    unittest.main()
