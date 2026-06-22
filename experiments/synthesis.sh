#!/usr/bin/env bash
# Multi-file synthesis: answer = combine K planted values across K files. Tests recall (find
# all K) + computation (sum/conditional). Break is on ARITHMETIC, not recall, and is
# model-dependent. NOTE: keep values RANDOM/non-consecutive — a guessable pattern lets the
# model infer the total WITHOUT reading the files (we observed exactly that with 500..539).
#   Usage: experiments/synthesis.sh
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"; W=/tmp/synth-exp; rm -rf "$W"; mkdir -p "$W/out"
python3 - "$W" <<'PY'
import os,sys
r=sys.argv[1]
# pseudo-random-ish values (no obvious pattern) so the total can't be inferred from the index
def vals(K): return [1000 + ((i*37+13)%900) for i in range(K)]
for K in (3,8,20):
    v=vals(K); b=os.path.join(r,f"k{K}",".claude","skills","mega"); refs=os.path.join(b,"references"); os.makedirs(refs,exist_ok=True)
    open(os.path.join(b,"SKILL.md"),"w").write("---\nname: mega\ndescription: Deployment budget components.\n---\n# mega\nFind EVERY component file in references/, read each, SUM all values, report the integer total.\n"+
        "\n".join(f"- [references/comp-{i:02d}.md](references/comp-{i:02d}.md) — component {i}" for i in range(1,K+1)))
    for i,x in enumerate(v,1): open(os.path.join(refs,f"comp-{i:02d}.md"),"w").write(f"# component {i}\nValue: {x}.\n")
    open(os.path.join(r,f"k{K}","EXPECTED"),"w").write(str(sum(v)))
    print(f"k{K}: expected sum = {sum(v)}")
PY
for M in claude-opus-4-8 claude-haiku-4-5; do
  for K in 3 8 20; do bash "$RIG/run_trials.sh" "$W/k$K" mega "$M" 3 "Sum every component value. Output ONLY the integer total." "$W/out/k$K" >/dev/null; done
done
for K in 3 8 20; do exp=$(cat "$W/k$K/EXPECTED"); echo "== k$K (expect $exp) =="; python3 "$RIG/score.py" --dir "$W/out/k$K" --needle "$exp"; done
echo "Finding: recall holds (model reads all K) but the WEAKER model mis-sums from ~k8; the stronger model stays clean through k20+. If you see 100%% at trivial K with random values and 0 file reads, the values weren't random enough — the model inferred them."
