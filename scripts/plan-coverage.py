#!/usr/bin/env python3
"""Fail when an open REQUESTS.md item is missing from PLAN.md."""
import re
import sys
from pathlib import Path

REQUESTS = Path.home() / ".hermes/shared/REQUESTS.md"
PLAN = Path(__file__).resolve().parent.parent / "PLAN.md"
DEFERRED = {"A01", "A02", "A03", "A09", "W01", "W02", "W03", "W04", "W05", "W06", "W07"}

requests = REQUESTS.read_text()
plan = PLAN.read_text()
ranges = re.findall(r"([A-Z]{1,2})(\d\d)–\1(\d\d)", plan)


def covered(item: str) -> bool:
    if item in plan:
        return True
    return any(item[:-2] == prefix and int(lo) <= int(item[-2:]) <= int(hi) for prefix, lo, hi in ranges)


open_items = re.findall(r"^\s*- \[ \] ([A-Z]{1,2}\d\d)\.", requests, re.M)
missing = [item for item in open_items if item not in DEFERRED and not covered(item)]
if missing:
    sys.exit(f"PLAN.md misses open requests: {' '.join(missing)}")
print(f"PLAN.md covers {len(open_items) - len(DEFERRED & set(open_items))} open requests")
