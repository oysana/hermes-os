#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
acceptance="$root/.artifacts/hermes-bridge-20261011/live-repair/proactivity-completion-20261012"
acceptance_runner=${HERMES_ACCEPTANCE_CHECK_ROOT:-"$acceptance/prd-wave1/acceptance"}
failed=()

step() {
  local name=$1 dir=$2
  shift 2
  printf '== %s\n' "$name"
  if (cd "$dir" && "$@"); then printf 'ok  %s\n' "$name"; else failed+=("$name"); printf 'FAIL %s\n' "$name"; fi
}

step plan-coverage "$root" python3 scripts/plan-coverage.py
step file-size "$root" python3 -B scripts/check-file-size.py
step custom-ratchet "$root" python3 -B scripts/custom-ratchet.py
step laws "$root" bash scripts/check-laws.sh
step native-memory "$root" bash tests/native-memory/run.sh
step konspekt "$HOME/.hermes/profiles/sqecea/scripts" python3 -B -m unittest -q test_konspekt_check
step communications "${HERMES_COMMUNICATIONS_CHECK_ROOT:-$HOME/.hermes/shared/communications}" "$HOME/.hermes/profiles/yqecea/tools/tguser-env/bin/python" -B -m unittest discover -q -p 'test_*.py'
step y-watchers "$root" python3 -B "$root/scripts/run-selected.py" "$root/tests/y-watchers/run.py"
step smartnation-intake "$root" bash tests/smartnation-intake/run.sh
step acceptance-runner "$acceptance_runner" python3 -B -m unittest -q test_runner
step acceptance-dry-run "$acceptance_runner" python3 run_suite.py --dry-run
step acceptance-boundaries "${HERMES_ACCEPTANCE_BOUNDARY_CHECK_ROOT:-$root/tests/acceptance-runner}" env ACCEPTANCE_RUNNER_SOURCE="$acceptance_runner/run_suite.py" python3 -B -m unittest -q test_boundaries
step video-pipeline "${HERMES_VIDEO_CHECK_ROOT:-$root/tests/video-pipeline}" env SAMPLER_SOURCE="${SAMPLER_SOURCE:-$HOME/.hermes/skills/creative/visual-output/scripts/video/vo_video.py}" timeout --kill-after=2s 28s python3 -B -m unittest -q test_sheets_beats
step harness "$root" python3 -B scripts/run-selected.py scripts/verify-harness.py

step group-addressing "${HERMES_GROUP_ADDRESSING_CHECK_ROOT:-$root/tests/group-addressing}" timeout --kill-after=2s 30s bash run.sh
step comment-gate "$root/tests/comment-gate" sh -c 'python3 -B -m unittest -q test_check_comments >/dev/null 2>&1 && python3 -B check_comments.py'

step post-audit "$root" python3 -B -m unittest discover -q -s tests/post-audit -p "test_*.py"
step post-receipts "$root" sh -c 'python3 -B scripts/check-post-receipts.py --selftest && python3 -B scripts/check-post-receipts.py'
step doc-paths "$root" sh -c 'python3 -B scripts/check-doc-paths.py --selftest && python3 -B scripts/check-doc-paths.py'

testenv_py=$("$HOME/.hermes/hermes-agent/.venv/bin/python" -B -c \
  "import sys; sys.path.insert(0, '$HOME/.hermes/hermes-agent'); from pm.testenv import testenv_python; print(testenv_python('$HOME/.hermes/hermes-agent'))")
step admission-native "$root" "$testenv_py" -B -m unittest discover -q -s tests/admission -p test_admission_native.py
step continuity "$root" "$testenv_py" -B -m unittest discover -q -s tests/continuity -p 'test_*.py'
step runtime-state-contracts "$root" python3 -B -m unittest discover -q -s tests/runtime-state -p 'test_*.py'
step browser-access "$root" python3 -B -m unittest discover -q -s tests/browser-access -p 'test_*.py'
step runtime-state "$root" python3 -B "$root/scripts/run-selected.py" "$root/scripts/runtime-state-check.py"
step check-select "$root" python3 -B scripts/check-select.py --selftest
step task-state "$root" python3 -B scripts/task-state-check.py --selftest
step task-state-live "$root" python3 -B scripts/task-state-check.py
step merge-guard "$root" python3 -B -m unittest discover -q -s tests/merge-guard -p 'test_*.py'
step agents-tools "$root" python3 -B -m unittest discover -q -s tests/agents-tools -p 'test_*.py'
step agents-update "$root" python3 -B -m unittest discover -q -s tests/agents-update -p 'test_*.py'
step api-keys "$root" python3 -B "$root/scripts/run-selected.py" "$root/tests/api-keys/run.py"
step t3-harnesses "$root" bash scripts/check-t3-harnesses.sh

step outer-inner "$root" "$testenv_py" -B -m pytest tests/outer-inner -q

printf '\ntotal %ss\n' "$SECONDS"
if ((${#failed[@]})); then
  printf '\nFAILED: %s\n' "${failed[*]}"
  exit 1
fi
printf '\nall checks passed\n'
