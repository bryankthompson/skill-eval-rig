#!/usr/bin/env bash
# Drive N headless trials of `claude -p` against a prepared project dir, capturing the full
# stream-json (tool calls + final answer) for scoring.
#
# Usage:
#   run_trials.sh <project_dir> <skill_or_-> <model> <N> <question> [outdir]
#     skill_or_-  : skill name to force-load via "/<skill>" (selection/chaining), or "-" for
#                   a bare natural prompt (NOTE: headless does NOT auto-activate skills; "-"
#                   only makes sense once you accept it can't fire a skill on its own).
#   model        : e.g. claude-opus-4-8 | claude-haiku-4-5  (or "-" for session default)
#
# AUTH NOTE: this unsets ANTHROPIC_API_KEY for the child so it uses your interactive (OAuth)
# credentials. On a clean machine with a valid key set you can drop the `env -u`.
set -u
PROJ="$1"; SKILL="$2"; MODEL="$3"; N="$4"; Q="$5"; OUT="${6:-$PROJ/_trials}"
mkdir -p "$OUT"
MFLAG=(); [ "$MODEL" != "-" ] && MFLAG=(--model "$MODEL")
PREFIX=""; [ "$SKILL" != "-" ] && PREFIX="/$SKILL

"
for n in $(seq 1 "$N"); do
  f="$OUT/$(basename "$PROJ")__${MODEL}__${n}.jsonl"
  ( cd "$PROJ" && env -u ANTHROPIC_API_KEY claude -p "${PREFIX}${Q}" "${MFLAG[@]}" \
      --output-format stream-json --verbose --max-turns 16 \
      --allowedTools "Read,Bash,Glob,Grep" </dev/null >"$f" 2>/dev/null ) || true
  echo "trial $n -> $f"
done
