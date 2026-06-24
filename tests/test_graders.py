#!/usr/bin/env python3
"""Pins for the grading seam (graders.py / label_model.py / slices.py). Pure functions, no live
claude — runs under `make test` exactly like the other CI-safe tests."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graders import Vote, DARK, SchemaGrader, LLMJudgeGrader   # noqa: E402
import label_model as LM                                       # noqa: E402
from slices import Slice, slice_report                         # noqa: E402


def _v(question, grader, vote, conf=1.0):
    return Vote(question=question, vote=vote, confidence=conf, grader=grader)


def _tx(lines):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for ev in lines:
            fh.write(json.dumps(ev) + "\n")
    return p


def _A(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _TU(name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


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


SCHEMA = {"send_email": {"type": "object", "required": ["to", "subject"],
                         "properties": {"to": {"type": "string"},
                                        "kind": {"enum": ["draft", "send"]}}}}


class SchemaLive(unittest.TestCase):
    def setUp(self):
        self.g = SchemaGrader(SCHEMA)

    def test_valid_is_ok(self):
        p = _tx([_TU("send_email", {"to": "joe@x.com", "subject": "hi", "kind": "draft"})])
        self.assertEqual(self.g.grade(p, {}).vote, "ok")

    def test_missing_required_is_invalid(self):
        p = _tx([_TU("send_email", {"to": "joe@x.com"})])   # no subject
        self.assertEqual(self.g.grade(p, {}).vote, "invalid")

    def test_wrong_type_is_invalid(self):
        p = _tx([_TU("send_email", {"to": 123, "subject": "hi"})])
        self.assertEqual(self.g.grade(p, {}).vote, "invalid")

    def test_enum_violation_is_invalid(self):
        p = _tx([_TU("send_email", {"to": "j", "subject": "hi", "kind": "archive"})])
        self.assertEqual(self.g.grade(p, {}).vote, "invalid")

    def test_unconfigured_tool_abstains(self):
        # A tool_use with no schema is "nothing to check", not a failure.
        self.assertTrue(self.g.grade(_tx([_TU("other_tool", {"x": 1})]), {}).abstain)

    def test_no_tool_use_abstains(self):
        self.assertTrue(self.g.grade(_tx([_A("just prose, no tool call")]), {}).abstain)

    def test_no_schemas_abstains(self):
        self.assertTrue(SchemaGrader().grade(_tx([_TU("send_email", {})]), {}).abstain)


class JudgeLive(unittest.TestCase):
    """The live judge with an INJECTED fake runner — no live `claude` call, so CI stays offline."""
    def _drafted(self):
        return _tx([_A("Here is a draft email you can send: Hi Joe, ...")])

    def test_pass_with_confidence(self):
        g = LLMJudgeGrader("did it draft an email?",
                           runner=lambda p, m: '{"verdict":"pass","confidence":0.8}')
        v = g.grade(self._drafted(), {"prompt": "draft an email"})
        self.assertEqual(v.vote, "pass")
        self.assertAlmostEqual(v.confidence, 0.8)

    def test_fail(self):
        g = LLMJudgeGrader("r", runner=lambda p, m: '{"verdict":"fail","confidence":0.6}')
        self.assertEqual(g.grade(self._drafted(), {}).vote, "fail")

    def test_unparseable_abstains(self):
        g = LLMJudgeGrader("r", runner=lambda p, m: "I really cannot decide either way.")
        self.assertTrue(g.grade(self._drafted(), {}).abstain)

    def test_no_rubric_abstains(self):
        # Abstains before the runner is ever consulted.
        self.assertTrue(LLMJudgeGrader(runner=lambda p, m: "x").grade(self._drafted(), {}).abstain)

    def test_no_output_to_judge_abstains(self):
        g = LLMJudgeGrader("r", runner=lambda p, m: '{"verdict":"pass"}')
        self.assertTrue(g.grade(_tx([{"type": "user", "message": {"content": "hi"}}]), {}).abstain)

    def test_runner_error_abstains(self):
        def boom(p, m):
            raise RuntimeError("network down")
        self.assertTrue(LLMJudgeGrader("r", runner=boom).grade(self._drafted(), {}).abstain)

    def test_runner_sees_rubric_and_output(self):
        seen = {}

        def cap(prompt, model):
            seen["prompt"] = prompt
            return '{"verdict":"pass","confidence":0.5}'
        LLMJudgeGrader("rubric-X", runner=cap).grade(self._drafted(), {"prompt": "draft"})
        self.assertIn("rubric-X", seen["prompt"])
        self.assertIn("draft email you can send", seen["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
