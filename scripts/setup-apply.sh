#!/usr/bin/env bash
# setup-apply — install the repo's managed surface into the live setup.
# Serialized on the shared lock: concurrent applies queue, never interleave.
# Env: SETUP_ROOT (default $HOME), SETUP_REPO (default: repo of this script),
#      SETUP_APPLY_HOLD (test seam: seconds to hold the lock before copying).
set -u
ROOT="${SETUP_ROOT:-$HOME}"
REPO="${SETUP_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
LOCK="$ROOT/.setup-sync/lock"
JOURNAL="$ROOT/.setup-sync/journal.log"
mkdir -p "$ROOT/.setup-sync"
ts() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
(
  flock -w 600 9 || { echo "$(ts) pid=$$ lock-timeout" >>"$JOURNAL"; exit 4; }
  echo "$(ts) pid=$$ lock-acquired" >>"$JOURNAL"
  [ -n "${SETUP_APPLY_HOLD:-}" ] && sleep "$SETUP_APPLY_HOLD"
  python3 "$REPO/scripts/setup-sync.py" apply "$@"
  rc=$?
  echo "$(ts) pid=$$ lock-released rc=$rc" >>"$JOURNAL"
  exit $rc
) 9>"$LOCK"
