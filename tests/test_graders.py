#!/usr/bin/env python3
"""Pins for the grading seam (graders.py / label_model.py / slices.py). Pure functions, no live
claude — runs under `make test` exactly like the other CI-safe tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graders import Vote, DARK                      # noqa: E402
import label_model as LM                            # noqa: E402
from slices import Slice, slice_report              # noqa: E402


def _v(question, grader, vote, conf=1.0):
    return Vote(question=question, vote=vote, confidence=conf, grader=grader)


class Aggregate(unittest.TestCase):
    def test_all_abstain_is_none(self):
        self.assertEqual(LM.aggregate([Vote.no_opinion("q"), Vote.no_opinion("q")])[0], None)

    def test_equal_reliability_is_majority(self):
        # With no reliability override every grader weighs equally → plain majority: 2 'fail' win.
        votes = [_v("q", "a", "pass"), _v("q", "b", "fail"), _v("q", "c", "fail")]
        self.assertEqual(LM.aggregate(votes)[0], "fail")

    def test_reliable_dissenter_overturns_majority(self):
        # THE point of the layer: one trusted grader outvotes two coin-flippers. Plain majority says
        # 'fail'; the denoiser says 'pass'. A single hard-coded scorer can never down-weight noise.
        votes = [_v("q", "trusted", "pass"), _v("q", "n1", "fail"), _v("q", "n2", "fail")]
        reliab = {"trusted": 0.95, "n1": 0.55, "n2": 0.55}
        self.assertEqual(LM.aggregate(votes, reliab)[0], "pass")

    def test_low_self_confidence_is_discounted(self):
        votes = [_v("q", "a", "pass", conf=0.1), _v("q", "b", "fail", conf=1.0)]
        self.assertEqual(LM.aggregate(votes)[0], "fail")


class PerQuestion(unittest.TestCase):
    def test_questions_are_not_pooled(self):
        # The defect the review fixed: a 'command' vote and a 'no_error' vote must NOT compete in one
        # argmax. trial_verdict keeps them as separate verdicts.
        votes = [_v("command", "command", "dir-reply"), _v("no_error", "no_error", "ok")]
        verdict = LM.trial_verdict(votes)
        self.assertEqual(verdict["command"][0], "dir-reply")
        self.assertEqual(verdict["no_error"][0], "ok")

    def test_dark_is_a_label_not_an_abstention(self):
        # A dark trial (no command fired) is the LABEL DARK and counts in aggregation; only a missing
        # transcript abstains. Conflating them would silently hide the activation cliff.
        self.assertEqual(LM.aggregate([_v("command", "command", DARK)])[0], DARK)
        self.assertEqual(LM.aggregate([Vote.no_opinion("command")])[0], None)

    def test_pass_policy_requires_all_targets(self):
        verdict = {"command": ("dir-reply", 0.9), "no_error": ("ok", 0.9)}
        self.assertTrue(LM.pass_policy(verdict, {"command": "dir-reply", "no_error": "ok"}))
        self.assertFalse(LM.pass_policy(verdict, {"command": "dir-reply", "no_error": "error"}))
        # a dark command fails a dir-reply target (the cliff is a fail, not an abstain)
        self.assertFalse(LM.pass_policy({"command": (DARK, 0.9)}, {"command": "dir-reply"}))


class Reliability(unittest.TestCase):
    def test_consensus_agreer_scores_higher(self):
        # 'steady' always agrees with the per-question majority; 'flaky' often dissents → steady's
        # estimated reliability exceeds flaky's, with NO gold labels.
        tv = [[_v("q", "steady", "pass"), _v("q", "other", "pass"), _v("q", "flaky", "fail")],
              [_v("q", "steady", "fail"), _v("q", "other", "fail"), _v("q", "flaky", "pass")],
              [_v("q", "steady", "pass"), _v("q", "other", "pass"), _v("q", "flaky", "pass")]]
        r = LM.estimate_reliability(tv)
        self.assertGreater(r["steady"], r["flaky"])


class CellReliability(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(LM.cell_reliability([True] * 8 + [False] * 2)["rate"], 0.8)

    def test_ci_is_wide_at_n10(self):
        # Executable form of "N=10 can't distinguish 20% from 40%": 2/10 has a 95% CI spanning well
        # beyond +/-10pp. Small-N reliability numbers come with an honest range.
        lo, hi = LM.cell_reliability([True] * 2 + [False] * 8)["ci95"]
        self.assertLess(lo, 0.10)
        self.assertGreater(hi, 0.45)


class Slices(unittest.TestCase):
    def test_below_floor_flagged(self):
        cells = [{"arm": "OLD", "reliability": {"rate": 0.5}},
                 {"arm": "REVISED", "reliability": {"rate": 0.95}}]
        rep = {r["slice"]: r for r in slice_report(
            cells, [Slice("arm:OLD", lambda c: c["arm"] == "OLD"),
                    Slice("arm:REVISED", lambda c: c["arm"] == "REVISED")], floor=0.8)}
        self.assertTrue(rep["arm:OLD"]["below_floor"])
        self.assertFalse(rep["arm:REVISED"]["below_floor"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
