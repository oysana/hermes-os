#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
export TG_ADDRESSING_SOURCE=${TG_ADDRESSING_SOURCE:-"$HOME/.hermes/shared/communications/addressing.py"}
export WA_PERSONAL_EVENT_SOURCE=${WA_PERSONAL_EVENT_SOURCE:-"$HOME/.hermes/profiles/yqecea/wa-reader/personal-event.mjs"}
cd "$here"
python3 -B -m unittest -q test_addressing
node --test test_addressing.mjs personal-event.test.mjs
