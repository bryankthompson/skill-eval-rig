#!/usr/bin/env python3
"""Drive ONE interactive (pty) claude session against a probe sandbox and report which
memory sentinels reached the model — the interactive counterpart to a headless `claude -p`.

Spawns claude in a pty (pexpect, needs the rig's .venv), submits ONE prompt with an
Enter-retry loop (the claude TUI's bracketed-paste mode makes a single `\\r` unreliable for a
long prompt — it becomes a newline, not a submit; we resend Enter until an assistant answer
actually lands in the transcript), then reads the transcript's ASSISTANT text (surface a) for
the sentinels. Emits one JSON line: {valid, reason, hits{token:bool}, french, transcript}.

Why read assistant text (not the raw transcript body): Phase 0 proved the injected memory block
is NOT serialized into the transcript — only the model's OWN echo of a sentinel it saw appears.
So a sentinel in the assistant text == the model saw it in context == recall reached the model.
The positive control (MEMORY.md always-load sentinel) MUST appear or the surface is BLIND —
a probe miss with the positive control absent is invalid (a plumbing failure), never a no-fire.

Multi-turn: pass --prompt repeatedly (order preserved); each is submitted in sequence in the
SAME session (for the Q4 timing arm). Sentinels are scored over the assistant text after EACH
turn (--per-turn emits one JSON object per turn).
"""
import argparse
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "activation"))
import drive_interactive as di  # noqa: E402

PROJ_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def assistant_texts(transcript):
    """Ordered list of every assistant text block in the transcript jsonl."""
    out = []
    if not transcript or not os.path.exists(transcript):
        return out
    for line in open(transcript, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "assistant":
            continue
        msg = e.get("message", e)
        c = msg.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text":
                    out.append(b.get("text", ""))
    return out


def _hit(token, text):
    return re.search(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])", text) is not None


def _french(text):
    fr = len(re.findall(r"\b(vous|pour|les|une|avec|seuil|est|dans|votre|à|le|la|de)\b", text, re.I))
    en = len(re.findall(r"\b(the|and|you|should|with|utilization|threshold)\b", text, re.I))
    return fr > en and fr >= 3


def _submit(child, text, transcript, want_count, timeout):
    """Send `text` and await a NEW settled assistant turn (>= want_count total). Thin wrapper over
    the shared di.submit_and_await primitive — the bracketed-paste Enter-retry lives there now so
    every interactive experiment shares one robust submit path."""
    return di.submit_and_await(
        child, text, transcript, timeout=timeout,
        done=lambda: len(assistant_texts(transcript)) >= want_count and di.turn_settled(transcript))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj", required=True)
    ap.add_argument("--prompt", action="append", required=True, help="repeatable; multi-turn in order")
    ap.add_argument("--sentinels", required=True)
    ap.add_argument("--ready-wait", type=int, default=12)
    ap.add_argument("--turn-timeout", type=int, default=120)
    ap.add_argument("--per-turn", action="store_true", help="emit one JSON per turn (Q4)")
    ap.add_argument("--allow-unsafe-nontmp", action="store_true",
                    help="override the temp-root confinement guard (deliberate non-tmp sandbox)")
    args = ap.parse_args()

    # Temp-root confinement (mirrors gen_memory._guard_or_die guard 1): refuse to drive an
    # interactive session against a non-sandbox project cwd, so a caller mistake can't point the
    # session at a real project's memory. Read-only allowedTools keeps blast radius low regardless.
    _real = os.path.realpath(args.proj)
    _tmps = [os.path.realpath(p) for p in ("/tmp", "/private/tmp", os.environ.get("TMPDIR", "/tmp"))]
    if not args.allow_unsafe_nontmp and not any(_real == r or _real.startswith(r + os.sep) for r in _tmps):
        print(json.dumps({"valid": False, "reason": f"refused-nontmp-proj:{_real}"}))
        sys.exit(2)

    import pexpect
    sid = str(uuid.uuid4())
    transcript = os.path.join(PROJ_ROOT, di.slug_for(args.proj), f"{sid}.jsonl")
    tokens = [t.strip() for t in args.sentinels.split(",") if t.strip()]
    env = di.child_env()
    try:
        child = pexpect.spawn("claude", ["--session-id", sid, "--allowedTools", "Read",
                                         "--dangerously-skip-permissions"],
                              cwd=args.proj, env=env, timeout=args.turn_timeout,
                              encoding="utf-8", codec_errors="replace")
    except Exception as e:
        print(json.dumps({"valid": False, "reason": f"spawn_failed:{type(e).__name__}",
                          "transcript": transcript}))
        return
    time.sleep(args.ready_wait)
    results = []
    ok = True
    try:
        for i, p in enumerate(args.prompt, 1):
            got = _submit(child, p, transcript, want_count=i, timeout=args.turn_timeout)
            texts = assistant_texts(transcript)
            latest = texts[-1] if texts else ""
            rec = {"turn": i, "valid": got, "reason": "" if got else "no_answer",
                   "hits": {t: _hit(t, latest) for t in tokens},
                   "hits_cumulative": {t: _hit(t, "\n".join(texts)) for t in tokens},
                   "french": _french(latest), "answer_chars": len(latest)}
            results.append(rec)
            if not got:
                ok = False
                break
    finally:
        try:
            child.send("/exit"); time.sleep(0.4); child.send("\r"); time.sleep(1)
            child.close(force=True)
        except Exception:
            pass

    if args.per_turn:
        for r in results:
            print(json.dumps({**r, "transcript": transcript}))
    else:
        last = results[-1] if results else {"valid": False, "reason": "no_turns"}
        print(json.dumps({**last, "valid": ok and bool(results), "transcript": transcript,
                          "turns": len(results)}))


if __name__ == "__main__":
    main()
