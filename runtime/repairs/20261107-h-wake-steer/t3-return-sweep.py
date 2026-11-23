#!/usr/bin/env python3
"""Resume H's current completed T3 wait through native Kanban; never dispatch project work.

Execution completion is read through the native ``t3_thread_wait`` MCP tool anchored on the
run the card binds: ``untilDelegatedWorkSettled`` resolves the causal result run (continuation
children settle under their parent's wait), while the organizational ``thread.settle`` mark
(manual/PR-merge bookkeeping) is not execution state and is never consulted."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

# Cron may choose a bare Python; reuse the launcher's selected dependency
# environment before importing native MCP. Its bootstrap marks this process.
if __name__ == '__main__' and not getattr(sys, '_hermes_pin_default_home', False):
    selected = Path.home() / '.hermes/shared/proactive/run-selected.py'
    os.execv(sys.executable, [sys.executable, '-B', str(selected), __file__, *sys.argv[1:]])

ROOT = Path.home() / '.hermes'
sys.path.insert(0, str(ROOT / 'shared/proactive'))
from t3_wait import ordinary_thread, owner_control, resumable_t3_wait

LEGACY_OWNER_WAIT = re.compile(r'запрет|deny|explicit user|решени[ея] (владельц|Йусуф)|owner decision|needs? (the )?owner|оплат|billing|payment', re.I)

T3_SERVER = 't3'
T3_WAIT_TOOL = 't3_thread_wait'
# A settled wait answers at once; this only bounds how long a still-working chain stalls the sweep.
WAIT_TIMEOUT_MS = 3000
# Every T3 state a bound run can end in — an unlisted terminal state parks the
# card forever: no receipt, no wake, silent loss (class found via t_a6efc204).
TERMINAL_RUN_STATES = {'completed', 'failed', 'interrupted', 'cancelled', 'rolled_back'}
THREAD_STATES = {
    'idle', 'preparing', 'queued', 'starting', 'running', 'waiting',
    'completed', 'failed', 'interrupted', 'cancelled', 'rolled_back'}
RUN_STATES = THREAD_STATES - {'idle'}


def comments_for(conn, task_id):
    return [dict(row) for row in conn.execute(
        'SELECT id,author,body FROM task_comments WHERE task_id=? ORDER BY id', (task_id,))]


def current_wait(conn, task_id):
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        return None
    card = dict(row)
    # Only dedicated return receipts are sweep-eligible: a product card that merely
    # carries T3-Thread meta (decomposer/handoff cards) must never be auto-completed —
    # completing it fires its own dependency lifecycle (dependent children spawn) and
    # resurrects the measured double orchestration. bind_run only binds its own cards.
    if card.get('created_by') != 't3-return':
        return None
    binding = resumable_t3_wait(card)
    if not binding or not ordinary_thread(binding['thread']):
        return None
    control = owner_control(comments_for(conn, task_id))
    if control == 'hold':
        return None
    if card.get('block_kind') is None and control != 'resume' and LEGACY_OWNER_WAIT.search(card.get('result') or ''):
        return None
    return binding


class T3ProbeError(Exception):
    """A stable diagnostic code; never retain probe output or SDK error text."""


def _unwrap_wait_payload(payload):
    """Native handler JSON -> the tool's structured wait result; stable codes on any deviation."""
    if not isinstance(payload, dict):
        raise T3ProbeError('metadata_invalid')
    if 'error' in payload:
        raise T3ProbeError('tool_error')
    data = payload.get('structuredContent')
    if not isinstance(data, dict):
        data = payload.get('result')
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise T3ProbeError('metadata_invalid')
    if not isinstance(data, dict):
        raise T3ProbeError('metadata_invalid')
    return data


def observed_from_wait(data, thread, requested):
    """One causal wait result -> the result run to ingest, or None while work is still in flight."""
    status = data.get('status')
    if (data.get('threadId') != thread or not isinstance(data.get('timedOut'), bool)
            or not isinstance(status, str) or status not in THREAD_STATES):
        raise T3ProbeError('metadata_invalid')
    result = data.get('resultRun')
    if result is not None and (not isinstance(result, dict)
            or not isinstance(result.get('runId'), str) or not result['runId']
            or result.get('status') not in RUN_STATES):
        raise T3ProbeError('metadata_invalid')
    if data['timedOut']:
        return None  # Causal delegates still working; their own completion owns the wake.
    if result is not None:
        run_id, state = result['runId'], result['status']
    else:
        run_id, state = data.get('runId'), status
        if requested and run_id != requested:
            return None  # The answer resolved to a run the card never bound; never substitute it.
        if not isinstance(run_id, str) or not run_id:
            raise T3ProbeError('metadata_invalid')
    if state not in TERMINAL_RUN_STATES:
        return None
    return {'thread': thread, 'run': run_id, 'status': state, 'waited_run': requested or ''}


