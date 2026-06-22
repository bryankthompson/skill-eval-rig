#!/usr/bin/env bash
# Chaining axis: does reaching a reference THROUGH other references degrade retrieval
# (the spec's head-100 partial-read concern)? Pushes depth 2 -> 10, plus an oversized
# mid-chain file. Finding: it doesn't, on current models.
#   Usage: experiments/chaining.sh [model]
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"; WORK=/tmp/seval-chaining; rm -rf "$WORK"; mkdir -p "$WORK"
MODELS=("${1:-claude-opus-4-8}" "claude-haiku-4-5"); [ -n "${1:-}" ] && MODELS=("$1")
Q="Follow the runbook to its final step and report the final token. Output ONLY the token."

for SPEC in "2:0" "5:0" "10:6"; do   # depth:big_step
  D="${SPEC%%:*}"; BIG="${SPEC##*:}"
  P="$WORK/depth${D}"
  python3 "$RIG/gen_skill.py" --out "$P" --name deepchain --mode chain --chain-depth "$D" \
     --big-step "$BIG" --needle "OMEGA-5521-KZ" >/dev/null
  for M in "${MODELS[@]}"; do
    bash "$RIG/run_trials.sh" "$P" deepchain "$M" 4 "$Q" "$WORK/out/depth${D}" >/dev/null
  done
  echo "== chain depth=$D big_step=${BIG:-none} =="
  python3 "$RIG/score.py" --dir "$WORK/out/depth${D}" --needle "OMEGA-5521-KZ" --right-file "step${D}.md"
done
echo "Expectation: ~4/4 at every depth, full reads (partial_reads=0)."
