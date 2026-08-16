#!/usr/bin/env bash
# Example cron wrapper showing coord_guard.sh as step 0.
#
# Pattern: your scheduler runs THIS wrapper; the wrapper consults the
# coordination board before doing any real work. If an interactive session
# holds what this job touches, the job defers (rc 75 from the guard) and
# simply exits 0 — silent skip; the job fires again on its own schedule.
#
# The guard is FAIL-OPEN: if the board/CLI is missing or broken, the job
# proceeds unguarded. Coordination protects work; it never blocks a backup.

set -euo pipefail

# --- step 0: coordination guard -------------------------------------------
# COORD_SC defaults to ~/.hermes/scripts/session_coord.py; override if you
# installed the CLI elsewhere.
# shellcheck source=scripts/coord_guard.sh disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/coord_guard.sh"

# args: <job-id-in-your-manifest> <policy skip|wait> <wait-timeout-s> <claim-ttl-min>
coord_guard "nightly-backup-example" wait 900 90
case $? in
  75) exit 0 ;;   # a session holds our resources: skip this tick silently
esac
# rc 0: either claimed (COORD_GUARD_ID set, EXIT trap releases) or fail-open.

# --- the actual job --------------------------------------------------------
echo "backing up..." >&2
# tar -czf ... etc.

# Claims auto-release via the EXIT trap installed by coord_guard.
# If you define your own EXIT trap later in this script, include `coord_done`.
