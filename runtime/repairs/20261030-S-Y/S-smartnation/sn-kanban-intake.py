#!/usr/bin/env python3
"""Admit a verified SmartNation scan into the native root kanban board.

One verified *unfinished* portal row -> one native preparation card on the
``default`` board of the root ``~/.hermes/kanban.db``, assigned to sqecea.
Existing ownership is never duplicated: rows already held by a legacy SQ
ledger task, a foreign kanban card, an ``sn_external_owners`` reservation or
the ``sn_preparation_tasks`` notepad map are reported held, and ambiguous or
dropped-only ownership becomes an explicit blocker, not a guess.

Admission per new row uses only native primitives, each retry-safe:

1. ``hermes kanban --board default create --initial-status blocked
   --idempotency-key smartnation-prepare-<portal_id> --assignee sqecea`` —
   parked so no dispatcher tick can claim the card before notification
   wiring exists; the idempotency key makes the create replay-safe.
2. ``hermes kanban --board default notify-subscribe --platform telegram
   --chat-id OWNER_CHAT_ID --thread-id <subject topic> --user-id OWNER_CHAT_ID
   --chat-type dm --notifier-profile sqecea --delivery-mode notify+wake`` —
   INSERT OR IGNORE inside kanban_db_notify, so re-subscribing is a no-op.
3. ``hermes kanban --board default unblock`` — only while the card still
   sits at its creation gate: status ``blocked``, zero runs, no
   ``unblocked`` event, exactly one ``blocked`` event whose payload reason
   is ``initial_status``. A card past that gate (claimed, parked on a typed
   ``needs_input`` wait, done, scheduled) is left exactly as found — a
   retry never re-opens an owner's hold and never reverts a card to 'new'.

A crash between any two steps is repaired by re-running: the idempotency
key returns the same card, the subscription no-ops, and the gate check —
read from board events, not local state — decides whether activation is
still owed. No new database, daemon, timer or dispatcher is introduced;
the existing root dispatcher admits the card once it lands in ``ready``.

Freshness: ``sn-monitor.py --verify`` runs BEFORE anything else and must
exit 0 — no state is mutated on a stale or incomplete scan.

``sn_preparation_tasks`` stays a lookup aid: it is updated only with real
native receipts (verified card ids), never consulted as the authority.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

if __name__ == '__main__' and not getattr(sys, '_hermes_pin_default_home', False):
    selected = Path.home() / '.hermes/shared/proactive/run-selected.py'
    os.execv(sys.executable, [sys.executable, '-B', str(selected), __file__, *sys.argv[1:]])

JOB = 'a6cc0b23507d'
ROOT_HOME = Path('/home/yqecea/.hermes')
SQ_HOME = ROOT_HOME / 'profiles' / 'sqecea'
MONITOR = 'scripts/sn-monitor.py'
LEDGER = 'scripts/ledger'
NOTES_DB = 'cron/notepad.db'
MAP_KEY = 'sn_preparation_tasks'
OWNERS_KEY = 'sn_external_owners'

BOARD = 'default'
PLATFORM = 'telegram'
CHAT_ID = 'OWNER_CHAT_ID'
USER_ID = 'OWNER_CHAT_ID'
CHAT_TYPE = 'dm'
ASSIGNEE = 'sqecea'
NOTIFIER = 'sqecea'
DELIVERY_MODE = 'notify+wake'
CREATED_BY = 'smartnation-scan'
IDEM_PREFIX = 'smartnation-prepare-'
TOPIC_OTHER = '9494'
PORTAL_URL = 'https://college.smartnation.kz/ru/mng/control/assigned-homeworks/'

# Subject -> fixed DM topic (config platforms.telegram.extra.dm_topics).
# First regex match wins; ``other`` always lands in Уведомления.
TOPIC_RULES = (
    ('sql', re.compile(r'баз\w*\s*данн|данных|sql|деректер', re.I), '9499'),
    ('economics', re.compile(r'эконом', re.I), '9498'),
    ('graphics', re.compile(r'график|дизайн|проектирован', re.I), '9497'),
    ('ict', re.compile(r'информационн|информатик|икт|веб[- ]?портал|цифров', re.I), '9495'),
    ('python', re.compile(r'алгоритм|программ|python|код', re.I), '9496'),
)

HOLD_OUTCOMES = {'held_external', 'held_map', 'held_ledger', 'held_native', 'exists'}
BLOCK_OUTCOMES = {'blocked_ambiguous', 'blocked_dropped', 'error'}


class CliError(RuntimeError):
    def __init__(self, argv, cp):
        super().__init__(f"rc={cp.returncode}: {' '.join(map(str, argv))}\n{cp.stderr.strip()[:500]}")
        self.argv, self.cp = argv, cp


class HermesCli:
    """Native entry points only: ``hermes kanban`` on the pinned root board,
    the sqecea ledger/notepad CLIs, and the existing sn-monitor validator.
    ``runner`` is the single subprocess seam tests replace."""

    def __init__(self, *, hermes='hermes', board=BOARD, root_home=ROOT_HOME,
                 sq_home=SQ_HOME, runner=subprocess.run):
        self.hermes, self.board = hermes, board
        self.root_home, self.sq_home = Path(root_home), Path(sq_home)
        self._run = runner

    def _env(self):
        env = dict(os.environ)
        env['HERMES_HOME'] = str(self.root_home)  # pin the root board, never a profile-local one
        return env

    def _call(self, argv, timeout=120):
        cp = self._run([str(a) for a in argv], env=self._env(),
                       capture_output=True, text=True, timeout=timeout)
        if cp.returncode != 0:
            raise CliError(argv, cp)
        return cp

    def _call_json(self, argv, timeout=120):
        return json.loads(self._call(argv, timeout).stdout)

    def _kanban(self, *args):
        return [self.hermes, 'kanban', '--board', self.board, *args]

    def verify_scan(self, scan_path):
        cp = self._run([sys.executable, str(self.sq_home / MONITOR), '--verify', str(scan_path)],
                       env=self._env(), capture_output=True, text=True, timeout=60)
        return cp.returncode

    def ledger_list(self):
        cp = self._run([str(self.sq_home / LEDGER), 'list'], env=self._env(),
                       capture_output=True, text=True, timeout=120)
        if cp.returncode != 0:
            raise CliError(['ledger', 'list'], cp)
        return json.loads(cp.stdout or '[]')

    def list_tasks(self):
        # Native CLI `list` recomputes ready tasks; use its read-only DB query.
        with sqlite3.connect((self.root_home / 'kanban.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                "SELECT id,title,body,created_by,assignee,status FROM tasks WHERE status != 'archived'")]

    def create_task(self, title, body_file, *, assignee, idempotency_key,
                    initial_status='blocked', created_by=CREATED_BY,
                    skills=None, tenant=None, priority=0, max_runtime=None):
        argv = self._kanban('create', title, '--body-file', str(body_file),
                            '--assignee', assignee, '--initial-status', initial_status,
                            '--idempotency-key', idempotency_key, '--created-by', created_by,
                            '--priority', str(priority))
        for skill in skills or ():
            argv += ['--skill', skill]
        if tenant:
            argv += ['--tenant', tenant]
        if max_runtime:
            argv += ['--max-runtime', str(max_runtime)]
        argv.append('--json')
        return self._call_json(argv)

    def show_task(self, task_id):
        return self._call_json(self._kanban('show', task_id, '--json'))

    def notify_subscribe(self, task_id, topic, *, delivery_mode=DELIVERY_MODE):
        self._call(self._kanban(
            'notify-subscribe', task_id, '--platform', PLATFORM, '--chat-id', CHAT_ID,
            '--thread-id', topic, '--user-id', USER_ID, '--chat-type', CHAT_TYPE,
            '--notifier-profile', NOTIFIER, '--delivery-mode', delivery_mode))

    def unblock(self, task_id, reason):
        # Admission guard and native transition share BEGIN IMMEDIATE: a hold
        # arriving after the outer `show` must win, never be blindly resumed.
        if self.board != BOARD or self.root_home.resolve() != ROOT_HOME.resolve():
            raise PermissionError('SmartNation admission requires the root default board')
        sys.path.insert(0, str(ROOT_HOME / 'hermes-agent'))
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_connect as kbc
        with kbc.connect_closing(self.root_home / 'kanban.db') as conn:
            with kb.write_txn(conn):
                return activate_creation_gate(conn, task_id, kb)

    def notepad_set(self, key, value):
        self._call([self.hermes, '--profile', 'sqecea', 'cron', 'notepad', JOB, 'set',
                    key, json.dumps(value, ensure_ascii=False, separators=(',', ':'))])


def load_notes(notes_db):
    """Job notepad keys, read-only — same access pattern as sn-monitor."""
    if not Path(notes_db).exists():
        return {}
    with sqlite3.connect(f'file:{notes_db}?mode=ro', uri=True) as conn:
        rows = conn.execute('SELECT key, value FROM cron_notepad WHERE job_id=?', (JOB,)).fetchall()
    out = {}
    for key, value in rows:
        try:
            out[key] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            out[key] = value
    return out


def route_topic(subject, overrides=None):
    sid, name = str(subject.get('id') or ''), str(subject.get('name') or '')
    if overrides and sid in overrides:
        return overrides[sid], 'override'
    for category, pattern, topic in TOPIC_RULES:
        if pattern.search(name):
            return topic, category
    return TOPIC_OTHER, 'other'


def _pid_pattern(portal_id):
    return re.compile(r'(?<!\d)' + re.escape(str(portal_id)) + r'(?!\d)')


def _ledger_blob(task):
    parts = [task.get('title'), task.get('source'), task.get('next'), task.get('dest')]
    parts += [n.get('text') for n in task.get('notes') or () if isinstance(n, dict)]
    return '\n'.join(p for p in parts if p)


def _native_blob(task):
    return '\n'.join(p for p in (task.get('title'), task.get('body')) if p)


def _mentions(blob, pattern):
    return bool(pattern.search(blob or ''))


def _is_ours(task, portal_id, created_by):
    return (task.get('created_by') == created_by
            and str(task.get('title') or '').startswith(f'SmartNation {portal_id} topic '))


def at_creation_gate(show):
    """True only while the card still waits at its own creation gate:
    blocked via ``--initial-status blocked`` and never touched since."""
    task, events, runs = show.get('task') or {}, show.get('events') or [], show.get('runs') or []
    if (task.get('status') != 'blocked' or task.get('block_kind') is not None
            or task.get('current_run_id') or runs):
        return False
    if any(e.get('kind') == 'unblocked' for e in events):
        return False
    blocked = [e for e in events if e.get('kind') == 'blocked']
    if len(blocked) != 1:
        return False
    payload = blocked[0].get('payload') or {}
    return payload.get('reason') == 'initial_status' and 'kind' not in payload


def activate_creation_gate(conn, task_id, kb):
    """Called under the native write transaction; recheck all mutable custody."""
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        return False
    task = dict(row)
    if (task.get('created_by') != CREATED_BY or task.get('assignee') != ASSIGNEE
            or not re.match(r'^SmartNation [0-9]+ topic [0-9]+:', task.get('title') or '')):
        return False
    events = [dict(row) for row in conn.execute(
        'SELECT kind,payload FROM task_events WHERE task_id=? ORDER BY id', (task_id,))]
    for event in events:
        event['payload'] = json.loads(event['payload']) if event['payload'] else None
    runs = list(conn.execute('SELECT id FROM task_runs WHERE task_id=?', (task_id,)))
    # Any intervening owner comment is deliberately preserved for reconciliation.
    comments = conn.execute('SELECT COUNT(*) FROM task_comments WHERE task_id=?', (task_id,)).fetchone()[0]
    if comments or not at_creation_gate({'task': task, 'events': events, 'runs': runs}):
        return False
    return kb.unblock_task(conn, task_id)


def _is_our_card(show, portal_id, created_by):
    return _is_ours(show.get('task') or {}, portal_id, created_by)


def idempotency_key(portal_id):
    return f'{IDEM_PREFIX}{portal_id}'


def build_title(row, topic):
    title = ' '.join(str(row.get('title') or '').split()) or '(без названия)'
    return f"SmartNation {row['id']} topic {topic}: prepare {title}"[:200]


def build_body(row, subject, topic, scan_path, checked_at):
    pid, sid = row['id'], subject.get('id')
    deadline = row.get('deadline') or 'не указан — срок на портале отсутствует'
    criteria = str(row.get('criteria') or '').strip() or '(критерии не видны в карточке)'
    if len(criteria) > 4000:
        criteria = criteria[:4000] + '…'
    attachments = row.get('attachments') or []
    brief_name = {'9499':'sql','9497':'graphics','9496':'python','9495':'ict'}.get(str(topic))
    brief = str(SQ_HOME / 'home/subjects' / (brief_name + '.md')) if brief_name else 'актуальные материалы этой дисциплины'
    names = ', '.join(str(a.get('name')) for a in attachments if isinstance(a, dict) and a.get('name')) or 'нет'
    return f"""SmartNation {pid} · «{row.get('title')}» · тема {topic}