def _t3_wait_handler():
    """The registered native handler for the T3 causal-wait tool; OAuth and transport stay native."""
    sys.path.insert(0, str(ROOT / 'hermes-agent'))
    from tools.mcp_tool_discovery import discover_mcp_tools
    from tools.mcp_tool_schema import mcp_prefixed_tool_name
    from tools.registry import registry
    discover_mcp_tools(allowed_mcp_names=[T3_SERVER])
    entry = registry.get_entry(mcp_prefixed_tool_name(T3_SERVER, T3_WAIT_TOOL))
    return getattr(entry, 'handler', None)


def _shutdown_native_t3():
    try:
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers
        shutdown_mcp_servers(names={T3_SERVER}, timeout=10.0)
    except Exception:
        pass  # A failed teardown must not mask the probe result the sweep already holds.


T3_STATE = Path.home() / '.t3/userdata/statev2.sqlite'
LOST_CLAIM_SECONDS = 3600
SUBAGENT_DONE = {'completed', 'failed', 'cancelled', 'interrupted', 'rolled_back', 'idle'}


def only_lost_claim(thread, db_path=None, now=None):
    """T3 never releases a delivery whose claimant died, so its wait stays unsettled forever.
    True only when such an old claim is all that holds the thread; anything unclear keeps waiting."""
    now = now or datetime.now(timezone.utc).timestamp()
    try:
        with sqlite3.connect(f'file:{db_path or T3_STATE}?mode=ro', uri=True, timeout=5) as db:
            lost = 0
            for status, delivery, updated in db.execute(
                    'SELECT status,completion_delivery_state,updated_at FROM orchestration_v2_projection_subagents '
                    'WHERE thread_id=?', (thread,)):
                if status not in SUBAGENT_DONE:
                    return False
                if status != 'idle' and delivery in ('pending', 'claimed'):
                    age = now - datetime.fromisoformat(updated.replace('Z', '+00:00')).timestamp()
                    if delivery != 'claimed' or age < LOST_CLAIM_SECONDS:
                        return False
                    lost += 1
            for payload, in db.execute(
                    'SELECT payload_json FROM orchestration_v2_projection_provider_threads WHERE thread_id=?', (thread,)):
                if json.loads(payload).get('pendingBackgroundTasks'):
                    return False
            return lost > 0
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return False


class NativeWait:
    """In-process causal probe over the native MCP wiring; reads T3 metadata only."""

    def __init__(self, handler=None):
        if handler is None:
            try:
                handler = _t3_wait_handler()
            except Exception as error:
                raise T3ProbeError('mcp_unavailable') from error
        if not callable(handler):
            raise T3ProbeError('mcp_unavailable')
        self._handler = handler

    def result(self, binding):
        thread, run = binding['thread'], binding.get('run') or ''
        if not run:
            raise T3ProbeError('binding_run_missing')
        args = {'threadId': thread, 'timeoutMs': WAIT_TIMEOUT_MS, 'untilDelegatedWorkSettled': True}
        if run:
            args['runId'] = run
        try:
            raw = self._handler(args)
        except TimeoutError as error:
            raise T3ProbeError('wait_timeout') from error
        except Exception as error:
            raise T3ProbeError('mcp_unavailable') from error
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise T3ProbeError('metadata_invalid') from error
        data = _unwrap_wait_payload(payload)
        observed = observed_from_wait(data, thread, run)
        if observed is None and data['timedOut'] and data['status'] in TERMINAL_RUN_STATES and only_lost_claim(thread):
            return {'thread': thread, 'run': data.get('runId') or run, 'status': data['status'], 'waited_run': run}
        return observed

    def close(self):
        _shutdown_native_t3()


def result_already_queued(conn, task_id, observed):
    """A parked card must not re-queue a result the sweep already receipted for it."""
    for row in conn.execute(
            "SELECT body FROM task_comments WHERE task_id=? AND author='t3-return-sweep'", (task_id,)):
        head = (row[0] or '').split('\n', 1)[0]
        if not head.startswith('T3-Return: '):
            continue
        try:
            prior = json.loads(head[len('T3-Return: '):])
        except ValueError:
            continue
        if (isinstance(prior, dict) and prior.get('thread') == observed['thread']
                and prior.get('run') == observed['run']):
            return True
    return False


