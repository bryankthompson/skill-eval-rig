#!/usr/bin/env python3
"""Auto-fill the interactive cross-skill / activation report by SCRAPING Claude Code's
session transcripts — so you don't hand-transcribe /doctor + answers.

Claude Code logs every interactive session to ~/.claude/projects/<cwd-slug>/<id>.jsonl,
where <cwd-slug> is the working directory with '/' -> '-' (macOS realpath, so /tmp -> /private/tmp).
This reads the transcripts for each test's project dir, splits them into user->assistant turns,
finds the NATURAL-prompt turn (the bare question, not a /slash invocation), and records whether
a skill auto-fired and whether the needle token came back.

Run it at the END of the session (after you've run the xskill-validate tasks):
    python3 prefill_report.py [--since-hours 12]
Prints a filled report to stdout and writes /tmp/xskill-validation-report.md.
"""
import argparse, json, os, glob, time

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")

# (label, project-slug substring, needle token, target skill name, natural-prompt marker)
TESTS = [
    ("T1 auto-handoff (answer in skill-B FILE)", "skills-xskill-xauto", "XHANDOFF-4200", "vacuum-detail", "autovacuum_vacuum_cost_limit"),
    ("T2 handoff to skill-B LOGIC (answer in body)", "skills-xskill-xlogic", "XLOGIC-7700", "vac-logic", "autovacuum_vacuum_cost_limit"),
    ("T3 activation-cliff replication (aggregate)", "skills-activation-L4-opaque-dropped", "4200", None, "autovacuum_vacuum_cost_limit"),
]

def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                out.append(c.get("text", ""))
    return "\n".join(out)

def is_real_prompt(e):
    """A 'user' line is a real turn boundary only if it's a genuine prompt — NOT a skill
    injection ('Base directory for this skill:') and NOT a tool_result echo. When a skill
    fires, Claude Code injects a synthetic user message carrying the skill body; treating it
    as a new turn would split the answer away from its prompt."""
    msg = e.get("message") or {}
    cont = msg.get("content")
    if isinstance(cont, list):
        for c in cont:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                return False
    txt = text_of(cont)
    if "Base directory for this skill:" in txt:
        return False
    return True

