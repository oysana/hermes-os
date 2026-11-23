#!/usr/bin/env bash
# Prove every maintained Bend law in this repository with the proven kernel.
# A LAWS.bend found here joins the gate by itself; one without a PROOF.bend beside it fails.
# runtime/repairs/ holds staged copies of other projects, .artifacts/ and work/ hold evidence.
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
bend=${BEND:-$HOME/.bend/bin/bend}
cd "$root"
mapfile -t laws < <(find . \( -path ./.git -o -path ./.artifacts -o -path ./work -o -path ./runtime/repairs \
  -o -path ./deliverables -o -name node_modules \) -prune -o -name LAWS.bend -print | sort)
((${#laws[@]})) || { echo "no LAWS.bend found"; exit 1; }
bad=0
for law in "${laws[@]}"; do
  dir=$(dirname "$law")
  if [[ ! -f $dir/PROOF.bend ]]; then echo "FAIL $law: no PROOF.bend beside it"; bad=1; continue; fi
  if out=$(cd "$dir" && timeout 300 "$bend" PROOF.bend --verdict 2>&1); then
    echo "ok   $dir"
  else
    echo "FAIL $dir"; printf '%s\n' "$out" | tail -5; bad=1
  fi
done
exit $bad
