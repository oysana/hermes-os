#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root/runtime/repairs/20261030-S-Y/S-smartnation"
python3 -B -m unittest discover -q -s tests -p 'test_*.py'