def turns(path):
    """Yield (user_text, assistant_text, tool_uses[]) per REAL user->assistant turn.
    Skill-injection / tool-result 'user' lines are folded into the current turn, not split."""
    cur_u, cur_a, cur_t = None, [], []
    def flush():
        if cur_u is not None:
            return (cur_u, "\n".join(cur_a), list(cur_t))
    for line in open(path, errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        t = e.get("type")
        if t == "user" and is_real_prompt(e):
            t0 = flush()
            if t0:
                yield t0
            cur_u, cur_a, cur_t = text_of((e.get("message") or {}).get("content")), [], []
        elif t == "user":
            # skill injection / tool result: capture any injected skill name into the span's tools
            inj = text_of((e.get("message") or {}).get("content"))
            m = __import__("re").search(r"Base directory for this skill:\s*\S*/skills/([^/\s]+)", inj)
            if m and cur_u is not None:
                cur_t.append(("SkillInjected", json.dumps({"skill": m.group(1)})))
        elif t == "assistant":
            msg = e.get("message") or {}
            cont = msg.get("content") if isinstance(msg.get("content"), list) else []
            for c in cont:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    cur_a.append(c.get("text", ""))
                elif c.get("type") == "tool_use":
                    cur_t.append((c.get("name", ""), json.dumps(c.get("input", {}))))
    t0 = flush()
    if t0:
        yield t0

def analyze_session(path, token, target, marker):
    """Return the NATURAL-prompt turn's result: (fired, fired_skill, target_engaged, token_hit)."""
    for u, a, tools in turns(path):
        u = (u or "")
        if marker in u and not u.lstrip().startswith("/"):   # natural prompt, not /skill invocation
            skills = [inp for (nm, inp) in tools if nm.lower() in ("skill", "skillinjected")]
            fired = len(skills) > 0
            fired_skill = ",".join(sorted(set(skills))) if skills else ""
            blob = a + " " + " ".join(inp for _, inp in tools)
            target_engaged = bool(target) and (target in blob)
            token_hit = (token in a) or (token in blob)   # answer text OR a tool span carrying it
            return (fired, fired_skill, target_engaged, token_hit)
    return None  # no natural-prompt turn found in this session

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-hours", type=float, default=12.0)
    # M9: parameterize a single ad-hoc test so a fresh clone can point this at its OWN run
    # (slug/needle/target matching what gen_listing.py emits) instead of the baked-in past run.
    ap.add_argument("--slug", help="project-slug substring to scrape (overrides the built-in TESTS)")
    ap.add_argument("--needle", help="needle token proving the skill was used")
    ap.add_argument("--target", default=None, help="target skill name to confirm engagement (optional)")
    ap.add_argument("--marker", help="natural-prompt marker substring")
    ap.add_argument("--label", default="custom", help="label for the --slug test")
    args = ap.parse_args()
    cutoff = time.time() - args.since_hours * 3600

    if args.slug:
        if not (args.needle and args.marker):
            ap.error("--slug requires --needle and --marker")
        tests_to_run = [(args.label, args.slug, args.needle, args.target, args.marker)]
    else:
        tests_to_run = TESTS

    out = ["# Cross-skill + activation validation — auto-filled report",
           f"_scraped from interactive transcripts (last {args.since_hours:g}h) — verify the actuals before quoting_\n"]
    for label, slug, token, target, marker in tests_to_run:
        out.append(f"## {label}")
        dirs = [d for d in glob.glob(os.path.join(PROJ, f"*{slug}*")) if os.path.isdir(d)]
        if len(dirs) > 1:
            # The slug glob is an unanchored substring match — warn on collisions so a stale or
            # unrelated project dir isn't silently scraped without provenance (M9).
            out.append(f"- ⚠ {len(dirs)} project dirs match `*{slug}*` — scraping all; verify provenance: "
                       + ", ".join(os.path.basename(d) for d in dirs))
        sessions = []
        for d in dirs:
            for f in glob.glob(os.path.join(d, "*.jsonl")):
                if os.path.getmtime(f) >= cutoff:
                    sessions.append(f)
        if not sessions:
            out.append(f"- **NOT RUN** (no session transcript in `*{slug}*` within {args.since_hours:g}h)\n")
            continue
        results = []
        for f in sorted(sessions, key=os.path.getmtime):
            r = analyze_session(f, token, target, marker)
            if r:
                results.append(r)
        if not results:
            out.append(f"- ran ({len(sessions)} session(s)) but no natural-prompt turn matched `{marker}` — check manually\n")
            continue
        hits = sum(1 for (_, _, _, th) in results if th)
        fired = sum(1 for (f_, _, _, _) in results if f_)
        teng = sum(1 for (_, _, te, _) in results if te)
        n = len(results)
        if label.startswith("T3"):
            # cliff signal = did a skill AUTO-FIRE (not token presence: '4200' is short and leaks into prose)
            dark = n - fired
            out.append(f"- **{fired}/{n}** runs auto-fired the skill; **{dark}/{n} went dark** (no skill step → answered from general knowledge).")
            out.append(f"  - activation cliff {'CONFIRMED — replicates across '+str(dark)+' run(s)' if dark >= 1 and fired == 0 else 'MIXED — '+str(fired)+' fired, '+str(dark)+' dark (stochastic)' if dark else 'NOT reproduced (all fired)'}\n")
        else:
            tail = f"; reached `{target}`={teng}/{n}" if target else ""
            out.append(f"- skill auto-fired: **{fired}/{n}**{tail}; answer={token}: **{hits}/{n}**")
            verdict = "HANDOFF WORKS" if hits == n and n > 0 else ("PARTIAL" if hits else "HANDOFF FAILED (answered from general knowledge)")
            out.append(f"  - per-run: {['fired+token' if (f_ and th) else 'fired,no-token' if f_ else 'token,no-skill' if th else 'dark' for (f_,_,_,th) in results]}")
            out.append(f"  - **verdict: {verdict}**\n")
    rep = "\n".join(out)
    print(rep)
    open("/tmp/xskill-validation-report.md", "w").write(rep + "\n")
    print("\n(written to /tmp/xskill-validation-report.md)")

if __name__ == "__main__":
    main()
