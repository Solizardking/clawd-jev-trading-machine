"""Backtest replay: recorded JSONL -> simulated fills -> report."""
import json
import tempfile
import unittest
from pathlib import Path

from clawd_jev.backtest import BANNER, replay
from clawd_jev.config import Config


def _config(state_root):
    return Config(venues=("jupiter",), ticket_sol=0.1, max_ticket_sol=1.0,
                  max_slippage_bps=50.0, fee_bps=25.0, paper_start_usdc=1000.0,
                  quote_stale_s=300.0, state_root=Path(state_root))


def _record(cycle, operation, target_id, quote=None):
    return {
        "cycle": cycle,
        "mode": "paper",
        "mock": True,
        "decision": {
            "operation": operation,
            "target_id": target_id,
            "confidence": 0.9,
            "rationale": "test",
            "model": "mock",
            "mock": True,
        },
        "quotes": {
            "jupiter": quote or {
                "venue": "jupiter", "bid": 119.0, "ask": 121.0, "mid": 120.0,
                "spread_bps": 16.7, "ref_price_usd": 120.0, "ok": True, "error": None,
            }
        },
    }


class TestReplay(unittest.TestCase):
    def test_replay_buy_then_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "decisions.jsonl"
            src.write_text("\n".join([
                json.dumps(_record(1, "CLICK", "BUY_SOL_JUPITER")),
                json.dumps(_record(2, "WAIT", None)),
            ]))
            run_dir = replay(src, _config(Path(tmp) / "runtime"))
            report = json.loads((run_dir / "report.json").read_text())
            self.assertEqual(report["cycles"], 2)
            self.assertEqual(report["filled"], 1)
            self.assertEqual(report["skipped"], 1)
            self.assertEqual(report["rejected"], 0)
            self.assertAlmostEqual(report["portfolio"]["sol"], 0.1)
            self.assertIn("simulated fills", report["banner"].lower())
            html_p = run_dir / "report.html"
            self.assertTrue(html_p.is_file())
            html_txt = html_p.read_text()
            self.assertIn("simulated fills", html_txt.lower())
            self.assertIn(BANNER.split("-")[0].strip(), html_txt)

    def test_replay_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                replay(Path(tmp) / "nope.jsonl", _config(Path(tmp)))

    def test_replay_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "empty.jsonl"
            src.write_text("")
            with self.assertRaises(ValueError):
                replay(src, _config(Path(tmp)))

    def test_replay_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.jsonl"
            src.write_text("{not json}\n")
            with self.assertRaises(ValueError):
                replay(src, _config(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
