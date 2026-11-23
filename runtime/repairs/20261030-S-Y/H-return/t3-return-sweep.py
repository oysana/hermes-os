#!/usr/bin/env python3
"""Resume H's current completed T3 wait through native Kanban; never dispatch project work."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT = Path.home() / '.hermes'
sys.path.insert(0, str(ROOT / 'shared/proactive'))
from t3_wait import ordinary_thread, owner_control, resumable_t3_wait

LEGACY_OWNER_WAIT = re.compile(r'запрет|deny|explicit user|решени[ея] (владельц|Йусуф)|owner decision|needs? (the )?owner|оплат|billing|payment', re.I)


def comments_for(conn, task_id):
    return [dict(row) for row in conn.execute(
        'SELECT id,author,body FROM task_comments WHERE task_id=? ORDER BY id', (task_id,))]


def current_wait(conn, task_id):
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        return None
    card = dict(row)
    binding = resumable_t3_wait(card)
    if not binding or not ordinary_thread(binding['thread']):
        return None
    control = owner_control(comments_for(conn, task_id))
    if control == 'hold':
        return None
    if card.get('block_kind') is None and control != 'resume' and LEGACY_OWNER_WAIT.search(card.get('result') or ''):
        return None
    return binding


def t3_state(thread, *, binary=None):
    result = subprocess.run([str(binary or ROOT.parent / '.t3/bin/t3'), 'thread', 'show', thread,
                             '--messages', '0', '--json'], capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        return None
    item = data.get('thread', data)
    if (not isinstance(item, dict) or item.get('status') not in ('completed', 'failed')
            or item.get('settled') is not True or 'activeRunId' not in item
            or item['activeRunId'] is not None or not isinstance(item.get('latestRunId'), str)
            or not item['latestRunId']):
        return None
    return {'thread': thread, 'run': item['latestRunId'], 'status': item['status']}


def enqueue_result(db_path, task_id, observed):
    # Use the native composition boundary, not a second transition implementation.
    sys.path.insert(0, str(ROOT / 'hermes-agent'))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_constants import get_hermes_home
    if os.environ.get('HERMES_KANBAN_TASK'):
        raise PermissionError('T3 return is orchestrator-only; a worker must use its own terminal lifecycle')
    if (get_hermes_home().resolve() != ROOT.resolve()
            or Path(db_path).resolve() != (ROOT / 'kanban.db').resolve()):
        raise PermissionError('T3 return requires the H default board in its owning profile scope')
    with kbc.connect_closing(Path(db_path)) as conn:
        with kb.write_txn(conn):
            binding = current_wait(conn, task_id)
            if (not binding or binding['thread'] != observed['thread']
                    or (binding['run'] and binding['run'] != observed['run'])):
                return False
            if not kb.unblock_task(conn, task_id):
                return False
            receipt = {'thread': observed['thread'], 'run': observed['run'],
                       'status': observed['status'], 'observed_utc': datetime.now(timezone.utc).isoformat(),
                       'ingested': False, 'delivered': False}
            kb.add_comment(conn, task_id, 't3-return-sweep',
                'T3-Return: ' + json.dumps(receipt, ensure_ascii=False) + '\n'
                'Read this exact run through t3_thread_read, verify task match and artifacts, '
                'deliver the requested result to its owner topic and keep receipts. '
                'Do not redispatch project implementation. Mark T3-State: ingested only on accepted evidence, '
                'then finish the native task lifecycle or record the actual blocker.')
    return True


def sweep(db_path, *, dry_run=False, task_id=None, lookup=t3_state):
    path = Path(db_path)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id FROM tasks WHERE assignee IN ('default','hqecea') AND status='blocked' ORDER BY id").fetchall()
        candidates = [(row['id'], current_wait(conn, row['id'])) for row in rows
                      if task_id is None or row['id'] == task_id]
    actions = []
    for ident, binding in candidates:
        if not binding:
            continue
        try:
            observed = lookup(binding['thread'])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
        if not observed or (binding['run'] and binding['run'] != observed['run']):
            continue
        if dry_run or enqueue_result(path, ident, observed):
            actions.append({'task_id': ident, 'action': 'unblock_for_ingestion',
                            'thread': observed['thread'], 'run': observed['run'], 'dry_run': dry_run,
                            'delivered': False})
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--task')
    args = parser.parse_args()
    actions = sweep(ROOT / 'kanban.db', dry_run=args.dry_run, task_id=args.task)
    for action in actions:
        print(json.dumps(action, ensure_ascii=False), flush=True)
    if args.task and not actions:
        print(json.dumps({'task_id': args.task, 'queued': False, 'reason': 'no current eligible settled T3 wait'}))


if __name__ == '__main__':
    main()
