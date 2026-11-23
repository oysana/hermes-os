#!/usr/bin/env python3
"""check-rollback-links.py — every artifact path cited in ROLLBACK.md must exist.

Regression: ROLLBACK.md cited REPORT-ROUND3.md, which was never written, and
claimed TokenEconomizer protection was verified when its mechanism had fired
zero times. Claims without resolvable artifacts are how unverified work ships.

Usage: python3 scripts/check-rollback-links.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, ".artifacts", "harness-ultra-audit-20261107")
RB = os.path.join(AUDIT, "ROLLBACK.md")

HOME = os.path.expanduser("~")


def resolve(tok):
    if tok.startswith("~"):
        return os.path.join(HOME, tok[1:].lstrip("/"))
    return os.path.join(AUDIT, tok)


def main():
    text = open(RB, errors="replace").read()
    tokens = set()
    # relative artifact paths like backups/foo or REPORT-ROUNDx.md
    for m in re.finditer(r"(backups/[\w.\-]+|(?:REPORT|ROLLBACK|HARNESS)[\w.\-]*\.md)", text):
        tokens.add(m.group(1))
    # home paths only when they are backups; plain ~/ paths are rollback
    # destinations and may legitimately not exist yet
    for m in re.finditer(r"(~/[\w./\-]*\.bak-[\w.\-]+)", text):
        tokens.add(m.group(1).rstrip(".,;`)"))
    missing = sorted(t for t in tokens if not os.path.exists(resolve(t)))
    if missing:
        print("MISSING artifacts cited in ROLLBACK.md:")
        for t in missing:
            print("  ", t)
        return 1
    print("rollback-links ok: %d cited paths exist" % len(tokens))
    return 0


if __name__ == "__main__":
    sys.exit(main())
