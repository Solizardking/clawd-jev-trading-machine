"""Dry-run simulator math: fills, rejections, skips, mark-to-market."""
import time
import unittest

from clawd_jev.config import Config
from clawd_jev.decision import Decision
from clawd_jev.sim import Fill, Portfolio, mark_to_market, parse_target, simulate
from clawd_jev.venues import Quote


def _quote(**kw):
    base = dict(venue="jupiter", bid=119.0, ask=121.0, mid=120.0,
                spread_bps=16.7, ref_price_usd=120.0,
                fetched_at=time.time(), ok=True)
    base.update(kw)
    return Quote(**base)


def _config(**kw):
    base = dict(venues=("jupiter",), ticket_sol=0.1, max_ticket_sol=1.0,
                max_slippage_bps=50.0, fee_bps=25.0, paper_start_usdc=1000.0,
                quote_stale_s=300.0)
    base.update(kw)
    return Config(**base)


def _decision(op, target=None, conf=0.9):
    return Decision(operation=op, target_id=target, confidence=conf,
                    rationale="test", model="test")


class TestParseTarget(unittest.TestCase):
    def test_buy(self):
        self.assertEqual(parse_target("BUY_SOL_JUPITER"), ("buy", "jupiter"))

    def test_sell_dflow(self):
        self.assertEqual(parse_target("SELL_SOL_DFLOW"), ("sell", "dflow"))

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            parse_target("YOLO")


class TestSimulate(unittest.TestCase):
    def test_buy_fill_math(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.0, 1000.0)
        fill = simulate(_decision("CLICK", "BUY_SOL_JUPITER"), {"jupiter": _quote()},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "filled")
        # price = ask 121; notional = 0.1*121 = 12.1; fee 25bps = 0.03025
        self.assertAlmostEqual(fill.price_usdc, 121.0)
        self.assertAlmostEqual(fill.notional_usdc, 12.1)
        self.assertAlmostEqual(fill.fee_usdc, 0.03025)
        self.assertAlmostEqual(pf.usdc, 1000.0 - 12.1 - 0.03025)
        self.assertAlmostEqual(pf.sol, 0.1)

    def test_sell_fill_math(self):
        cfg = _config()
        pf = Portfolio(1000.0, 1.0, 1000.0)
        fill = simulate(_decision("CLICK", "SELL_SOL_JUPITER"), {"jupiter": _quote()},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "filled")
        # price = bid 119; notional = 11.9; fee = 0.02975; usdc += 11.87025
        self.assertAlmostEqual(fill.price_usdc, 119.0)
        self.assertAlmostEqual(pf.sol, 0.9)
        self.assertAlmostEqual(pf.usdc, 1000.0 + 11.9 - 0.02975)

    def test_spread_rejection(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.0, 1000.0)
        q = _quote(spread_bps=100.0)
        fill = simulate(_decision("CLICK", "BUY_SOL_JUPITER"), {"jupiter": q},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "rejected")
        self.assertIn("spread", fill.reason)
        self.assertEqual(pf.usdc, 1000.0)
        self.assertEqual(pf.sol, 0.0)

    def test_insufficient_usdc_rejected(self):
        cfg = _config()
        pf = Portfolio(1.0, 0.0, 1000.0)
        fill = simulate(_decision("CLICK", "BUY_SOL_JUPITER"), {"jupiter": _quote()},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "rejected")
        self.assertIn("USDC", fill.reason)

    def test_insufficient_sol_rejected(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.0, 1000.0)
        fill = simulate(_decision("CLICK", "SELL_SOL_JUPITER"), {"jupiter": _quote()},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "rejected")
        self.assertIn("SOL", fill.reason)

    def test_stale_quote_skipped(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.0, 1000.0)
        q = _quote(fetched_at=time.time() - 10_000)
        fill = simulate(_decision("CLICK", "BUY_SOL_JUPITER"), {"jupiter": q},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "skipped")
        self.assertIn("stale", fill.reason)

    def test_missing_venue_skipped(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.0, 1000.0)
        fill = simulate(_decision("CLICK", "BUY_SOL_DFLOW"), {"jupiter": _quote()},
                        pf, cfg, 1)
        self.assertEqual(fill.status, "skipped")
        self.assertIn("dflow", fill.reason)

    def test_wait_skipped_portfolio_untouched(self):
        cfg = _config()
        pf = Portfolio(1000.0, 0.5, 1000.0)
        for op in ("WAIT", "OPEN_REVIEW", "BLOCKED"):
            fill = simulate(_decision(op), {"jupiter": _quote()}, pf, cfg, 1)
            self.assertEqual(fill.status, "skipped", op)
        self.assertEqual(pf.usdc, 1000.0)
        self.assertEqual(pf.sol, 0.5)


class TestMarkToMarket(unittest.TestCase):
    def test_mtm_math(self):
        pf = Portfolio(987.86975, 0.1, 1000.0)
        mtm = mark_to_market(pf, 120.0)
        # reports round to 4dp
        self.assertAlmostEqual(mtm["total_usdc"], round(987.86975 + 12.0, 4))
        self.assertAlmostEqual(mtm["pnl_usdc"], round(999.86975 - 1000.0, 4))

    def test_mtm_none_on_bad_mid(self):
        pf = Portfolio(1000.0, 0.0, 1000.0)
        self.assertIsNone(mark_to_market(pf, None))
        self.assertIsNone(mark_to_market(pf, 0))


if __name__ == "__main__":
    unittest.main()
