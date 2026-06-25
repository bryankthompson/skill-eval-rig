#!/usr/bin/env python3
"""Automated interactive auto-activation axis — the fifth axis, and the only one that can't run
headless (`claude -p` resolves a command only via an explicit /name; it never AUTO-decides). This
drives *interactive* `claude` sessions in a pty, types a natural prompt, and scores which slash
command (if any) the model reached for on its own — over the dir-reply OLD/REVISED A/B battery.

It is the scripted form of experiments/activation/dir-reply/RUNBOOK.md (the human-in-the-loop
protocol). Scoring reuses prefill_report.invoked_command() — no second transcript scraper.

Phase-0 findings this encodes (a live spike settled them before this was written):
  * pty-interactive claude DOES auto-activate (fires a `Skill` tool_use, stub replies INVOKED).
  * The child MUST run with a CLEAN env — strip every CLAUDE_*/CLAUDECODE var, else
    CLAUDE_CODE_CHILD_SESSION makes claude behave as a spawned child and never persists a
    normal transcript (discovery silently fails).
  * `--session-id <uuid>` pins the transcript at ~/.claude/projects/<cwd-slug>/<uuid>.jsonl,
    where <cwd-slug> = re.sub('[^A-Za-z0-9]','-', realpath(cwd)) (EVERY non-alnum char, not just '/').
  * The prompt must be TYPED then submitted with a discrete '\r' — a positional prompt arg is
    swallowed by the variadic --allowedTools, and a pasted trailing newline doesn't submit.
  * Turn-completion is read from the TRANSCRIPT (content-aware), never the ANSI-laden PTY output.

This module is the ONLY place pexpect is imported (so `make test` / CI stay green without it).
Run: experiments/activation.sh   (or: .venv/bin/python3 drive_interactive.py --help)
"""
import argparse, json, os, re, sys, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))         # skill-eval-rig/
sys.path.insert(0, ROOT)
import prefill_report as pr  # noqa: E402  (reuse turns()/invoked_command — no second scraper)

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects")
FIX_REVISED = os.path.join(HERE, "dir-reply")        # REVISED description
FIX_OLD = os.path.join(HERE, "dir-reply-old")        # OLD (original) description

# Battery (from dir-reply/RUNBOOK.md). Negatives carry their expected owner; both run under BOTH
# arms so the A/B "globals cancel" logic has the paired baseline to attribute a hold/steal to.
POSITIVES = [
    "create this draft email in gmail",
    "draft them an email",
    "write a reply to Joe about his submission",
    "reply to the partner on this thread",
]
NEGATIVES = [
    ("sync my email tracking db with gmail", "dir-email-sync"),
    ("this test is failing, fix it", "dir-fix-tests"),
]


def slug_for(path):
    """Claude's ~/.claude/projects/<slug> dir: EVERY non-alnum char in the realpath → '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))


def child_env():
    """OAuth + clean env. Default strips ANTHROPIC_API_KEY (a stale key shadows OAuth; USE_OAUTH=0
    keeps it for a key-authenticated/CI box) AND every CLAUDE_*/CLAUDECODE var (the child must be a
    fresh top-level session — see module docstring)."""
    keep_key = os.environ.get("USE_OAUTH", "1") == "0"
    out = {}
    for k, v in os.environ.items():
        if k.startswith("CLAUDE_") or k == "CLAUDECODE":
            continue
        if k == "ANTHROPIC_API_KEY" and not keep_key:
            continue
        out[k] = v
    return out


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


def turn_settled(path):
    """Content-aware completion (NOT byte-idle alone — a Skill tool-execution gap leaves the file
    byte-idle with an assistant message already present, and a pure idle check would call the turn
    done mid-command and false-negative the headline metric). The turn is settled when there is an
    assistant text block AND every assistant tool_use has a matching tool_result (nothing dangling).
    The stub's INVOKED reply is a definitive fast-path."""
    evs = _events(path)
    if not evs:
        return False
    tool_use_ids, tool_result_ids = set(), set()
    has_assistant_text = False
    for e in evs:
        msg = e.get("message") or {}
        cont = msg.get("content")
        if e.get("type") == "assistant" and isinstance(cont, list):
            for c in cont:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    has_assistant_text = True
                    if "INVOKED /" in (c.get("text") or ""):
                        return True            # definitive: stub replied and stopped
                elif c.get("type") == "tool_use":
                    # Track by id INCLUDING a missing id (None). An id-less tool_use then can't be
                    # cleared by the (id-bearing) tool_results, so the turn stays UNSETTLED and falls
                    # to the hard-timeout — a safe invalid trial — rather than settling EARLY
                    # mid-command (the dangerous direction: a false-negative on the headline metric).
                    # Real transcripts always carry ids (Phase-0 capture), so this only governs
                    # malformed input; erring toward "wait" is correct there.
                    tool_use_ids.add(c.get("id"))
        elif e.get("type") == "user" and isinstance(cont, list):
            for c in cont:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    tool_result_ids.add(c.get("tool_use_id"))
    return has_assistant_text and tool_use_ids.issubset(tool_result_ids)


