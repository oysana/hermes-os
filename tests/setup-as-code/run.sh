#!/usr/bin/env bash
# Proof run of the setup-as-code mechanism against an ISOLATED HOME copy.
# Creates $SCRATCH/home (fake $HOME) and $SCRATCH/repo (this repo's setup/
# + scripts/), seeds live files, then asserts:
#   T1 clean drift, T2 concurrent applies serialize on the lock,
#   T3 out-of-band edit caught by drift, T4 broken repo config never touches
#   live (pre-verify), T5 mid-apply failure rolls back, T6 json-state merge
#   keeps scheduler runtime fields while installing repo config.
# Usage: bash tests/setup-as-code/run.sh   (SCRATCH defaults under
#   ~/.hermes/cache/scratch; live $HOME is never touched)
set -u
SCRATCH="${SETUP_TEST_SCRATCH:-$HOME/.hermes/cache/scratch/setup-as-code}"
ROOT="$SCRATCH/home"; REPO="$SCRATCH/repo"
SELF="$(cd "$(dirname "$0")/../.." && pwd)"
export SETUP_ROOT="$ROOT" SETUP_REPO="$REPO"
rm -rf "$SCRATCH"; mkdir -p "$ROOT" "$REPO/scripts" "$REPO/setup"
cp "$SELF/scripts/setup-sync.py" "$SELF/scripts/setup-apply.sh" \
   "$SELF/scripts/setup-pull.sh" "$SELF/scripts/setup-lock.sh" "$REPO/scripts/"
cp "$SELF/setup/manifest.tsv" "$REPO/setup/"
pass=0; fail=0
ok()   { echo "PASS $1"; pass=$((pass+1)); }
bad()  { echo "FAIL $1 — $2"; fail=$((fail+1)); }

# --- seed a representative fake home -----------------------------------------
mkdir -p "$ROOT/.hermes/cron" "$ROOT/.hermes/scripts" "$ROOT/.codex" \
         "$ROOT/.config/systemd/user"
cat >"$ROOT/.hermes/config.yaml" <<'EOF'
model: gpt-6.1-terra
gateway:
  multiplex_profiles: true
EOF
cat >"$ROOT/.hermes/cron/jobs.json" <<'EOF'
{"jobs": [{"id": "j1", "name": "morning", "schedule": "0 9 * * *",
           "enabled": true, "last_run_at": "2026-11-23T04:00:00Z",
           "next_run_at": "2026-11-24T04:00:00Z", "failure_streak": 0,
           "fire_claim": null}]}
EOF
echo "echo hi" >"$ROOT/.hermes/scripts/a.sh"
echo "[model]" >"$ROOT/.codex/config.toml"
echo "gateway" >"$ROOT/.config/systemd/user/hermes-gateway.service"

bash "$REPO/scripts/setup-pull.sh" >/dev/null || { bad "T0 pull" "rc=$?"; exit 1; }

# T1 clean drift
if python3 "$REPO/scripts/setup-sync.py" drift >/dev/null; then ok "T1 drift-clean"; else bad "T1 drift-clean" "rc=$?"; fi

# T2 serialization: two applies, lock journal must not interleave
SETUP_APPLY_HOLD=2 bash "$REPO/scripts/setup-apply.sh" >/dev/null & p1=$!
sleep 0.3
bash "$REPO/scripts/setup-apply.sh" >/dev/null & p2=$!
rc1=0; rc2=0; wait $p1 || rc1=$?; wait $p2 || rc2=$?
rel1=$(grep 'lock-released' "$ROOT/.setup-sync/journal.log" | head -1 | awk '{print $1}')
acq2=$(grep 'lock-acquired' "$ROOT/.setup-sync/journal.log" | tail -1 | awk '{print $1}')
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && [[ "$acq2" > "$rel1" || "$acq2" == "$rel1" ]]; then
  ok "T2 serialize (acq2=$acq2 >= rel1=$rel1)"
else bad "T2 serialize" "rc=$rc1/$rc2 journal:"; cat "$ROOT/.setup-sync/journal.log"; fi

# T3 out-of-band edit caught
echo "rogue: true" >>"$ROOT/.hermes/config.yaml"
out=$(python3 "$REPO/scripts/setup-sync.py" drift); rc=$?
if [ $rc = 1 ] && echo "$out" | grep -q 'CHANGED.*\.hermes/config.yaml'; then ok "T3 drift-catches-bypass"; else bad "T3 drift-catches-bypass" "rc=$rc out=$out"; fi
bash "$REPO/scripts/setup-pull.sh" >/dev/null   # capture the change back into repo
python3 "$REPO/scripts/setup-sync.py" drift >/dev/null && ok "T3b pull-normalizes" || bad "T3b pull-normalizes" "still drifting"

# T4 broken repo config -> apply refuses BEFORE touching live
sha_before=$(sha256sum "$ROOT/.hermes/config.yaml" | cut -d' ' -f1)
printf 'not: [valid\n' >"$REPO/setup/home/.hermes/config.yaml"
bash "$REPO/scripts/setup-apply.sh" >/dev/null; rc=$?
sha_after=$(sha256sum "$ROOT/.hermes/config.yaml" | cut -d' ' -f1)
if [ $rc != 0 ] && [ "$sha_before" = "$sha_after" ]; then ok "T4 broken-apply-blocked rc=$rc"; else bad "T4 broken-apply-blocked" "rc=$rc live-changed"; fi
bash "$REPO/scripts/setup-pull.sh" >/dev/null   # restore repo from live

# T5 mid-apply failure -> earlier rows roll back to the backup.
# config.yaml (row 1) gets a new repo value; .agents/bin gets an unreadable
# repo file so the apply dies mid-way; assert config.yaml is restored.
mkdir -p "$ROOT/.agents/bin" && echo "x" >"$ROOT/.agents/bin/x"
bash "$REPO/scripts/setup-pull.sh" >/dev/null
echo "applied_marker: 1" >>"$REPO/setup/home/.hermes/config.yaml"
echo "bad" >"$REPO/setup/home/.agents/bin/bad.sh"; chmod 000 "$REPO/setup/home/.agents/bin/bad.sh"
bash "$REPO/scripts/setup-apply.sh" >/dev/null; rc=$?
chmod 644 "$REPO/setup/home/.agents/bin/bad.sh" 2>/dev/null
if [ $rc != 0 ] && ! grep -q applied_marker "$ROOT/.hermes/config.yaml"; then
  ok "T5 rollback-on-error rc=$rc"
else bad "T5 rollback-on-error" "rc=$rc"; grep applied_marker "$ROOT/.hermes/config.yaml" || true; fi

# T6 json-state merge: repo schedule wins, live runtime fields survive
python3 - "$REPO/setup/home/.hermes/cron/jobs.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d["jobs"][0]["schedule"]="30 8 * * *"
open(sys.argv[1],"w").write(json.dumps(d,indent=2)+"\n")
PY
bash "$REPO/scripts/setup-apply.sh" >/dev/null || { bad "T6 apply" "rc=$?"; }
python3 - "$ROOT/.hermes/cron/jobs.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1]))["jobs"][0]
assert j["schedule"]=="30 8 * * *", "repo config lost"
assert j["last_run_at"]=="2026-11-23T04:00:00Z", "runtime field wiped"
PY
[ $? = 0 ] && ok "T6 json-state-merge" || bad "T6 json-state-merge" "see assert"

echo "== $pass passed, $fail failed (scratch: $SCRATCH)"
[ $fail = 0 ]
