#!/usr/bin/env python3
"""Fixture tests for sn-kanban-intake.py — mocked native entrypoints, no live DB."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ADAPTER = Path(__file__).resolve().parents[1] / 'sn-kanban-intake.py'
spec = importlib.util.spec_from_file_location('sn_kanban_intake', ADAPTER)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

SOURCE = 'https://college.smartnation.kz/ru/mng/control/assigned-homework/disciplines-list'
CHECKED = '2026-10-30T17:20:00+05:00'


def make_row(pid, title='Тапсырма', **kw):
    row = {'id': pid, 'category': 'unfinished', 'title': title, 'status': 'В ожидании',
           'deadline': None, 'source_url': adapter.PORTAL_URL + pid,
           'criteria': 'критерии', 'attachments': [{'name': 'task.docx'}],
           'detail_checked': True}
    row.update(kw)
    return row


def make_scan(subjects):
    return {'checked_at': CHECKED, 'source_url': SOURCE,
            'discovered_ids': [s['id'] for s in subjects], 'subjects': subjects}


def subject(sid, name, rows, unf=None):
    unf = len(rows) if unf is None else unf
    return {'id': sid, 'name': name, 'total': unf, 'completed': 0,
            'unfinished': unf, 'closed': 0, 'rows': rows}


def our_task(tid, pid, topic='9496', status='blocked'):
    return {'id': tid, 'title': f'SmartNation {pid} topic {topic}: prepare X',
            'created_by': adapter.CREATED_BY, 'assignee': 'sqecea', 'status': status,
            'body': 'body'}


def gate_show(tid, pid='3559999', topic='9496'):
    return {'task': our_task(tid, pid, topic),
            'events': [{'kind': 'created', 'payload': {}},
                       {'kind': 'blocked', 'payload': {'reason': 'initial_status',
                                                       'status': 'blocked', 'actor': adapter.CREATED_BY}}],
            'runs': []}


def needs_input_show(tid, pid='3559999'):
    show = gate_show(tid, pid)
    show['task']['status'] = 'blocked'
    show['task']['block_kind'] = 'needs_input'
    show['events'] += [{'kind': 'unblocked', 'payload': {'status': 'ready'}},
                       {'kind': 'blocked', 'payload': {'reason': 'explicit Yusuf approval',
                                                       'kind': 'needs_input', 'recurrences': 1}}]
    show['runs'] = [{'id': 1, 'profile': 'sqecea', 'status': 'ended', 'outcome': 'blocked'}]
    return show


def ready_show(tid, pid='3559999'):
    show = gate_show(tid, pid)
    show['task']['status'] = 'ready'
    show['events'].insert(1, {'kind': 'unblocked', 'payload': {'status': 'ready'}})
    return show


def done_show(tid, pid='3559999'):
    show = ready_show(tid, pid)
    show['task']['status'] = 'done'
    show['runs'] = [{'id': 1, 'profile': 'sqecea', 'status': 'ended', 'outcome': 'completed'}]
    return show


class FakeCli:
    """Mocked native entrypoint: records every call, never touches a live DB."""

    def __init__(self, *, board='default', verify_rc=0, tasks=None, shows=None,
                 ledger=None, create_response=None, unblock_error=False):
        self.board = board
        self.verify_rc = verify_rc
        self.tasks = tasks or []
        self.shows = dict(shows or {})
        self.ledger = ledger or []
        self._next = 0
        if create_response is None:
            def create_response(title, kw):
                self._next += 1
                return {'id': f't_new{self._next}'}
        self.create_response = create_response
        self.unblock_error = unblock_error
        self.calls = []
        self.map_writes = {}

    def _log(self, name, *args):
        self.calls.append((name,) + args)

    def verify_scan(self, path):
        self._log('verify_scan', path)
        return self.verify_rc

    def ledger_list(self):
        self._log('ledger_list')
        return self.ledger

    def list_tasks(self):
        self._log('list_tasks')
        return self.tasks

    def create_task(self, title, body_file, **kw):
        self._log('create_task', title, kw)
        resp = self.create_response(title, kw)
        tid = resp['id']
        self.shows.setdefault(tid, {
            'task': {'id': tid, 'status': kw.get('initial_status', 'blocked'),
                     'title': title, 'created_by': kw.get('created_by'),
                     'assignee': kw.get('assignee'), 'body': 'body'},
            'events': [{'kind': 'created', 'payload': {}},
                       {'kind': 'blocked', 'payload': {'reason': 'initial_status',
                                                       'status': 'blocked',
                                                       'actor': kw.get('created_by')}}],
            'runs': []})
        return resp

    def show_task(self, tid):
        self._log('show_task', tid)
        return self.shows[tid]

    def notify_subscribe(self, tid, topic, **kw):
        self._log('notify_subscribe', tid, topic, kw)

    def unblock(self, tid, reason):
        self._log('unblock', tid, reason)
        if self.unblock_error:
            cp = type('CP', (), {'returncode': 1, 'stderr': 'cannot unblock'})()
            raise adapter.CliError(['unblock', tid], cp)
        show = self.shows[tid]
        show['task']['status'] = 'ready'
        show['events'].append({'kind': 'unblocked', 'payload': {'status': 'ready'}})

    def notepad_set(self, key, value):
        self._log('notepad_set', key)
        self.map_writes[key] = value

    def mutating_calls(self):
        return [c for c in self.calls if c[0] in ('create_task', 'notify_subscribe',
                                                'unblock', 'notepad_set')]


def run(scan, cli, *, notes=None, ledger=None, native=None, dry_run=False, **kw):
    if ledger is None:
        ledger = cli.ledger
    if native is None:
        native = cli.tasks
    return adapter.run_scan(scan, cli=cli, notes=notes or {}, ledger_tasks=ledger,
                            native_tasks=native, dry_run=dry_run,
                            scan_path='/tmp/scan.json', **kw)


def one_row_scan(pid='3559999', sid='56844', name='Составлять алгоритм для отдельных модулей программного обеспечения'):
    return make_scan([subject(sid, name, [make_row(pid)])])


class TestRouting(unittest.TestCase):
    def test_current_subject_names(self):
        cases = [
            ('56844', 'Составлять алгоритм для отдельных модулей программного обеспечения', '9496', 'python'),
            ('56810', 'Применять технологию программирования', '9496', 'python'),
            ('56893', 'Находить ошибки в коде программы', '9496', 'python'),
            ('56761', 'Собирать данные по использованию базы данных', '9499', 'sql'),
            ('39553', 'Владеть основами информационно-коммуникационных технологий', '9495', 'ict'),
            ('39554', 'Использовать услуги информационно-справочных и интерактивных веб-порталов', '9495', 'ict'),
            ('14578', 'Графика и проектирование', '9497', 'graphics'),
            ('1', 'Экономика и основы предпринимательства', '9498', 'economics'),
            ('2', 'Физическая культура', '9494', 'other'),
        ]
        for sid, name, topic, cat in cases:
            with self.subTest(name=name):
                t, c = adapter.route_topic({'id': sid, 'name': name})
                self.assertEqual((t, c), (topic, cat))

    def test_id_override_wins(self):
        t, c = adapter.route_topic({'id': '56844', 'name': 'Составлять алгоритм'}, {'56844': '9497'})
        self.assertEqual((t, c), ('9497', 'override'))


class TestGate(unittest.TestCase):
    def test_fresh_card_at_gate(self):
        self.assertTrue(adapter.at_creation_gate(gate_show('t_1')))

    def test_needs_input_not_gate(self):
        self.assertFalse(adapter.at_creation_gate(needs_input_show('t_1')))

    def test_unblocked_card_not_gate(self):
        self.assertFalse(adapter.at_creation_gate(ready_show('t_1')))

    def test_done_card_not_gate(self):
        self.assertFalse(adapter.at_creation_gate(done_show('t_1')))

    def test_runs_present_not_gate(self):
        show = gate_show('t_1')
        show['runs'] = [{'id': 1}]
        self.assertFalse(adapter.at_creation_gate(show))

    def test_two_blocked_events_not_gate(self):
        show = gate_show('t_1')
        show['events'].append({'kind': 'blocked', 'payload': {'reason': 'other'}})
        self.assertFalse(adapter.at_creation_gate(show))


class TestPlanDedup(unittest.TestCase):
    def ctx(self, **kw):
        base = {'external_owners': {}, 'prep_map': {}, 'ledger': [], 'native': []}
        base.update(kw)
        return base

    def plan(self, pid='3559999', ctx=None, topic='9496'):
        row = make_row(pid)
        subj = {'id': '56844', 'name': 'x'}
        return adapter.plan_row(row, subj, topic, ctx or self.ctx(), adapter.CREATED_BY)

    def test_external_owner_holds(self):
        ctx = self.ctx(external_owners={'3559999': {'topic': '9495', 'owner': 'other bot'}})
        d = self.plan(ctx=ctx)
        self.assertEqual(d['decision'], 'held_external')
        self.assertEqual(d['blocker']['kind'], 'external_owner')
        self.assertEqual(d['blocker']['row']['id'], '3559999')  # original row preserved

    def test_live_ledger_holds(self):
        ctx = self.ctx(ledger=[{'id': 'abc', 'status': 'waiting',
                                'title': 'SmartNation 3559999 topic 9496: prepare X'}])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'held_ledger')

    def test_done_ledger_holds(self):
        ctx = self.ctx(ledger=[{'id': 'abc', 'status': 'done', 'source': 'работа 3559999'}])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'held_ledger')

    def test_dropped_only_blocks(self):
        ctx = self.ctx(ledger=[{'id': 'abc', 'status': 'dropped',
                                'title': 'SmartNation 3559999 topic 9496: prepare X'}])
        d = self.plan(ctx=ctx)
        self.assertEqual(d['decision'], 'blocked_dropped')
        self.assertEqual(d['blocker']['ledger_ids'], ['abc'])

    def test_map_to_live_ledger_holds(self):
        ctx = self.ctx(prep_map={'3559999': {'ledger_task': 'abc', 'topic': '9496'}},
                       ledger=[{'id': 'abc', 'status': 'open', 'title': 'без айди в названии'}])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'held_map')

    def test_map_to_native_holds(self):
        ctx = self.ctx(prep_map={'3559999': {'kanban_task': 't_x', 'topic': '9496'}},
                       native=[{'id': 't_x', 'status': 'blocked', 'title': 'other', 'created_by': 'user'}])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'held_map')

    def test_stale_map_blocks(self):
        ctx = self.ctx(prep_map={'3559999': {'ledger_task': 'gone'}})
        d = self.plan(ctx=ctx)
        self.assertEqual(d['decision'], 'blocked_ambiguous')
        self.assertEqual(d['blocker']['kind'], 'stale_map_ref')

    def test_map_without_ref_blocks(self):
        ctx = self.ctx(prep_map={'3559999': {'topic': '9496'}})
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'blocked_ambiguous')

    def test_foreign_native_holds(self):
        ctx = self.ctx(native=[{'id': 't_f', 'status': 'done', 'created_by': 'sqecea',
                                'title': 'H: срочно АКТ (3559999)', 'body': 'x'}])
        d = self.plan(ctx=ctx)
        self.assertEqual(d['decision'], 'held_native')
        self.assertEqual(d['held']['native_ids'], ['t_f'])

    def test_ours_and_foreign_ambiguous(self):
        ctx = self.ctx(native=[our_task('t_1', '3559999'),
                               {'id': 't_f', 'status': 'open', 'created_by': 'user',
                                'title': '3559999 помоги'}])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'blocked_ambiguous')

    def test_duplicate_own_cards_ambiguous(self):
        ctx = self.ctx(native=[our_task('t_1', '3559999'), our_task('t_2', '3559999')])
        self.assertEqual(self.plan(ctx=ctx)['decision'], 'blocked_ambiguous')

    def test_our_card_exists(self):
        ctx = self.ctx(native=[our_task('t_1', '3559999', status='ready')])
        d = self.plan(ctx=ctx)
        self.assertEqual((d['decision'], d['task_id']), ('exists', 't_1'))

    def test_free_row_creates(self):
        d = self.plan()
        self.assertEqual(d['decision'], 'create')
        self.assertEqual(d['idempotency_key'], 'smartnation-prepare-3559999')
        self.assertEqual(d['title'], 'SmartNation 3559999 topic 9496: prepare Тапсырма')

    def test_substring_pid_no_false_match(self):
        ctx = self.ctx(ledger=[{'id': 'a', 'status': 'open', 'title': 'работа 35599990'}])
        self.assertEqual(self.plan(pid='3559999', ctx=ctx)['decision'], 'create')


class TestRunScan(unittest.TestCase):
    def test_verify_first_no_mutation_on_reject(self):
        cli = FakeCli(verify_rc=1)
        rep = run(one_row_scan(), cli)
        self.assertFalse(rep['ok'])
        self.assertFalse(rep['validated'])
        self.assertEqual(cli.calls, [('verify_scan', '/tmp/scan.json')])
        self.assertEqual(cli.mutating_calls(), [])

    def test_happy_path_full_sequence(self):
        cli = FakeCli(create_response=lambda t, kw: {'id': 't_new1'})
        rep = run(one_row_scan(), cli)
        self.assertTrue(rep['ok'], rep)
        names = [c[0] for c in cli.calls]
        self.assertEqual(names, ['verify_scan', 'create_task', 'show_task',
                                 'notify_subscribe', 'show_task', 'unblock',
                                 'show_task', 'notepad_set'])
        _, title, kw = cli.calls[1]
        self.assertEqual(kw['idempotency_key'], 'smartnation-prepare-3559999')
        self.assertEqual(kw['assignee'], 'sqecea')
        self.assertEqual(kw['initial_status'], 'blocked')
        self.assertEqual(kw['skills'], ('academic-assistance',))
        sub = cli.calls[3]
        self.assertEqual(sub[1], 't_new1')
        self.assertEqual(sub[2], '9496')
        self.assertEqual(sub[3]['delivery_mode'], 'notify+wake')
        outcome = rep['outcomes'][0]
        self.assertEqual(outcome['outcome'], 'created')
        self.assertTrue(outcome['unblocked'])
        self.assertEqual(outcome['state'], 'ready')
        m = cli.map_writes[adapter.MAP_KEY]
        self.assertEqual(m['3559999']['kanban_task'], 't_new1')
        self.assertEqual(m['3559999']['topic'], '9496')

    def test_retry_still_at_gate_wires_without_create(self):
        cli = FakeCli(shows={'t_1': gate_show('t_1')},
                      tasks=[our_task('t_1', '3559999')])
        rep = run(one_row_scan(), cli)
        self.assertTrue(rep['ok'])
        names = [c[0] for c in cli.calls]
        self.assertNotIn('create_task', names)
        self.assertEqual(names, ['verify_scan', 'notify_subscribe', 'show_task',
                                 'unblock', 'show_task', 'notepad_set'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'exists')
        self.assertTrue(rep['outcomes'][0]['unblocked'])

    def test_retry_running_card_not_unblocked(self):
        cli = FakeCli(shows={'t_1': ready_show('t_1')},
                      tasks=[our_task('t_1', '3559999', status='ready')])
        rep = run(one_row_scan(), cli)
        self.assertNotIn('unblock', [c[0] for c in cli.calls])
        self.assertIn('notify_subscribe', [c[0] for c in cli.calls])
        self.assertEqual(rep['outcomes'][0]['state'], 'ready')

    def test_needs_input_card_never_unblocked(self):
        cli = FakeCli(shows={'t_1': needs_input_show('t_1')},
                      tasks=[our_task('t_1', '3559999', status='blocked')])
        rep = run(one_row_scan(), cli)
        self.assertNotIn('unblock', [c[0] for c in cli.calls])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'exists')
        self.assertFalse(rep['outcomes'][0]['unblocked'])

    def test_done_card_not_reopened(self):
        cli = FakeCli(shows={'t_1': done_show('t_1')},
                      tasks=[our_task('t_1', '3559999', status='done')])
        rep = run(one_row_scan(), cli)
        self.assertNotIn('unblock', [c[0] for c in cli.calls])
        self.assertEqual(rep['outcomes'][0]['state'], 'done')

    def test_dry_run_pure(self):
        cli = FakeCli()
        rep = run(one_row_scan(), cli, dry_run=True)
        self.assertTrue(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'would_create')
        self.assertEqual(cli.mutating_calls(), [])
        self.assertEqual(cli.map_writes, {})

    def test_key_collision_blocks_without_wiring(self):
        cli = FakeCli(create_response=lambda t, kw: {'id': 't_foreign'})
        cli.shows['t_foreign'] = {'task': {'id': 't_foreign', 'status': 'blocked',
                                           'title': 'unrelated card', 'created_by': 'user'},
                                  'events': [], 'runs': []}
        rep = run(one_row_scan(), cli)
        self.assertFalse(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'blocked_ambiguous')
        self.assertEqual(rep['outcomes'][0]['blocker']['kind'], 'idempotency_key_collision')
        self.assertNotIn('notify_subscribe', [c[0] for c in cli.calls])
        self.assertNotIn('unblock', [c[0] for c in cli.calls])
        self.assertEqual(cli.map_writes, {})

    def test_ledger_hold_no_mutation(self):
        cli = FakeCli()
        ledger = [{'id': 'abc', 'status': 'waiting', 'title': 'SmartNation 3559999 topic 9496: prepare X'}]
        rep = run(one_row_scan(), cli, ledger=ledger)
        self.assertTrue(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'held_ledger')
        self.assertEqual(cli.mutating_calls(), [])

    def test_external_owner_hold_recorded(self):
        cli = FakeCli()
        notes = {adapter.OWNERS_KEY: {'3559999': {'owner': 'other bot', 'topic': '9495'}}}
        rep = run(one_row_scan(), cli, notes=notes)
        self.assertTrue(rep['ok'])  # resolved hold, not an adapter failure
        self.assertEqual(rep['outcomes'][0]['outcome'], 'held_external')
        self.assertEqual(rep['outcomes'][0]['blocker']['reservation']['owner'], 'other bot')
        self.assertEqual(cli.mutating_calls(), [])

    def test_ambiguous_fails_run(self):
        cli = FakeCli()
        notes = {adapter.MAP_KEY: {'3559999': {'ledger_task': 'gone'}}}
        rep = run(one_row_scan(), cli, notes=notes)
        self.assertFalse(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'blocked_ambiguous')
        self.assertEqual(cli.mutating_calls(), [])

    def test_unblock_refused_still_gate_errors(self):
        cli = FakeCli(unblock_error=True)
        rep = run(one_row_scan(), cli)
        self.assertFalse(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'error')
        self.assertEqual(cli.map_writes, {})

    def test_multi_row_independent(self):
        scan = make_scan([
            subject('56844', 'Алгоритм', [make_row('3559999'), make_row('3558888')]),
            subject('56761', 'База данных', [make_row('3557777')]),
        ])
        cli = FakeCli(shows={'t_1': ready_show('t_1', '3558888')},
                      tasks=[our_task('t_1', '3558888', status='ready')])
        rep = run(scan, cli)
        self.assertTrue(rep['ok'])
        outcomes = {o['portal_id']: o['outcome'] for o in rep['outcomes']}
        self.assertEqual(outcomes, {'3559999': 'created', '3558888': 'exists', '3557777': 'created'})
        topics = {o['portal_id']: o['topic'] for o in rep['outcomes']}
        self.assertEqual(topics['3557777'], '9499')
        self.assertEqual(topics['3559999'], '9496')
        self.assertIn('3559999', cli.map_writes[adapter.MAP_KEY])
        self.assertIn('3558888', cli.map_writes[adapter.MAP_KEY])

    def test_error_row_does_not_stop_others(self):
        scan = make_scan([subject('56844', 'Алгоритм', [make_row('3559999'), make_row('3558888')])])
        calls = iter([{'id': 't_new1'}, {'id': 't_new2'}])
        cli = FakeCli(create_response=lambda t, kw: next(calls))
        cli.shows['t_new2'] = {'task': {'id': 't_new2', 'title': 'foreign', 'created_by': 'x'},
                               'events': [], 'runs': []}
        rep = run(scan, cli)
        self.assertFalse(rep['ok'])  # second row collided on key check
        outcomes = {o['portal_id']: o['outcome'] for o in rep['outcomes']}
        self.assertEqual(outcomes['3559999'], 'created')
        self.assertEqual(outcomes['3558888'], 'blocked_ambiguous')

    def test_map_merge_preserves_existing(self):
        cli = FakeCli(create_response=lambda t, kw: {'id': 't_new1'})
        notes = {adapter.MAP_KEY: {'other_pid': {'topic': '9495', 'ledger_task': 'abc'}}}
        run(one_row_scan(), cli, notes=notes)
        m = cli.map_writes[adapter.MAP_KEY]
        self.assertEqual(m['other_pid'], {'topic': '9495', 'ledger_task': 'abc'})
        self.assertEqual(m['3559999']['kanban_task'], 't_new1')

    def test_closed_rows_skipped(self):
        scan = make_scan([subject('56844', 'Алгоритм', [make_row('3559999', category='closed')],
                                 unf=1)])
        scan['subjects'][0]['closed'] = 1
        cli = FakeCli()
        rep = run(scan, cli)
        self.assertTrue(rep['ok'])
        self.assertEqual(rep['outcomes'][0]['outcome'], 'skipped')
        self.assertEqual(cli.mutating_calls(), [])

    def test_no_map_write_flag(self):
        cli = FakeCli()
        rep = run(one_row_scan(), cli, write_map=False)
        self.assertTrue(rep['ok'])
        self.assertEqual(cli.map_writes, {})
        self.assertNotIn('notepad_set', [c[0] for c in cli.calls])


class TestMain(unittest.TestCase):
    def test_main_dry_run_exit0(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(one_row_scan(), f)
            path = f.name
        cli = FakeCli()
        rc = adapter.main(['--scan', path, '--dry-run'], cli=cli,
                          notes={}, ledger_tasks=[], native_tasks=[])
        self.assertEqual(rc, 0)
        self.assertEqual(cli.mutating_calls(), [])

    def test_main_invalid_verify_exit2(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(one_row_scan(), f)
            path = f.name
        cli = FakeCli(verify_rc=1)
        rc = adapter.main(['--scan', path, '--json'], cli=cli,
                          notes={}, ledger_tasks=[], native_tasks=[])
        self.assertEqual(rc, 2)

    def test_main_bad_route_exit2(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(one_row_scan(), f)
            path = f.name
        rc = adapter.main(['--scan', path, '--route', 'bogus'], cli=FakeCli(),
                          notes={}, ledger_tasks=[], native_tasks=[])
        self.assertEqual(rc, 2)

    def test_report_out_written(self):
        with tempfile.TemporaryDirectory() as td:
            scan_path = Path(td) / 'scan.json'
            out_path = Path(td) / 'report.json'
            scan_path.write_text(json.dumps(one_row_scan()), encoding='utf-8')
            cli = FakeCli()
            rc = adapter.main(['--scan', str(scan_path), '--dry-run',
                               '--report-out', str(out_path)], cli=cli,
                              notes={}, ledger_tasks=[], native_tasks=[])
            self.assertEqual(rc, 0)
            rep = json.loads(out_path.read_text())
            self.assertTrue(rep['dry_run'])
            self.assertEqual(rep['outcomes'][0]['outcome'], 'would_create')


class TestBody(unittest.TestCase):
    def test_body_contract(self):
        row = make_row('3559999', 'ПР 16 FastAPI', deadline='2026-11-02T10:00:00+05:00')
        body = adapter.build_body(row, {'id': '56844', 'name': 'Алгоритм'},
                                  '9496', '/tmp/scan.json', CHECKED)
        for needle in ('3559999', 'assigned-homeworks/3559999', '56844',
                       '2026-11-02T10:00:00+05:00', 'telegram:OWNER_CHAT_ID:9496',
                       'kanban_block kind=needs_input', 'explicit Yusuf approval',
                       'ЗАПРЕЩЕНО', 'readback', 'task.docx'):
            self.assertIn(needle, body)

    def test_body_null_deadline_named(self):
        body = adapter.build_body(make_row('1'), {'id': 's', 'name': 'x'},
                                  '9494', '/p', CHECKED)
        self.assertIn('не указан', body)


if __name__ == '__main__':
    unittest.main()
