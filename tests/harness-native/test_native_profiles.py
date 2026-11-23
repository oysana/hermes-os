#!/usr/bin/env python3
"""Fixture-only checks for the native subagent profile candidates.

Run: python3 -B tests/harness-native/test_native_profiles.py

These check the *source candidates* under runtime/harness-config/devin/
against facts verified on this machine (2026-10-31, RS52 native-wave2), not
against installed state. Nothing here loads a harness, spawns a session or
makes a model call.

Verified loader facts the checks encode:

  Devin (~/.t3/tools/devin/3000.11.3): custom agents are markdown under
  .devin/agents/ or ~/.config/devin/agents/ with YAML frontmatter; `model:`
   is pinned here to the owner-selected SWE-2 roles; `devin doctor --json` reports loaded
  profiles but does NOT validate `model:` or `allowed-tools` values (a
  foreign slug and a typo'd tool both loaded silently), so values are checked
  here against fixture excerpts.

"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEVIN_DIR = os.path.join(ROOT, "runtime", "harness-config", "devin")

# Catalog excerpt captured from `devin models list` on 2026-10-31. Only the
# slugs candidates may pin; the full catalog is much larger.
DEVIN_MODELS = {"swe-2-medium", "swe-2-high", "swe-2-max"}
# This is our verified role-policy subset, NOT the complete Devin model catalog.
# Some other model families can be supported by Devin; never infer routing from a prefix.

# Tool names proven by bundled docs (subagents.mdx example, lifecycle-hooks.mdx).
DEVIN_TOOLS = {"read", "grep", "glob", "exec", "edit", "write", "webfetch",
               "web_search", "todo_write", "run_subagent", "read_subagent",
               "notebook_edit", "notebook_read", "skill", "get_output",
               "kill_shell", "request_scope", "find_file_by_name", "mcp_call_tool"}
DEVIN_BUILTIN_PROFILES = {"subagent_explore", "subagent_general"}
DEVIN_NEVER_GRANTABLE = {"ask_user_question"}  # withheld from subagents per docs

NO_SECRETS = "credentials"
PARENT_CHECKS = "parent"


def parse_frontmatter(path):
    """Minimal parser for the frontmatter subset these files use."""
    with open(path, encoding="utf-8") as stream:
        text = stream.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return None, text
    fields, body = {}, m.group(2)
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+-\s+", line):
            if key is None:
                raise ValueError(f"{path}: list item without a key")
            if not isinstance(fields.get(key), list):
                fields[key] = []
            fields[key].append(line.split("-", 1)[1].strip())
            continue
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            fields[key] = kv.group(2).strip()
    return fields, body


class Failures:
    def __init__(self):
        self.items = []

    def check(self, ok, msg):
        if not ok:
            self.items.append(msg)
        return ok


def check_devin(f):
    for fname in ("explorer.md", "implementer.md"):
        path = os.path.join(DEVIN_DIR, fname)
        if not f.check(os.path.isfile(path), f"devin: missing {fname}"):
            continue
        fm, body = parse_frontmatter(path)
        f.check(fm is not None, f"devin/{fname}: no frontmatter")
        if fm is None:
            continue
        name = fm.get("name", "")
        f.check(bool(name), f"devin/{fname}: missing name")
        f.check(name not in DEVIN_BUILTIN_PROFILES,
                f"devin/{fname}: name collides with built-in {name}")
        f.check(bool(fm.get("description")), f"devin/{fname}: missing description")
        model = fm.get("model")
        f.check(model in DEVIN_MODELS,
                f"devin/{fname}: model {model!r} not in verified Devin catalog excerpt")
        tools = fm.get("allowed-tools")
        if tools is not None:
            f.check(isinstance(tools, list), f"devin/{fname}: allowed-tools must be a list")
            for t in (tools or []):
                f.check(t in DEVIN_TOOLS, f"devin/{fname}: unknown tool {t!r}")
                f.check(t not in DEVIN_NEVER_GRANTABLE,
                        f"devin/{fname}: {t} cannot be granted to subagents")
    fm, body = parse_frontmatter(os.path.join(DEVIN_DIR, "explorer.md"))
    if fm is None:
        return
    f.check(NO_SECRETS in body and PARENT_CHECKS in body,
            "devin/explorer.md: missing read-only/safety contract in body")
    f.check(fm.get("allowed-tools") == ["read", "grep", "glob"],
             "devin/explorer.md: clerk must carry exactly read/grep/glob")


def check_contract_parity(f):
    """Every clerk, whichever harness, states the same bounded contract."""
    for path in (os.path.join(DEVIN_DIR, "explorer.md"),
                 os.path.join(ROOT, "runtime", "harness-config", "claude", "Explore.md")):
        if not os.path.isfile(path):
            continue
        fm, body = parse_frontmatter(path)
        whole = " ".join(str(v) for v in (fm or {}).values()) + "\n" + body
        f.check("reference" in whole or "paths and line" in whole,
                f"{os.path.basename(path)}: clerk must promise cited references")
        f.check("Do not edit" in body or "read-only" in body.lower()
                or "Read-only" in body,
                f"{os.path.basename(path)}: clerk must be read-only")


def main():
    f = Failures()
    check_devin(f)
    check_contract_parity(f)
    if f.items:
        print("FAIL")
        for item in f.items:
            print(" -", item)
        return 1
    print("OK: native profile candidates pass fixture checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