def run_trial(prompt, fixture, *, settle_idle=6, hard_timeout=150, ready_wait=7):
    """Drive ONE fresh interactive session; return a trial dict.
      {fixture, prompt, invoked, mismatch, valid, reason, elapsed, transcript}
    valid=False (reason=timeout|no_transcript) marks an INFRA failure — never a model miss."""
    import pexpect  # imported HERE only — keeps prefill_report.py / the tests pexpect-free
    sid = str(uuid.uuid4())
    transcript = os.path.join(PROJ, slug_for(fixture), f"{sid}.jsonl")
    base = {"fixture": os.path.basename(fixture), "prompt": prompt, "transcript": transcript}
    env = child_env()
    # --allowedTools is VARIADIC: terminate it with the trailing --dangerously flag and pass NO
    # positional prompt (it would be swallowed as tool names). SlashCommand,Read lets a command
    # fire while denying the raw tools that would let the model answer WITHOUT reaching for one.
    args = ["--session-id", sid, "--allowedTools", "SlashCommand,Read",
            "--dangerously-skip-permissions"]
    t0 = time.time()
    try:
        child = pexpect.spawn("claude", args, cwd=fixture, env=env,
                              timeout=hard_timeout, maxread=16384, encoding="utf-8",
                              codec_errors="replace")
    except Exception as e:
        # A spawn failure (claude not on PATH, pty exhaustion) is an INFRA failure for THIS trial —
        # return it invalid rather than aborting the whole battery.
        return {**base, "invoked": None, "mismatch": False, "valid": False,
                "reason": f"spawn_failed:{type(e).__name__}", "elapsed": 0.0}
    try:
        time.sleep(ready_wait)                 # let the TUI come up
        child.send(prompt); time.sleep(1.5)
        child.send("\r")                       # discrete Enter (paste newline doesn't submit)
        time.sleep(3)
        if not os.path.exists(transcript):
            child.send("\r")                   # second Enter if the first raced the paste flush
        # Poll the transcript (deterministic path) for content-aware completion.
        last_size, last_change = -1, time.time()
        settled = False
        while time.time() - t0 < hard_timeout:
            time.sleep(2)
            try:
                child.read_nonblocking(size=65536, timeout=0)   # keep the pty drained
            except Exception:
                pass
            if not os.path.exists(transcript):
                continue
            sz = os.path.getsize(transcript)
            if sz != last_size:
                last_size, last_change = sz, time.time()
            if turn_settled(transcript) and time.time() - last_change >= settle_idle:
                settled = True
                break
        elapsed = round(time.time() - t0, 1)
    finally:
        try:
            child.send("/exit"); time.sleep(0.4); child.send("\r"); time.sleep(1)
            child.close(force=True)
        except Exception:
            pass

    if not os.path.exists(transcript):
        return {**base, "invoked": None, "mismatch": False, "valid": False,
                "reason": "no_transcript", "elapsed": round(time.time() - t0, 1)}
    if not settled:
        return {**base, "invoked": None, "mismatch": False, "valid": False,
                "reason": "timeout", "elapsed": elapsed}
    det = pr.detect_invocation(transcript)
    return {**base, "invoked": det["name"], "mismatch": det["mismatch"],
            "valid": True, "reason": "", "elapsed": elapsed}


def _majority(names):
    """Majority winner among VALID trials (None counts as 'dark'). Returns (winner, tally)."""
    from collections import Counter
    tally = Counter(n if n is not None else "(none)" for n in names)
    if not tally:
        return None, tally
    top, n = tally.most_common(1)[0]
    return (None if top == "(none)" else top), tally