def check_ingested_or_reason(conn, task_id, observed):
    """Owner requirement (verbatim): add a check `ingested=true or an explicit reason`,
    otherwise the card must NOT close."""
    # 1. Observed dictionary
    obs_ingested = observed.get('ingested')
    if obs_ingested is True or (isinstance(obs_ingested, str) and obs_ingested.lower() in ('true', 'yes', '1')):
        return True, None
    reason = (observed.get('explicit_reason') or observed.get('reason') or '').strip()
    if reason:
        return False, reason

    # 2. Task comments: only non-sweep comments can declare ingestion, as t3-return-sweep's
    # own receipt instructions contain the literal phrase 'Record T3-State: ingested'.
    for row in conn.execute(
            "SELECT author, body FROM task_comments WHERE task_id=? ORDER BY id", (task_id,)):
        author = row[0] or ''
        body = row[1] or ''
        if author == 't3-return-sweep':
            head = body.split('\n', 1)[0]
            if head.startswith('T3-Return: '):
                try:
                    data = json.loads(head[len('T3-Return: '):])
                    if isinstance(data, dict) and data.get('ingested') is True:
                        return True, None
                except ValueError:
                    pass
            continue
        # Origin session / user / worker comments
        if (re.search(r'(?i)\bT3-State:\s*ingested\b', body)
                or re.search(r'(?i)\b["\']?ingested["\']?\s*[:=]\s*(?:true|1|yes)\b', body)):
            return True, None
        m = re.search(r'(?i)\b(?:T3-Reason|Explicit[- ]reason|Reason):\s*(\S.+)', body)
        if m and not reason:
            reason = m.group(1).strip()

    # 3. Task body
    row = conn.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row and row[0]:
        body = row[0]
        if (re.search(r'(?i)\bT3-State:\s*ingested\b', body)
                or re.search(r'(?i)\b["\']?ingested["\']?\s*[:=]\s*(?:true|1|yes)\b', body)):
            return True, None
        m = re.search(r'(?i)\b(?:T3-Reason|Explicit[- ]reason):\s*(\S.+)', body)
        if m and not reason:
            reason = m.group(1).strip()

    return False, reason or None


def check_can_close(db_path, task_id, observed):
    path = Path(db_path)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        binding = current_wait(conn, task_id)
        waited = observed.get('waited_run') or observed.get('run')
        if (not binding or not observed.get('run')
                or binding['thread'] != observed.get('thread')
                or (binding['run'] and binding['run'] != waited)):
            return False
        ingested, explicit_reason = check_ingested_or_reason(conn, task_id, observed)
        return bool(ingested or explicit_reason)


def emit_return_wake(conn, task_id, observed):
    """Wake the card's notify+wake subscriber WITHOUT closing the card.

    The ingestion guard parks the card until the origin records evidence; the
    receipt's 'commented' event is never claimable, so parking used to leave
    the origin asleep (t_f7d20516: parked 14:34, read 17:45). A 'blocked' event
    is claimed via the notifier's TERMINAL_KINDS and wakes via _WAKE_KINDS;
    kind='needs_input' keeps it out of the diagnostic class so the ping is a
    'result' delivery and last_ping_event_id advances. The task row is already
    blocked, so the event only tells the truth. Emitted once per bound run —
    in the same txn as the receipt it can never be skipped or doubled."""
    sys.path.insert(0, str(ROOT / 'hermes-agent'))
    from hermes_cli import kanban_db as kb
    kb._append_event(conn, task_id, 'blocked', {
        'kind': 'needs_input',
        'reason': (f"T3 run {observed['status']}: {observed['run']} — "
                   'read via t3_thread_read, then record T3-State: ingested or a T3-Reason'),
        'status': 'blocked',
        'actor': 't3-return-sweep',
        't3_thread': observed['thread'],
        't3_run': observed['run'],
        'run_status': observed['status'],
    })


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
            waited = observed.get('waited_run') or observed.get('run')
            if (not binding or not observed.get('run')
                    or binding['thread'] != observed.get('thread')
                    or (binding['run'] and binding['run'] != waited)):
                return False
            ingested, explicit_reason = check_ingested_or_reason(conn, task_id, observed)
            receipt = {'thread': observed['thread'], 'run': observed['run'],
                       'bound_run': binding['run'], 'status': observed['status'],
                       'observed_utc': datetime.now(timezone.utc).isoformat(),
                       'ingested': ingested, 'delivered': False}
            if explicit_reason:
                receipt['reason'] = explicit_reason
            # The receipt comment is idempotent; the completion below must still
            # run even when a previous pass crashed between the two steps.
            if not result_already_queued(conn, task_id, observed):
                kb.add_comment(conn, task_id, 't3-return-sweep',
                    'T3-Return: ' + json.dumps(receipt, ensure_ascii=False) + '\n'
                    'Read this exact run through t3_thread_read, verify task match and artifacts, '
                    'deliver the requested result to its owner topic and keep receipts. '
                    'Do not redispatch project implementation. Record T3-State: ingested in a task comment only '
                    'on accepted evidence. This card completes itself — no dispatcher worker, '
                    'no second reviewer; the woken origin session owns reading and delivery. '
                    'Record any actual missing output as a blocker.')
                if not (ingested or explicit_reason):
                    emit_return_wake(conn, task_id, observed)
    # Owner requirement (verbatim): add a check `ingested=true or an explicit reason`,
    # otherwise the card must NOT close.
    if not (ingested or explicit_reason):
        return False
    try:
        with kbc.connect_closing(Path(db_path)) as conn:
            if explicit_reason and not ingested:
                result_text = f"T3 run {observed['status']}: {observed['run']} (reason: {explicit_reason})"
                summary_text = f"T3-Return: run {observed['status']} — closed with reason: {explicit_reason}"
            else:
                result_text = f"T3 run {observed['status']}: {observed['run']}"
                summary_text = (f"T3-Return: run {observed['status']} — read {observed['run']} "
                                f"via t3_thread_read and deliver to the owner topic (see receipt comment).")
            return bool(kb.complete_task(
                conn, task_id,
                result=result_text,
                summary=summary_text))
    except Exception:
        return False