Карточка: {PORTAL_URL}{pid}
Дисциплина: {sid} «{subject.get('name')}»
Предметный brief: {brief} — прочитай до подготовки.
Дедлайн: {deadline}
Критерии: {criteria}
Файлы преподавателя: {names}
Снимок-источник: {scan_path} (проверен {checked_at}); полная строка обхода ниже.

Задача — подготовка И сдача после явного разрешения. Работай по
references/smartnation-monitor-workflow.md и references/smartnation-portal-login.md
навыка academic-assistance.

1. Скачай и полностью прочитай материалы преподавателя — монитор файлы не
   скачивает, это часть подготовки этой задачи.
2. Подготовь ВСЕ требуемые части решения; проверь или запусти результат
   настоящей проверкой, не заявляй готовность без неё.
3. Покажи готовое решение с обычным объяснением в предметном топике
   telegram:{CHAT_ID}:{topic} и сохрани receipt доставки (message_id).
4. Затем kanban_block kind=needs_input reason='explicit Yusuf approval for
   SmartNation {pid}'. Без явного разрешения Йусуфа на ЭТУ конкретную работу
   загружать или сохранять ответ на портале ЗАПРЕЩЕНО.
5. После разрешения (владелец разблокирует карту): сдай по portal-login —
   только одобренные файлы, поле ответа оставь пустым, сохрани, переоткрой
   карточку и проверь статус и каждый файл.
