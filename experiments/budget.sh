#!/usr/bin/env bash
# Budget axis (STRUCTURAL, headless): how big does the always-loaded skill listing get, and
# when does the harness start dropping descriptions? Reads the listing size + over-budget
# warning straight out of `claude --debug`. (The BEHAVIORAL half — does a dropped skill still
# auto-fire — is interactive only; see experiments/activation/.)
#   Usage: experiments/budget.sh
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"; WORK=/tmp/seval-budget; rm -rf "$WORK"; mkdir -p "$WORK"

# Auth (M8): default unsets a stale ANTHROPIC_API_KEY so the CLI uses interactive OAuth creds;
# set USE_OAUTH=0 on a key-authenticated / CI machine to keep the key.
AUTHENV=(env -u ANTHROPIC_API_KEY)
if [ "${USE_OAUTH:-1}" = "0" ]; then AUTHENV=(env); fi

probe () { # $1=label $2=projdir
  ( cd "$2" && "${AUTHENV[@]}" claude -p hi --debug --debug-file "$WORK/$1.debug" </dev/null >/dev/null 2>&1 ) || true
  local sent over model
  model=$(grep -aoE "model=claude-[a-z0-9.-]+(\[1m\])?" "$WORK/$1.debug" | head -1)
  sent=$(grep -aoE "Sending [0-9]+ skills via attachment" "$WORK/$1.debug" | head -1)
  over=$(grep -a "over budget" "$WORK/$1.debug" | sed 's/.*\] //' | head -1)
  printf "%-10s %s | %s | %s\n" "$1" "$model" "$sent" "${over:-under budget (no warning)}"
}

for SPEC in "under:6:20" "edge:24:1000" "over:70:1000"; do
  L="${SPEC%%:*}"; rest="${SPEC#*:}"; NF="${rest%%:*}"; DC="${rest##*:}"
  P="$WORK/$L"
  python3 "$RIG/gen_listing.py" --out "$P" --fillers "$NF" --filler-desc-chars "$DC" >/dev/null
  probe "$L" "$P"
done
echo "The budget reported in the warning is ~1% of the model's context window, in CHARACTERS."
echo "Run /doctor inside an interactive session to see WHICH descriptions get dropped."
