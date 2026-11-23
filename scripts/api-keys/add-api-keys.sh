#!/usr/bin/env bash
# Install OpenAI/Gemini API keys into all Hermes profiles (H default, S sqecea, Y yqecea).
#
# Thin wrapper: finds install_api_keys.py next to this script and runs it inside the
# selected Hermes interpreter through the standard run-selected.py bootstrap.
# Key values are NEVER arguments here — pass private files or type them interactively:
#   bash add-api-keys.sh --openai-key-file /path/to/openai.key --gemini-key-file /path/to/gemini.key
#   bash add-api-keys.sh                # hidden getpass prompts for both keys
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

installer="${HERMES_API_KEY_INSTALLER:-}"
if [[ -z "$installer" ]]; then
    for candidate in "$here/api-keys/install_api_keys.py" "$here/install_api_keys.py"; do
        if [[ -f "$candidate" ]]; then installer="$candidate"; break; fi
    done
fi
if [[ -z "$installer" || ! -f "$installer" ]]; then
    echo "error: install_api_keys.py not found (set HERMES_API_KEY_INSTALLER)" >&2
    exit 2
fi

run_selected="${HERMES_RUN_SELECTED:-$HOME/coding-projects/hermes-pro/scripts/run-selected.py}"
if [[ ! -f "$run_selected" ]]; then
    echo "error: run-selected.py not found at $run_selected" >&2
    exit 2
fi

if (($# == 0)); then
    set -- --interactive
fi

exec python3 -B "$run_selected" "$installer" "$@"
