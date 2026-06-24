#!/usr/bin/env python3
"""Minimal label model — the denoising step. Operates WITHIN one question (graders answering the
same question are redundant noisy voters); combining ACROSS questions is pass_policy, not a vote.

  aggregate(votes)         combine one question's votes for one transcript into (label, confidence).
                           Naive-Bayes / Dawid-Skene-lite log-odds: each voted class gets weight
                           log(r*(K-1)/(1-r)), r = reliability*confidence. == majority when all r are
                           equal; diverges when one grader is far more reliable — the point of the
                           layer (a single hard-coded scorer can't down-weight a noisy voter).
  trial_verdict(votes)     group a transcript's votes by question, aggregate each → {question:(label,conf)}.
  estimate_reliability     bootstrap per-grader reliability with NO gold labels, from agreement with
                           the per-question majority consensus. ONE iteration.
  pass_policy(verdict)     explicit combiner: a trial PASSES iff every targeted question matches.
  cell_reliability(passes) pass-rate of a cell's valid trials + Wilson 95% CI.

CAVEAT (in code, not just docs): aggregate/estimate_reliability assume graders err INDEPENDENTLY
given the truth. A clique of correlated graders all agree, so estimate_reliability upweights them
and aggregate double-counts them → false confidence. A full label model would iterate and model
inter-grader correlations; this does neither. Use diverse graders per question; treat the estimated
reliability as a prior to override, not gospel."""
import math
from collections import defaultdict

from graders import by_question

DEFAULT_RELIABILITY = 0.7


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _conf(c):
    """A vote's self-confidence, defended: None/bool/non-numeric → DEFAULT_RELIABILITY, else clamped
    to [0,1]. A single malformed vote must not crash aggregate (and thus the whole run)."""
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return DEFAULT_RELIABILITY
    return _clamp(float(c), 0.0, 1.0)


def aggregate(votes, reliab=None, classes=None):
    """votes: list[Vote] for ONE question, ONE transcript. Returns (label, confidence, dist).
    Reliability below 0.5 is anti-informative; a minimal model floors it (you'd flip such a grader,
    not trust it — out of scope here). With a SINGLE voter the confidence is necessarily 1.0 — read
    it alongside cell_reliability's Wilson CI, not on its own."""
    active = [v for v in votes if not v.abstain and v.vote is not None]
    if not active:
        return None, 0.0, {}
    classes = classes or sorted({v.vote for v in active})
    k = max(2, len(classes))
    logp = {c: 0.0 for c in classes}
    for v in active:
        logp.setdefault(v.vote, 0.0)   # a vote outside an explicit `classes` must not KeyError
        r = _clamp((reliab or {}).get(v.grader, DEFAULT_RELIABILITY) * _conf(v.confidence), 0.5 + 1e-6, 1 - 1e-6)
        logp[v.vote] += math.log(r * (k - 1) / (1 - r))
    m = max(logp.values())
    exps = {c: math.exp(s - m) for c, s in logp.items()}
    z = sum(exps.values())
    dist = {c: e / z for c, e in exps.items()}
    label = max(logp, key=logp.get)   # ties break to the lexicographically-first class (sorted order)
    return label, dist[label], dist


def trial_verdict(votes, reliab=None):
    """One transcript's votes → {question: (label, confidence)} — a SEPARATE verdict per question."""
    return {q: aggregate(vs, reliab)[:2] for q, vs in by_question(votes).items()}


def estimate_reliability(trial_votes, classes=None):
    """trial_votes: list of list[Vote] (one inner list per transcript). One-step unsupervised:
    consensus is the per-QUESTION uniform-reliability aggregate (== majority); reliability[grader]
    = how often it agreed with its own question's consensus."""
    agree = defaultdict(lambda: [0, 0])
    for votes in trial_votes:
        for q, vs in by_question(votes).items():
            cons = aggregate(vs, reliab=None, classes=classes)[0]
            if cons is None:
                continue
            for v in vs:
                if v.abstain or v.vote is None:
                    continue
                agree[v.grader][1] += 1
                agree[v.grader][0] += int(v.vote == cons)
    return {g: (h / t if t else DEFAULT_RELIABILITY) for g, (h, t) in agree.items()}


def pass_policy(verdict, targets):
    """verdict: {question: (label, conf)}. targets: {question: expected_label}. PASS iff every
    targeted question is present and its label matches. Untargeted questions are informational."""
    return all(q in verdict and verdict[q][0] == want for q, want in targets.items())


def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half) / d, (centre + half) / d)


def cell_reliability(passes):
    """passes: list[bool] over a cell's VALID trials. Pass-rate + Wilson 95% CI — the calibrated
    reliability number, not a bare fraction (small N → honestly wide interval)."""
    n = len(passes)
    k = sum(1 for p in passes if p)
    lo, hi = _wilson(k, n)
    return {"n": n, "pass": k, "rate": (k / n if n else 0.0), "ci95": (round(lo, 3), round(hi, 3))}
