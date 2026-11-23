#!/usr/bin/env python3
"""Integration health probe for installed Hermes: Google aliases, Helium relay,
agent Chrome, Bitwarden CLI bridge.

Runs from a systemd timer. Detects state transitions and notifies the owner via
the personal Telegram session (Saved Messages) once per transition, instead of
letting failures sit silently until the next task hits them.

Secrets never leave the local boundary: refresh tokens are read from disk and
POSTed to Google's token endpoint only; the session token reaches `bw` as a
child env var; nothing secret is printed, logged or sent.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
GWS_TOKENS = HOME / ".config/gws/tokens"
BW_BIN = HOME / ".hermes/profiles/yqecea/tools/bw/bw"
BW_SESSION_FILE = HOME / ".config/bw-session"
RELAY_HEALTH = "http://127.0.0.1:9223/healthz"
AGENT_CHROME = "http://127.0.0.1:9333/json/version"
STATE_FILE = HOME / ".hermes/shared/integrations/health-state.json"
REPORT_FILE = HOME / ".hermes/shared/integrations/STATUS-integrations.md"
TG_SCRIPT = HOME / ".hermes/shared/communications/tg-personal.py"
TG_PY = HOME / ".hermes/profiles/yqecea/tools/tguser-env/bin/python"
ALERT_COOLDOWN = 6 * 3600  # repeat the same alert at most every 6h


def http_json(url, data=None, timeout=15):
    try:
        req = urllib.request.Request(url, data=data) if data else urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, {}


def check_google(client_id, client_secret):
    """Refresh-grant probe per alias. 'ok' / 'invalid_grant' / 'network' / detail."""
    results = {}
    for tf in sorted(GWS_TOKENS.glob("*.json")):
        if ".bak" in tf.name:
            continue
        try:
            token = json.loads(tf.read_text())
        except (OSError, ValueError):
            results[tf.stem] = "unreadable"
            continue
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token"}).encode()
        status, doc = http_json("https://oauth2.googleapis.com/token", data=body)
        if status == 200 and doc.get("access_token"):
            results[tf.stem] = "ok"
        elif status == 400:
            results[tf.stem] = doc.get("error", "invalid_grant")
        elif status is None:
            results[tf.stem] = "network"
        else:
            results[tf.stem] = f"http_{status}"
    return results


def check_bitwarden():
    """'unlocked' / 'locked' / 'unauthenticated' / 'missing' / 'error'."""
    if not BW_BIN.exists():
        return "missing_binary"
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env["NO_COLOR"] = "1"
    try:
        token = BW_SESSION_FILE.read_text().strip()
        if token:
            env["BW_SESSION"] = token
    except OSError:
        return "missing_session_file"
    try:
        proc = subprocess.run(
            [str(BW_BIN), "status", "--nointeraction"],
            env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return json.loads(proc.stdout).get("status", "unknown")
        return "error"
    except (OSError, subprocess.SubprocessError, ValueError):
        return "error"


def check_helium():
    state, doc = http_json(RELAY_HEALTH, timeout=5)
    if state != 200:
        return {"relay": "down"}
    out = {"relay": doc.get("upstream_state", "?")}
    proc = subprocess.run(["pgrep", "-f", "/opt/helium/helium"],
                          capture_output=True)
    out["helium_alive"] = proc.returncode == 0
    return out


def check_agent_chrome():
    state, _ = http_json(AGENT_CHROME, timeout=5)
    return "ok" if state == 200 else "down"


def notify(text):
    """Send one owner-facing note to Saved Messages via the personal session."""
    if not TG_SCRIPT.exists() or not TG_PY.exists():
        return False
    import hashlib
    op_id = "integration-health-" + hashlib.sha256(text.encode()).hexdigest()[:12]
    try:
        proc = subprocess.run(
            [str(TG_PY), str(TG_SCRIPT), "send", "--chat", "me",
             "--operation-id", op_id],
            input=text, text=True, capture_output=True, timeout=45)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def flat_states(now):
    """component -> state string, for transition detection."""
    gws_cfg = {}
    try:
        gws_cfg = json.loads((HOME / ".config/gws/client_secret.json")
                             .read_text())["installed"]
    except (OSError, ValueError, KeyError):
        pass
    out = {}
    if gws_cfg:
        for alias, st in check_google(gws_cfg["client_id"],
                                      gws_cfg["client_secret"]).items():
            out[f"google:{alias}"] = st
    else:
        out["google"] = "no_client_secret"
    out["bitwarden"] = check_bitwarden()
    he = check_helium()
    out["helium"] = ("ok" if he.get("relay") == "connected"
                     else f"relay_{he.get('relay')}")
    if he.get("helium_alive") is False:
        out["helium"] = "helium_not_running"
    out["agent_chrome"] = check_agent_chrome()
    return out


def main():
    now = time.time()
    old = {}
    try:
        old = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        pass
    prev = old.get("states", {})
    alerted = old.get("alerted_at", {})

    cur = flat_states(now)
    bad = {k: v for k, v in cur.items()
           if v not in ("ok", "unlocked") and not v.startswith("http_2")}
    recovered = [k for k in prev
                 if prev[k] not in ("ok", "unlocked")
                 and cur.get(k) in ("ok", "unlocked")]

    msgs = []
    for k, v in bad.items():
        if prev.get(k) == v:
            last = alerted.get(k, 0)
            if now - last < ALERT_COOLDOWN:
                continue  # same failure, still cooling down
        msgs.append(f"FAIL {k}: {v}")
        alerted[k] = now
    for k in recovered:
        msgs.append(f"RECOVERED {k}: {cur[k]}")
        alerted.pop(k, None)

    if msgs:
        text = ("Hermes integration health " + time.strftime("%H:%M %d.%m")
                + "\n" + "\n".join(msgs)
                + "\nsee ~/.hermes/shared/integrations/STATUS-integrations.md")
        notify(text)

    STATE_FILE.write_text(json.dumps(
        {"states": cur, "alerted_at": alerted, "checked_at": now},
        indent=1, ensure_ascii=False))
    REPORT_FILE.write_text(
        "# Integration health\n\n"
        f"checked: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
        + "".join(f"- `{k}`: **{v}**\n" for k, v in sorted(cur.items()))
        + ("\nUnhealthy: " + ", ".join(sorted(bad)) + "\n" if bad
           else "\nAll integrations healthy.\n"))
    print(json.dumps({"bad": bad, "recovered": recovered,
                      "alerted": bool(msgs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
