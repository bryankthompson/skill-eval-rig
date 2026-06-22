#!/usr/bin/env bash
# Selection axis: does the model find the ONE right file as the count grows, and how does
# index quality matter? Reproduces the headline result: same corpus, good vs uniform index.
#   Usage: experiments/selection.sh [model]   (default both opus + haiku)
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"; WORK=/tmp/seval-selection; rm -rf "$WORK"; mkdir -p "$WORK"
MODELS=("${1:-claude-opus-4-8}" "claude-haiku-4-5"); [ -n "${1:-}" ] && MODELS=("$1")
Q="What is the documented value governing the aggressive maintenance profile on the high-write OLTP tier? Output ONLY the exact value."

for FILES in 100 1000; do
  for IDX in good uniform; do
    P="$WORK/n${FILES}_${IDX}"
    python3 "$RIG/gen_skill.py" --out "$P" --name mega --mode selection --files "$FILES" \
       --needle-file $((FILES/2+1)) --index "$IDX" --needle "CL-4200-OLTP" >/dev/null
    for M in "${MODELS[@]}"; do
      bash "$RIG/run_trials.sh" "$P" mega "$M" 5 "$Q" "$WORK/out/n${FILES}_${IDX}" >/dev/null
    done
    echo "== files=$FILES index=$IDX =="
    python3 "$RIG/score.py" --dir "$WORK/out/n${FILES}_${IDX}" --needle "CL-4200-OLTP" --right-file "t$(printf %04d $((FILES/2+1))).md"
  done
done
echo "Expectation: good index -> ~5/5 at every count; uniform index + non-lexical query -> collapses (~0/5)."
