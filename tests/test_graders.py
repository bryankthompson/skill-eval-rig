#!/usr/bin/env python3
"""Pins for the grading seam (graders.py / label_model.py / slices.py). Pure functions, no live
claude — runs under `make test` exactly like the other CI-safe tests."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evaluate                                                 # noqa: E402
from graders import (Vote, DARK, CommandGrader, NoErrorGrader,  # noqa: E402
                     SchemaGrader, LLMJudgeGrader, grade_transcript,
                     classify_is_error, ERROR_BUCKETS, _result_text,
                     _validate, _validate_full, _discriminator)

try:
    import jsonschema as _JS  # noqa: F401
    _HAS_JS = True
except ImportError:
    _HAS_JS = False
import label_model as LM                                        # noqa: E402
from slices import Slice, slice_report                          # noqa: E402


def _v(question, grader, vote, conf=1.0):
    return Vote(question=question, vote=vote, confidence=conf, grader=grader)


_TMP = []


def _tx(lines):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    _TMP.append(p)
    with os.fdopen(fd, "w") as fh:
        for ev in lines:
            fh.write(json.dumps(ev) + "\n")
    return p


def _A(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _TU(name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def _U(text):
    return {"type": "user", "message": {"content": text}}


def _ERR(tuid="t1", content="boom"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tuid,
                                                     "is_error": True, "content": content}]}}


# Real corpus marker strings (re-derived from a 3,551-transcript sweep), one per non-failure bucket.
_REJECT_MARK = ("The user doesn't want to proceed with this tool use. The tool use was rejected "
                "(eg. if it was a file edit, the new_string was NOT written to the file).")
_PERM_MARK = "Claude requested permissions to use mcp__plugin-db__plugins, but you haven't granted it yet."
# the trailing "(#NN) — resolve…" tail is deliberately present to prove the matcher ignores it.
_GUARD_MARK = "verify-before-assert gate (#42) — resolve before drafting:\n- \"some claim\": status=\"assumed\""


def tearDownModule():
    for p in _TMP:
        try:
            os.unlink(p)
        except OSError:
            pass


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
        # Labels chosen so a naive equal-weight tie would resolve to 'fail' (lexical) — only genuine
        # confidence discounting lets the high-confidence 'pass' win.
        votes = [_v("q", "a", "fail", conf=0.1), _v("q", "b", "pass", conf=1.0)]
        self.assertEqual(LM.aggregate(votes)[0], "pass")

    def test_tie_breaks_lexicographically(self):
        # Pins the documented tie-break: equal-weight votes resolve to the lexicographically-first
        # class ('fail' < 'pass'). Load-bearing for the discounting test above.
        self.assertEqual(LM.aggregate([_v("q", "a", "pass"), _v("q", "b", "fail")])[0], "fail")


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


# Discriminated-union (oneOf-on-action) schema — the item-C shape the composer emits: per-action
# `const` arms with their own `required`, and an `anyOf` OR-condition arm.
DU_SCHEMA = {"db": {
    "type": "object", "required": ["action"],
    "properties": {"action": {"enum": ["update", "list", "get"]}, "slug": {"type": "string"}},
    "oneOf": [
        {"type": "object", "properties": {"action": {"const": "update"}}, "required": ["action", "slug"]},
        {"type": "object", "properties": {"action": {"const": "list"}}, "required": ["action"]},
        {"type": "object", "properties": {"action": {"const": "get"}}, "required": ["action"],
         "anyOf": [{"required": ["id"]}, {"required": ["slug"]}]},
    ]}}


class SchemaDiscriminatedUnion(unittest.TestCase):
    """oneOf-on-action discriminated unions (item C) — only a real per-action shortfall votes invalid."""
    def setUp(self):
        self.g = SchemaGrader(DU_SCHEMA)

    def test_du_update_without_slug_is_invalid(self):
        v = self.g.grade(_tx([_TU("db", {"action": "update"})]), {})
        self.assertEqual(v.vote, "invalid")
        self.assertIn("missing required 'slug'", v.note)   # discriminator attribution, not a generic miss

    def test_du_update_with_slug_is_ok(self):
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "update", "slug": "x"})]), {}).vote, "ok")

    def test_du_action_with_no_required_fields_is_ok(self):
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "list"})]), {}).vote, "ok")

    def test_du_or_condition_either_key_ok(self):
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "get", "id": 1})]), {}).vote, "ok")
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "get", "slug": "x"})]), {}).vote, "ok")

    def test_du_or_condition_neither_key_invalid(self):
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "get"})]), {}).vote, "invalid")

    def test_du_unknown_action_invalid(self):
        self.assertEqual(self.g.grade(_tx([_TU("db", {"action": "bogus"})]), {}).vote, "invalid")

    def test_du_non_object_input_fails_fast(self):
        self.assertEqual(_validate(5, DU_SCHEMA["db"]), ["expected object"])  # early type-return, never reaches oneOf


class ValidatorUnit(unittest.TestCase):
    """_validate's new oneOf/const/anyOf branches at the function level."""
    def test_discriminator_finds_action(self):
        self.assertEqual(_discriminator(DU_SCHEMA["db"]), "action")

    def test_discriminator_none_when_no_shared_const(self):
        self.assertIsNone(_discriminator({"oneOf": [{"properties": {"a": {"const": 1}}},
                                                    {"properties": {"b": {"const": 2}}}]}))

    def test_const_match_and_mismatch(self):
        self.assertEqual(_validate("update", {"const": "update"}), [])
        self.assertEqual(_validate("get", {"const": "update"}), ["const mismatch"])

    def test_oneof_exactly_one_no_discriminator(self):
        # No shared const → generic exactly-one-arm semantics.
        sch = {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}
        self.assertEqual(_validate({"a": 1}, sch), [])              # matches exactly one
        self.assertTrue(_validate({"a": 1, "b": 2}, sch))          # matches both → not exactly one

    def test_anyof_at_least_one(self):
        sch = {"anyOf": [{"required": ["id"]}, {"required": ["slug"]}]}
        self.assertEqual(_validate({"id": 1}, sch), [])
        self.assertTrue(_validate({}, sch))                        # neither → error

    def test_malformed_arm_does_not_crash(self):
        # A non-dict / property-less arm must NOT crash: _discriminator collapses to None and the
        # generic exactly-one path tolerates it via _validate's isinstance(schema, dict) guard.
        sch = {"oneOf": [{"properties": {"action": {"const": "update"}}, "required": ["slug"]},
                         "not-a-dict"]}
        self.assertIsNone(_discriminator(sch))
        self.assertTrue(_validate({"action": "update"}, sch))      # invalid (no slug), but no crash