def run_cell(prompt, fixture, repeats, escalate, **kw):
    """Run a (prompt, fixture) cell `repeats` times; if the valid winners split (no >50%), escalate
    to `escalate` total. Returns the trial list."""
    trials = [run_trial(prompt, fixture, **kw) for _ in range(repeats)]
    valid = [t["invoked"] for t in trials if t["valid"]]
    if valid:
        _, tally = _majority(valid)
        top_n = tally.most_common(1)[0][1]
        if top_n * 2 <= len(valid) and len(trials) < escalate:   # no strict majority → escalate
            trials += [run_trial(prompt, fixture, **kw) for _ in range(escalate - len(trials))]
    return trials


def provenance():
    """Pin the run to its claude version + the global command set (the A/B 'globals cancel within a
    run' assumption holds within a run, not across time — record it so the result is auditable)."""
    import subprocess
    env = child_env()
    try:
        ver = subprocess.run(["claude", "--version"], capture_output=True, text=True, env=env,
                             timeout=30).stdout.strip()
    except Exception:
        ver = "(unavailable)"
    gdir = os.path.join(os.path.expanduser("~"), ".claude", "commands")
    globals_ = sorted(f[:-3] for f in os.listdir(gdir)) if os.path.isdir(gdir) else []
    return {"claude_version": ver, "global_commands": globals_}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=2, help="trials per cell (default 2)")
    ap.add_argument("--escalate", type=int, default=5, help="trials per cell on a split (default 5)")
    ap.add_argument("--timeout", type=int, default=150, help="hard per-trial timeout seconds")
    ap.add_argument("--arms", default="OLD,REVISED", help="comma list of arms to run")
    ap.add_argument("--positives-only", action="store_true")
    ap.add_argument("--negatives-only", action="store_true")
    ap.add_argument("--smoke", action="store_true",
                    help="one positive × REVISED, 1 trial — a fast e2e of the whole path")
    ap.add_argument("--json", help="also write the full result record to this path")
    args = ap.parse_args()

    arms = {"OLD": FIX_OLD, "REVISED": FIX_REVISED}
    arms = {k: v for k, v in arms.items() if k in args.arms.split(",")}
    kw = dict(hard_timeout=args.timeout)

    prov = provenance()
    print(f"claude: {prov['claude_version']}")
    print(f"global commands present: {len(prov['global_commands'])}; "
          f"competitor /mcp-prime-dev-email present: {'mcp-prime-dev-email' in prov['global_commands']}")

    if args.smoke:
        t = run_trial(POSITIVES[0], FIX_REVISED, hard_timeout=args.timeout)
        print(f"\n[smoke] {t}")
        sys.exit(0 if (t["valid"] and t["invoked"] == "dir-reply") else 1)

    # Floor the battery at 2 trials/cell: the activation axis is the documented stochastic
    # silent-cliff axis, so a single trial is indicative, not a verdict (RUNBOOK). --smoke (above)
    # is the deliberate 1-trial e2e and is exempt.
    repeats = max(2, args.repeats)
    if repeats != args.repeats:
        print(f"(repeats floored {args.repeats}→{repeats}: a verdict needs ≥2 trials/cell)")

    results = {"provenance": prov, "cells": []}
    for arm, fixture in arms.items():
        if not args.negatives_only:
            for p in POSITIVES:
                trials = run_cell(p, fixture, repeats, args.escalate, **kw)
                winner, tally = _majority([t["invoked"] for t in trials if t["valid"]])
                inv = sum(1 for t in trials if not t["valid"])
                results["cells"].append({"arm": arm, "kind": "positive", "prompt": p,
                                         "winner": winner, "tally": dict(tally), "invalid": inv,
                                         "trials": trials})
                print(f"[{arm:7}] POS  {p[:42]:42} → {winner or 'dark':20} {dict(tally)}"
                      + (f"  invalid={inv}" if inv else ""))
        if not args.positives_only:
            for p, owner in NEGATIVES:
                trials = run_cell(p, fixture, repeats, args.escalate, **kw)
                winner, tally = _majority([t["invoked"] for t in trials if t["valid"]])
                inv = sum(1 for t in trials if not t["valid"])
                results["cells"].append({"arm": arm, "kind": "negative", "prompt": p,
                                         "expected_owner": owner, "winner": winner,
                                         "tally": dict(tally), "invalid": inv, "trials": trials})
                print(f"[{arm:7}] NEG  {p[:42]:42} → {winner or 'dark':20} (want {owner}) {dict(tally)}"
                      + (f"  invalid={inv}" if inv else ""))

    verdict = score_battery(results["cells"])
    results["verdict"] = verdict
    print(f"\n==== VERDICT: {verdict['label']} ====\n{verdict['detail']}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"(full record → {args.json})")
    sys.exit(0)


def score_battery(cells):
    """Apply the RUNBOOK bar to the OLD/REVISED cells.

    The honest measure of a DESCRIPTION change is its MARGINAL effect: gain is only possible on the
    positives OLD did not ALREADY auto-fire. The `/dir-reply` command NAME alone routes "reply…"
    prompts even under the original (OLD, email-token-free) description, so those are at ceiling under
    OLD with no headroom for the description to move — counting them as "not gained" would
    mislabel a working fix. So gain is scored over the OLD-DARK denominator (positives OLD missed),
    not all positives. Buckets:
      untestable    — /mcp-prime-dev-email wins ≥half the positives under BOTH arms (masks the delta).
      no-headroom   — OLD already auto-fires dir-reply on ALL positives (the NAME routes them); the
                      description delta is unmeasurable on this battery.
      fix-validated — REVISED gains dir-reply on EVERY OLD-dark positive (full marginal gain) + holds negatives.
      fix-effective(partial) — REVISED gains SOME but not all OLD-dark positives + holds negatives.
      fix-failed    — REVISED gains NONE of the OLD-dark positives, or steals a negative into dir-reply.
    Pure function (no live runs) so tests can pin it."""
    def winners(arm, kind):
        return {c["prompt"]: c["winner"] for c in cells if c["arm"] == arm and c["kind"] == kind}
    old_pos, rev_pos = winners("OLD", "positive"), winners("REVISED", "positive")
    # expected_owner is the ONLY optional negative-cell field — arm/kind/prompt/winner are always
    # set (driver lines 268/278, _cell), so the sibling bracket-reads here stay bare on purpose.
    # .get(...,"?") renders a defensive sentinel for a SYNTHETIC/future negative recorded without an
    # owner; it is render-only (flows solely into neg_detail), never re-entering the cells. No shipped
    # battery omits the key, so every verdict label and the detail string are unchanged.
    rev_neg = {c["prompt"]: (c["winner"], c.get("expected_owner", "?"))
               for c in cells if c["arm"] == "REVISED" and c["kind"] == "negative"}

    # OLD-dark = the addressable set: positives OLD did NOT already route to dir-reply.
    old_dark = [p for p in rev_pos if old_pos.get(p) != "dir-reply"]
    gained = [p for p in old_dark if rev_pos[p] == "dir-reply"]
    name_routed = len(rev_pos) - len(old_dark)   # already won by the /dir-reply NAME under OLD
    comp_both = [p for p in rev_pos
                 if rev_pos[p] == "mcp-prime-dev-email" and old_pos.get(p) == "mcp-prime-dev-email"]
    # "REVISED must NOT STEAL these into /dir-reply": a negative is HELD unless dir-reply won it.
    # Dark / routed-to-owner / routed-elsewhere are all non-steals = held; only dir-reply is a regression.
    neg_held = all(w != "dir-reply" for (w, _owner) in rev_neg.values()) if rev_neg else True
    neg_detail = ", ".join(f"{p[:24]}→{w or 'dark'}(want {o})" for p, (w, o) in rev_neg.items())
    marginal = (f"gained dir-reply on {len(gained)}/{len(old_dark)} of the positives OLD did NOT "
                f"already fire (OLD auto-routes {name_routed}/{len(rev_pos)} via the /dir-reply NAME)")

    if rev_pos and len(comp_both) >= max(1, len(rev_pos) // 2):
        return {"label": "DESCRIPTION-DELTA UNTESTABLE",
                "detail": f"/mcp-prime-dev-email wins {len(comp_both)}/{len(rev_pos)} positives under "
                          f"BOTH arms — the global competitor masks the description change. "
                          f"Negatives: {neg_detail}"}
    if rev_pos and not old_dark:
        return {"label": "NO HEADROOM (OLD already name-routes all positives)",
                "detail": f"OLD auto-fires dir-reply on all {len(rev_pos)} positives via the command "
                          f"NAME alone — the description delta is unmeasurable here. Negatives: {neg_detail}"}
    if old_dark and len(gained) == len(old_dark) and neg_held:
        return {"label": "FIX VALIDATED",
                "detail": f"{marginal}; negatives held: {neg_detail}"}
    if gained and neg_held:
        return {"label": "FIX EFFECTIVE (PARTIAL)",
                "detail": f"{marginal}; negatives held: {neg_detail}"}
    return {"label": "FIX FAILED / INCONCLUSIVE",
            "detail": f"{marginal}; negatives held={neg_held}: {neg_detail}"}


if __name__ == "__main__":
    main()
