#!/bin/bash
# Existing account and helper; native GWS owns API errors, URL encoding and pagination.
set -euo pipefail
export TZ=Asia/Almaty
script_dir=$(cd "$(dirname "$0")" && pwd)
source "$script_dir/gws-helpers.sh"
AT=$(gws_token /home/yqecea/.config/gws/tokens/yqecea.gmail.json /home/yqecea/.config/gws/client_secret.json)
if [ -z "$AT" ]; then
  echo 'Проверка календаря: нужен повторный вход в Google.' >&2
  exit 1
fi
params=$(python3 - <<'PY'
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
import json
zone=ZoneInfo('Asia/Almaty');today=datetime.now(zone).date()
print(json.dumps({'calendarId':'primary','timeMin':datetime.combine(today,time.min,zone).isoformat(),
 'timeMax':datetime.combine(today+timedelta(days=7),time.max,zone).isoformat(),
 'timeZone':'Asia/Almaty','singleEvents':True,'orderBy':'startTime','maxResults':2500,
 'fields':'items(start,summary),nextPageToken'}))
PY
)
calendar_result=$(mktemp "${TMPDIR:-/tmp}/hermes-calendar-XXXXXX")
trap 'rm -f -- "$calendar_result"' EXIT
gws_cli=$(command -v gws || true)
# The gateway service PATH excludes the owner's NVM bin; reuse the installed native CLI.
if [ -z "$gws_cli" ]; then gws_cli=/home/yqecea/.nvm/versions/node/v24.12.0/bin/gws; fi
if [ ! -x "$gws_cli" ]; then echo 'Проверка календаря: GWS CLI недоступен.' >&2; exit 127; fi
GOOGLE_WORKSPACE_CLI_TOKEN="$AT" "$gws_cli" calendar events list --params "$params" --page-all --page-limit 100 --page-delay 0 > "$calendar_result"
python3 "$script_dir/cal-audit.py" < "$calendar_result"
