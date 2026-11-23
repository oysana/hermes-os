#!/usr/bin/env bash
# setup-pull — copy the live setup's managed surface into the repo (setup/home/).
# Use after a legitimate out-of-band write (hermes update, config set, cron add);
# commit the result so the repo stays the source of truth. Same lock as apply.
set -u
ROOT="${SETUP_ROOT:-$HOME}"
REPO="${SETUP_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
LOCK="$ROOT/.setup-sync/lock"
mkdir -p "$ROOT/.setup-sync"
flock -w 600 "$LOCK" -c "python3 '$REPO/scripts/setup-sync.py' pull"
