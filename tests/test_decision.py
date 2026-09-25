"""Dynamic action space + decision mapping: offer -> choice -> Decision."""
import unittest

from clawd_jev.decision import (
    ALWAYS_ACTIONS,
    DecisionError,
    build_criteria,
    build_questions,
    map_answer,
    offer_actions,
)


def _ans(choice, conf=0.8, probs=None):
    return {
        "type": "choice",
        "choice": choice,
        "confidence": conf,
        "probabilities": probs if probs is not None else {choice: conf},
    }


def _allowed(*venues):
    return frozenset(offer_actions(tuple(venues)))


class TestOfferActions(unittest.TestCase):
    def test_jupiter_only(self):
        self.assertEqual(offer_actions(("jupiter",)),
                         ("BUY_SOL_JUPITER", "SELL_SOL_JUPITER",
                          "WAIT", "OPEN_REVIEW", "BLOCKED"))

    def test_jupiter_and_dflow(self):
        acts = offer_actions(("jupiter", "dflow"))
        self.assertIn("BUY_SOL_JUPITER", acts)
        self.assertIn("SELL_SOL_DFLOW", acts)
        for a in ALWAYS_ACTIONS:
            self.assertIn(a, acts)

    def test_no_venues_still_safe(self):
        self.assertEqual(offer_actions(()), tuple(ALWAYS_ACTIONS))

    def test_imperial_stub_excluded(self):
        # imperial is a stub: never offered even when "reachable"
        self.assertEqual(offer_actions(("imperial",)), tuple(ALWAYS_ACTIONS))

    def test_unknown_venue_ignored(self):
        self.assertEqual(offer_actions(("atlantis",)), tuple(ALWAYS_ACTIONS))


class TestBuildQuestions(unittest.TestCase):
    def test_questions_cover_offered_space(self):
        actions = offer_actions(("jupiter",))
        q = build_questions(0.1, actions)
        self.assertEqual(q["action"]["type"], "choice")
        self.assertEqual(set(q["action"]["criteria"]), set(actions))
        self.assertIn("BUY_SOL_JUPITER", q["action"]["criteria"])
        self.assertNotIn("BUY_SOL_DFLOW", q["action"]["criteria"])

    def test_criteria_text_mentions_venue(self):
        crit = build_criteria(0.1, offer_actions(("jupiter", "dflow")))
        self.assertIn("Jupiter", crit["BUY_SOL_JUPITER"])
        self.assertIn("DFlow", crit["SELL_SOL_DFLOW"])

    def test_empty_space_raises(self):
        with self.assertRaises(DecisionError):
            build_questions(0.1, ())


class TestMapAnswer(unittest.TestCase):
    def test_buy_jupiter_maps_to_click(self):
        d = map_answer(_ans("BUY_SOL_JUPITER"), allowed=_allowed("jupiter"))
        self.assertEqual(d.operation, "CLICK")
        self.assertEqual(d.target_id, "BUY_SOL_JUPITER")
        self.assertAlmostEqual(d.confidence, 0.8)

    def test_all_directional_actions_click(self):
        allowed = _allowed("jupiter", "dflow")
        for action in ("BUY_SOL_JUPITER", "SELL_SOL_JUPITER",
                       "BUY_SOL_DFLOW", "SELL_SOL_DFLOW"):
            d = map_answer(_ans(action, 0.9), allowed=allowed)
            self.assertEqual(d.operation, "CLICK", action)
            self.assertEqual(d.target_id, action, action)

    def test_wait_open_review_blocked_have_no_target(self):
        allowed = _allowed()
        for action in ("WAIT", "OPEN_REVIEW", "BLOCKED"):
            d = map_answer(_ans(action, 0.7), allowed=allowed)
            self.assertEqual(d.operation, action, action)
            self.assertIsNone(d.target_id, action)

    def test_choice_outside_offered_space_raises(self):
        # BUY_SOL_DFLOW is valid in principle but was NOT offered this cycle:
        # fail closed to BLOCKED.
        with self.assertRaises(DecisionError):
            map_answer(_ans("BUY_SOL_DFLOW"), allowed=_allowed("jupiter"))

    def test_invalid_choice_raises(self):
        with self.assertRaises(DecisionError):
            map_answer(_ans("YOLO_SOL_MOON"), allowed=_allowed("jupiter"))

    def test_empty_allowed_raises(self):
        with self.assertRaises(DecisionError):
            map_answer(_ans("WAIT"), allowed=frozenset())

    def test_missing_choice_raises(self):
        with self.assertRaises(DecisionError):
            map_answer({"type": "choice", "confidence": 0.5},
                       allowed=_allowed("jupiter"))

    def test_non_dict_answer_raises(self):
        with self.assertRaises(DecisionError):
            map_answer("WAIT", allowed=_allowed("jupiter"))

    def test_missing_confidence_raises(self):
        with self.assertRaises(DecisionError):
            map_answer({"type": "choice", "choice": "WAIT"},
                       allowed=_allowed("jupiter"))

    def test_non_numeric_confidence_raises(self):
        with self.assertRaises(DecisionError):
            map_answer({"type": "choice", "choice": "WAIT", "confidence": "high"},
                       allowed=_allowed("jupiter"))

    def test_confidence_clamped(self):
        d = map_answer(_ans("WAIT", 1.5), allowed=_allowed())
        self.assertEqual(d.confidence, 1.0)
        d = map_answer(_ans("WAIT", -0.2), allowed=_allowed())
        self.assertEqual(d.confidence, 0.0)

    def test_rationale_mentions_choice_and_confidence(self):
        d = map_answer(_ans("BUY_SOL_JUPITER", 0.82,
                            {"BUY_SOL_JUPITER": 0.82, "WAIT": 0.18}),
                       allowed=_allowed("jupiter"))
        self.assertIn("BUY_SOL_JUPITER", d.rationale)
        self.assertIn("0.82", d.rationale)

    def test_mock_flag_labels_rationale(self):
        d = map_answer(_ans("WAIT", 1.0), allowed=_allowed(),
                       model="mock", mock=True)
        self.assertTrue(d.mock)
        self.assertIn("MOCK", d.rationale)


if __name__ == "__main__":
    unittest.main()
