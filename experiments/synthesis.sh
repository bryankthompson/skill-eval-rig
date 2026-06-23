#!/usr/bin/env bash
# Multi-file synthesis: answer = combine K planted values across K files. Tests recall (find
# all K) + computation (sum/conditional). Break is on ARITHMETIC, not recall, and is
# model-dependent. The values MUST be non-patterned: a guessable sequence (arithmetic/geometric
# progression) lets the model infer the total via a closed form WITHOUT reading the files, which
# contaminates both the recall and the arithmetic-break conclusions (H4). The generator seeds a
# fixed RNG (deterministic across runs) and ASSERTS the values are not a constant-step series.
#   Usage: experiments/synthesis.sh
set -euo pipefail
RIG="$(cd "$(dirname "$0")/.." && pwd)"; W=/tmp/synth-exp; rm -rf "$W"; mkdir -p "$W/out"
python3 - "$W" <<'PY'
import os,sys,random
r=sys.argv[1]
rng=random.Random(20260622)  # fixed seed -> reproducible, but NOT a guessable progression
def vals(K):
    # distinct, non-consecutive magnitudes with varied digit counts; explicitly NOT an
    # arithmetic/geometric progression (a constant-step series has closed-form sum
    # K*(first+last)/2 the model can produce without reading all K files).
    v=rng.sample(range(17,9973),K)
    diffs={v[i+1]-v[i] for i in range(len(v)-1)}
    assert len(diffs)>1, "values form a constant-step progression — inference risk (H4)"
    return v
for K in (3,8,20):
    v=vals(K); b=os.path.join(r,f"k{K}",".claude","skills","mega"); refs=os.path.join(b,"references"); os.makedirs(refs,exist_ok=True)
    open(os.path.join(b,"SKILL.md"),"w").write("---\nname: mega\ndescription: Deployment budget components.\n---\n# mega\nFind EVERY component file in references/, read each, SUM all values, report the integer total.\n"+
        "\n".join(f"- [references/comp-{i:02d}.md](references/comp-{i:02d}.md) — component {i}" for i in range(1,K+1)))
    for i,x in enumerate(v,1): open(os.path.join(refs,f"comp-{i:02d}.md"),"w").write(f"# component {i}\nValue: {x}.\n")
    open(os.path.join(r,f"k{K}","EXPECTED"),"w").write(str(sum(v)))
    print(f"k{K}: values={v} expected sum = {sum(v)}")
PY
for M in claude-opus-4-8 claude-haiku-4-5; do
  for K in 3 8 20; do bash "$RIG/run_trials.sh" "$W/k$K" mega "$M" 3 "Sum every component value. Output ONLY the integer total." "$W/out/k$K" >/dev/null; done
done
for K in 3 8 20; do exp=$(cat "$W/k$K/EXPECTED"); echo "== k$K (expect $exp) =="; python3 "$RIG/score.py" --dir "$W/out/k$K" --needle "$exp"; done
echo "Finding: recall holds (model reads all K) but the WEAKER model mis-sums from ~k8; the stronger model stays clean through k20+. Values are seeded-random and asserted non-progressive, so a correct sum reflects recall+computation, not index inference."