# Schemas exercising the keywords the stdlib `_validate` subset documents as out of scope.
_REF_SCHEMA = {"type": "object", "properties": {"x": {"$ref": "#/$defs/P"}},
               "required": ["x"], "$defs": {"P": {"type": "integer", "enum": [1, 2, 3]}}}
_ALLOF_SCHEMA = {"type": "object", "properties": {"a": {}, "b": {}},
                 "allOf": [{"required": ["a"]}, {"required": ["b"]}]}
_ADDPROPS_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}},
                    "additionalProperties": False}


class ValidatorFull(unittest.TestCase):
    """_validate_full is a strict SUPERSET of the stdlib `_validate`: it runs the subset first
    (preserving its messages + oneOf attribution) and only consults jsonschema when the subset
    passed — so it is never MORE lenient, and on bare python3 it IS the subset."""

    def test_stdlib_caught_errors_pass_through_verbatim(self):
        # When the stdlib subset finds an error, _validate_full returns it UNCHANGED (same message +
        # discriminator attribution) regardless of whether jsonschema is installed. Runs in both states.
        for v, s in (({"action": "update"}, DU_SCHEMA["db"]),       # missing required 'slug' (attribution)
                     ({"x": "z"}, {"type": "object", "properties": {"x": {"enum": ["a", "b"]}},
                                   "required": ["x"]})):             # enum (the positive control)
            self.assertEqual(_validate_full(v, s), _validate(v, s))
            self.assertTrue(_validate_full(v, s))

    def test_valid_input_is_clean_in_either_state(self):
        self.assertEqual(_validate_full({"x": 2}, _REF_SCHEMA), [])
        self.assertEqual(_validate_full({"a": 1, "b": 2}, _ALLOF_SCHEMA), [])
        self.assertEqual(_validate_full({"x": "ok"}, _ADDPROPS_SCHEMA), [])


