#!/usr/bin/env python3
"""Diagnose the configured browser CDP endpoint for a Hermes profile.

Prescribed by ~/.hermes/shared/browser-routing.md before any browser/password
failure diagnosis, run from a hermes-pro checkout:

    python3 -B ~/.hermes/shared/proactive/run-selected.py scripts/browser-access.py --profile yqecea

run-selected.py executes this under the Hermes-selected interpreter, so the
``hermes_constants`` / ``hermes_cli`` imports stay lazy inside functions: the
module must remain importable under a plain interpreter for the repo unittest.

``--ensure`` opens the configured owner Chrome through its installed .desktop
entry (gtk-launch, then gio/xdg-open) ONLY when the configured loopback
endpoint is not listening. Chrome singleton ownership hands the launch to the
already-running owner instance; this helper never kills or restarts a browser.

stdout: one JSON record. stderr: one human-readable line.
Exit: 0 endpoint listening (or came up after ensure); 1 checked and down;
2 usage or configuration error.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PROBE_TIMEOUT = 3.0
DEFAULT_CHROME_DIR = str(Path.home() / ".config/google-chrome")
ENSURE_WAIT_SECONDS = 25.0
LAUNCH_ATTEMPTS = ("gtk-launch-id", "gio-launch", "xdg-open")


def default_root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def profile_home(profile: str, root: Path) -> Path:
    return root if profile in ("", "default") else root / "profiles" / profile


def read_cdp_url(home: Path) -> str:
    """Configured browser.cdp_url through the hermes_cli effective reader."""
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = set_hermes_home_override(home)
    try:
        cfg = load_user_config_effective()
    finally:
        reset_hermes_home_override(token)
    browser = cfg.get("browser")
    if not isinstance(browser, dict):
        return ""
    value = browser.get("cdp_url")
    return str(value).strip() if value else ""


def http_base(cdp_url: str) -> str:
    base = cdp_url.strip().rstrip("/")
    if base.startswith("ws://"):
        base = "http://" + base[5:]
    elif base.startswith("wss://"):
        base = "https://" + base[6:]
    return base


def endpoint_host_port(cdp_url: str) -> tuple[str, int | None]:
    parsed = urllib.parse.urlsplit(http_base(cdp_url))
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None and parsed.scheme in ("http", "https", "ws", "wss"):
        port = 443 if parsed.scheme in ("https", "wss") else 80
    return host, port


def is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def fetch_relay(cdp_url: str, timeout: float = PROBE_TIMEOUT) -> dict | None:
    """State of the owner-Chrome CDP relay, or None when the endpoint is a plain browser.

    The relay (hermes-chrome-cdp-relay.service) listens even while Chrome is closed, so
    its port being open says nothing; only an approved upstream connection counts."""
    try:
        with urllib.request.urlopen(http_base(cdp_url) + "/healthz", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(data, dict) or "upstream_state" not in data:
        return None
    return {"relay": True, "upstream_state": data.get("upstream_state"),
            "profile_dir": data.get("profile_dir")}


def fetch_version(cdp_url: str, timeout: float = PROBE_TIMEOUT) -> dict:
    relay = fetch_relay(cdp_url, timeout)
    if relay is not None and relay["upstream_state"] != "connected":
        return {"listening": False, "error": f"relay {relay['upstream_state']}", **relay}
    try:
        with urllib.request.urlopen(http_base(cdp_url) + "/json/version", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return {"listening": True, "browser": data.get("Browser"),
                "protocol": data.get("Protocol-Version"), **(relay or {})}
    except Exception as exc:
        return {"listening": False, "error": type(exc).__name__, "detail": str(exc)[:200]}


def port_listener(port: int) -> list[dict]:
    """PIDs listening on the TCP port: ``ss`` first, /proc fallback."""
    listeners = _port_listener_ss(port)
    return listeners if listeners is not None else _port_listener_proc(port)


def _port_listener_ss(port: int) -> list[dict] | None:
    try:
        out = subprocess.run(["ss", "-tlnpH"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[3].rsplit(":", 1)[-1] == str(port):
            continue
        for name, pid in re.findall(r'"(.*?)",pid=(\d+)', line):
            found.append({"pid": int(pid), "process": name})
        if not found:
            found.append({"pid": None, "process": None})
    return found


def _port_listener_proc(port: int) -> list[dict]:
    inodes = set()
    want = f"{port & 0xFFFF:04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 9 and fields[3] == "0A" and fields[1].rsplit(":", 1)[-1] == want:
                inodes.add(fields[9])
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                if os.readlink(fd) in (f"socket:[{i}]" for i in inodes):
                    found.append({"pid": int(proc.name),
                                  "process": (proc / "comm").read_text().strip()})
                    break
        except OSError:
            continue
    return found


def applications_dirs() -> list[Path]:
    dirs = [Path.home() / ".local/share/applications"]
    for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if base:
            dirs.append(Path(base) / "applications")
    return [d for d in dirs if d.is_dir()]


def find_desktop_entry(port: int, dirs: list[Path] | None = None, profile_dir: str | None = None,
                       default_chrome_dir: str = DEFAULT_CHROME_DIR) -> dict | None:
    """Installed .desktop entry whose Exec advertises this CDP port, else (behind the
    owner-Chrome relay) the entry that opens the relay's profile_dir: an explicit
    --user-data-dir, or google-chrome.desktop without one for Chrome's default dir."""
    needle = f"--remote-debugging-port={port}"
    needle_sp = f"--remote-debugging-port {port}"
    by_dir = None
    for directory in (dirs if dirs is not None else applications_dirs()):
        for path in sorted(directory.glob("*.desktop")):
            try:
                lines = path.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.startswith("Exec="):
                    continue
                udd = _exec_value(line, "--user-data-dir")
                entry = {"id": path.stem, "path": str(path), "exec": line[5:].strip(),
                         "user_data_dir": udd}
                if needle in line or needle_sp in line:
                    return entry
                if by_dir is None and profile_dir and (
                        udd == profile_dir or (udd is None and profile_dir == default_chrome_dir
                                               and path.stem == "google-chrome")):
                    by_dir = {**entry, "user_data_dir": profile_dir}
    return by_dir