6. Доставь в предметный топик факт сдачи (предмет, работа, подтверждённый
   статус, файлы) и запиши message_id receipt.
7. kanban_complete только с доказательствами ВСЕХ результатов: проверенное
   решение; доставка решения в тему; readback сдачи; receipt уведомления о
   сдаче. Подготовка ≠ сдано/проверено — без подтверждённой сдачи и
   доставленного уведомления карта не закрывается.

Чужое владение сохраняется: если на портале активна другая сессия или бот —
не конкурируй, зафиксируй факт и сообщи; решение принимает владелец.

Исходная строка обхода:
{json.dumps(row, ensure_ascii=False, separators=(',', ':'))}"""


def plan_row(row, subject, topic, ctx, created_by):
    """Dedup/ownership decision for one unfinished row. ``ctx`` carries
    external_owners, prep_map, ledger tasks and native tasks (pre-fetched)."""
    pid = str(row['id'])
    pattern = _pid_pattern(pid)
    out = {'portal_id': pid, 'subject_id': subject.get('id'), 'subject': subject.get('name'),
           'topic': topic, 'title': build_title(row, topic),
           'idempotency_key': idempotency_key(pid)}

    reservation = (ctx['external_owners'] or {}).get(pid)
    if reservation:
        return {**out, 'decision': 'held_external',
                'blocker': {'kind': 'external_owner', 'reservation': reservation, 'row': row}}

    entry = (ctx['prep_map'] or {}).get(pid)
    map_stale = False
    if entry:
        refs = ([('native', entry['kanban_task'])] if entry.get('kanban_task') else []) + \
               ([('ledger', entry['ledger_task'])] if entry.get('ledger_task') else [])
        if not refs:
            return {**out, 'decision': 'blocked_ambiguous',
                    'blocker': {'kind': 'map_without_owner_ref', 'map_entry': entry, 'row': row}}
        for kind, ref in refs:
            pool = ctx['native'] if kind == 'native' else ctx['ledger']
            hit = next((t for t in pool if t.get('id') == ref), None)
            if hit is not None and hit.get('status') != 'dropped':
                return {**out, 'decision': 'held_map',
                        'held': {'map_entry': entry, f'{kind}_ids': [ref],
                                 'status': hit.get('status')}}
        # Every recorded ref is gone or dropped — keep it as a stale hint,
        # never as authority; the general scans below still apply.
        map_stale = True

    ledger_matches = [t for t in ctx['ledger'] if _mentions(_ledger_blob(t), pattern)]
    native_matches = [t for t in ctx['native'] if _mentions(_native_blob(t), pattern)]
    ours = [t for t in native_matches if _is_ours(t, pid, created_by)]
    foreign = [t for t in native_matches if not _is_ours(t, pid, created_by)]
    live_ledger = [t for t in ledger_matches if t.get('status') not in ('done', 'dropped')]
    done_ledger = [t for t in ledger_matches if t.get('status') == 'done']
    dropped_only = bool(ledger_matches) and not live_ledger and not done_ledger

    if live_ledger or done_ledger:
        return {**out, 'decision': 'held_ledger',
                'held': {'ledger_ids': [t['id'] for t in ledger_matches],
                         'statuses': {t['id']: t.get('status') for t in ledger_matches}}}
    if foreign:
        if ours:
            return {**out, 'decision': 'blocked_ambiguous',
                    'blocker': {'kind': 'ours_and_foreign_coexist',
                                'ours': [t['id'] for t in ours],
                                'foreign': [t['id'] for t in foreign], 'row': row}}
        return {**out, 'decision': 'held_native',
                'held': {'native_ids': [t['id'] for t in foreign],
                         'statuses': {t['id']: t.get('status') for t in foreign},
                         'created_by': {t['id']: t.get('created_by') for t in foreign}}}
    if ours:
        if len(ours) > 1:
            return {**out, 'decision': 'blocked_ambiguous',
                    'blocker': {'kind': 'duplicate_own_cards',
                                'ours': [t['id'] for t in ours], 'row': row}}
        return {**out, 'decision': 'exists', 'task_id': ours[0]['id'],
                'status': ours[0].get('status')}
    if map_stale:
        return {**out, 'decision': 'blocked_ambiguous',
                'blocker': {'kind': 'stale_map_ref', 'map_entry': entry,
                            'missing_refs': [r for _, r in refs], 'row': row}}
    if dropped_only:
        return {**out, 'decision': 'blocked_dropped',
                'blocker': {'kind': 'dropped_only_owners',
                            'ledger_ids': [t['id'] for t in ledger_matches], 'row': row}}
    return {**out, 'decision': 'create'}


def _wire(task_id, topic, cli, delivery_mode):
    """subscribe -> gate-check -> unblock, all idempotent. Returns wiring facts."""
    cli.notify_subscribe(task_id, topic, delivery_mode=delivery_mode)
    show = cli.show_task(task_id)
    if not at_creation_gate(show):
        return {'subscribed': True, 'unblocked': False,
                'state': (show.get('task') or {}).get('status')}
    try:
        activated = cli.unblock(task_id, 'sn-kanban-intake: wiring verified')
    except (CliError, sqlite3.Error):
        show = cli.show_task(task_id)
        if at_creation_gate(show):
            raise
        return {'subscribed': True, 'unblocked': False,
                'state': (show.get('task') or {}).get('status')}
    final = cli.show_task(task_id)
    return {'subscribed': True, 'unblocked': activated is not False,
            'state': (final.get('task') or {}).get('status')}


def run_scan(scan, *, cli, notes, ledger_tasks, native_tasks, dry_run=False,
             scan_path=None, routes=None, assignee=ASSIGNEE, created_by=CREATED_BY,
             delivery_mode=DELIVERY_MODE, initial_status='blocked',
             skills=('academic-assistance',), tenant=None, priority=0,
             max_runtime=None, write_map=True, now=None):
    """Verify, plan and execute intake for every unfinished row."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc).isoformat()
    report = {'checked_at': scan.get('checked_at'), 'scan_path': str(scan_path or ''),
              'dry_run': dry_run, 'board': cli.board, 'outcomes': [], 'ok': True,
              'counts': {}, 'notepad_map_updates': {}}
    if cli.verify_scan(scan_path) != 0:
        report['validated'] = False
        report['ok'] = False
        report['error'] = 'sn-monitor --verify rejected the scan; nothing was touched'
        return report
    report['validated'] = True
    ctx = {'external_owners': notes.get(OWNERS_KEY) or {},
           'prep_map': notes.get(MAP_KEY) or {},
           'ledger': ledger_tasks or [], 'native': native_tasks or []}

    for subject in scan.get('subjects') or []:
        topic, category = route_topic(subject, routes)
        for row in subject.get('rows') or []:
            if row.get('category') != 'unfinished':
                report['outcomes'].append({'portal_id': row.get('id'), 'topic': topic,
                                           'outcome': 'skipped', 'category': row.get('category')})
                continue
            plan = plan_row(row, subject, topic, ctx, created_by)
            plan['route_category'] = category
            outcome = dict(plan)
            decision = outcome.pop('decision')
            try:
                if decision == 'create':
                    if dry_run:
                        outcome['outcome'] = 'would_create'
                    else:
                        with tempfile.TemporaryDirectory(prefix='sn-kanban-intake-') as body_dir:
                            body_file = Path(body_dir) / f"body-{plan['portal_id']}.md"
                            body_file.write_text(
                                build_body(row, subject, topic, scan_path, scan.get('checked_at')),
                                encoding='utf-8')
                            created = cli.create_task(
                                plan['title'], body_file, assignee=assignee,
                                idempotency_key=plan['idempotency_key'],
                                initial_status=initial_status, created_by=created_by,
                                skills=skills, tenant=tenant, priority=priority,
                                max_runtime=max_runtime)
                        task_id = created.get('id')
                        show = cli.show_task(task_id)
                        if not _is_our_card(show, plan['portal_id'], created_by):
                            outcome.update(outcome='blocked_ambiguous',
                                           blocker={'kind': 'idempotency_key_collision',
                                                    'task_id': task_id,
                                                    'title': (show.get('task') or {}).get('title'),
                                                    'row': row})
                        else:
                            wiring = _wire(task_id, topic, cli, delivery_mode)
                            outcome.update(outcome='created', task_id=task_id, **wiring)
                            report['notepad_map_updates'][plan['portal_id']] = {
                                'topic': topic, 'kanban_task': task_id,
                                'subject_id': subject.get('id'), 'owner': f'native kanban {task_id}',
                                'admitted_by': 'sn-kanban-intake', 'at': now}
                elif decision == 'exists':
                    if dry_run:
                        outcome.update(outcome='exists', would_wire=True)
                    else:
                        wiring = _wire(plan['task_id'], topic, cli, delivery_mode)
                        outcome.update(outcome='exists', **wiring)
                        report['notepad_map_updates'].setdefault(plan['portal_id'], {
                            'topic': topic, 'kanban_task': plan['task_id'],
                            'subject_id': subject.get('id'), 'owner': f"native kanban {plan['task_id']}",
                            'admitted_by': 'sn-kanban-intake', 'at': now})
                else:
                    outcome['outcome'] = decision
            except (CliError, OSError, sqlite3.Error, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                outcome.update(outcome='error', blocker={'kind': 'cli_failure',
                                                       'error': str(exc), 'row': row})
            report['outcomes'].append(outcome)

    if report['notepad_map_updates'] and not dry_run and write_map:
        merged = dict(ctx['prep_map'])
        merged.update(report['notepad_map_updates'])
        try:
            cli.notepad_set(MAP_KEY, merged)
        except CliError as exc:
            report['ok'] = False
            report['error'] = f'map write failed after native receipts existed: {exc}'
    report['counts'] = {}
    for o in report['outcomes']:
        report['counts'][o.get('outcome', '?')] = report['counts'].get(o.get('outcome', '?'), 0) + 1
    if any(o.get('outcome') in BLOCK_OUTCOMES for o in report['outcomes']):
        report['ok'] = False
    return report


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--scan', type=Path, required=True, help='Verified scan JSON (sn-monitor candidate shape)')
    p.add_argument('--dry-run', action='store_true', help='Plan and report only; zero mutations')
    p.add_argument('--json', action='store_true', help='Print the full JSON report')
    p.add_argument('--report-out', type=Path, help='Also write the JSON report to this path')
    p.add_argument('--hermes', default='hermes', help='hermes executable')
    p.add_argument('--board', default=BOARD, help='Native board slug (pinned root board)')
    p.add_argument('--root-home', type=Path, default=ROOT_HOME, help='Root HERMES_HOME for kanban calls')
    p.add_argument('--sq-home', type=Path, default=SQ_HOME, help='sqecea profile home (monitor/ledger/notepad)')
    p.add_argument('--notes-db', type=Path, help='Job notepad.db (default: <sq-home>/cron/notepad.db)')
    p.add_argument('--no-map-write', action='store_true', help='Skip the sn_preparation_tasks lookup-map update')
    p.add_argument('--route', action='append', default=[], metavar='SUBJECT_ID=TOPIC',
                   help='Explicit subject-id -> topic override (repeatable)')
    p.add_argument('--assignee', default=ASSIGNEE)
    p.add_argument('--created-by', default=CREATED_BY)
    p.add_argument('--delivery-mode', default=DELIVERY_MODE, choices=('notify', 'notify+wake', 'wake'))
    p.add_argument('--initial-status', default='blocked', choices=('blocked',))
    p.add_argument('--skill', action='append', dest='skills', default=None,
                   help='Force-load skill into the worker (repeatable; default: academic-assistance)')
    p.add_argument('--no-skill', action='store_true', help='Create cards without --skill')
    p.add_argument('--tenant', default=None)
    p.add_argument('--priority', type=int, default=0)
    p.add_argument('--max-runtime', default=None, help='Per-task runtime cap (90s/30m/2h/1d or seconds)')
    return p


def _parse_routes(pairs):
    routes = {}
    for pair in pairs or ():
        if '=' not in pair:
            raise ValueError(f'--route expects SUBJECT_ID=TOPIC, got {pair!r}')
        sid, topic = pair.split('=', 1)
        if not sid.strip() or not topic.strip().isdigit():
            raise ValueError(f'--route expects SUBJECT_ID=TOPIC, got {pair!r}')
        routes[sid.strip()] = topic.strip()
    return routes


def main(argv=None, *, cli=None, notes=None, ledger_tasks=None, native_tasks=None):
    args = build_arg_parser().parse_args(argv)
    try:
        scan = json.loads(args.scan.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'sn-kanban-intake: cannot read scan: {exc}', file=sys.stderr)
        return 2
    try:
        routes = _parse_routes(args.route)
    except ValueError as exc:
        print(f'sn-kanban-intake: {exc}', file=sys.stderr)
        return 2
    cli = cli or HermesCli(hermes=args.hermes, board=args.board,
                           root_home=args.root_home, sq_home=args.sq_home)
    skills = [] if args.no_skill else (args.skills if args.skills is not None else ('academic-assistance',))
    if notes is None:
        try:
            notes = load_notes(args.notes_db or (args.sq_home / NOTES_DB))
        except (sqlite3.Error, OSError) as exc:
            print(f'sn-kanban-intake: notepad unavailable: {type(exc).__name__}', file=sys.stderr)
            return 2
    if ledger_tasks is None:
        try:
            ledger_tasks = cli.ledger_list()
        except (CliError, sqlite3.Error, json.JSONDecodeError) as exc:
            print(f'sn-kanban-intake: ledger list failed: {exc}', file=sys.stderr)
            return 2
    if native_tasks is None:
        try:
            native_tasks = cli.list_tasks()
        except (CliError, sqlite3.Error, json.JSONDecodeError) as exc:
            print(f'sn-kanban-intake: kanban list failed: {exc}', file=sys.stderr)
            return 2
    report = run_scan(scan, cli=cli, notes=notes, ledger_tasks=ledger_tasks,
                      native_tasks=native_tasks, dry_run=args.dry_run, scan_path=args.scan,
                      routes=routes, assignee=args.assignee, created_by=args.created_by,
                      delivery_mode=args.delivery_mode, initial_status=args.initial_status,
                      skills=skills, tenant=args.tenant, priority=args.priority,
                      max_runtime=args.max_runtime, write_map=not args.no_map_write)
    if args.report_out:
        args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for o in report['outcomes']:
            line = f"{o.get('outcome', '?'):<18} {o.get('portal_id')} topic={o.get('topic')}"
            if o.get('task_id'):
                line += f" task={o['task_id']}"
            if o.get('blocker'):
                line += f" blocker={o['blocker'].get('kind')}"
            print(line)
        print(f"validated={report.get('validated')} ok={report['ok']} counts={report.get('counts')}")
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
