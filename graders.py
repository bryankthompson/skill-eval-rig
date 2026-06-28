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


# is_error taxonomy. Not every `tool_result.is_error` is a reliability failure: a tool can come
# back is_error because the USER declined it, because a permission prompt wasn't granted, or because
# a by-design verify gate fired and blocked an action on purpose. Treating those as failures makes
# the no_error reliability axis untrustworthy on a real corpus — a sweep of 3,551 transcripts
# (3,183 is_error tool_results) found ~13% of them in these three non-failure buckets, the rest real
# tool errors. The marker strings below were re-derived FROM that corpus, not invented.
ERROR_BUCKETS = ("real-error", "user-rejection", "permission-not-granted", "by-design-guard")


def _result_text(content):
    """A tool_result's `content` is usually a plain string; the API list-of-blocks shape
    (`[{type:"text", text:...}]`) is the other form. Flatten both to one string; anything else → ''."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, dict):                      # single-object shape (undocumented but possible)
        return content.get("text", "")
    return "" if content is None else str(content)


def classify_is_error(text):
    """Bucket an is_error tool_result's content text into one of ERROR_BUCKETS by stable marker
    string. Only `real-error` (the default) is a genuine tool-reliability failure; the other three
    are workflow friction the no_error axis must NOT penalize:
      - user-rejection        the operator declined the tool use (the harness rejection string)
      - permission-not-granted a permission prompt that wasn't granted (the harness prompt string)
      - by-design-guard        a verify-before-assert gate firing as intended
    Markers are substring matches re-derived from the real corpus, each anchored on the single
    most-specific OUTCOME token (not a request preamble or a generic phrase) so a genuine real-error
    that merely echoes gate/permission vocabulary is not wrongly excused. They are loose only on the
    trailing tail (e.g. `verify-before-assert gate`, not the gate's full versioned header) so an
    evolving gate still classifies. Misclassifying toward `real-error` is the safe direction
    (over-count, not mask)."""
    t = text or ""
    if "The user doesn't want to proceed with this tool use" in t:
        return "user-rejection"
    if "haven't granted it yet" in t:                 # the NOT-granted outcome, not the request preamble
        return "permission-not-granted"
    if "verify-before-assert gate" in t:              # the gate header; a generic phrase would be unsafe
        return "by-design-guard"
    return "real-error"


class NoErrorGrader:
    """A DIFFERENT question from CommandGrader: did any tool_result come back is_error? Votes
    ok/error. Deterministic and independent of which command fired — the independence the label
    model wants.

    Not every is_error is a failure (see `classify_is_error`): a user rejection, an ungranted
    permission prompt, or a by-design verify gate are workflow friction, not tool failures, so they
    vote `ok` (the bucket is recorded in `note`). Only a `real-error` votes `error`. This keeps the
    vocabulary — and the `no_error: "ok"` pass policy — unchanged while making the reliability axis
    trustworthy on a real corpus."""
    name = "no_error"
    question = "no_error"

    def grade(self, transcript, ctx):
        evs = _events(transcript)
        if not evs:
            return Vote.no_opinion(self.question, note="empty transcript")
        non_fatal = []
        for e in evs:
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    bucket = classify_is_error(_result_text(c.get("content")))
                    if bucket == "real-error":
                        # First REAL error decides it — scan no further.
                        return Vote(self.question, "error", confidence=0.99, grader=self.name,
                                    note="real-error")
                    non_fatal.append(bucket)
        if non_fatal:
            # is_error(s) present, but every one is a non-failure bucket — NOT a reliability failure.
            # Buckets are sorted + de-duped for a deterministic note. Slightly lower confidence than
            # a clean `ok`: this `ok` rests on substring markers, so a future no_error grader that
            # disagrees should be able to down-weight it.
            note = "non-fatal is_error: " + ",".join(sorted(set(non_fatal)))
            return Vote(self.question, "ok", confidence=0.85, grader=self.name, note=note)
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


def _discriminator(schema):
    """If every `oneOf` arm constrains the SAME property to a distinct `const`, return that property
    name — the discriminator. This lets a failed discriminated union report the matching arm's OWN
    errors (e.g. `missing required 'slug'`) instead of a useless "matched no arm". Returns None when
    the arms aren't a clean discriminated union → fall back to generic oneOf semantics."""
    arms = schema.get("oneOf")
    if not isinstance(arms, list) or not arms:
        return None
    candidates = None
    for arm in arms:
        props = (arm.get("properties") or {}) if isinstance(arm, dict) else {}
        keyed = {k for k, sub in props.items() if isinstance(sub, dict) and "const" in sub}
        candidates = keyed if candidates is None else (candidates & keyed)
        if not candidates:
            return None
    for disc in sorted(candidates):                       # require the consts be mutually distinct
        seen = [arm["properties"][disc]["const"] for arm in arms]
        if len(seen) == len(set(map(repr, seen))):
            return disc
    return None


def _validate_oneof(value, schema):
    """oneOf = valid against EXACTLY ONE arm. With a discriminator present, the arm whose const the
    input matches is selected and ITS errors are returned (the high-value attribution); otherwise
    fall back to exactly-one-arm — safe because a discriminated union's consts are mutually
    exclusive, so at-most-one can ever match."""
    if not isinstance(value, dict):
        return ["expected object for oneOf"]             # own guard — don't assume a prior type check
    arms = schema["oneOf"]
    disc = _discriminator(schema)
    if disc is not None and disc in value:
        for arm in arms:
            if value.get(disc) == arm["properties"][disc]["const"]:
                return _validate(value, arm)             # report THIS arm's errors verbatim
        return [f"{disc} '{value[disc]}' matches no known variant"]
    # Generic path (no clean discriminator): ignore malformed non-dict arms — a non-dict arm makes
    # `_validate` return [] (vacuous match), which would let it act as a wildcard that passes anything.
    arms = [a for a in arms if isinstance(a, dict)]
    if not arms:
        return []                                        # degenerate union — nothing well-formed to check
    matches = [arm for arm in arms if not _validate(value, arm)]
    if len(matches) == 1:
        return []
    if not matches:
        return _validate(value, arms[0]) or ["matched no oneOf variant"]
    return [f"matched {len(matches)} oneOf variants (expected exactly one)"]


def _validate(value, schema):
    """Minimal JSON-Schema check: type, required, properties, enum, const, array items, and
    oneOf/anyOf (the latter for discriminated unions — see `_validate_oneof`). A documented SUBSET —
    no $ref / allOf / formats / patternProperties. Swap in `jsonschema` for full coverage; kept
    stdlib so `make test` stays dependency-free (CI runs bare python3). Returns error strings."""
    if not isinstance(schema, dict):
        return []
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return [f"expected {t}"]              # type mismatch makes deeper checks meaningless
    errs = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append("not in enum")
    if "const" in schema and value != schema["const"]:
        errs.append("const mismatch")
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
    if isinstance(schema.get("oneOf"), list):
        errs += _validate_oneof(value, schema)
    if isinstance(schema.get("anyOf"), list):            # at-least-one (used inside an arm's OR-condition)
        arms = schema["anyOf"]
        if all(_validate(value, arm) for arm in arms):
            # Don't report arm[0]'s missing key as if it were the only option — say how many there are.
            errs.append(f"satisfies no anyOf variant ({len(arms)} options)")
    # De-dup while preserving order: the no-discriminator oneOf fallback can re-report a top-level
    # `required` error. Harmless via SchemaGrader (first-error only) but keeps a full errs[] render clean.
    return list(dict.fromkeys(errs))


try:                                                          # OPTIONAL dependency, never required
    import jsonschema as _jsonschema
    from jsonschema.validators import validator_for as _validator_for
    _HAS_JSONSCHEMA = True
except ImportError:                                          # pragma: no cover - stdlib-only fallback
    _HAS_JSONSCHEMA = False


def _validate_full(value, schema):
    """Full JSON-Schema validation when `jsonschema` is importable; the stdlib `_validate` subset
    otherwise. Run STDLIB FIRST and return its errors verbatim if any — that preserves `_validate`'s
    messages (incl. the oneOf discriminator attribution) and its already-exactly-one oneOf semantics,
    so existing graders/tests are unchanged. Only when the stdlib subset finds NOTHING do we consult
    `jsonschema` to catch the keywords the subset documents as out of scope ($ref / allOf /
    additionalProperties / patternProperties / formats). This is strictly a SUPERSET of `_validate`:
    never more lenient, and on bare python3 (no jsonschema) it IS `_validate` (dependency-free
    `make test`). Returns a list of error strings (non-empty = invalid), matching `_validate`'s
    contract so SchemaGrader.grade is unaffected by the swap."""
    errs = _validate(value, schema)
    if errs or not _HAS_JSONSCHEMA or not isinstance(schema, dict):
        return errs
    try:
        cls = _validator_for(schema, default=_jsonschema.Draft202012Validator)
        cls.check_schema(schema)                            # malformed schema → SchemaError → fall back below
        validator = cls(schema)
        # str(path elems) for a total, type-mixed-safe ordering (path holds str keys AND int indices).
        return [e.message for e in sorted(validator.iter_errors(value),
                                          key=lambda e: [str(p) for p in e.path])]
    except _jsonschema.exceptions.SchemaError:
        return errs                                          # malformed schema is the author's fault, not the input's


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
                    checked.append((c["name"], _validate_full(c.get("input"), self.schemas[c["name"]])))
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