def _exec_value(exec_line: str, key: str) -> str | None:
    match = re.search(re.escape(key) + r'=(?:"([^"]*)"|(\S+))', exec_line)
    return (match.group(1) or match.group(2)) if match else None


def _proc_cmdlines():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            yield int(proc.name), (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace")
        except OSError:
            continue


def owner_browser_pids(user_data_dir: str | None, procs=None,
                       default_chrome_dir: str = DEFAULT_CHROME_DIR) -> list[int]:
    """PIDs of a chrome/chromium process started with this --user-data-dir; for Chrome's
    default dir, main chrome processes started without any --user-data-dir."""
    if not user_data_dir:
        return []
    pids = []
    for pid, cmdline in (procs if procs is not None else _proc_cmdlines()):
        if "chrome" not in cmdline and "chromium" not in cmdline:
            continue
        if user_data_dir in cmdline:
            pids.append(pid)
        elif (user_data_dir == default_chrome_dir and "--user-data-dir" not in cmdline
              and "--type=" not in cmdline and "/chrome/chrome" in cmdline):
            pids.append(pid)
    return sorted(pids)


def launch_entry(entry: dict, runner=subprocess.run) -> tuple[bool, str | None, list[str]]:
    tried = []
    commands = [["gtk-launch", entry["id"]],
                ["gio", "launch", entry["path"]],
                ["xdg-open", entry["path"]]]
    for cmd in commands:
        try:
            rc = runner(cmd, capture_output=True, timeout=15).returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            tried.append(f"{cmd[0]}: {type(exc).__name__}")
            continue
        tried.append(f"{cmd[0]}: rc={rc}")
        if rc == 0:
            return True, cmd[0], tried
    return False, None, tried


def may_launch(listening: bool, ensure: bool, loopback: bool, entry_found: bool) -> bool:
    """Spec twin: verification/browser-access/laws/browser_access.bend.

    Launch the owner Chrome desktop entry iff the loopback endpoint is down,
    --ensure was requested and a matching entry exists. Never True while
    anything listens — an active browser is never restarted.
    """
    if listening:
        return False
    if not ensure:
        return False
    if not loopback:
        return False
    return entry_found


def wait_for_endpoint(cdp_url: str, wait_s: float, deps: dict) -> dict:
    start = deps["monotonic"]()
    result = deps["fetch"](cdp_url)
    while not result.get("listening") and deps["monotonic"]() - start < wait_s:
        deps["sleep"](1.0)
        result = deps["fetch"](cdp_url)
    return result


def run(profile: str, root: Path, ensure: bool, wait_s: float, deps: dict) -> tuple[dict, int]:
    home = profile_home(profile, root)
    record = {"profile": profile, "hermes_home": str(home)}
    if not (home / "config.yaml").is_file():
        record["error"] = "profile home has no config.yaml"
        return record, 2
    try:
        cdp_url = deps["read_cdp_url"](home)
    except Exception as exc:
        record["error"] = f"config read failed: {type(exc).__name__}"
        return record, 2
    record["cdp_url"] = cdp_url or None
    if not cdp_url:
        record["error"] = "browser.cdp_url not configured"
        return record, 2
    host, port = endpoint_host_port(cdp_url)
    record["endpoint"] = {"host": host, "port": port, "loopback": is_loopback(host)}
    record["probe"] = deps["fetch"](cdp_url)
    if port is not None:
        record["port_listener"] = deps["listener"](port)
    relay_dir = record["probe"].get("profile_dir") if record["probe"].get("relay") else None
    if port is None:
        entry = None
    else:
        entry = deps["entry"](port, profile_dir=relay_dir) if relay_dir else deps["entry"](port)
    record["desktop_entry"] = entry
    record["owner_browser_pids"] = deps["pids"](entry["user_data_dir"]) if entry else []

    record["ensure"] = {"requested": ensure, "launched": False, "via": None}
    if may_launch(record["probe"]["listening"], ensure,
                  record["endpoint"]["loopback"], entry is not None):
        ok, via, tried = deps["launch"](entry)
        record["ensure"].update({"launched": ok, "via": via, "attempts": tried})
        if ok:
            record["probe"] = wait_for_endpoint(cdp_url, wait_s, deps)
            if port is not None:
                record["port_listener"] = deps["listener"](port)

    if record["probe"].get("listening"):
        record["ok"] = True
        return record, 0
    record["ok"] = False
    record["remedy"] = _remedy(record, ensure, entry, port)
    return record, 1


def _remedy(record: dict, ensure: bool, entry: dict | None, port: int | None) -> str:
    probe = record["probe"]
    if probe.get("relay") and record["owner_browser_pids"] and probe.get("upstream_state") == "connecting":
        return ("owner Chrome is open and waiting for its one-time 'Allow remote debugging' "
                "click for this browser launch; only the owner can click it")
    if not record["endpoint"]["loopback"]:
        return ("configured endpoint is not loopback; --ensure only opens the "
                "owner Chrome desktop entry for a loopback CDP port")
    if record["ensure"]["launched"]:
        if record["owner_browser_pids"]:
            return ("owner Chrome is already running without the debug port; "
                    "only the owner may restart it — this helper never restarts "
                    "an active browser")
        return "desktop entry launched but the CDP endpoint did not come up"
    if entry is None:
        return (f"no installed .desktop entry advertises "
                f"--remote-debugging-port={port}; create one or open the "
                "configured browser by hand")
    return "rerun with --ensure to open the owner Chrome desktop entry"


def human_line(record: dict) -> str:
    ep = record.get("endpoint") or {}
    probe = record.get("probe") or {}
    listener = record.get("port_listener")
    listener_s = ",".join(f"{l.get('process') or '?'}:{l.get('pid') or '?'}"
                          for l in listener) if listener else "none"
    entry = record.get("desktop_entry")
    ensure = record.get("ensure") or {}
    ensure_s = (f"launched via {ensure['via']}" if ensure.get("launched")
                else ("refused" if ensure.get("requested") else "not requested"))
    status = "UP" if probe.get("listening") else f"DOWN ({probe.get('error', 'not probed')})"
    verdict = "OK" if record.get("ok") else "FAIL"
    remedy = f" | remedy: {record['remedy']}" if record.get("remedy") else ""
    return (f"browser-access[{record.get('profile')}]: cdp={record.get('cdp_url')} "
            f"{status} listener={listener_s} entry={entry['id'] if entry else 'none'} "
            f"ensure={ensure_s} => {verdict}{remedy}")


def build_deps() -> dict:
    return {"read_cdp_url": read_cdp_url, "fetch": fetch_version,
            "listener": port_listener, "entry": find_desktop_entry,
            "pids": owner_browser_pids, "launch": launch_entry,
            "sleep": time.sleep, "monotonic": time.monotonic}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose the configured browser CDP endpoint; --ensure opens "
                    "the owner Chrome desktop entry only when it is not listening.")
    parser.add_argument("--profile", default="default",
                        help="Hermes profile: default or a name under ~/.hermes/profiles/")
    parser.add_argument("--ensure", action="store_true",
                        help="open the owner Chrome .desktop entry when the endpoint is down")
    parser.add_argument("--wait", type=float, default=ENSURE_WAIT_SECONDS,
                        help="seconds to wait for the endpoint after a launch")
    parser.add_argument("--root", default=None,
                        help="Hermes root override (default: hermes_constants)")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else default_root()
    record, rc = run(args.profile, root, args.ensure, args.wait, build_deps())
    print(human_line(record), file=sys.stderr)
    print(json.dumps(record, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
