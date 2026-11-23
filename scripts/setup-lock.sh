#!/usr/bin/env bash
# setup-lock — run a command under the shared setup lock.
# The single gate for anything that mutates the live setup or restarts the
# gateway: core/plugin updates, `hermes update`, omh-update, service restarts.
# Usage: setup-lock.sh <cmd...>   (auto-update threads wrap themselves in this)
set -u
ROOT="${SETUP_ROOT:-$HOME}"
LOCK="$ROOT/.setup-sync/lock"
mkdir -p "$ROOT/.setup-sync"
exec flock -w 900 "$LOCK" "$@"
