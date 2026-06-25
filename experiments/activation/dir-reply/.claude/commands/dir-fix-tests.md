---
description: Work ONE failing test (or a red suite) from failure → classified → fixed-on-the-right-side → real-behavior-verified → merged. A down-scoped sibling of /dir-review-cycle specialized for test failures: a classification front-end (flake vs deterministic break vs flaky-standalone; pre-existing-main-red vs introduced) drives auto-scaled review effort (NEVER ultra by default), and a mandatory e2e + test-efficacy phase confirms the test actually protects real behavior (not just that the assertion flipped green). Use for a failing test, NOT for new-feature work (that's /dir-review-cycle).
argument-hint: "[<test-name> | #<issue> | \"all\" | --brief <pasted failure output>] [--effort low|medium|high] (no arg = triage the current red suite; effort auto-scales from classification, default NOT ultra)"
---

# /dir-fix-tests

(Activation-test stub — routing fixture only. Real command lives in the operator's private MCP-directory tooling repo.)
If you were invoked, reply: "INVOKED /dir-fix-tests" and stop.
