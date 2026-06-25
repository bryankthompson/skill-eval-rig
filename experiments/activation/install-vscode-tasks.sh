#!/usr/bin/env bash
# Install the synthetic-skill activation experiment's VS Code launcher.
# VS Code only discovers tasks at <workspace-root>/.vscode/tasks.json — NOT at
# experiments/activation/tasks.json — so an external cloner who opens the repo and
# runs "Tasks: Run Task" sees nothing until this script copies the launcher into
# place. The source of truth stays experiments/activation/tasks.json (tracked);
# the generated .vscode/tasks.json is ephemeral per-clone (gitignored).
#
# set -euo pipefail is safe here: the only operations are mkdir -p (idempotent) and
# cp (no marker-grep that legitimately exits nonzero, unlike budget.sh/activation.sh).
# Don't "harmonize" it down to set -u — there's nothing here that needs it.
set -euo pipefail

# Resolve the repo root from the script's own location (two levels up:
# experiments/activation/ -> repo root), so this works from any cwd. $0 matches
# the house style of every other script in this repo; run it as
# `bash experiments/activation/install-vscode-tasks.sh` (do not `source` it).
RIG="$(cd "$(dirname "$0")/../.." && pwd)"

SRC="$RIG/experiments/activation/tasks.json"
DEST_DIR="$RIG/.vscode"
DEST="$DEST_DIR/tasks.json"

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST"

cat <<EOF
Installed VS Code launcher -> $DEST
(source of truth: $SRC; .vscode/ is gitignored, so this copy is per-clone)

Next steps:
  1. Build the conditions if you haven't yet — run FROM THE REPO ROOT:
       cd "$RIG"
       python3 gen_listing.py --out /tmp/act/L0_under   --fillers 8  --filler-desc-chars 20
       # ...the rest of the build block in experiments/activation/RUNBOOK.md
     (gen_listing.py lives at the repo root; the task cwds default to /tmp/act/<level>.)

  2. In VS Code: Cmd-Shift-P -> "Tasks: Run Task" -> pick a level:
       activation: L0_under
       activation: L2_over
       activation: L4_present (control)
       activation: L4_dropped (needle goes dark)

  3. Follow the protocol in experiments/activation/RUNBOOK.md.

Note: re-running this script OVERWRITES $DEST. If you built the conditions
somewhere other than /tmp/act/<level>, edit the SOURCE ($SRC)
and re-run this script — do NOT hand-edit .vscode/tasks.json (it gets clobbered).
EOF
