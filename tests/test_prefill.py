#!/usr/bin/env python3
"""Self-test for prefill_report.analyze_session — the interactive-transcript scorer behind the
activation/cross-skill findings. Pins its firing + token detection on a committed synthetic
transcript so the turn-scan logic can't drift silently (M9/H6). No live claude."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import prefill_report as pr  # noqa: E402

MARKER = "autovacuum_vacuum_cost_limit"
TOKEN = "XHANDOFF-4200"
TARGET = "vacuum-detail"


def _transcript(path, lines):
    with open(path, "w") as fh:
        for ev in lines:
            fh.write(json.dumps(ev) + "\n")


def _user(text):
    return {"type": "user", "message": {"content": text}}


def _assistant_tool(name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def _assistant_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class AnalyzeSession(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_fired_and_token_returned(self):
        p = os.path.join(self.d, "s1.jsonl")
        _transcript(p, [
            _user(f"What is the {MARKER} for the high-write tier?"),  # natural prompt (marker, not /-cmd)
            _assistant_tool("Skill", {"skill": TARGET}),
            _assistant_tool("Read", {"file_path": "references/autovacuum.md"}),
            _assistant_text(f"The documented value is {TOKEN}."),
        ])
        fired, fired_skill, target_engaged, token_hit = pr.analyze_session(p, TOKEN, TARGET, MARKER)
        self.assertTrue(fired)
        self.assertIn(TARGET, fired_skill)
        self.assertTrue(target_engaged)
        self.assertTrue(token_hit)

    def test_went_dark_no_skill(self):
        # Activation cliff: no skill fires; the model answers from general knowledge.
        p = os.path.join(self.d, "s2.jsonl")
        _transcript(p, [
            _user(f"What is the {MARKER} for the high-write tier?"),
            _assistant_text("It is typically around 2000 by default."),
        ])
        fired, fired_skill, target_engaged, token_hit = pr.analyze_session(p, TOKEN, TARGET, MARKER)
        self.assertFalse(fired)
        self.assertFalse(token_hit)

    def test_slash_command_turn_is_not_natural_prompt(self):
        # The /-command guard is what distinguishes AUTO-activation from a FORCED /skill invocation
        # — the core of the activation-cliff measurement. A turn whose prompt starts with /<skill>
        # must NOT be scored as the natural-activation prompt (here: no natural turn -> None).
        p = os.path.join(self.d, "s4.jsonl")
        _transcript(p, [
            _user(f"/{TARGET}\nWhat is the {MARKER}?"),
            _assistant_tool("Read", {"file_path": "references/autovacuum.md"}),
            _assistant_text(f"The value is {TOKEN}."),
        ])
        self.assertIsNone(pr.analyze_session(p, TOKEN, TARGET, MARKER))

    def test_skill_injection_line_not_a_turn_boundary(self):
        # A skill-injection 'user' line must fold into the current turn, not split the answer away.
        p = os.path.join(self.d, "s3.jsonl")
        _transcript(p, [
            _user(f"What is the {MARKER}?"),
            _user("Base directory for this skill: /tmp/x/.claude/skills/vacuum-detail"),
            _assistant_tool("Read", {"file_path": "references/autovacuum.md"}),
            _assistant_text(f"The value is {TOKEN}."),
        ])
        fired, fired_skill, target_engaged, token_hit = pr.analyze_session(p, TOKEN, TARGET, MARKER)
        self.assertTrue(token_hit)
        self.assertTrue(fired)  # the injected "Base directory" line is captured as a SkillInjected step


if __name__ == "__main__":
    unittest.main(verbosity=2)