@unittest.skipUnless(_HAS_JS, "jsonschema not installed — _validate_full is the stdlib subset here")
class ValidatorFullWithJsonschema(unittest.TestCase):
    """With jsonschema importable, _validate_full closes the subset's blind spots ($ref / allOf /
    additionalProperties) the stdlib walker silently skips."""

    def test_ref_violation_caught_but_stdlib_skips_it(self):
        self.assertTrue(_validate_full({"x": "zzz"}, _REF_SCHEMA))   # violates the $ref'd integer enum
        self.assertEqual(_validate({"x": "zzz"}, _REF_SCHEMA), [])   # the subset still skips $ref

    def test_allof_violation_caught_but_stdlib_skips_it(self):
        self.assertTrue(_validate_full({"a": 1}, _ALLOF_SCHEMA))     # missing b (second allOf arm)
        self.assertEqual(_validate({"a": 1}, _ALLOF_SCHEMA), [])     # the subset skips allOf

    def test_additionalproperties_violation_caught_but_stdlib_skips_it(self):
        self.assertTrue(_validate_full({"x": "ok", "bog": 1}, _ADDPROPS_SCHEMA))
        self.assertEqual(_validate({"x": "ok", "bog": 1}, _ADDPROPS_SCHEMA), [])

    def test_schema_grader_vote_uses_full_validation(self):
        # End-to-end through the grader: an additionalProperties violation now votes invalid.
        g = SchemaGrader({"f": _ADDPROPS_SCHEMA})
        self.assertEqual(g.grade(_tx([_TU("f", {"x": "ok", "bog": 1})]), {}).vote, "invalid")
        self.assertEqual(g.grade(_tx([_TU("f", {"x": "ok"})]), {}).vote, "ok")

    def test_malformed_schema_falls_back_not_crashes(self):
        # A schema author error (not the model's fault) must not raise — fall back to the subset.
        bad = {"type": "object", "properties": {"x": {"type": 123}}}   # invalid: type must be a string
        self.assertEqual(_validate_full({"x": "v"}, bad), _validate({"x": "v"}, bad))


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


