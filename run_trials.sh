#!/usr/bin/env bash
# Drive N headless trials of `claude -p` against a prepared project dir, capturing the full
# stream-json (tool calls + final answer) for scoring.
#
# Usage:
#   run_trials.sh <project_dir> <skill_or_-> <model> <N> <question> [outdir]
#     skill_or_-  : skill name to force-load via "/<skill>" (selection/chaining), or "-" for
#                   a bare natural prompt (NOTE: headless does NOT auto-activate skills; "-"
#                   only makes sense once you accept it can't fire a skill on its own).
#     model        : e.g. claude-opus-4-8 | claude-haiku-4-5  (or "-" for session default)
#
# AUTH (M8): by default this unsets ANTHROPIC_API_KEY for the child so it uses your interactive
# (OAuth) credentials — a stale/invalid key otherwise shadows them. On a key-authenticated or CI
# machine, set USE_OAUTH=0 to KEEP the key (otherwise the forced OAuth path fails and, per the
# validity guard, every trial is reported INVALID rather than silently scored as a model miss).
#
# Each trial's stderr is captured to <trial>.err (not discarded) so a failure is diagnosable;
# the scorer marks empty/no-result/errored trials INVALID (excluded from rates), so infra
# failures can never masquerade as a behavioral 0/N (H1).
set -euo pipefail
PROJ="$1"; SKILL="$2"; MODEL="$3"; N="$4"; Q="$5"; OUT="${6:-$PROJ/_trials}"
mkdir -p "$OUT"

# Fixture sanity (M7): a missing/partial skill would otherwise drive every trial to a false miss.
if [ "$SKILL" != "-" ] && [ ! -f "$PROJ/.claude/skills/$SKILL/SKILL.md" ]; then
  echo "FATAL: skill '$SKILL' not found at $PROJ/.claude/skills/$SKILL/SKILL.md — fixture not built?" >&2
  exit 3
fi

MFLAG=()
if [ "$MODEL" != "-" ]; then MFLAG=(--model "$MODEL"); fi
PREFIX=""
if [ "$SKILL" != "-" ]; then PREFIX="/$SKILL

"; fi

AUTHENV=(env -u ANTHROPIC_API_KEY)
if [ "${USE_OAUTH:-1}" = "0" ]; then AUTHENV=(env); fi

for n in $(seq 1 "$N"); do
  f="$OUT/$(basename "$PROJ")__${MODEL}__${n}.jsonl"
  # ${MFLAG[@]+"..."} guards the empty-array expansion: on bash 3.2 (stock macOS) a bare
  # "${MFLAG[@]}" under set -u aborts with "unbound variable" when MODEL='-' (MFLAG empty),
  # which would silently turn every trial INVALID. The +-form expands to nothing when unset.
  if ( cd "$PROJ" && "${AUTHENV[@]}" claude -p "${PREFIX}${Q}" ${MFLAG[@]+"${MFLAG[@]}"} \
        --output-format stream-json --verbose --max-turns 16 \
        --allowedTools "Read,Bash,Glob,Grep" </dev/null >"$f" 2>"$f.err" ); then
    echo "trial $n -> $f"
  else
    rc=$?
    echo "trial $n -> $f  (claude exit $rc; see $f.err — scored INVALID, not a miss)" >&2
  fi
done
