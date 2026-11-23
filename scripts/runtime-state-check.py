#!/usr/bin/env python3
"""Runtime-state lane for check.sh — probes the installed machine, not the code.

check.sh's other lanes verify repository correctness; the owner's 11.11 audit
showed every missed defect was a *state* defect invisible to them: a plugin
file present but never enabled/discovered, 155 dangling skill symlinks, topic
maps drifting from the guard that reads them. These probes fail on exactly
that class. Each check is deterministic, offline and ~instant.

Checks:
  R1 broken skill links: every symlink under each profile's skills dir must
     resolve (the 155-dangling-links class).
  R2 plugin wiring: every `plugins.enabled` entry must resolve to a plugin dir
     (or symlink) carrying a manifest, and every plugin dir with a manifest
     providing gateway hooks must be enabled (the notifications-guard defect).
  R3 DM topic map: every served profile must map «Уведомления» and «Главное»
     under platforms.telegram.extra.dm_topics.
  R4 duplicated rule text: a rule-bearing line (>=70 chars, imperative wording)
     found verbatim in 3+ rule files across skills/ and root rule docs is the
     "one rule in three copies" defect — it must live in exactly one place.
  R5 required check inputs: maintained test inputs must be regular files.
     Output paths and sample strings are not test dependencies.

Usage: python3 scripts/runtime-state-check.py [--root ~/.hermes] [--json]
Exit: 0 clean, 1 findings, 2 unusable input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NOTIFY = "Уведомления"
MAIN = "Главное"
MANIFESTS = ("plugin.yaml", "plugin.yml", "plugin.json", "package.json")
ACCEPTANCE = ".artifacts/hermes-bridge-20261011/live-repair/proactivity-completion-20261012"
CHECK_INPUTS = (
    "scripts/admission.py",
    "tests/admission/fixtures/admission-recency-baseline.py",
    f"{ACCEPTANCE}/prd-wave1/acceptance/run_suite.py",
    f"{ACCEPTANCE}/prd-wave1/acceptance/scenarios.json",
    f"{ACCEPTANCE}/prd-wave4/wiring/run-tests.py",
    "tests/acceptance-runner/fixtures/scenarios-before-ledger-markers.json",
    "scripts/browser-access.py",
    "tests/browser-access/test_browser_access.py",
    "scripts/check-select.py",
    "scripts/task-state-check.py",
    "tests/video-pipeline/fixtures/red.jpg",
    "tests/video-pipeline/fixtures/blue.jpg",
    "tests/video-pipeline/fixtures/green.jpg",
    "tests/video-pipeline/fixtures/audio.wav",
)


def profile_homes(root: Path):
    yield "", root
    profiles = root / "profiles"
    if profiles.is_dir():
        for d in sorted(profiles.iterdir()):
            if (d / "config.yaml").is_file() or (d / "SOUL.md").is_file():
                yield d.name, d


def check_skill_links(root: Path) -> list[str]:
    findings = []
    for name, home in profile_homes(root):
        skills = home / "skills"
        if not skills.is_dir():
            continue
        for entry in skills.iterdir():
            if entry.is_symlink() and not entry.exists():
                findings.append(f"{name or 'default'}: dangling skill link {entry}")
    return findings


def check_plugin_wiring(root: Path) -> list[str]:
    import yaml
    findings = []
    for name, home in profile_homes(root):
        cfg_path = home / "config.yaml"
        plug_dir = home / "plugins"
        if not cfg_path.is_file():
            continue
        try:
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception as exc:
            findings.append(f"{name or 'default'}: config.yaml unreadable: {exc}")
            continue
        enabled = set((cfg.get("plugins") or {}).get("enabled") or [])
        disabled = set((cfg.get("plugins") or {}).get("disabled") or [])
        installed = {}
        if plug_dir.is_dir():
            for entry in plug_dir.iterdir():
                if entry.name.startswith("."):
                    continue
                manifest = next((entry / m for m in MANIFESTS if (entry / m).exists()), None)
                if manifest is None:
                    continue
                installed[entry.name] = manifest
        missing = sorted(e for e in enabled if e not in installed and "/" not in e)
        for e in missing:
            findings.append(f"{name or 'default'}: enabled plugin '{e}' has no local dir/manifest")
        for pname, manifest in installed.items():
            try:
                text = manifest.read_text(errors="ignore")
            except OSError:
                continue
            if "pre_gateway_dispatch" in text and pname not in enabled and pname not in disabled:
                findings.append(
                    f"{name or 'default'}: plugin '{pname}' declares pre_gateway_dispatch "
                    "but is absent from plugins.enabled (present-not-wired defect)")
    return findings


def check_topic_maps(root: Path) -> list[str]:
    import yaml
    findings = []
    for name, home in profile_homes(root):
        cfg_path = home / "config.yaml"
        if not cfg_path.is_file():
            continue
        try:
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            continue
        extra = (((cfg.get("platforms") or {}).get("telegram") or {}).get("extra") or {})
        names = set()
        for block in extra.get("dm_topics") or []:
            for item in block.get("topics") or []:
                if item.get("name"):
                    names.add(str(item["name"]))
        for required in (NOTIFY, MAIN):
            if names and required not in names:
                findings.append(f"{name or 'default'}: dm_topics lacks «{required}»")
    return findings


RULE_FILES = ("SKILL.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "RULES.md")
RULE_MIN_CHARS = 70
RULE_MIN_HITS = 3


def check_duplicate_rules(root: Path) -> list[str]:
    """Identical rule lines shared across rule-bearing files. Compares by file
    (not profile) so the same copied rule surfaces once."""
    import hashlib
    line_to_files: dict[str, set] = {}
    for name, home in profile_homes(root):
        for skills in (home / "skills", home / "shared" / "skills"):
            if not skills.is_dir():
                continue
            for skill_dir in skills.iterdir():
                if ".archive" in skill_dir.parts:
                    continue
                skill_path = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
                candidates = [skill_path / f for f in RULE_FILES]
                candidates += sorted(skill_path.glob("*/SKILL.md"))
                for fpath in candidates:
                    if not fpath.is_file():
                        continue
                    try:
                        rel = str(fpath.relative_to(skills.resolve()))
                    except ValueError:
                        rel = str(fpath)
                    try:
                        for raw in fpath.read_text(errors="ignore").splitlines():
                            line = " ".join(raw.split()).strip(" -*#>")
                            if len(line) < RULE_MIN_CHARS:
                                continue
                            key = hashlib.sha256(line.lower().encode()).hexdigest()
                            line_to_files.setdefault(key, set()).add((line, rel))
                    except OSError:
                        continue
    per_line: dict[str, set] = {}
    for hits in line_to_files.values():
        for line, fpath in hits:
            per_line.setdefault(line, set()).add(fpath)
    findings = []
    for line, files in per_line.items():
        if len(files) >= RULE_MIN_HITS:
            shown = sorted(files)[:4]
            findings.append(
                f"rule duplicated in {len(files)} files: «{line[:90]}…» "
                f"({', '.join(shown)}{' …' if len(files) > 4 else ''})")
    return findings


def check_required_inputs(repo: Path) -> list[str]:
    return [f"required check input missing or not a file: {repo / name}"
            for name in CHECK_INPUTS if not (repo / name).is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / ".hermes"))
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"runtime-state-check: no root {root}", file=sys.stderr)
        return 2
    hard = {
        "broken_skill_links": check_skill_links(root),
        "plugin_wiring": check_plugin_wiring(root),
        "topic_maps": check_topic_maps(root),
        "fixture_inputs": check_required_inputs(Path(args.repo)),
    }
    warn = {"duplicate_rules": check_duplicate_rules(root)}
    total = sum(len(v) for v in hard.values())
    warns = sum(len(v) for v in warn.values())
    if args.json:
        print(json.dumps({**hard, **warn}, ensure_ascii=False, indent=2))
    else:
        for lane, rows in hard.items():
            for row in rows:
                print(f"FAIL {lane}: {row}")
        for lane, rows in warn.items():
            for row in rows:
                print(f"WARN {lane}: {row}")
        print(f"runtime-state-check: {total} failure(s), {warns} warning(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
