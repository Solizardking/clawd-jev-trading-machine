"""Supermemory recall/write: disabled-without-key + fail-soft errors."""
import os
import unittest
from unittest import mock

from clawd_jev import memory


def _clean(**overrides):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SUPERMEMORY_")}
    env.update(overrides)
    return env


class TestDisabledWithoutKey(unittest.TestCase):
    def test_recall_disabled(self):
        with mock.patch.dict(os.environ, _clean(), clear=True):
            r = memory.recall("test query")
        self.assertFalse(r["enabled"])
        self.assertEqual(r["memories"], [])
        self.assertTrue(r["error"])

    def test_write_disabled_noop(self):
        with mock.patch.dict(os.environ, _clean(), clear=True):
            r = memory.write("cycle 1: WAIT")
        self.assertFalse(r["enabled"])
        self.assertFalse(r["ok"])

    def test_enabled_flag(self):
        with mock.patch.dict(os.environ, _clean(), clear=True):
            self.assertFalse(memory.enabled())
        with mock.patch.dict(os.environ,
                             _clean(SUPERMEMORY_API_KEY="x"), clear=True):
            self.assertTrue(memory.enabled())


class TestFailSoftWithKey(unittest.TestCase):
    def test_recall_network_failure_labeled(self):
        # Unreachable base URL: must return an error dict, never raise.
        with mock.patch.dict(os.environ,
                             _clean(SUPERMEMORY_API_KEY="sekret",
                                    SUPERMEMORY_BASE_URL="http://127.0.0.1:1"),
                             clear=True):
            r = memory.recall("test query", timeout=3.0)
        self.assertTrue(r["enabled"])
        self.assertEqual(r["memories"], [])
        self.assertTrue(r["error"])
        self.assertNotIn("sekret", repr(r))

    def test_write_network_failure_labeled(self):
        with mock.patch.dict(os.environ,
                             _clean(SUPERMEMORY_API_KEY="sekret",
                                    SUPERMEMORY_BASE_URL="http://127.0.0.1:1"),
                             clear=True):
            r = memory.write("cycle 1: WAIT", timeout=3.0)
        self.assertTrue(r["enabled"])
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])


if __name__ == "__main__":
    unittest.main()
