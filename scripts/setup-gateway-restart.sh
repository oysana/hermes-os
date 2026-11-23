#!/usr/bin/env bash
# setup-gateway-restart — the ONLY supported restart of hermes-gateway.
# In-band USR1 (restarts when idle, kills no turn) under the shared setup lock,
# so a restart can never interleave with an apply or an update.
set -u
ROOT="${SETUP_ROOT:-$HOME}"
REPO="${SETUP_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
exec "$REPO/scripts/setup-lock.sh" bash -c '
  pid=$(systemctl --user show -p MainPID --value hermes-gateway.service)
  [ "$pid" = "0" ] || [ -z "$pid" ] && { echo "gateway not running"; exit 1; }
  kill -USR1 "$pid" && echo "USR1 sent to gateway pid $pid (restarts when idle)"
'
