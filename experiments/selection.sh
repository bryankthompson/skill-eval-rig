#!/usr/bin/env bash
# Selection axis: does the model find the ONE right file as the count grows, and how does index
# quality matter? Two blocks:
#  (1) SCALE + the uniform-recovers-via-grep contrast (lexically-ALIGNED query: the needle BODY
#      shares query terms, so a uniform index still recovers via grep — a real finding, not a bug);
#  (2) the FLAGSHIP decisive contrast: identical 500-file corpus, DISJOINT body (no lexical grep
#      handle), flip ONLY the index line good->uniform -> ~5/5 vs collapse ~0/5. A pre-run assert
#      guarantees no reference file carries a query term, so grep genuinely cannot recover.
#   Usage: experiments/selection.sh [model]   (default opus + haiku)
set -euo pipefail
RIG="$(cd "$(dirname "$0")/.." && pwd)"; WORK=/tmp/seval-selection; rm -rf "$WORK"; mkdir -p "$WORK"
if [ -n "${1:-}" ]; then MODELS=("$1"); else MODELS=(claude-opus-4-8 claude-haiku-4-5); fi
Q="What is the documented value governing the aggressive maintenance profile on the high-write OLTP tier? Output ONLY the exact value."

# (1) scale + lexically-aligned uniform (recovers via grep — the body shares query terms)
for FILES in 100 300 1000; do
  for IDX in good uniform; do
    P="$WORK/n${FILES}_${IDX}"
    python3 "$RIG/gen_skill.py" --out "$P" --name mega --mode selection --files "$FILES" \
       --needle-file $((FILES/2+1)) --index "$IDX" --needle "CL-4200-OLTP" >/dev/null
    for M in "${MODELS[@]}"; do
      bash "$RIG/run_trials.sh" "$P" mega "$M" 5 "$Q" "$WORK/out/n${FILES}_${IDX}" >/dev/null
    done
    echo "== files=$FILES index=$IDX (aligned query) =="
    python3 "$RIG/score.py" --dir "$WORK/out/n${FILES}_${IDX}" --needle "CL-4200-OLTP" --right-file "t$(printf %04d $((FILES/2+1))).md"
  done
done

# (2) FLAGSHIP: identical 500-file corpus, DISJOINT body, good vs uniform index. The needle token
# avoids query terms (no 'OLTP') so the disjointness assert below is meaningful.
# QKEYS includes the query's distinctive phrasing the body could otherwise reuse as a grep handle
# ('documented value'), not just the content terms — the disjoint body must share NONE of these.
FILES=500; NI=$((FILES/2+1)); QKEYS='aggressive|maintenance|high-write|OLTP|documented value'
for IDX in good uniform; do
  P="$WORK/n500disjoint_${IDX}"
  python3 "$RIG/gen_skill.py" --out "$P" --name mega --mode selection --files "$FILES" \
     --needle-file "$NI" --index "$IDX" --disjoint-body --needle "CLX-4200-ZQ" >/dev/null
  if grep -rilE "$QKEYS" "$P/.claude/skills/mega/references/" >/dev/null 2>&1; then
    echo "FATAL: disjoint-body cell has a query term in a reference file — not lexically disjoint" >&2
    exit 3
  fi
  for M in "${MODELS[@]}"; do
    bash "$RIG/run_trials.sh" "$P" mega "$M" 5 "$Q" "$WORK/out/n500disjoint_${IDX}" >/dev/null
  done
  echo "== files=500 index=$IDX (DISJOINT body — no grep handle) =="
  python3 "$RIG/score.py" --dir "$WORK/out/n500disjoint_${IDX}" --needle "CLX-4200-ZQ" --right-file "t$(printf %04d $NI).md"
done

echo "Expectation: good index -> ~5/5 at every count; 100/300/1000 uniform RECOVER via grep (body shares query terms); the 500 DISJOINT uniform cell COLLAPSES ~0/5 (no index signal AND no lexical handle) vs its good twin ~5/5 — THAT is the headline routing-signal contrast."
