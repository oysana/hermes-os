#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

root=$(cd "$(dirname "$0")/../.." && pwd)
native="$HOME/.hermes/hermes-agent"
tests="$root/tests/native-memory"
evidence="$root/.artifacts/native-memory"

if [[ ! -f "$native/scripts/_activation.sh" ]]; then
  printf 'Native memory gate: installed Hermes activation is missing at %s.\n' "$native" >&2
  exit 1
fi
source "$native/scripts/_activation.sh"
unset __HERMES_TEST_PYTHON
env_script=$(hermes_compose_env "$native" sh)
hermes_apply_env "$env_script"
if ! hermes_activation_current "$native"; then
  printf 'Native memory gate: selected test environment is stale; this gate never syncs or installs dependencies.\n' >&2
  exit 1
fi
if [[ -z "${__HERMES_TEST_PYTHON:-}" ]]; then
  printf 'Native memory gate: native test environment selection is missing; select an existing test environment before rerunning. This gate never syncs or installs dependencies.\n' >&2
  exit 1
fi
mkdir -p "$evidence"
# Snapshot only tool_search. Do not import the live config loader or resolve variables.
python3 -B - "$HOME/.hermes" "$evidence/installed-visibility.json" <<'PYTHON'
import json
import os
from pathlib import Path
import sys
import yaml


def tool_search(path):
    raw = yaml.safe_load(path.read_text()) or {}
    tools = raw.get('tools')
    return tools if isinstance(tools, dict) else {}


home = Path(sys.argv[1])
managed_paths = {Path('/etc/hermes/config.yaml')}
if os.environ.get('HERMES_MANAGED_DIR', '').strip():
    managed_paths.add(Path(os.environ['HERMES_MANAGED_DIR'].strip()) / 'config.yaml')
snapshot = {
    'profiles': {name: tool_search(home / sub / 'config.yaml').get('tool_search')
                 for name, sub in [('H', ''), ('S', 'profiles/sqecea'), ('Y', 'profiles/yqecea')]},
    'managed': [str(path) for path in sorted(managed_paths)
                if path.is_file() and 'tool_search' in tool_search(path)],
}
Path(sys.argv[2]).write_text(json.dumps(snapshot, indent=2) + '\n')
PYTHON
# Invoke only the native list handler with exact public inputs in a private namespace.
# Full CLI startup can resume gateways; see native_list.py.
"$__HERMES_TEST_PYTHON" -B "$tests/check_toolsets.py" "$evidence"
# Native imports and synthetic stores share the deny-by-default namespace.
"$__HERMES_TEST_PYTHON" -B "$tests/native_sandbox.py" "$native" "$tests" "$evidence" "$@"
