#!/usr/bin/env bash
# export-public.sh — publish a secrets-free snapshot of this repo to the
# public mirror (oysana/hermes-os).
#
# The private repo's history is not publishable (old commits contain the
# owner's chat id and personal notes), so the mirror receives squashed
# snapshots: its own history only ever contains already-clean trees.
#
# Usage: scripts/export-public.sh [--push]   # default: build + scan only
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
PUBLIC_REMOTE=git@github.com:oysana/hermes-os.git
EXCLUDES=(PLAN.md CURRENT_WORK.md .devin)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage="$work/tree" && mkdir -p "$stage"

git -C "$REPO_ROOT" archive HEAD | tar -x -C "$stage"
for f in "${EXCLUDES[@]}"; do rm -rf "$stage/$f"; done

# Regenerate the map without personal cron names.
python3 "$REPO_ROOT/scripts/setup-map.py" --public > "$stage/SETUP-MAP.md"

# Secrets gate: abort the export on any finding, secrets or not.
gitleaks dir "$stage" --report-format json --report-path "$work/leaks.json"
if [ -s "$work/leaks.json" ] && [ "$(python3 -c "import json;print(len(json.load(open('$work/leaks.json'))))")" != 0 ]; then
    echo "gitleaks findings in snapshot — aborting" >&2
    cat "$work/leaks.json" >&2
    exit 1
fi

# Personal-data gate: owner chat id, private emails, phone numbers.
if grep -rE 'OWNER_CHAT_ID|\+7[0-9]{10}' "$stage" | grep -v export-public; then
    echo "personal data found in snapshot — aborting" >&2
    exit 1
fi

echo "snapshot clean: $(find "$stage" -type f | wc -l) files"
[ "${1:-}" = "--push" ] || { echo "dry run, pass --push to publish"; exit 0; }

if [ -d "$work/mirror" ] || git clone -q "$PUBLIC_REMOTE" "$work/mirror" 2>/dev/null; then
    cd "$work/mirror"
else
    mkdir -p "$work/mirror" && cd "$work/mirror"
    git init -qb master && git remote add origin "$PUBLIC_REMOTE"
fi
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$stage"/. .
git add -A
if git diff --cached --quiet; then echo "mirror already up to date"; exit 0; fi
git -c user.name="hermes-pro export" -c user.email="bot@oysana" commit -qm \
    "snapshot of oysana/hermes-pro $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
git push -q origin master
echo "published to $PUBLIC_REMOTE"
