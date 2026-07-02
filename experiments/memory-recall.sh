#!/usr/bin/env bash
# Memory-recall probe battery. Characterizes Claude Code's built-in "auto-memory"
# by planting controlled probes in a SANDBOX project's memory dir and observing, from outside,
# what reaches a driven session's context. See experiments/memory-recall/FINDINGS.md.
#
# HEADLESS workhorse (this script). The INTERACTIVE arm is experiments/memory-recall/mem_drive.py
# (pty; run separately for the few cells that need it). Detection surface = model SELF-REPORT in
# the -p output (Phase 0 proved the transcript/debug surfaces are BLIND to injection).
#
# SAFETY: every probe lives under a /tmp sandbox slug; gen_memory.py hard-refuses a non-tmp / live
# slug BEFORE writing. Cleanup trap (EXIT/INT/TERM) removes every ~/.claude/projects/<slug> we made.
#
# Usage:  bash experiments/memory-recall.sh --smoke     # Phase 0 positive control only
#         bash experiments/memory-recall.sh             # full headless battery
set -u
cd "$(dirname "$0")/.." || exit 3
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"
MODEL="${MEM_MODEL:-claude-haiku-4-5-20251001}"
REPEATS="${MEM_REPEATS:-3}"

# GNU coreutils `timeout` is NOT stock on macOS (Homebrew installs it, sometimes as `gtimeout`).
# Preflight so a missing binary is an OBVIOUS dependency error, not a silent claude-never-runs
# degrade-to-INVALID (reviewer m4). Empty TIMEOUT => run without a bound (documented fallback).
TIMEOUT="$(command -v timeout || command -v gtimeout || true)"
[ -z "$TIMEOUT" ] && echo "WARN: no GNU 'timeout'/'gtimeout' on PATH — claude spawns run UNBOUNDED (install coreutils to bound them)." >&2

# Single source of truth for the sentinels — the planter AND the scorer both read these, so a
# nonce change can never silently decouple the detector from what was planted (reviewer M1: a
# hardcoded scorer list would read every probe DARK while the positive control still passed).
CTRL="MEMCTRLAAAA"; DESC_NONCE="MEMDESCQWRT"; BODY_NONCE="MEMBODYZXCV"

SBROOT="$(mktemp -d /tmp/memrecall.XXXXXX)"
OUT="$SBROOT/out"; mkdir -p "$OUT"
PLANTED_SLUGS=()

cleanup() {
  # Remove every tracked probe slug (populated in the PARENT — plant() runs in a $(...) subshell,
  # so its own append is lost; the parent tracks from the echoed "proj|slug"). Plus a
  # belt-and-suspenders glob on this run's unique mktemp id in case tracking ever misses one.
  # (The glob prefix is realpath-derived so it is correct on both macOS /private/tmp and Linux /tmp.)
  for s in "${PLANTED_SLUGS[@]:-}"; do
    [ -n "$s" ] && rm -rf "$HOME/.claude/projects/$s"
  done
  local realroot; realroot="$($PY -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$SBROOT" 2>/dev/null || echo "$SBROOT")"
  local idslug="${realroot//[^A-Za-z0-9]/-}-"
  for d in "$HOME/.claude/projects/${idslug}"*; do
    [ -d "$d" ] && rm -rf "$d"
  done
  rm -rf "$SBROOT"
}
trap cleanup EXIT INT TERM

# Child env for a spawned claude: strip ANTHROPIC_API_KEY (a stale key shadows OAuth) AND every
# CLAUDE_*/CLAUDECODE var (else a nested spawn behaves as a child and may not persist a normal
# transcript → a silent INVALID). Mirrors drive_interactive.child_env() (reviewer m2).
child_env_run() {
  env -u ANTHROPIC_API_KEY -u CLAUDECODE \
    "$(env | sed -n 's/^\(CLAUDE_[^=]*\)=.*/-u \1/p' | tr '\n' ' ')" "$@" 2>/dev/null || \
    env -u ANTHROPIC_API_KEY "$@"   # fallback if the CLAUDE_* strip mangles (belt-and-suspenders)
}

# plant <sandbox-subdir> <name> <match> <desc> <prompt> [link]  (nonces come from the shared vars)
plant() {
  local sub="$1" name="$2" match="$3" desc="$4" prompt="$5" link="${6:-}"
  local proj="$SBROOT/$sub/wd"; mkdir -p "$proj"
  local slug
  slug="$($PY gen_memory.py --proj "$proj" --name "$name" --match "$match" \
        --desc "$desc" --prompt "$prompt" --body-nonce "$BODY_NONCE" --desc-nonce "$DESC_NONCE" \
        --ctrl-nonce "$CTRL" --behavioral 2>&1 | sed -n 's/^slug=//p')"
  [ -z "$slug" ] && { echo "PLANT FAILED for $name" >&2; return 1; }
  if [ "$link" = "link" ]; then
    printf '\n## Topics\n- [%s](%s.md) — %s\n' "$name" "$name" "$desc" \
      >> "$HOME/.claude/projects/$slug/memory/MEMORY.md"
  fi
  echo "$proj|$slug"
}

