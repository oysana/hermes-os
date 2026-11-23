#!/usr/bin/env python3
"""Every deferred ('отложено') post must end in adopt-with-card or reject-with-reason.

Class fix (owner 15.11 item 8): a shared post may not stay parked without an
owner of the next step. The check reads the corpus's review.jsonl and the
append-only decisions.jsonl beside it and fails when a deferred row lacks
exactly one well-formed decision. Adopt requires a kanban card id and an
acceptance string; reject requires a concrete reason. Card existence is a
live-board property, verified by the integrator at decision time -- the lane
checks form and coverage only.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

CARD_ID = re.compile(r'^t_[0-9a-f]{8}$')
DECISIONS = {'adopt', 'reject'}


def validate(rows, decisions):
    deferred = [row for row in rows if row.get('status') == 'отложено']
    errors = []
    by_index = {}
    for line_no, dec in enumerate(decisions, 1):
        index = dec.get('index')
        if not isinstance(index, int):
            errors.append(f'decisions.jsonl:{line_no}: missing integer index')
            continue
        if index in by_index:
            errors.append(f'decisions.jsonl:{line_no}: duplicate decision for index {index}')
            continue
        by_index[index] = dec
        kind = dec.get('decision')
        if kind not in DECISIONS:
            errors.append(f'index {index}: decision must be adopt or reject, got {kind!r}')
            continue
        if kind == 'adopt':
            if not CARD_ID.fullmatch(str(dec.get('card', ''))):
                errors.append(f'index {index}: adopt needs a kanban card id (t_xxxxxxxx)')
            if not str(dec.get('acceptance', '') or '').strip():
                errors.append(f'index {index}: adopt needs a verifiable acceptance')
        else:
            if not str(dec.get('reason', '') or '').strip():
                errors.append(f'index {index}: reject needs a concrete reason')
    for row in deferred:
        index = row.get('index')
        if index not in by_index:
            errors.append(f'index {index}: deferred post has no adopt/reject decision')
    decided = set(by_index)
    for index in sorted(decided - {row.get('index') for row in deferred}):
        errors.append(f'index {index}: decision names a row that is not deferred')
    counts = Counter(d.get('decision') for d in decisions)
    return errors, {'deferred': len(deferred), 'decisions': len(decisions),
                    'adopt': counts.get('adopt', 0), 'reject': counts.get('reject', 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    folder = args.root / 'research/x-posts-20261112'
    rows = [json.loads(line) for line in (folder / 'review.jsonl').read_text().splitlines() if line.strip()]
    decisions_path = folder / 'decisions.jsonl'
    decisions = [json.loads(line) for line in decisions_path.read_text().splitlines() if line.strip()] \
        if decisions_path.is_file() else []
    errors, summary = validate(rows, decisions)
    verdict = 'FAIL' if errors else 'PASS'
    print(json.dumps({'verdict': verdict, 'errors': errors, **summary}, ensure_ascii=False))
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
