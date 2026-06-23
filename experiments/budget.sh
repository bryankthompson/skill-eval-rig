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

# M13/M6: echo the CLI version — this table is parsed from UNPINNED claude --debug log strings.
"${AUTHENV[@]}" claude --version 2>/dev/null | sed 's/^/cli: /' || echo "cli: (unavailable)"
# NOTE: set -e is deliberately NOT used here — the marker greps below legitimately exit nonzero
# on a no-match (that IS the signal), so set -e would abort spuriously; the guard below catches
# a vanished format loudly instead.

probe () { # $1=label $2=projdir
  ( cd "$2" && "${AUTHENV[@]}" claude -p hi --debug --debug-file "$WORK/$1.debug" </dev/null >/dev/null 2>&1 ) || true
  # M6: if NONE of the expected markers is present, the CLI debug format likely changed — the
  # table would otherwise silently print "under budget (no warning)" for every row while
  # confirming nothing. Fail loudly instead of quietly.
  if ! grep -aqE "Sending [0-9]+ skills|over budget|model=claude-" "$WORK/$1.debug" 2>/dev/null; then
    echo "WARN[$1]: no recognized marker in claude --debug output — format may have changed; budget row UNRELIABLE" >&2
  fi
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