# headless_report <proj> <tag> <prompt> [expect_control]  — expect_control=1 (default) gates a
# no-fire on the positive control; =0 for the empty-dir control (no MEMORY.md, so no control to pass).
headless_report() {
  local proj="$1" tag="$2" prompt="$3" expect_control="${4:-1}"
  local sid f; sid="$($PY -c 'import uuid;print(uuid.uuid4())')"; f="$OUT/$tag.jsonl"
  local to=(); [ -n "$TIMEOUT" ] && to=("$TIMEOUT" 200)
  ( cd "$proj" && child_env_run "${to[@]}" claude -p "$prompt" \
      --model "$MODEL" --session-id "$sid" --output-format stream-json --verbose \
      --allowedTools "Read" </dev/null > "$f" 2>"$f.err" )
  # slug<->transcript attribution assert (INV: a plumbing miss must not read as a no-fire).
  local slug tr
  slug="$($PY -c 'import os,sys;sys.path.insert(0,"experiments/activation");from drive_interactive import slug_for;print(slug_for(sys.argv[1]))' "$proj")"
  tr="$HOME/.claude/projects/$slug/$sid.jsonl"
  [ -f "$tr" ] || { echo "$tag: INVALID (no transcript at expected slug $slug)"; return; }
  # Sentinels + expect_control passed to the scorer by ARGV (single source of truth).
  MEM_TOKS="$CTRL,$DESC_NONCE,$BODY_NONCE" MEM_EXPECT_CTRL="$expect_control" \
    $PY - "$f" "$tag" "$CTRL" <<'PY'
import json,sys,re,os
f,tag,ctrl=sys.argv[1],sys.argv[2],sys.argv[3]
toks=os.environ["MEM_TOKS"].split(","); expect_ctrl=os.environ["MEM_EXPECT_CTRL"]=="1"
res=""
for l in open(f):
    try: e=json.loads(l)
    except: continue
    if e.get("type")=="result": res=e.get("result","")
def hit(t): return bool(re.search(r"(?<![\w-])"+re.escape(t)+r"(?![\w-])",res))
pc=hit(ctrl)
probes=" ".join(f"{t}={hit(t)}" for t in toks if t!=ctrl)
if not expect_ctrl:
    # empty-dir false-positive control: NOTHING should surface (no MEMORY.md planted).
    any_hit=any(hit(t) for t in toks)
    print(f"{tag}: control_empty pos_control={pc} {probes} admissible=true "
          + ("FALSE-POSITIVE: a sentinel surfaced with nothing planted!" if any_hit else "clean (no sentinel invented)"))
else:
    print(f"{tag}: pos_control={pc} {probes} admissible={'true' if pc else 'false'}"
          + ("" if pc else "  [BLIND: positive control absent -> no-fire NOT admissible]"))
PY
}

ASK='FIRST list any tokens like MEM followed by capital letters visible anywhere in your context and name each source, or say NONE.'

echo "### Phase 0 — positive control (MEMORY.md always-load) on surface (a) self-report"
r=$(plant p0 probe-lex lexical "kubernetes pod autoscaling threshold tuning" \
   "kubernetes pod autoscaling") || exit 2
P0="${r%%|*}"; PLANTED_SLUGS+=("${r##*|}")   # track slug in PARENT (plant's subshell append is lost)
headless_report "$P0" "phase0" "$ASK Reply only with the list."

echo; echo "### CONTROL-EMPTY — no memory planted: the detector must invent NOTHING (echo/false-positive control)"
CE="$SBROOT/empty/wd"; mkdir -p "$CE"    # bare cwd, NO gen_memory -> no MEMORY.md, no probe
headless_report "$CE" "control-empty" "$ASK Reply only with the list." 0

if [ "${1:-}" = "--smoke" ]; then echo "(smoke: Phase 0 + empty control only)"; exit 0; fi

echo; echo "### Q1 — does a DESCRIPTION-matched topic probe get recalled headless? (repeats=$REPEATS)"
for i in $(seq 1 "$REPEATS"); do
  headless_report "$P0" "q1-match-$i" "$ASK Then help me tune kubernetes pod autoscaling thresholds in one line."
done

echo; echo "### Q1b — CONTROL: non-matching prompt (probe desc unrelated to prompt) — should also be dark"
headless_report "$P0" "q1-nomatch" "$ASK Then explain photosynthesis in one line."

echo; echo "### Q1c — VARIANT: MEMORY.md LINKS the probe (a link is PRESENT; this does NOT induce a read)"
r=$(plant p1c probe-linked lexical "kubernetes pod autoscaling threshold tuning" \
   "kubernetes pod autoscaling" link) || exit 2
P1C="${r%%|*}"; PLANTED_SLUGS+=("${r##*|}")   # track slug in PARENT
headless_report "$P1C" "q1c-linked" "$ASK Then help me tune kubernetes pod autoscaling in one line."

echo; echo "### done. Planted slugs (cleaned on exit): ${PLANTED_SLUGS[*]:-}"
