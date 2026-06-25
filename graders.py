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
import math
import os
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


_PYTYPE = {"object": dict, "array": list, "string": str, "boolean": bool}


def _type_ok(value, t):
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "null":
        return value is None
    py = _PYTYPE.get(t)
    return py is None or isinstance(value, py)


def _validate(value, schema):
    """Minimal JSON-Schema check: type, required, properties, enum, array items. A documented SUBSET
    — no $ref / allOf / anyOf / formats / patternProperties. Swap in `jsonschema` for full coverage;
    kept stdlib so `make test` stays dependency-free (CI runs bare python3). Returns error strings."""
    if not isinstance(schema, dict):
        return []
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return [f"expected {t}"]              # type mismatch makes deeper checks meaningless
    errs = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append("not in enum")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"missing required '{req}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errs += [f"{k}.{m}" for m in _validate(value[k], sub)]
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs += [f"[{i}].{m}" for m in _validate(item, schema["items"])]
    return errs


class SchemaGrader:
    """LIVE deterministic grader: validate each tool_use input against the tool's declared JSON
    schema. Votes ok/invalid; abstains when no schemas are configured, the transcript is empty, or
    no tool_use matches a configured schema (nothing to check is not a failure)."""
    name = "schema"
    question = "schema"

    def __init__(self, schemas: Optional[dict] = None):
        self.schemas = schemas or {}

    def grade(self, transcript, ctx):
        if not self.schemas:
            return Vote.no_opinion(self.question, note="no schemas configured")
        evs = _events(transcript)
        if not evs:
            return Vote.no_opinion(self.question, note="empty transcript")
        checked = []
        for e in evs:
            if e.get("type") != "assistant":
                continue
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in self.schemas:
                    checked.append((c["name"], _validate(c.get("input"), self.schemas[c["name"]])))
        if not checked:
            return Vote.no_opinion(self.question, note="no tool_use matched a configured schema")
        bad = [(tool, errs) for tool, errs in checked if errs]
        if bad:
            return Vote(self.question, "invalid", confidence=0.99, grader=self.name,
                        note=f"{bad[0][0]}: {bad[0][1][0]}")
        return Vote(self.question, "ok", confidence=0.97, grader=self.name)


def _clip01(x):
    return max(0.0, min(1.0, x))


def _final_assistant_text(transcript):
    txt = ""
    for e in _events(transcript):
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                    txt = c["text"]
    return txt


def _judge_prompt(rubric, user_prompt, output):
    return ('You are grading an assistant response against a rubric. Reply with ONLY a JSON object '
            '{"verdict": "pass" | "fail", "confidence": <0..1>}.\n\n'
            f"RUBRIC:\n{rubric}\n\nUSER PROMPT:\n{user_prompt}\n\nASSISTANT RESPONSE:\n{output}\n")


def _balanced_objects(text):
    """Yield each OUTERMOST brace-balanced {...} substring, STRING/escape-aware so a brace inside a
    JSON string value can't mis-split the span — letting a nested object never shadow its parent
    (the shallow-regex bug where `{"verdict":"pass","x":{"verdict":"fail"}}` read as fail)."""
    depth = start = 0
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]


def _parse_judge(raw):
    """Extract a {verdict, confidence} judgment. Parses the whole reply and each OUTERMOST {...}
    span STRUCTURALLY (never a shallow regex — a nested object must not shadow the real verdict),
    taking the last valid verdict; falls back to a bare pass/fail word. Returns (verdict, confidence)
    or (None, 0.0) when unparseable (→ the grader abstains)."""
    raw = raw or ""
    objs = []
    try:
        top = json.loads(raw.strip())
        if isinstance(top, dict):
            objs.append(top)
    except Exception:
        pass
    for span in _balanced_objects(raw):
        try:
            objs.append(json.loads(span))
        except Exception:
            continue
    for d in reversed(objs):
        if isinstance(d, dict):
            v = str(d.get("verdict", "")).lower()
            if v in ("pass", "fail"):
                try:
                    c = float(d.get("confidence", 0.5))
                    if not math.isfinite(c):
                        c = 0.5
                except Exception:
                    c = 0.5
                return v, _clip01(c)
    low = raw.lower()
    if "pass" in low and "fail" not in low:
        return "pass", 0.5
    if "fail" in low and "pass" not in low:
        return "fail", 0.5
    return None, 0.0


def _claude_runner(prompt, model=None):
    """The production runner: a headless `claude -p` judge call. Honors the rig's auth convention
    (a stale ANTHROPIC_API_KEY shadows OAuth — drop it unless USE_OAUTH=0). Only invoked when a
    rubric is configured AND no runner was injected, so `make test` never calls a live model."""
    import subprocess
    keep_key = os.environ.get("USE_OAUTH", "1") == "0"
    env = {k: v for k, v in os.environ.items() if not (k == "ANTHROPIC_API_KEY" and not keep_key)}
    args = ["claude", "-p", prompt, "--output-format", "text"]
    if model:
        args += ["--model", model]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=120).stdout


class LLMJudgeGrader:
    """LIVE noisy grader: judges the final assistant output against a rubric via a model call. The
    side-effecting boundary is the injectable `runner` (defaults to a headless `claude -p`); tests
    pass a fake runner so CI stays offline. Abstains with no rubric, no output to judge, a runner
    error, or unparseable output. The grader whose reliability the label model most needs to learn —
    never trust it solo."""
    name = "judge"
    question = "quality"

    def __init__(self, rubric: Optional[str] = None, model: Optional[str] = None, runner=None):
        self.rubric, self.model, self._runner = rubric, model, runner

    def grade(self, transcript, ctx):
        if not self.rubric:
            return Vote.no_opinion(self.question, note="no rubric configured")
        output = _final_assistant_text(transcript)
        if not output:
            return Vote.no_opinion(self.question, note="no assistant output to judge")
        prompt = _judge_prompt(self.rubric, (ctx or {}).get("prompt", ""), output)
        try:
            raw = (self._runner or _claude_runner)(prompt, self.model)
        except Exception as e:
            return Vote.no_opinion(self.question, note=f"judge runner failed: {type(e).__name__}")
        verdict, conf = _parse_judge(raw)
        if verdict is None:
            return Vote.no_opinion(self.question, note="unparseable judge output")
        return Vote(self.question, verdict, confidence=conf, grader=self.name)


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
