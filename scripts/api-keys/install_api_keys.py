#!/usr/bin/env python3
"""Install provider API keys into every Hermes profile through the native .env writer.

Run inside the selected Hermes interpreter — ``bash add-api-keys.sh`` resolves this
file through ``~/.hermes/shared/proactive/run-selected.py``. Key values are NEVER
command-line arguments: pass private files (``--openai-key-file`` /
``--gemini-key-file``) or run ``--interactive`` for hidden getpass prompts.

Per profile home (default root + each named profile) the write goes through the
canonical credential lifecycle:

- ``hermes_cli.config.require_env_writable`` hard-check first (managed/locked keys
  refuse loudly instead of pretending success),
- ``hermes_cli.credential_lifecycle.save_provider_env_credential`` — the writer every
  native surface uses: one atomic .env rewrite that UPDATES a non-empty existing
  value in place, preserves unrelated entries and the file mode (0600 on a new
  file), scrubs config.yaml mirrors of the old value, lifts env-source suppression
  and materializes the env-seeded credential-pool entry into auth.json.

Pre-pass: when ``.env`` already defines the key on more than one line (e.g.
``export KEY=`` plus ``KEY=`` — the old grep/sed script could create exactly that),
``remove_env_value`` collapses all of them before the save so no stale duplicate
line shadows the new value.

Exit codes: 0 = every provided key written in every home (and verified when asked);
1 = at least one write/verify failed — partial success is reported per row, never
masked as "all done"; 2 = usage or input error, nothing written.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import sys
from pathlib import Path

DEFAULT_PROFILES = ("sqecea", "yqecea")
KEY_SPECS = (("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY"))
_ENV_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=")
_MAX_KEY_FILE_BYTES = 8192


class UsageError(Exception):
    """Bad operator input; exit 2, nothing written."""


def _redact(text: object, secrets) -> str:
    """Scrub every collected secret out of a message before it can be printed."""
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out


def _validate_key_value(raw: str, label: str) -> str:
    """Return the bare key or raise UsageError. Messages carry metadata, never the value."""
    value = raw.strip()
    if not value:
        raise UsageError(f"{label}: key value is empty")
    if "\n" in value or "\r" in value:
        raise UsageError(f"{label}: file holds more than one line — pass a file with the bare key only")
    if not value.isascii():
        bad = sum(1 for c in value if ord(c) > 127)
        raise UsageError(
            f"{label}: value has {bad} non-ASCII character(s) — re-copy the key as plain text")
    if any(c.isspace() for c in value):
        raise UsageError(f"{label}: value contains whitespace — pass the bare key only")
    if _ENV_ASSIGNMENT_RE.match(value):
        raise UsageError(f"{label}: file looks like a dotenv assignment — pass the bare key only")
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        raise UsageError(f"{label}: value is quoted — remove the surrounding quotes")
    if len(value) < 8:
        raise UsageError(f"{label}: value is suspiciously short for an API key")
    return value


def _read_key_file(path_str: str, label: str) -> str:
    path = Path(path_str).expanduser()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"{label}: cannot read {path}: {exc.strerror or exc}") from exc
    if len(raw) > _MAX_KEY_FILE_BYTES:
        raise UsageError(f"{label}: {path} is too large for a bare API key")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError(f"{label}: {path} is not UTF-8 text")
    return _validate_key_value(text, label)


def _collect_keys(args) -> dict[str, str]:
    """Resolve key values from private files / hidden prompts. Returns {env_var: value}."""
    provided: dict[str, str] = {}
    for short, env_var in KEY_SPECS:
        file_arg = getattr(args, f"{short}_key_file")
        if file_arg:
            provided[env_var] = _read_key_file(file_arg, env_var)
    if args.interactive:
        if not sys.stdin.isatty():
            raise UsageError("--interactive needs a terminal; use --openai-key-file/--gemini-key-file")
        for short, env_var in KEY_SPECS:
            if env_var in provided:
                continue
            typed = getpass.getpass(f"{env_var} (input hidden, empty = skip): ").strip()
            if typed:
                provided[env_var] = _validate_key_value(typed, env_var)
    if not provided:
        raise UsageError("no keys given — pass --openai-key-file/--gemini-key-file or --interactive")
    return provided


def _target_homes(args) -> list[tuple[str, Path]]:
    root = Path(args.hermes_root).expanduser()
    profiles = args.profile if args.profile else list(DEFAULT_PROFILES)
    from hermes_constants import PROFILE_ID_RE

    homes = [("default", root)]
    for name in profiles:
        if not PROFILE_ID_RE.match(name):
            raise UsageError(f"invalid profile name {name!r}")
        homes.append((name, root / "profiles" / name))
    return homes


def _count_assignments(env_path: Path, key: str) -> int:
    """How many .env lines assign ``key`` — through the same native predicate the writer uses."""
    from hermes_cli.config import _env_line_defines_key

    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if _env_line_defines_key(line, key))


def _write_one_key(env_var: str, value: str, secrets) -> dict:
    """One credential write under the active home override; never raises."""
    from hermes_cli.config import (
        env_write_refusal, get_env_path, load_env, remove_env_value, require_env_writable)
    from hermes_cli.credential_lifecycle import (
        _scrub_config_yaml_mirrors, save_provider_env_credential)

    try:
        require_env_writable(env_var, "set")
    except ValueError as exc:
        return {"status": "refused", "error": _redact(exc, secrets)}

    env_path = get_env_path()
    try:
        old_value = load_env().get(env_var)
        duplicates = _count_assignments(env_path, env_var)
        deduplicated = False
        if duplicates > 1:
            remove_env_value(env_var)
            deduplicated = True
        result = save_provider_env_credential(env_var, value)
        if deduplicated and old_value and old_value != value:
            # The pre-save removal hid old_value from the lifecycle's mirror scrub;
            # scrub stale config.yaml copies of it ourselves (value-matched, safe).
            _scrub_config_yaml_mirrors(old_value, value)
        action = "update" if old_value else "add"
        return {"status": "ok", "action": action, "deduplicated": deduplicated,
                "config_updates": result.get("config_updates") or []}
    except Exception as exc:  # noqa: BLE001 — reported per row, secret scrubbed
        detail = _redact(exc, secrets)
        if deduplicated:
            detail = f"existing duplicate lines were removed, then save failed: {detail}"
        return {"status": "error", "error": detail}


def _apply_to_home(home: Path, provided: dict[str, str], secrets, dry_run: bool) -> dict:
    """Write every provided key into one profile home. Returns a per-home result row."""
    row: dict = {"ok": True, "keys": {}}
    if not home.is_dir():
        row["ok"] = False
        row["error"] = "profile home missing"
        for env_var in provided:
            row["keys"][env_var] = {"status": "error", "error": "profile home missing"}
        return row

    from hermes_cli.config import env_write_refusal, get_env_path
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    old_home_env = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        env_path = get_env_path()
        for env_var, value in provided.items():
            if dry_run:
                refusal = env_write_refusal(env_var, "set")
                if refusal:
                    row["keys"][env_var] = {"status": "refused", "error": refusal}
                    continue
                from agent.secret_scope import load_env_file

                parsed = load_env_file(env_path)
                duplicates = _count_assignments(env_path, env_var)
                row["keys"][env_var] = {
                    "status": "dry-run",
                    "action": "update" if parsed.get(env_var) else "add",
                    "deduplicated": duplicates > 1}
                continue
            row["keys"][env_var] = _write_one_key(env_var, value, secrets)
        if env_path.exists():
            row["env_mode"] = oct(stat.S_IMODE(env_path.stat().st_mode))
    finally:
        reset_hermes_home_override(token)
        if old_home_env is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home_env
    row["ok"] = all(k.get("status") in ("ok", "dry-run") for k in row["keys"].values())
    return row


def _verify_home(home: Path, provided: dict[str, str], secrets) -> dict:
    """Metadata-only check of what the routed gateway will resolve: .env parse + scope build."""
    from agent.secret_scope import build_profile_secret_scope, load_env_file

    env_path = home / ".env"
    row: dict = {"keys": {}}
    try:
        parsed = load_env_file(env_path)
    except Exception as exc:  # noqa: BLE001
        parsed = {}
        row["dotenv_error"] = _redact(exc, secrets)
    try:
        scope = build_profile_secret_scope(home)
        scope_error = None
    except Exception as exc:  # noqa: BLE001
        scope = {}
        scope_error = _redact(exc, secrets)
    if scope_error:
        row["scope_error"] = scope_error
    for env_var, value in provided.items():
        row["keys"][env_var] = {
            "dotenv_matches_written": parsed.get(env_var) == value,
            "scope_matches_written": scope.get(env_var) == value}
    if env_path.exists():
        row["env_mode"] = oct(stat.S_IMODE(env_path.stat().st_mode))
    row["ok"] = all(v["dotenv_matches_written"] and v["scope_matches_written"]
                    for v in row["keys"].values())
    return row


def _live_probe(env_var: str, value: str, secrets) -> dict:
    """``GET <base>/models`` through the same probe ``hermes doctor`` runs — metadata only."""
    from hermes_cli.credential_lifecycle import _providers_for_env_var

    providers = _providers_for_env_var(env_var)
    if not providers:
        return {"status": "skipped", "reason": "no provider registered for this env var"}
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_cli.doctor_connectivity import _probe_apikey_provider

    rows = []
    for pid in providers:
        cfg = PROVIDER_REGISTRY.get(pid)
        if cfg is None or not getattr(cfg, "api_key_env_vars", None):
            continue
        touched = {}
        try:
            for other in cfg.api_key_env_vars:
                touched[other] = os.environ.get(other)
                os.environ[other] = value if other == env_var else ""
            probe = _probe_apikey_provider(
                cfg.name, cfg.api_key_env_vars,
                cfg.inference_base_url.rstrip("/") + "/models" if cfg.inference_base_url else None,
                getattr(cfg, "base_url_env_var", ""), True)
            glyph = probe.lines[0][0] if probe.lines else ""
            status = ("ok" if "✓" in glyph else "fail" if "✗" in glyph
                      else "warn" if "⚠" in glyph else "skipped")
            rows.append({"provider": cfg.id, "status": status})
        except Exception as exc:  # noqa: BLE001
            rows.append({"provider": pid, "status": "error",
                         "error": _redact(exc, secrets)})
        finally:
            for name, old in touched.items():
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
    if not rows:
        return {"status": "skipped", "reason": "provider registry rows unresolved"}
    # A transport/import error is inconclusive; only a definitive provider refusal fails.
    if any(r["status"] == "fail" for r in rows):
        return {"status": "fail", "providers": rows}
    if any(r["status"] == "ok" for r in rows):
        return {"status": "ok", "providers": rows}
    return {"status": "skipped", "providers": rows}


def _print_text(result: dict) -> None:
    keys = result["keys"]
    for home in result["homes"]:
        for env_var in keys:
            wr = home["keys"].get(env_var, {})
            status = wr.get("status", "?")
            detail = wr.get("action") or wr.get("error") or ""
            dedup = " (duplicates collapsed)" if wr.get("deduplicated") else ""
            print(f"  {home['label']:<10} {env_var:<16} {status:<8} {detail}{dedup}")
        if home.get("env_mode"):
            print(f"  {home['label']:<10} .env mode          {home['env_mode']}")
        if home.get("error") and not home["keys"]:
            print(f"  {home['label']:<10} error: {home['error']}")
    verify = result.get("verify")
    if verify:
        print("verify (metadata only — no values, no network):")
        for home in verify["homes"]:
            for env_var, chk in home["keys"].items():
                flags = []
                flags.append("dotenv=ok" if chk["dotenv_matches_written"] else "dotenv=MISS")
                flags.append("scope=ok" if chk["scope_matches_written"] else "scope=MISS")
                print(f"  {home['label']:<10} {env_var:<16} {' '.join(flags)}")
            for err_key in ("dotenv_error", "scope_error"):
                if home.get(err_key):
                    print(f"  {home['label']:<10} {err_key}: {home[err_key]}")
    live = result.get("verify_live")
    if live:
        print("verify-live (provider metadata probe, real network):")
        for env_var, probe in live.items():
            providers = probe.get("providers") or []
            summary = probe["status"] if not providers else ", ".join(
                f"{r['provider']}:{r['status']}" for r in providers)
            print(f"  {env_var:<16} {summary}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Install OpenAI/Gemini API keys into every Hermes profile's .env "
                    "through the native credential writer. Key values come only from "
                    "private files or hidden prompts — never from argv.")
    p.add_argument("--openai-key-file", metavar="PATH", help="file holding only the OpenAI key")
    p.add_argument("--gemini-key-file", metavar="PATH", help="file holding only the Gemini key")
    p.add_argument("--interactive", action="store_true",
                   help="prompt for any missing key with hidden getpass input")
    p.add_argument("--hermes-root", metavar="PATH", default=str(Path.home() / ".hermes"),
                   help="Hermes root holding the default home and profiles/ (default: ~/.hermes)")
    p.add_argument("--profile", action="append", metavar="NAME",
                   help="named profile under <root>/profiles/ (repeatable; default: sqecea, yqecea)")
    p.add_argument("--dry-run", action="store_true", help="report planned writes, change nothing")
    p.add_argument("--verify", action="store_true",
                   help="after writing, metadata-check that the gateway's secret scope resolves each key")
    p.add_argument("--verify-live", action="store_true",
                   help="also run the provider GET /models metadata probe (real network, sends the key)")
    p.add_argument("--json", action="store_true", help="print the result object as JSON (no secrets)")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        provided = _collect_keys(args)
        homes = _target_homes(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    secrets = list(provided.values())
    result: dict = {"keys": list(provided), "homes": [], "ok": True}
    for label, home in homes:
        row = _apply_to_home(home, provided, secrets, args.dry_run)
        row["label"] = label
        row["path"] = str(home)
        result["homes"].append(row)

    result["ok"] = all(h["ok"] for h in result["homes"])

    if args.verify or args.verify_live:
        result["verify"] = {"homes": []}
        for label, home in homes:
            vrow = _verify_home(home, provided, secrets) if home.is_dir() else {
                "ok": False, "keys": {}, "error": "profile home missing"}
            vrow["label"] = label
            result["verify"]["homes"].append(vrow)
        result["verify"]["ok"] = all(v["ok"] for v in result["verify"]["homes"])
        result["ok"] = result["ok"] and result["verify"]["ok"]

    if args.verify_live:
        result["verify_live"] = {}
        for env_var, value in provided.items():
            os.environ[env_var] = value
            result["verify_live"][env_var] = _live_probe(env_var, value, secrets)
        result["ok"] = result["ok"] and all(
            p["status"] != "fail" for p in result["verify_live"].values())

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif not args.quiet:
        print("api-key install report (values never shown):")
        _print_text(result)
        total = sum(len(h["keys"]) for h in result["homes"])
        failed = sum(1 for h in result["homes"] for k in h["keys"].values()
                     if k.get("status") not in ("ok", "dry-run"))
        print(f"summary: {total - failed}/{total} writes ok"
              + (f"; {failed} FAILED" if failed else ""))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
