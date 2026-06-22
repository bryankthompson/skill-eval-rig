#!/usr/bin/env bash
# Instruction precedence: what wins when a SKILL conflicts with the project CLAUDE.md or the
# user's request? Maps an otherwise-undocumented hierarchy.
#   Usage: experiments/precedence.sh
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"; W=/tmp/prec-exp; rm -rf "$W"; mkdir -p "$W/out"
python3 - "$W" <<'PY'
import os,sys
r=sys.argv[1]
def setup(p, cmd, body, ref):
    os.makedirs(os.path.join(r,p), exist_ok=True)
    if cmd is not None: open(os.path.join(r,p,"CLAUDE.md"),"w").write(cmd)
    b=os.path.join(r,p,".claude","skills","mega"); os.makedirs(os.path.join(b,"references"),exist_ok=True)
    open(os.path.join(b,"SKILL.md"),"w").write(f"---\nname: mega\ndescription: Autovacuum cost-limit reference.\n---\n# mega\n{body}\n")
    if ref: open(os.path.join(b,"references","answer.md"),"w").write(ref)
setup("value","# Project config\nAUTHORITATIVE: the documented autovacuum_vacuum_cost_limit for OLTP is CMD-111.\n",
      "Read references/answer.md and report the documented value.","# x\nThe value is SKILL-222.\n")
setup("prohibit","# Project policy (MUST follow)\nNever output raw autovacuum cost-limit numbers; respond EXACTLY: REDACTED-ASK-DBA.\n",
      "Read references/answer.md and report the value directly.","# x\nThe value is SKILL-333.\n")
setup("format","# Project formatting rule\nWrap every value you report in double angle brackets <<value>>.\n",
      "Read references/answer.md and report the value.","# x\nThe value is VAL-444.\n")
print("built value(CMD-111 vs SKILL-222), prohibit(REDACTED vs SKILL-333), format(<<VAL-444>>)")
PY
Q="What is the documented autovacuum_vacuum_cost_limit for the high-write OLTP tier? Output ONLY the value."
for M in claude-opus-4-8 claude-haiku-4-5; do
  for C in value prohibit format; do bash "$RIG/run_trials.sh" "$W/$C" mega "$M" 3 "$Q" "$W/out/$C" >/dev/null; done
done
echo "Inspect answers (which token/format appears):"
for C in value prohibit format; do echo "-- $C --"; for f in "$W"/out/$C/*.jsonl; do
  python3 -c "import json,sys;[print('   ',json.loads(l).get('result','')[:55]) for l in open(sys.argv[1]) if l.strip() and json.loads(l).get('type')=='result']" "$f"; done; done
echo "Findings: CLAUDE.md POLICY/PROHIBITION overrides skills (both models); CLAUDE.md FORMATTING composes over skill output; but for a factual VALUE conflict there is NO reliable hierarchy (the freshly-read skill reference often wins). Don't rely on CLAUDE.md to 'correct' a skill's facts."
