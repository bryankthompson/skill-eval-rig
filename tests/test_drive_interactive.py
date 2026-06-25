#!/usr/bin/env python3
"""Self-test for the interactive activation axis scorer + verdict logic — the PURE functions
behind experiments/activation/drive_interactive.py. No live `claude`, no pexpect import (the
driver imports pexpect lazily inside run_trial only), so this runs under `make test` / CI exactly
like the headless-axis tests. Canned transcripts are built in Python (committing a *.jsonl is
pointless — *.jsonl is gitignored repo-wide).

Schemas are pinned to the REAL shapes captured in the Phase-0 spike:
  * auto-fired command  → assistant `Skill` tool_use, input {"skill":"<name>"}; stub text "INVOKED /<name>"
  * forced command      → a 'user' turn wrapped <command-name>/<name></command-name> (NOT leading '/')
"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import prefill_report as pr  # noqa: E402
# Import the driver's PURE helpers (score_battery). Importing the module is safe: pexpect is only
# imported inside run_trial(), never at module load — so this stays CI-safe without pexpect.
sys.path.insert(0, os.path.join(ROOT, "experiments", "activation"))
import drive_interactive as di  # noqa: E402


def _transcript(path, lines):
    with open(path, "w") as fh:
        for ev in lines:
            fh.write(json.dumps(ev) + "\n")


def _user(text):
    return {"type": "user", "message": {"content": text}}


def _skill_tooluse(name, tuid="toolu_1"):
    # Real auto-fire shape: a `Skill` tool_use whose input.skill is the command name.
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": tuid, "name": "Skill",
                                     "input": {"skill": name}, "caller": {"type": "direct"}}]}}


def _tool_result(tuid="toolu_1"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tuid,
                                                     "content": "Successfully loaded skill"}]}}


def _assistant_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class InvokedCommand(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _w(self, name, lines):
        p = os.path.join(self.d, name)
        _transcript(p, lines)
        return p

    def test_invoked_reply_primary_surface(self):
        # The PRIMARY signal: the stub's "INVOKED /<name>" reply (no tool_use needed).
        p = self._w("a.jsonl", [
            _user("create this draft email in gmail"),
            _assistant_text("INVOKED /dir-reply"),
        ])
        self.assertEqual(pr.invoked_command(p), "dir-reply")

    def test_skill_tooluse_corroborating(self):
        # The corroborating surface: a Skill tool_use input.skill, even without the INVOKED text.
        p = self._w("b.jsonl", [
            _user("create this draft email in gmail"),
            _skill_tooluse("dir-reply"),
            _tool_result(),
        ])
        det = pr.detect_invocation(p)
        self.assertEqual(det["name"], "dir-reply")
        self.assertEqual(det["skill"], "dir-reply")

    def test_competitor_surfaces(self):
        # The /mcp-prime-dev-email collision MUST surface as the winning name, not as a
        # bare dir-reply yes/no.
        p = self._w("c.jsonl", [
            _user("draft them an email"),
            _skill_tooluse("mcp-prime-dev-email"),
            _tool_result(),
            _assistant_text("INVOKED /mcp-prime-dev-email"),
        ])
        self.assertEqual(pr.invoked_command(p), "mcp-prime-dev-email")

    def test_went_dark_returns_none(self):
        # Activation cliff: no command fires; the model answers in prose.
        p = self._w("d.jsonl", [
            _user("create this draft email in gmail"),
            _assistant_text("Sure — here's a draft you could send: Hi ..."),
        ])
        self.assertIsNone(pr.invoked_command(p))

    def test_forced_command_serialization_is_not_natural(self):
        # A FORCED command in the interactive REPL serializes as a <command-name> user turn, NOT a
        # leading-'/' string. The natural-turn-by-position selection must skip it → None (no other
        # natural turn here). This is the auto-activation guard the whole axis rests on.
        p = self._w("e.jsonl", [
            _user("<command-message>dir-reply</command-message>\n<command-name>/dir-reply</command-name>\n<command-args></command-args>"),
            _skill_tooluse("dir-reply"),
            _tool_result(),
            _assistant_text("INVOKED /dir-reply"),
        ])
        self.assertIsNone(pr.invoked_command(p))

    def test_leading_slash_also_guarded(self):
        # The legacy leading-'/' form is still rejected as a forced invocation.
        p = self._w("f.jsonl", [
            _user("/dir-reply please"),
            _assistant_text("INVOKED /dir-reply"),
        ])
        self.assertIsNone(pr.invoked_command(p))

    def test_both_surfaces_agree_single_name(self):
        p = self._w("g.jsonl", [
            _user("reply to the partner on this thread"),
            _skill_tooluse("dir-reply"),
            _tool_result(),
            _assistant_text("INVOKED /dir-reply"),
        ])
        det = pr.detect_invocation(p)
        self.assertEqual(det["name"], "dir-reply")
        self.assertFalse(det["mismatch"])

    def test_both_surfaces_disagree_invoked_wins_and_flags(self):
        # On disagreement the stub's INVOKED reply (ground-truth side effect) wins, and the
        # divergence is flagged.
        p = self._w("h.jsonl", [
            _user("draft them an email"),
            _skill_tooluse("mcp-prime-dev-email"),
            _tool_result(),
            _assistant_text("INVOKED /dir-reply"),
        ])
        det = pr.detect_invocation(p)
        self.assertEqual(det["name"], "dir-reply")     # INVOKED reply wins
        self.assertTrue(det["mismatch"])

    def test_forced_turn_then_natural_turn_scores_the_natural(self):
        # The skip-loop's real job: skip a LEADING forced turn and score the FOLLOWING genuine
        # natural turn (not just return None when the only turn is forced).
        p = self._w("j.jsonl", [
            _user("<command-name>/dir-publish</command-name>"),
            _assistant_text("INVOKED /dir-publish"),
            _user("create this draft email in gmail"),
            _skill_tooluse("dir-reply"),
            _tool_result(),
            _assistant_text("INVOKED /dir-reply"),
        ])
        self.assertEqual(pr.invoked_command(p), "dir-reply")

    def test_invoked_capture_ignores_trailing_punctuation(self):
        # Charset-anchored capture: trailing ')'/'.' after the name must not bleed into it.
        p = self._w("k.jsonl", [_user("draft them an email"),
                                _assistant_text("Done — INVOKED /dir-reply).")])
        self.assertEqual(pr.invoked_command(p), "dir-reply")

    def test_prompt_echo_in_tool_span_does_not_break_selection(self):
        # Selecting the natural turn by POSITION (not a prompt-text marker) is robust to the prompt
        # echoing inside a later tool span / assistant text.
        p = self._w("i.jsonl", [
            _user("create this draft email in gmail"),
            _skill_tooluse("dir-reply"),
            _tool_result(),
            _assistant_text("I read 'create this draft email in gmail' and INVOKED /dir-reply"),
        ])
        self.assertEqual(pr.invoked_command(p), "dir-reply")


class ScoreBattery(unittest.TestCase):
    def _cell(self, arm, kind, prompt, winner, owner=None):
        c = {"arm": arm, "kind": kind, "prompt": prompt, "winner": winner}
        if owner is not None:
            c["expected_owner"] = owner
        return c

    def _battery(self, rev_pos_winners, old_pos_winners, neg_winners):
        cells = []
        for p, w in old_pos_winners.items():
            cells.append(self._cell("OLD", "positive", p, w))
        for p, w in rev_pos_winners.items():
            cells.append(self._cell("REVISED", "positive", p, w))
        for p, (w, o) in neg_winners.items():
            cells.append(self._cell("REVISED", "negative", p, w, o))
        return cells

    def test_fix_validated(self):
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: "dir-reply" for p in pos}
        old = {p: None for p in pos}                      # OLD fires nothing
        neg = {"n1": ("dir-email-sync", "dir-email-sync"), "n2": ("dir-fix-tests", "dir-fix-tests")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX VALIDATED")

    def test_untestable_when_competitor_wins_both(self):
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: "mcp-prime-dev-email" for p in pos}
        old = {p: "mcp-prime-dev-email" for p in pos}     # competitor wins under BOTH arms
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "DESCRIPTION-DELTA UNTESTABLE")

    def test_fix_failed_when_no_old_dark_positive_gained(self):
        # OLD missed all 4; REVISED still fires none → gained 0/4 of the addressable set → FAILED.
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: None for p in pos}
        old = {p: None for p in pos}
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX FAILED / INCONCLUSIVE")

    def test_fix_partial_when_some_old_dark_gained(self):
        # REVISED gains SOME but not all OLD-dark positives (+ holds negatives) → EFFECTIVE (PARTIAL).
        pos = ["p1", "p2", "p3", "p4"]
        rev = {"p1": "dir-reply", "p2": "dir-reply", "p3": None, "p4": None}   # 2 of 4 dark gained
        old = {p: None for p in pos}
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX EFFECTIVE (PARTIAL)")

    def test_marginal_gain_over_old_dark_is_validated(self):
        # The REAL battery shape: OLD already name-routes 2/4 positives (p3,p4 via the /dir-reply
        # NAME); REVISED gains BOTH positives OLD missed (p1,p2) + holds negatives → VALIDATED on the
        # addressable set, NOT "failed" just because raw gain is 2/4. (This is the e2e finding the
        # ≥3/4 raw bar would have mislabeled.)
        rev = {"p1": "dir-reply", "p2": "dir-reply", "p3": "dir-reply", "p4": "dir-reply"}
        old = {"p1": None, "p2": None, "p3": "dir-reply", "p4": "dir-reply"}
        neg = {"n1": ("dir-email-sync", "dir-email-sync"), "n2": (None, "dir-fix-tests")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX VALIDATED")

    def test_first_battery_replay_is_validated(self):
        # REPLAY of the documented FIRST battery (the repo-root FINDINGS.md, "First battery result", N=24,
        # claude 2.1.187): the live run that this scorer verdict pins. Uses the REAL battery
        # prompt strings (not synthetic p1..p4) so this test is the cross-check for the FINDINGS
        # paragraph. Documented outcome: OLD name-routes the two "reply…" framings on the
        # /dir-reply NAME alone (2/4) and is dark on the two non-"reply" email framings; REVISED
        # gains BOTH OLD-dark positives (2/2 marginal gain) + holds both negatives → FIX VALIDATED.
        #
        # Asserting ONLY the label would duplicate test_marginal_gain_over_old_dark_is_validated
        # (score_battery never reads prompt TEXT — c["prompt"] is just a dict key). So this also
        # pins the DOCUMENTED NUMBERS via the detail string (2/2 dark gained, 2/4 name-routed),
        # which nothing else asserts — that is the non-redundant pin of the published result.
        # This test ALWAYS pins the first battery; a higher-N re-run is recorded as a SEPARATE
        # FINDINGS paragraph (and, if its verdict differs, a separate test) — never an edit here.
        old = {"write a reply to Joe about his submission": "dir-reply",
               "reply to the partner on this thread": "dir-reply",
               "create this draft email in gmail": None,
               "draft them an email": None}
        rev = {p: "dir-reply" for p in old}
        neg = {"sync my email tracking db with gmail": ("dir-email-sync", "dir-email-sync"),
               "this test is failing, fix it": (None, "dir-fix-tests")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX VALIDATED")
        self.assertIn("gained dir-reply on 2/2", v["detail"])
        self.assertIn("OLD auto-routes 2/4 via the /dir-reply NAME", v["detail"])
        # Also pin the documented "both negatives held" tail: the dark negative renders as
        # held (→dark) on its owner, not stolen into /dir-reply (FINDINGS: "this test is
        # failing→dark, a non-steal"). neg_detail truncates the prompt to 24 chars (p[:24]).
        self.assertIn("→dark(want dir-fix-tests)", v["detail"])

    def test_no_headroom_when_old_routes_all_positives(self):
        # OLD already fires dir-reply on ALL positives (name alone) → description delta unmeasurable.
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: "dir-reply" for p in pos}
        old = {p: "dir-reply" for p in pos}
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertTrue(v["label"].startswith("NO HEADROOM"))

    def test_fix_failed_when_negative_stolen(self):
        # Positives gained, but REVISED STEALS a negative into dir-reply → not validated.
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: "dir-reply" for p in pos}
        old = {p: None for p in pos}
        neg = {"n1": ("dir-reply", "dir-email-sync")}     # stolen
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX FAILED / INCONCLUSIVE")

    def test_neg_dark_is_held(self):
        # C1 regression guard: a DARK negative (winner=None) is a NON-steal → held. With all
        # positives gained, the verdict must be FIX VALIDATED (the pre-fix `w == owner` check
        # wrongly flipped this to FAILED — the headline-metric bug the code gate caught).
        pos = ["p1", "p2", "p3", "p4"]
        rev = {p: "dir-reply" for p in pos}
        old = {p: None for p in pos}
        neg = {"n1": (None, "dir-email-sync"), "n2": ("dir-fix-tests", "dir-fix-tests")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "FIX VALIDATED")

    def test_untestable_boundary_two_of_four(self):
        # Boundary (upper): competitor wins 2/4 positives under BOTH arms (the max(1, n//2)=2
        # threshold) → UNTESTABLE wins over the 2 gained positives (check-ordering is load-bearing).
        rev = {"p1": "mcp-prime-dev-email", "p2": "mcp-prime-dev-email", "p3": "dir-reply", "p4": "dir-reply"}
        old = {"p1": "mcp-prime-dev-email", "p2": "mcp-prime-dev-email", "p3": None, "p4": None}
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertEqual(v["label"], "DESCRIPTION-DELTA UNTESTABLE")

    def test_one_of_four_competitor_is_not_untestable(self):
        # Boundary (just below): competitor wins only 1/4 under both arms (< the =2 threshold) → NOT
        # untestable. p1 is an OLD-dark positive REVISED did not gain (competitor took it), so gain
        # is 3/4 of the addressable set → EFFECTIVE (PARTIAL), explicitly NOT untestable. Pins the
        # threshold's lower side so a loosening to `>= 1` can't silently flip the verdict.
        rev = {"p1": "mcp-prime-dev-email", "p2": "dir-reply", "p3": "dir-reply", "p4": "dir-reply"}
        old = {"p1": "mcp-prime-dev-email", "p2": None, "p3": None, "p4": None}
        neg = {"n1": ("dir-email-sync", "dir-email-sync")}
        v = di.score_battery(self._battery(rev, old, neg))
        self.assertNotEqual(v["label"], "DESCRIPTION-DELTA UNTESTABLE")
        self.assertEqual(v["label"], "FIX EFFECTIVE (PARTIAL)")

    def test_missing_expected_owner_does_not_crash_and_is_held(self):
        # Latent footgun deferred from PR #6 — a SYNTHETIC/future shape (no shipped battery omits
        # expected_owner; every NEGATIVES entry carries an owner). Pins THREE things the existing
        # tests don't:
        #   (1) a negative whose expected_owner KEY IS OMITTED (not merely winner-dark, as
        #       test_neg_dark_is_held covers) must not KeyError in score_battery and is still HELD,
        #   (2) _cell preserves an explicitly-empty owner (the is-not-None guard), and
        #   (3) that "" owner survives _cell→score_battery end-to-end.
        # Each mutation is caught by a DIFFERENT line below: the score_battery bracket-read revert
        # ERRORS on the first call (KeyError), so it never reaches the assertions.
        rev = {"p1": "dir-reply", "p2": "dir-reply"}
        old = {"p1": None, "p2": None}
        neg = {"n1": (None, None)}            # winner dark, owner None → expected_owner key OMITTED
        v = di.score_battery(self._battery(rev, old, neg))   # ← bracket-read revert KeyErrors HERE
        self.assertEqual(v["label"], "FIX VALIDATED")        # sane verdict: both OLD-dark gained + neg held
        self.assertIn("(want ?)", v["detail"])               # pins the .get("?") SENTINEL LITERAL in neg_detail
        # fix half #2 — _cell keeps an explicit "" owner (revert to `if owner:` → key dropped → None != "").
        self.assertEqual(self._cell("REVISED", "negative", "n", "x", owner="").get("expected_owner"), "")
        # integration pin: an explicit "" owner survives _cell→score_battery and renders "(want )",
        # NOT the "(want ?)" sentinel (revert _cell → key dropped → .get default → "(want ?)" → fails).
        v2 = di.score_battery(self._battery({"q1": "dir-reply"}, {"q1": None}, {"m1": (None, "")}))
        self.assertIn("(want )", v2["detail"])
        # neg_held is owner-AGNOSTIC: a STOLEN negative (winner=dir-reply) with "" owner is still a
        # steal → FIX FAILED, and the sentinel/empty owner also renders in the FAIL branch ("→dir-reply(want )").
        v3 = di.score_battery(self._battery({"q1": "dir-reply"}, {"q1": None}, {"m1": ("dir-reply", "")}))
        self.assertEqual(v3["label"], "FIX FAILED / INCONCLUSIVE")
        self.assertIn("(want )", v3["detail"])


class DriverHelpers(unittest.TestCase):
    """Direct unit cover for the pure helpers that are otherwise only exercised live (turn_settled,
    slug_for, _majority) — their failures would be invisible until a real run."""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _w(self, name, lines):
        p = os.path.join(self.d, name)
        _transcript(p, lines)
        return p

    def test_turn_settled_invoked_fastpath(self):
        p = self._w("t1.jsonl", [_user("x"), _assistant_text("INVOKED /dir-reply")])
        self.assertTrue(di.turn_settled(p))

    def test_turn_settled_dangling_tooluse_with_text_is_not_settled(self):
        # The real /exit-race: the assistant emitted TEXT *and* a still-running tool_use (no
        # tool_result yet). has_assistant_text is True, so settling rests entirely on the
        # issubset(tool_use_ids ⊆ tool_result_ids) check → must be False (mid-command). (Including
        # the text is what actually exercises issubset — without it the has_assistant_text gate
        # short-circuits and the dangling-tool detection is never tested.)
        p = self._w("t2.jsonl", [_user("x"), _assistant_text("working on it"),
                                 _skill_tooluse("dir-reply", tuid="t9")])
        self.assertFalse(di.turn_settled(p))

    def test_turn_settled_idless_tooluse_with_text_is_not_settled(self):
        # M1 safety direction: a degenerate id-less tool_use (no id) must NOT settle early — it
        # stays unsettled (→ hard-timeout invalid trial), never a false-negative mid-command.
        idless = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "dir-reply"}}]}}
        p = self._w("t2b.jsonl", [_user("x"), _assistant_text("working"), idless])
        self.assertFalse(di.turn_settled(p))

    def test_turn_settled_resolved_tool_then_text(self):
        p = self._w("t3.jsonl", [_user("x"), _skill_tooluse("dir-reply", tuid="t9"),
                                 _tool_result(tuid="t9"), _assistant_text("done")])
        self.assertTrue(di.turn_settled(p))

    def test_turn_settled_empty(self):
        self.assertFalse(di.turn_settled(self._w("t4.jsonl", [])))

    def test_slug_for_collapses_every_nonalnum(self):
        import re as _re
        path = "/a.b_c/d-e"
        self.assertEqual(di.slug_for(path), _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path)))
        self.assertTrue(all(ch.isalnum() or ch == "-" for ch in di.slug_for(path)))

    def test_majority_winner_and_dark(self):
        self.assertEqual(di._majority(["dir-reply", "dir-reply", None])[0], "dir-reply")
        self.assertIsNone(di._majority([None, None])[0])

    def test_majority_tie_reports_via_tally(self):
        # On a 1-1 tie the winner pick is insertion-order-arbitrary, so the load-bearing signal for
        # run_cell's escalation is the TALLY counts, not the arbitrary winner. Pin the counts.
        _, tally = di._majority(["dir-reply", None])
        self.assertEqual(tally["dir-reply"], 1)
        self.assertEqual(tally["(none)"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
