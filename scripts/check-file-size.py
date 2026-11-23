#!/usr/bin/env python3
"""Owner gate 06.11.2026: no maintained code file over 1000 lines.

Scope: tracked and new (not ignored) code under scripts/, tests/ and runtime/,
except runtime/repairs/, which holds staged copies of other projects' files.
Exceptions live in scripts/file-size-allowlist.txt, one `path date reason` per
line. An entry whose file is gone or back under the cap fails, so the list
only shrinks.
"""
import subprocess
import sys
from pathlib import Path

LIMIT = 1000
ROOT = Path(__file__).resolve().parents[1]
ALLOW = ROOT / "scripts/file-size-allowlist.txt"
CODE = (".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".bend")


def lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def main() -> int:
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "--", "scripts", "tests", "runtime"],
        capture_output=True, text=True, check=True).stdout.splitlines()
    allowed = set()
    if ALLOW.exists():
        allowed = {row.split()[0] for row in ALLOW.read_text().splitlines()
                   if row.strip() and not row.startswith("#")}
    bad = []
    for rel in files:
        path = ROOT / rel
        if rel.startswith("runtime/repairs/") or not rel.endswith(CODE) or not path.is_file():
            continue
        if rel not in allowed and (n := lines(path)) > LIMIT:
            bad.append(f"{rel}: {n} lines > {LIMIT}; split it by behaviour")
    for rel in sorted(allowed):
        path = ROOT / rel
        if not path.is_file() or lines(path) <= LIMIT:
            bad.append(f"{rel}: allowlisted but gone or under {LIMIT}; remove the entry")
    print("\n".join(bad) or f"file-size: no maintained code file over {LIMIT} lines")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
