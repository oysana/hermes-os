#!/bin/sh
# oyvoice-watch — no_agent script for cron eed9ea1c0e41. 0 tokens.
# Daily digest of oysana/voicebridge, verbatim to owner. Marks change vs last snapshot.
set -u
SNAP="$HOME/.hermes/state/oyvoice-watch.last"
mkdir -p "$HOME/.hermes/state"
TMP="$(mktemp)"
{
  echo "OyVoice:"
  echo "— open PRs:"
  gh pr list -R oysana/voicebridge --state open --json number,title,updatedAt --jq '.[]|"  \(.number) \(.title)"' 2>/dev/null || echo "  (gh недоступен)"
  echo "— последние прогоны main:"
  gh run list -R oysana/voicebridge --branch main --limit 3 --json status,conclusion,createdAt --jq '.[]|"  \(.status)/\(.conclusion) \(.createdAt[0:10])"' 2>/dev/null || true
  echo "— head main: $(gh api repos/oysana/voicebridge/commits/main --jq '.sha[0:8]' 2>/dev/null || echo '?')"
} > "$TMP"
if [ -f "$SNAP" ] && cmp -s "$TMP" "$SNAP"; then
  echo "OyVoice: без изменений за сутки."
else
  cat "$TMP"
fi
mv "$TMP" "$SNAP"
