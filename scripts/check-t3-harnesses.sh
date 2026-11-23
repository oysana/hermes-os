#!/usr/bin/env bash
# Offline source checks only: no Hermes, live histories, accounts or inference.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python3 -B tests/harness-native/test_native_profiles.py
python3 -B -m unittest discover -s tests/harness-native -p 'test_profile_guards.py' -v
python3 -B -m unittest discover -s tests/harness-native -p 'test_route_probe.py' -v
python3 -B -m unittest discover -s tests/harness-native -p 'test_model_strategy.py' -v
python3 scripts/harness-usage.py --selftest
python3 scripts/check-rollback-links.py
# Compaction floor (owner rule, repeated 10x): no harness below 500k.
# Guard lives in ~/.hermes; --selftest proves it goes red on 100000.
python3 "$HOME/.hermes/scripts/compaction-floor.py" --selftest
python3 "$HOME/.hermes/scripts/compaction-floor.py"
# Permission-claim lint: "owner approved X" is only allowed with a verbatim
# quote on the same line (the 07.11 briefing error claimed a permission the
# owner never gave). Quoted/negated mentions are fine.
python3 - <<'PY'
import re, sys
PAT = re.compile(r'владелец разрешил|owner (approved|permitted|allowed|authorized)', re.I)
QUOTE = re.compile(r'[«»"]')
bad = []
for f in ('.artifacts/harness-ultra-audit-20261107/HARNESS-LEDGER.md',
          '.artifacts/harness-ultra-audit-20261107/ROLLBACK.md',
          '.artifacts/harness-ultra-audit-20261107/REPORT-ROUND3.md',
          'CURRENT_WORK.md'):
    try:
        for i, line in enumerate(open(f, encoding='utf-8'), 1):
            if PAT.search(line) and not QUOTE.search(line):
                bad.append(f'{f}:{i}: permission claim without verbatim quote')
    except OSError:
        pass
if bad:
    print('\n'.join(bad)); sys.exit(1)
print('permission-claim lint OK')
PY