def sweep(db_path, *, dry_run=False, task_id=None, lookup=None, probe_errors=None,
          unclosed_reasons=None, override_ingested=False, override_reason=None):
    path = Path(db_path)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id FROM tasks WHERE assignee IN ('default','hqecea') AND status='blocked' ORDER BY id").fetchall()
        candidates = [(row['id'], current_wait(conn, row['id'])) for row in rows
                      if task_id is None or row['id'] == task_id]
    actions = []
    probe_errors = [] if probe_errors is None else probe_errors
    probe = None
    probe_failure = None
    try:
        for ident, binding in candidates:
            if not binding:
                continue
            try:
                if not binding.get('run'):
                    raise T3ProbeError('binding_run_missing')
                if lookup is not None:
                    observed = lookup(binding)
                else:
                    if probe_failure is not None:
                        raise T3ProbeError(probe_failure)
                    if probe is None:
                        try:
                            probe = NativeWait()
                        except Exception as error:
                            probe_failure = str(error) if isinstance(error, T3ProbeError) else 'mcp_unavailable'
                            raise T3ProbeError(probe_failure) from error
                    observed = probe.result(binding)
            except Exception as error:
                code = (str(error) if isinstance(error, T3ProbeError) else
                        'wait_timeout' if isinstance(error, TimeoutError) else
                        'mcp_unavailable' if isinstance(error, OSError) else
                        'metadata_invalid' if isinstance(error, ValueError) else 'mcp_unavailable')
                diagnostic = {'event': 't3_return_probe_failed', 'task_id': ident, 'code': code}
                probe_errors.append(diagnostic)
                print(json.dumps(diagnostic), file=sys.stderr, flush=True)
                continue
            if not isinstance(observed, dict) or not isinstance(observed.get('run'), str) or not observed['run']:
                continue
            waited = observed.get('waited_run') or observed['run']
            if binding['run'] and binding['run'] != waited:
                continue
            if override_ingested:
                observed['ingested'] = True
            if override_reason:
                observed['explicit_reason'] = override_reason

            if dry_run:
                if check_can_close(path, ident, observed):
                    actions.append({'task_id': ident, 'action': 'completed_return',
                                    'thread': observed['thread'], 'run': observed['run'],
                                    'waited_run': waited, 'dry_run': True,
                                    'delivered': False})
                elif unclosed_reasons is not None:
                    unclosed_reasons.append({'task_id': ident, 'reason': 'not ingested and no explicit reason'})
            elif enqueue_result(path, ident, observed):
                actions.append({'task_id': ident, 'action': 'completed_return',
                                'thread': observed['thread'], 'run': observed['run'],
                                'waited_run': waited, 'dry_run': False,
                                'delivered': False})
            elif unclosed_reasons is not None:
                unclosed_reasons.append({'task_id': ident, 'reason': 'not ingested and no explicit reason'})
    finally:
        if probe is not None:
            probe.close()
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--task')
    parser.add_argument('--ingested', action='store_true', help='mark return as ingested')
    parser.add_argument('--reason', help='explicit reason for closing without ingestion')
    args = parser.parse_args()
    errors = []
    unclosed = []
    actions = sweep(ROOT / 'kanban.db', dry_run=args.dry_run, task_id=args.task,
                    probe_errors=errors, unclosed_reasons=unclosed,
                    override_ingested=args.ingested, override_reason=args.reason)
    for action in actions:
        print(json.dumps(action, ensure_ascii=False), flush=True)
    if args.task and not actions:
        if errors:
            reason = 'probe failed'
        elif unclosed:
            reason = unclosed[0]['reason']
        else:
            reason = 'no current eligible T3 wait result'
        print(json.dumps({'task_id': args.task, 'queued': False, 'reason': reason}))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