class DeterministicGraders(unittest.TestCase):
    """The two graders that VOTE on every bare run — previously uncovered (review High #2)."""
    def test_command_fired(self):
        p = _tx([_U("draft them an email"), _A("INVOKED /dir-reply")])
        self.assertEqual(CommandGrader().grade(p, {}).vote, "dir-reply")

    def test_command_dark_is_none_label(self):
        p = _tx([_U("draft them an email"), _A("Sure — here is a draft you could send: Hi ...")])
        self.assertEqual(CommandGrader().grade(p, {}).vote, DARK)

    def test_command_empty_abstains(self):
        self.assertTrue(CommandGrader().grade(_tx([]), {}).abstain)

    def test_no_error_flags_is_error(self):
        self.assertEqual(NoErrorGrader().grade(_tx([_A("running"), _ERR()]), {}).vote, "error")

    def test_no_error_clean_is_ok(self):
        self.assertEqual(NoErrorGrader().grade(_tx([_A("all good")]), {}).vote, "ok")

    def test_no_error_empty_abstains(self):
        self.assertTrue(NoErrorGrader().grade(_tx([]), {}).abstain)

    # --- is_error taxonomy: only a real-error penalizes the no_error reliability axis ---
    def test_real_error_votes_error(self):
        v = NoErrorGrader().grade(_tx([_A("x"), _ERR(content="<tool_use_error>boom</tool_use_error>")]), {})
        self.assertEqual(v.vote, "error")
        self.assertEqual(v.note, "real-error")

    def test_user_rejection_votes_ok(self):
        v = NoErrorGrader().grade(_tx([_A("x"), _ERR(content=_REJECT_MARK)]), {})
        self.assertEqual(v.vote, "ok")           # a declined tool is NOT a tool failure
        self.assertIn("user-rejection", v.note)

    def test_permission_not_granted_votes_ok(self):
        v = NoErrorGrader().grade(_tx([_A("x"), _ERR(content=_PERM_MARK)]), {})
        self.assertEqual(v.vote, "ok")
        self.assertIn("permission-not-granted", v.note)

    def test_by_design_guard_votes_ok(self):
        v = NoErrorGrader().grade(_tx([_A("x"), _ERR(content=_GUARD_MARK)]), {})
        self.assertEqual(v.vote, "ok")
        self.assertIn("by-design-guard", v.note)

    def test_real_error_wins_over_preceding_non_fatal(self):
        # A non-fatal is_error must NOT mask a later real error — the grader scans past it.
        p = _tx([_A("x"), _ERR(tuid="a", content=_REJECT_MARK), _ERR(tuid="b", content="boom")])
        self.assertEqual(NoErrorGrader().grade(p, {}).vote, "error")

    def test_only_non_fatal_errors_vote_ok(self):
        p = _tx([_A("x"), _ERR(tuid="a", content=_REJECT_MARK), _ERR(tuid="b", content=_PERM_MARK)])
        v = NoErrorGrader().grade(p, {})
        self.assertEqual(v.vote, "ok")
        # both buckets recorded, de-duped + sorted
        self.assertEqual(v.note, "non-fatal is_error: permission-not-granted,user-rejection")

    def test_content_as_block_list_is_classified(self):
        # The API list-of-blocks content shape, not just a bare string, must classify.
        blocks = [{"type": "text", "text": _REJECT_MARK}]
        v = NoErrorGrader().grade(_tx([_A("x"), _ERR(content=blocks)]), {})
        self.assertEqual(v.vote, "ok")
        self.assertIn("user-rejection", v.note)

    def test_classify_buckets_and_default(self):
        self.assertEqual(classify_is_error(_REJECT_MARK), "user-rejection")
        self.assertEqual(classify_is_error(_PERM_MARK), "permission-not-granted")
        self.assertEqual(classify_is_error("...you haven't granted it yet."), "permission-not-granted")
        self.assertEqual(classify_is_error(_GUARD_MARK), "by-design-guard")
        # only the anchored gate header classifies — the generic phrase alone does NOT (W#1)
        self.assertEqual(classify_is_error("verify-before-assert gate fired"), "by-design-guard")
        self.assertEqual(classify_is_error("resolve before drafting: claim X"), "real-error")
        self.assertEqual(classify_is_error("Exit code 1\nFAILED test foo"), "real-error")
        self.assertEqual(classify_is_error(""), "real-error")
        self.assertEqual(classify_is_error(None), "real-error")
        self.assertIn("real-error", ERROR_BUCKETS)

    def test_real_errors_mentioning_markers_stay_real(self):
        # A genuine failure that merely ECHOES gate/permission vocabulary must NOT be excused — the
        # markers are anchored on outcome tokens, not request preambles or generic phrases.
        self.assertEqual(classify_is_error("EACCES: permission denied, open /etc/foo"), "real-error")
        self.assertEqual(classify_is_error("Permission denied (publickey)"), "real-error")
        self.assertEqual(classify_is_error("FileNotFoundError: resolve before drafting.md"), "real-error")
        # the permission REQUEST preamble without the not-granted outcome is still a real error
        self.assertEqual(classify_is_error("crashed after Claude requested permissions to use X"), "real-error")

    def test_result_text_flattens_shapes(self):
        self.assertEqual(_result_text("hi"), "hi")
        self.assertEqual(_result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a b")
        self.assertEqual(_result_text({"type": "text", "text": "x"}), "x")
        self.assertEqual(_result_text(None), "")

    def test_user_rejection_passes_no_error_policy(self):
        # The headline: a trial whose only is_error is a user rejection must PASS the no_error
        # reliability target (`no_error: "ok"`), where pre-taxonomy it FAILED.
        p = _tx([_A("x"), _ERR(content=_REJECT_MARK)])
        verdict = LM.trial_verdict(grade_transcript(p, {}, [NoErrorGrader()]))
        self.assertTrue(LM.pass_policy(verdict, {"no_error": "ok"}))

    def test_grade_transcript_stamps_names_and_questions(self):
        p = _tx([_U("draft them an email"), _A("INVOKED /dir-reply")])
        votes = grade_transcript(p, {}, [CommandGrader(), NoErrorGrader()])
        self.assertEqual([v.grader for v in votes], ["command", "no_error"])
        self.assertEqual([v.question for v in votes], ["command", "no_error"])


class JudgeNestedJSON(unittest.TestCase):
    def test_nested_verdict_does_not_shadow_outer(self):
        # Review High #1: a nested {"verdict":"fail"} must NOT flip a real pass. The old shallow
        # regex matched the inner object and returned 'fail'; the structural parse returns 'pass'.
        raw = '{"verdict":"pass","confidence":0.9,"meta":{"verdict":"fail"}}'
        v = LLMJudgeGrader("r", runner=lambda p, m: raw).grade(_tx([_A("Here is your draft")]), {})
        self.assertEqual(v.vote, "pass")
        self.assertAlmostEqual(v.confidence, 0.9)

    def test_object_after_prose(self):
        g = LLMJudgeGrader("r", runner=lambda p, m: 'Reasoning… final: {"verdict":"fail","confidence":0.7}')
        self.assertEqual(g.grade(_tx([_A("x")]), {}).vote, "fail")

    def test_brace_in_string_value_keeps_clean_object(self):
        # Review M2: a '}' inside a JSON string value must not truncate the span. Pre-fix this aborted
        # to abstain (both 'pass' and 'fail' words present); the string-aware scanner keeps it intact.
        raw = 'judging: {"verdict":"pass","note":"not a fail }, fine","confidence":0.8} done'
        v = LLMJudgeGrader("r", runner=lambda p, m: raw).grade(_tx([_A("draft")]), {})
        self.assertEqual(v.vote, "pass")
        self.assertAlmostEqual(v.confidence, 0.8)


class AggregateGuards(unittest.TestCase):
    def test_vote_outside_explicit_classes_does_not_crash(self):
        # Review Medium #2: a label outside an explicit `classes` set must not KeyError.
        self.assertEqual(LM.aggregate([_v("q", "g", "z")], classes=["a", "b"])[0], "z")

    def test_non_numeric_confidence_does_not_crash(self):
        # Review Medium #3: None/garbage confidence falls back to the default, no TypeError.
        self.assertEqual(LM.aggregate([Vote(question="q", vote="a", confidence=None, grader="g")])[0], "a")

    def test_nonfinite_confidence_does_not_dominate(self):
        # Review M1: a NaN confidence must fall back to the default reliability, not clamp to 1.0 and
        # let a low-trust vote win. Pre-fix 'junk' (NaN→1.0) beats 'good'; post-fix they tie and the
        # lexical tie-break picks 'good' ('good' < 'junk').
        votes = [Vote(question="q", vote="junk", confidence=float("nan"), grader="g1"),
                 Vote(question="q", vote="good", confidence=0.6, grader="g2")]
        self.assertEqual(LM.aggregate(votes)[0], "good")


class EvaluateE2E(unittest.TestCase):
    """Review High #2: evaluate.py had no test."""
    def test_evaluate_scores_and_slices(self):
        p = _tx([_U("draft them an email"), _A("INVOKED /dir-reply"), _A("Here is your draft")])
        run = {"cells": [{"arm": "REVISED", "kind": "positive", "prompt": "x",
                          "trials": [{"valid": True, "transcript": p}]}]}
        rep = evaluate.evaluate(run, graders=[CommandGrader(), NoErrorGrader()],
                                targets={"command": "dir-reply", "no_error": "ok"})
        self.assertEqual(rep["cells"][0]["reliability"]["rate"], 1.0)
        self.assertTrue(any(r["slice"] == "arm:REVISED" for r in rep["slices"]))

    def test_invalid_trial_excluded(self):
        run = {"cells": [{"arm": "OLD", "kind": "positive", "prompt": "x",
                          "trials": [{"valid": False, "transcript": "/gone.jsonl"}]}]}
        rep = evaluate.evaluate(run, graders=[CommandGrader(), NoErrorGrader()],
                                targets={"command": "dir-reply"})
        self.assertEqual(rep["cells"][0]["reliability"]["n"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
