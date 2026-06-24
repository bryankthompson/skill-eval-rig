#!/usr/bin/env bash
# Activation axis (INTERACTIVE, automated): does a slash command AUTO-fire on a natural prompt?
# The fifth axis and the only one that can't run headless — `claude -p` resolves a command only
# via an explicit /name, so headless CI is blind to the silent activation cliff. This drives real
# interactive `claude` sessions in a pty (drive_interactive.py) over the dir-reply OLD/REVISED A/B
# battery and scores which command the model reached for on its own.
#
# NOTE: this is the AUTOMATED form of experiments/activation/dir-reply/RUNBOOK.md (the
# human-in-the-loop protocol). Unlike the other axes it does NOT use run_trials.sh / score.py —
# those are headless `claude -p` (no auto-activation) and structurally inapplicable here; routing
# through them would silently reproduce the forced-selection-≠-auto-activation confound this axis
# exists to escape. It reuses ONLY the wrapper boilerplate below.
#   Usage: experiments/activation.sh [--smoke] [extra drive_interactive.py flags]
#          (real interactive sessions — paid OAuth, ~24-36 short sessions for the full battery)
set -u
RIG="$(cd "$(dirname "$0")/.." && pwd)"

# pexpect lives in a project venv (the driver's only extra dep; the headless axes + `make test`
# stay on bare python3). Prefer the venv interpreter; fall back to python3 with a clear hint.
PY="$RIG/.venv/bin/python3"
if [ ! -x "$PY" ]; then
  if python3 -c "import pexpect" >/dev/null 2>&1; then
    PY=python3
  else
    echo "FATAL: pexpect not found. Create the venv:  python3 -m venv $RIG/.venv && $RIG/.venv/bin/pip install -r $RIG/requirements.txt" >&2
    exit 3
  fi
fi

# Auth (M8, shared with the other axes): default unsets a stale ANTHROPIC_API_KEY so the CLI uses
# interactive OAuth creds; USE_OAUTH=0 keeps the key on a key-authenticated/CI box. (The driver
# honors USE_OAUTH itself; exported here so a child inherits it.)
export USE_OAUTH="${USE_OAUTH:-1}"

echo "activation axis — interactive pty driver (OLD vs REVISED /dir-reply description)"
exec "$PY" "$RIG/experiments/activation/drive_interactive.py" "$@"
