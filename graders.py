#!/usr/bin/env python3
"""The grading seam. A Grader reads ONE trial transcript and emits a Vote {question, vote,
confidence, abstain} — its opinion on ONE question (e.g. "which command fired", "did it error").

This replaces the rig's single hard-coded scorer with an ENSEMBLE. Two rules the seam enforces,
both found while reviewing the first design:

  1. Graders are grouped by the QUESTION they answer. Votes are aggregated only WITHIN a question
     (label_model.trial_verdict) — you never argmax "error" against "dir-reply". Combining across
     questions is an explicit pass policy (label_model.pass_policy), not a vote.
  2. `vote=None` together with `abstain=True` means "I can't tell" (e.g. the transcript is missing).
     A real "nothing fired" outcome is the LABEL DARK, NOT an abstention — conflating them would
     hide the activation cliff (a dark trial is a finding, not a non-vote).

Diversity is the point: a deterministic check, a transcript regex, and an LLM judge make
somewhat-independent errors — the assumption label_model rests on. Five LLM judges are NOT diverse."""
import json
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import prefill_report as pr  # reuse detect_invocation — no second transcript scraper

DARK = "(none)"   # real "no command fired" label; distinct from an abstention


@dataclass
class Vote:
    question: str
    vote: Optional[str]          # the label; None ONLY together with abstain=True
    confidence: float = 1.0      # self-reported reliability of THIS judgment, 0..1
    abstain: bool = False        # "I can't tell" — excluded from aggregation
    grader: str = ""             # filled by grade_transcript
    note: str = ""

    @classmethod
    def no_opinion(cls, question, note=""):
        return cls(question=question, vote=None, confidence=0.0, abstain=True, note=note)


@runtime_checkable
class Grader(Protocol):
    name: str
    question: str
    def grade(self, transcript: str, ctx: dict) -> Vote: ...


def _events(path):
    out = []
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


class CommandGrader:
    """Which command auto-fired (the rig's current behavior, wrapped). Deterministic surface read,
    so high confidence. A dark trial is the label DARK, not an abstention; abstains only when the
    transcript is gone."""
    name = "command"
    question = "command"

    def grade(self, transcript, ctx):
        if not _events(transcript):
            return Vote.no_opinion(self.question, note="empty transcript")
        det = pr.detect_invocation(transcript)
        return Vote(self.question, det["name"] or DARK, confidence=0.95, grader=self.name,
                    note="mismatch" if det.get("mismatch") else "")


class NoErrorGrader:
    """A DIFFERENT question from CommandGrader: did any tool_result come back is_error? Votes
    ok/error. Deterministic and independent of which command fired — the independence the label
    model wants."""
    name = "no_error"
    question = "no_error"

    def grade(self, transcript, ctx):
        evs = _events(transcript)
        if not evs:
            return Vote.no_opinion(self.question, note="empty transcript")
        for e in evs:
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    return Vote(self.question, "error", confidence=0.99, grader=self.name,
                                note="tool_result is_error")
        return Vote(self.question, "ok", confidence=0.9, grader=self.name)


class SchemaGrader:
    """Seam stub: validate each tool_use input against the tool's declared schema. Conforms to the
    protocol and ABSTAINS until given schemas, so it is safe to register in the ensemble today."""
    name = "schema"
    question = "schema"

    def __init__(self, schemas: Optional[dict] = None):
        self.schemas = schemas or {}

    def grade(self, transcript, ctx):
        return Vote.no_opinion(self.question,
                               note="no schemas configured" if not self.schemas else "not implemented")


class LLMJudgeGrader:
    """The NOISY grader: shells `claude -p` with a rubric, parses pass/fail + confidence. Stubbed to
    abstain without a rubric so the PR stays runtime-free in CI. The grader whose reliability the
    label model most needs to learn — never trust it solo."""
    name = "judge"
    question = "quality"

    def __init__(self, rubric: Optional[str] = None, model: Optional[str] = None):
        self.rubric, self.model = rubric, model

    def grade(self, transcript, ctx):
        return Vote.no_opinion(self.question,
                               note="no rubric configured" if not self.rubric else "not implemented")


def grade_transcript(transcript, ctx, graders):
    """Run every grader over one transcript; stamp each Vote with its grader name."""
    votes = []
    for g in graders:
        v = g.grade(transcript, ctx)
        v.grader = v.grader or g.name
        votes.append(v)
    return votes


def by_question(votes):
    """Group a transcript's votes by the question they answer."""
    out = {}
    for v in votes:
        out.setdefault(v.question, []).append(v)
    return out
