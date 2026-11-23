"""Fixture-only checks. Never open a real Hermes profile or run its ledger."""
import contextlib
import importlib.util
import io
import json
import re
import subprocess
import os
import shlex
import sqlite3
import hashlib
import zipfile
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('turn_brief', Path(__file__).with_name('turn-brief.py'))
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def write_kanban_shim(root):
    """A fake `hermes` for child-process fixtures: logs argv to kanban-calls.jsonl,
    persists created cards to kanban-cards.json, answers list with assignee filtering."""
    fake = root / 'hermes-fake.py'
    fake.write_text(
        '#!/usr/bin/env python3\n'
        'import json, sys\n'
        'from pathlib import Path\n'
        'root = Path(sys.argv.pop(1))\n'
        'args = sys.argv[1:]\n'
        'with (root / "kanban-calls.jsonl").open("a") as out:\n'
        '    out.write(json.dumps(args) + "\\n")\n'
        'if args[:3] == ["kanban", "--board", "default"]:\n'
        '    args = args[3:]\n'
        'store = root / "kanban-cards.json"\n'
        'cards = json.loads(store.read_text()) if store.exists() else []\n'
        'if args and args[0] == "list":\n'
        '    rows = cards\n'
        '    if "--assignee" in args:\n'
        '        rows = [c for c in rows if c.get("assignee", "default") == args[args.index("--assignee") + 1]]\n'
        '    print(json.dumps(rows))\n'
        'elif args and args[0] == "create":\n'
        '    card = {"id": "k%d" % (len(cards) + 1), "title": args[1], "status": "blocked",\n'
        '            "created_by": "turn-brief"}\n'
        '    for flag in ("--body", "--assignee", "--idempotency-key"):\n'
        '        if flag in args:\n'
        '            card[flag.lstrip("-").replace("-", "_")] = args[args.index(flag) + 1]\n'
        '    cards.append(card)\n'
        '    store.write_text(json.dumps(cards))\n'
        '    print(json.dumps({"id": card["id"]}))\n'
        'else:\n'
        '    print("{}")\n')
    shim = root / 'hermes'
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} {fake} {root} "$@"\n')
    shim.chmod(0o700)
    return str(shim)


def kanban_calls(root):
    log = root / 'kanban-calls.jsonl'
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [row[3:] if row[:3] == ['kanban', '--board', 'default'] else row for row in rows]


def kanban_cards(root):
    store = root / 'kanban-cards.json'
    return json.loads(store.read_text()) if store.exists() else []


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'scripts').mkdir()
        (self.home / 'scripts/ledger').touch()
        for name, path in [('HOME', self.home), ('PROJECTS_INDEX', self.home / 'PROJECTS.md'), ('SUBS_FILE', self.home / 'subs.jsonl')]:
            fixture = patch.object(hook, name, path)
            fixture.start()
            self.addCleanup(fixture.stop)
        self.calls = []
        self.tasks = []
        self.ktasks = []
        self.kcalls = []

    def kanban(self, *args):
        """run_kanban fake: (ok, out). kfail=True refuses every call like the guard does."""
        self.kcalls.append(args)
        if getattr(self, 'kfail', None):
            return False, "kanban: refused (fixture)"
        if args[0] == 'list':
            rows = self.ktasks
            if '--assignee' in args:
                rows = [r for r in rows if r.get('assignee', 'default') == args[args.index('--assignee') + 1]]
            return True, json.dumps(rows)
        if args[0] == 'create':
            card_id = 'k' + str(len(self.ktasks) + 1)
            card = dict(id=card_id, title=args[1],
                        body=args[args.index('--body') + 1],
                        status='blocked', created_by='turn-brief')
            for flag in ('--assignee',):
                if flag in args:
                    card['assignee'] = args[args.index(flag) + 1]
            self.ktasks.append(card)
            return True, json.dumps({'id': card_id})
        if args[0] == 'comment':
            card = next((c for c in self.ktasks if c['id'] == args[1]), None)
            if card is None:
                return False, 'unknown card'
            card.setdefault('comments', []).append(args[2])
            return True, ""
        return True, ""

    def ledger(self, path, *args):
        self.calls.append(args)
        if args[0] == 'list':
            return json.dumps(self.tasks)
        if args[0] == 'add':
            task_id = 'task' + str(len(self.tasks) + 1)
            self.tasks.append(dict(id=task_id, title=args[args.index('--title') + 1],
                                   dest=args[args.index('--dest') + 1],
                                   source=args[args.index('--source') + 1], status='open'))
            return task_id
        return args[1]

    def added_cards(self):
        return [c for c in self.kcalls if c[0] == 'create']

    def invoke(self, text, session='s1', turn='t1', profile='default'):
        payload = dict(profile=profile, session_id=session,
                       extra=dict(user_message=text, turn_id=turn))
        out = io.StringIO()
        with patch.object(hook, 'HOME', self.home), patch.object(hook, 'run', self.ledger), \
                patch.object(hook, 'run_kanban', self.kanban), \
                patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
            hook.main()
        return json.loads(out.getvalue())['context']

    def test_capture_hook_uses_run_ledger_and_preserves_source_state(self):
        script = Path(__file__).with_name('proactive.py')
        wrapper = self.home / 'scripts/ledger'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(script), '--home', str(self.home), 'ledger']) + ' "$@"\n')
        wrapper.chmod(0o700)
        def command(*args):
            result = subprocess.run([str(wrapper), *args], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        task_id = command('add', '--title', 'Fixture promise', '--outputs', 'answer', '--dest', 'chat')
        source = self.home / 'state/tasks.jsonl'
        before = source.read_bytes()
        capture = self.home / 'capture'; capture.mkdir()
        payload = dict(profile='default', session_id='fixture', extra=dict(user_message='Напиши ответ по тестовому примеру', turn_id='one'))
        output = io.StringIO()
        with patch.dict(os.environ, {'HERMES_ACCEPTANCE_CAPTURE': str(capture)}), patch.object(hook, 'HOME', self.home), patch.object(hook, 'run_kanban', self.kanban), patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(output):
            hook.main()
            command('update', task_id, '--next', 'Inspect fixture')
            command('done', task_id, '--evidence', 'answer=fixture checked')
            self.assertEqual(json.loads(command('show', task_id))['status'], 'done')
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.home / 'state/turn-brief.json').exists())
        self.assertTrue(list(capture.rglob('turn-brief.json')))
        self.assertNotIn('Fixture promise', json.loads(output.getvalue())['context'])

    def test_capture_cross_profile_learning_facts_use_seeded_ledger(self):
        source = self.home / 'profiles/sqecea/state/tasks.jsonl'
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps(dict(id='original-s', title='Scratch fixture', status='open')) + '\n')
        capture = self.home / 'capture'; capture.mkdir()
        with patch.dict(os.environ, {'HERMES_ACCEPTANCE_CAPTURE': str(capture)}), patch.object(hook, 'HOME', self.home):
            self.assertIn('original-s', hook.learning_task_facts('yqecea', []))
            source.write_text(json.dumps(dict(id='later-s', title='Scratch fixture', status='open')) + '\n')
            facts = hook.learning_task_facts('yqecea', [])
            self.assertIn('original-s', facts)
            self.assertNotIn('later-s', facts)
            self.assertIn(str(capture), facts)

    def test_each_profile_gets_its_execution_role(self):
        cases = [('default', 't3-work-bridge'), ('yqecea', 'Personal-life duties'),
                 ('sqecea', 'Study duties')]
        for profile, expected in cases:
            with self.subTest(profile=profile):
                if profile != 'default':
                    ledger = self.home / 'profiles' / profile / 'scripts' / 'ledger'
                    ledger.parent.mkdir(parents=True, exist_ok=True)
                    ledger.touch()
                context = self.invoke('Что ты делаешь?', profile=profile)
                self.assertIn(expected, context)
                self.assertEqual(self.tasks, [])

    def test_native_cron_keeps_roles_promises_and_silence_without_capture(self):
        self.tasks = [dict(id='existing', title='Existing promise', status='open', next='Read source')]
        context = self.invoke('[IMPORTANT: You are running as a scheduled cron job.]\nПроверь сайт',
                              session='cron_123abc_20261012_080000')
        self.assertIn('t3-work-bridge', context)
        self.assertIn('existing', context)
        self.assertIn('[SILENT]', context)
        self.assertEqual([c[0] for c in self.calls], ['list'])
        self.assertFalse((self.home / 'state/turn-brief.json').exists())

    def test_short_imperatives(self):
        for text in ['Проверь сайт', 'Исправь', 'fix bug', 'please check site',
                     'Объясни тему циклов с урока', 'Нарисуй схему DNS', 'Составь план на неделю',
                     'Открой мой отчёт и покажи итог']:
            with self.subTest(text=text):
                self.assertTrue(hook.is_request(text))

    def test_capture_names_the_one_call_close(self):
        # Live smoke 2026-10-13: a one-word reply cost 4 ledger calls (list, list, show+wrong key, done).
        # Owner rule 2026-11-04: a "reply with one word" ping is not a request and never enters the journal.
        self.assertNotIn('Captured', self.invoke('Ответь одним словом: работает'))
        context = self.invoke('Исправь опечатку в проекте')
        self.assertIn('Captured kanban card k1', context)
        self.assertIn('complete k1 --result', context)
        self.assertIn('Skip kanban list/show', context)
        self.assertEqual(self.tasks, [])

    def test_tool_brief_close_lists_every_output(self):
        # Rerun 2026-10-13: the one-key template could not close a four-output tool brief.
        # Owner rule 2026-10-25: an H tool brief without an explicit ask owes no visual page.
        context = self.invoke('https://github.com/acme/tool')
        body = self.ktasks[0]['body']
        for output in ('разбор инструмента', 'пример или проверенная причина невозможности',
                       'вердикт и область применения', 'подтверждение доставки'):
            self.assertIn(output, body)
        self.assertIn('Captured kanban card', context)
        self.assertNotIn('/abs/page.desktop.png', context)

    def test_visual_completion_orders_render_inspect_evidence_finish_and_ledger(self):
        text = self.invoke('Объясни тему циклов с урока и нарисуй схему')
        for value in ('--record-evidence', '--check-finish', '/abs/page.desktop.png', '/abs/page.mobile.png',
                      '/abs/page.finish.json', 'keep every exact MEDIA line'):
            self.assertIn(value, text)
        self.assertLess(text.index('--record-evidence'), text.index('--check-finish'))
        context = self.invoke('Объясни тему циклов с урока и нарисуй схему')
        self.assertIn('page.finish.json', context)

    def test_visual_request_gets_a_gated_output(self):
        # Rerun 2026-10-13 F4: visual requests carried 'результат Йусуфу', so `done` never demanded PNG+HTML.
        # Owner rule 2026-10-25: in hqecea the ask must name the visual explicitly.
        context = self.invoke('Объясни тему циклов с урока и нарисуй схему')
        self.assertEqual(self.ktasks[0]['title'], 'Объясни тему циклов с урока и нарисуй схему')
        self.assertIn('Ожидаемый результат: визуал', self.ktasks[0]['body'])
        self.assertIn('page.finish.json', context)
        self.assertNotIn('--check-finish', self.invoke('Проверь сайт', turn='nonvisual'))

    def test_dates_line_resolves_tomorrow_after_midnight(self):
        # Rerun 2026-10-13 S03/Y02: just after midnight the model called today "tomorrow".
        fixed = hook.datetime(2026, 10, 13, 0, 30, tzinfo=hook.TZ)
        with patch.object(hook, 'datetime', wraps=hook.datetime) as clock:
            clock.now.return_value = fixed
            context = self.invoke('Что у меня завтра?')
        self.assertIn('Dates: today Tue 13.10; tomorrow Wed 14.10; this calendar week Mon 12.10–Sun 18.10.', context)

    def test_status_questions_read_the_current_profile_index(self):
        # Acceptance 2026-10-13 G11/H02/S12/Y08: interactive status answers used stale sources.
        (self.home / 'work').mkdir()
        (self.home / 'work/STUDIO_PLAN.md').write_text('STUDIO-INDEX-MARKER')
        self.assertIn('STUDIO-INDEX-MARKER', self.invoke('Что по проектам сейчас?'))
        self.assertNotIn('STUDIO-INDEX-MARKER', self.invoke('Переведи слово apple', turn='t2'))

    def test_file_request_requires_the_files(self):
        # Acceptance 2026-10-13 G03/H09: "send the files" was captured as a generic result.
        context = self.invoke('Скинь мне файлы презентации и docx по АКТ')
        self.assertIn('файлы во вложении', self.ktasks[0]['body'])
        self.assertIn('attach k1 /abs/file', context)

    def test_file_request_delivers_here_with_media_lines(self):
        # Rerun-2 2026-10-13 H09: replied with bare paths and asked where to send instead of attaching.
        context = self.invoke('Пришли итог из T3 вместе с картинками, видео и файлами.')
        self.assertIn('This chat is the destination', context)
        self.assertIn('MEDIA:/abs/path', context)

    def test_created_video_is_attached_in_this_chat(self):
        context = self.invoke('Сделай короткое видео с русским голосом про план учёбы.')
        self.assertIn('This chat is the destination', context)
        self.assertIn('MEDIA:/abs/path', context)

    def test_generate_document_is_attached_in_this_chat(self):
        context = self.invoke('generate a document')
        self.assertIn('This chat is the destination', context)
        self.assertIn('MEDIA:/abs/path', context)

    def test_create_file_trigger_requires_a_file_object(self):
        for text in ['Создай документ', 'Нарисуй изображение', 'Подготовь презентацию',
                     'Собери архив', 'Покажи визуал', 'make a video', 'create an image',
                     'generate a document']:
            with self.subTest(text=text):
                self.assertRegex(text, hook.FILE_ASK)
        for text in ['Сделай проверку', 'create a task', 'remake the plan']:
            with self.subTest(text=text):
                self.assertNotRegex(text, hook.FILE_ASK)

    def test_wait_until_is_not_shown_as_deadline(self):
        # Judge batch 2 2026-10-13 R07: a decision-wait time was presented as the task deadline.
        self.tasks = [dict(id='w1', title='Wait only', status='waiting', until='2026-10-14T09:00:00+05:00'),
                      dict(id='w2', title='Both', status='open', due='2026-10-20T12:00:00+05:00',
                           until='2026-10-14T09:00:00+05:00')]
        context = self.invoke('Что у меня по задачам?')
        wait_line = next(l for l in context.splitlines() if 'w1 ' in l)
        both_line = next(l for l in context.splitlines() if 'w2 ' in l)
        self.assertNotIn('due', wait_line)
        self.assertIn('follow-up 10-14 09:00', wait_line)
        self.assertIn('due 10-20 12:00', both_line)
        self.assertIn('follow-up 10-14 09:00', both_line)

    def test_authorized_owner_send_uses_existing_native_profile_route(self):
        for profile, selector in [('default', ' --profile default'), ('sqecea', ' --profile sqecea'), ('yqecea', ' --profile yqecea')]:
            with self.subTest(profile=profile):
                ledger = self.home / ('scripts/ledger' if profile == 'default' else f'profiles/{profile}/scripts/ledger')
                ledger.parent.mkdir(parents=True, exist_ok=True)
                ledger.touch()
                context = self.invoke('Отправь мне в мой Telegram тест: проверка связи.', profile=profile,
                                      session=profile, turn=profile)
                for required in (f'hermes{selector} send --list telegram --json',
                                 f'hermes{selector} config get platforms.telegram.home_channel --json',
                                 f'hermes{selector} send --to telegram:<chat_id>:<thread_id> --json',
                                 'home_channel.chat_id', 'home_channel.thread_id',
                                 'shared/profile-aliases.md', 'shared/communications',
                                 'before asking Yusuf', 'not an agent-callable tool',
                                 'local file cannot satisfy', 'accepted message ID', 'active_profile', 'bare telegram may omit the topic',
                                 'Do not invent a wait', '--receipt'):
                    self.assertIn(required, context)
                self.assertFalse(any('send' in call for call in self.calls))

    def test_request_kind_sets_the_required_proof(self):
        # Judge batch 2 2026-10-13 C03/R13/V13/L03: specific outcomes were captured as "результат Йусуфу".
        # Immediate requests become kanban cards; contract-bound work keeps the ledger gate.
        cases = [('Отправь мне в мой Telegram тест: проверка связи.', 'отправка с receipt', '--receipt', 'card'),
                 ('Открой страницу регистрации MLH и проверь, что дальше.', 'страница в браузере', 'kanban card', 'card'),
                 ('Подготовь всё для внешнего агента, чтобы он понял без доступа к моему ПК.', 'пакет файлов', '--artifact', 'ledger'),
                 ('Сверь мой список с доказательствами и скажи, что реально закрыто.', 'файл сверки', '--artifact', 'ledger')]
        for n, (text, output, flag, store) in enumerate(cases):
            with self.subTest(text=text):
                self.calls.clear()
                self.kcalls.clear()
                context = self.invoke(text, session=f's{n}', turn=f't{n}')
                if store == 'card':
                    body = next(c[c.index('--body') + 1] for c in self.kcalls if c[0] == 'create')
                    self.assertIn(output, body)
                else:
                    add = next(c for c in self.calls if c[0] == 'add')
                    self.assertIn(output, add[add.index('--outputs') + 1])
                self.assertIn(flag, context)

    def test_opportunity_stays_candidate_until_organizer_and_travel_checks(self):
        ledger = self.home / 'profiles/yqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        for n, text in enumerate(('Какая ближайшая возможность для меня?', 'Стоит идти на IEF?',
                                  'Что по поступлению за границей?')):
            context = self.invoke(text, profile='yqecea', turn=f'eligibility-{n}')
            for required in ('unverified candidate', "organizer's primary source", 'age eligibility',
                             'registration cutoff', 'ticket availability', 'location', 'cost',
                             'Saved owner facts are not organizer permission',
                             'travel time', 'gym', 'timing unverified',
                             'final answer', 'unconditional attendance recommendation'):
                self.assertIn(required, context)
        self.assertFalse(self.tasks)

    def test_admissions_reads_exact_comparison_and_existing_s_task_result(self):
        for profile in ('yqecea', 'sqecea'):
            ledger = self.home / f'profiles/{profile}/scripts/ledger'
            ledger.parent.mkdir(parents=True)
            ledger.touch()
            context = self.invoke('Что мне подтянуть для поступления в Китай или Саудовскую Аравию?',
                                  profile=profile, turn=profile)
            for required in ('exact requirement/document comparison',
                             'kanban/attachments/t_9ec64b06/readiness-brief.md',
                             't_9ec64b06', 'current status and result',
                             'official program documents', 'recorded documents',
                             'not only summaries', 'unknown admission dates stay unknown',
                             'independent Saudi shortlist', 'same S task',
                             'read-only question', 'no duplicate', 'new academic requirements'):
                self.assertIn(required, context)
        self.assertEqual([c[0] for c in self.calls], ['list', 'list'])

    def test_personal_facts_separate_due_wait_and_source_check_before_priority(self):
        ledger = self.home / 'profiles/yqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        self.tasks = [dict(id='fact1', title='Decision', status='waiting', due=None,
                           until='2026-10-14T09:00:00+05:00',
                           last_checked_at='2026-10-12T17:00:00+05:00',
                           last_update='2026-10-14T03:00:00+05:00')]
        context = self.invoke('Что мне полезно сделать сегодня?', profile='yqecea')
        line = next(line for line in context.splitlines() if 'fact1 ' in line)
        self.assertNotIn('due', line)
        self.assertIn('follow-up 10-14 09:00', line)
        self.assertIn('last-checked 2026-10-12T17:00:00+05:00', line)
        self.assertNotIn('last-checked 2026-10-14', line)
        for required in ('due, wait-until and last-checked', 'bounded current record',
                         'before prioritizing', 'newest dated entry', 'not the file prefix',
                         'MLH', 'SmartNation', "S's owning record", 'unverified',
                         'wait-until is not a deadline', 'mtime is not a fact check'):
            self.assertIn(required, context)
        self.tasks[0].pop('last_checked_at')
        context = self.invoke('Что по моим задачам?', profile='yqecea', turn='without-check')
        line = next(line for line in context.splitlines() if 'fact1 ' in line)
        self.assertNotIn('last-checked', line)

    def test_topic_questions_name_their_required_sources(self):
        # Judge batch 4 2026-10-13 H02/H03/S12/S14/S17/Y06/Y08: answers skipped the canonical sources.
        cases = [('default', 'Как сейчас дела у Oysana и проектов? Коротко.', 'PROJECTS.md'),
                 ('default', 'Что мешает продвинуть студию сегодня?', 'cron list'),
                 ('sqecea', 'Хочу начать Scratch с нуля. С чего начнём?', 'LEARNING_COACH.md'),
                 ('sqecea', 'Какое сегодня число и когда завтра удобно позаниматься? Коротко.', 'gws-account.py'),
                 ('sqecea', 'Что мне подтянуть для поступления за границей?', 'goals-admissions.md'),
                 ('yqecea', 'Какие подписки скоро спишутся и что лишнее?', 'subs.jsonl'),
                 ('yqecea', 'Что у меня сегодня важного?', 'STUDY_NEXT_LESSON.md')]
        for n, (profile, text, marker) in enumerate(cases):
            with self.subTest(text=text):
                if profile != 'default':
                    ledger = self.home / 'profiles' / profile / 'scripts' / 'ledger'
                    ledger.parent.mkdir(parents=True, exist_ok=True)
                    ledger.touch()
                context = self.invoke(text, profile=profile, session=f'q{n}', turn=f'q{n}')
                self.assertIn('Required sources', context)
                self.assertIn(marker, context)
        self.assertNotIn('Required sources', self.invoke('Переведи слово apple', session='z', turn='z'))

    def test_parallel_request_requires_dispatch_receipts(self):
        # Judge batch 4 2026-10-13 G12: "in parallel" closed with role labels and no model dispatch.
        self.invoke('Разбери мои рабочие задачи параллельно и верни итог.')
        add = next(c for c in self.calls if c[0] == 'add')
        self.assertIn('taskId каждого исполнителя', add[add.index('--outputs') + 1])

    def test_topic_facts_are_injected_not_only_named(self):
        # Judge batch 5 2026-10-13 H02/Y06: named sources were skipped; the hook now pastes their current facts.
        repo = self.home / 'repos/oynote'
        repo.mkdir(parents=True)
        (repo / 'HANDOFF.md').write_text('# OyNote\n\nТекущий статус: APK-MARKER не опубликован.\n')
        index = self.home / 'PROJECTS.md'
        index.write_text(f'- Project: OyNote | Open: `{repo}`\n- Project: Gone | Open: `{self.home}/missing`\n')
        subs = self.home / 'subs.jsonl'
        subs.write_text('{"vendor": "idhost", "amount": "1000 KZT", "payment_notice": "paid", "source_at": "2026-10-07T10:00:00+00:00", "status": "active"}\n')
        with patch.object(hook, 'PROJECTS_INDEX', index), patch.object(hook, 'SUBS_FILE', subs):
            context = self.invoke('Как сейчас дела у Oysana и проектов? Коротко.')
            self.assertIn('APK-MARKER', context)
            self.assertIn('OyNote', context)
            ledger = self.home / 'profiles/yqecea/scripts/ledger'
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.touch()
            context = self.invoke('Какие подписки скоро спишутся и что лишнее?', profile='yqecea', session='y', turn='y')
            self.assertIn('idhost', context)
            self.assertIn('2026-10-07', context)

    def test_subscription_table_keeps_receipt_and_payment_times_distinct(self):
        subs = self.home / 'subs.jsonl'
        subs.write_text('\n'.join(json.dumps(row) for row in [
            dict(vendor='charity', source_at='2026-10-12', payment_notice='paid'),
            dict(vendor='cloud', paid_at='2026-09-10', source_at='2026-10-10',
                 service_period='2026-09-10..2026-10-10', status='active',
                 note='Renewal unverified', billing_evidence='No renewal receipt')]))
        with patch.object(hook, 'SUBS_FILE', subs):
            facts = hook.subscription_facts()
        self.assertIn('| Service | Last confirmed paid date | Source/receipt time | Service period | Status | Recurrence |', facts)
        self.assertIn('| charity | unknown | 2026-10-12 | unknown | unknown | unknown |', facts)
        self.assertIn('| cloud | 2026-09-10 | 2026-10-10 | 2026-09-10..2026-10-10 | active | unknown |', facts)
        self.assertIn('Copy this script-rendered table verbatim', facts)
        self.assertIn('Renewal unverified', facts)
        self.assertIn('No renewal receipt', facts)

    def test_subscription_brief_preserves_payment_period_and_evidence(self):
        subs = self.home / 'subs.jsonl'
        records = [dict(vendor='cloud', payment_notice='paid', paid_at='2026-09-10',
                        period_start='2026-09-10', period_end='2026-10-10',
                        source_at='2026-10-10', billing_evidence='No renewal receipt',
                        note='Last confirmed payment 10.09', amount=0),
                   dict(vendor='charity', payment_notice='paid', status='unknown',
                        note='Recurrence unknown')]
        subs.write_text('\n'.join(json.dumps(row) for row in records))
        with patch.object(hook, 'SUBS_FILE', subs):
            facts = hook.subscription_facts()
        for value in ('2026-09-10', '2026-10-10', 'No renewal receipt',
                      'Last confirmed payment 10.09', 'Recurrence unknown', ' | 0 | '):
            self.assertIn(value, facts)
        self.assertIn('source_at is when the source was received, not when payment occurred', facts)
        self.assertIn('recurrence stays unknown', facts)

    def test_slot_routes_bind_actual_due_task_and_location_transitions(self):
        for profile, question in [('sqecea', 'Когда завтра удобно позаниматься?'),
                                  ('yqecea', 'Как завтра совместить колледж и зал?')]:
            ledger = self.home / 'profiles' / profile / 'scripts/ledger'
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.touch()
            tasks = [dict(id='original-scratch', title='Scratch practice', status='open', due='2026-10-14T15:30:00+05:00'),
                     dict(id='duplicate-scratch', title='Scratch practice', status='open')]
            state = self.home / 'profiles/sqecea/state'
            state.mkdir(parents=True, exist_ok=True)
            (state / 'tasks.jsonl').write_text('\n'.join(json.dumps(t) for t in tasks))
            self.tasks = tasks if profile == 'sqecea' else []
            context = self.invoke(question, profile=profile, session=profile)
            for required in ('original-scratch', '2026-10-14T15:30:00+05:00',
                             'learning-window.py --verify PLAN.json', 'different locations',
                             'travel duration', '30-minute buffer after gym',
                             'Never use an undated duplicate', 'deadline warning'):
                self.assertIn(required, context)
            self.assertIn('selected task_id=original-scratch', context)

    def test_actual_learning_verifier_rejects_original_due_despite_undated_duplicate(self):
        script = Path(__file__).resolve().parents[2] / 'profiles/sqecea/scripts/learning-window.py'
        ledger = self.home / 'tasks.jsonl'
        ledger.write_text('\n'.join(json.dumps(t) for t in [
            dict(id='original-scratch', status='open', due='2098-10-14T15:30:00+05:00'),
            dict(id='duplicate-scratch', status='open')]))
        calendar = self.home / 'calendar-fixture.json'
        calendar.write_text('[]')
        plan = self.home / 'PLAN.json'
        plan.write_text(json.dumps(dict(windows=[dict(task_id='original-scratch',
            start='2098-10-14T16:30:00+05:00', end='2098-10-14T17:15:00+05:00')],
            busy=[dict(label='gym', start='2098-10-14T14:00:00+05:00', end='2098-10-14T16:00:00+05:00') ])))
        result = subprocess.run([sys.executable, '-B', str(script), '--verify', str(plan), '--ledger', str(ledger), '--calendar-fixture', str(calendar)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'after_deadline')

    def test_scratch_reuses_original_task_and_requires_clickable_resources_and_session(self):
        ledger = self.home / 'profiles/sqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        self.tasks = [dict(id='duplicate', title='Scratch practice', status='open'),
                      dict(id='original', title='TUMO Scratch practice', status='open',
                           due='2026-10-14T15:30:00+05:00')]
        context = self.invoke('Подготовь Scratch с нуля', profile='sqecea')
        self.assertFalse(any(call[0] == 'add' for call in self.calls))
        self.assertIn('Reuse existing Scratch task original', context)
        for required in ('https://scratch.mit.edu/starter-projects', 'clickable', 'one dated verified session',
                         'learning-window.py --verify PLAN.json', 'position-reset block', '1400 characters'):
            self.assertIn(required, context)

    def test_scratch_due_original_wins_even_when_undated_copy_matches_exact_request(self):
        ledger = self.home / 'profiles/sqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True)
        ledger.touch()
        text = 'Подготовь Scratch с нуля'
        self.tasks = [dict(id='duplicate', title=text, status='open', dest='origin',
                           source='[session:' + hook.digest('s1') + ']'),
                      dict(id='original', title='Scratch practice', status='open', due='2026-10-14T15:30:00+05:00')]
        self.invoke(text, profile='sqecea')
        updates = [call[1] for call in self.calls if call[0] == 'update']
        self.assertEqual(updates, ['original'])

    def test_all_learning_slot_routes_require_verified_fallbacks(self):
        for question in ('Хочу начать Scratch с нуля. С чего начнём?',
                         'Когда завтра удобно позаниматься?'):
            with self.subTest(question=question):
                route = hook.topic_sources('sqecea', question)
                self.assertIn('learning-window.py --verify PLAN.json', route)
                self.assertIn('every proposed slot, including fallbacks', route)
                self.assertIn('30-minute buffer', route)
                self.assertIn('every calendar event and known lesson', route)
                self.assertIn('task_id', route)
                self.assertIn('success=true', route)
                self.assertIn('unknown deadline', route)

    def test_studio_route_requires_delivery_and_prepared_comparison(self):
        route = hook.topic_sources('default', 'Что мешает продвинуть студию сегодня?')
        for required in ('job_id', 'execution_id', 'delivery_outcome', 'deliveries.db',
                         'benefit/time/dependency/evidence comparison', 'supplied dated excerpts'):
            self.assertIn(required, route)
        self.assertIn('configured destination or completed cron execution is not delivery proof', route)

    def test_morning_review_requires_its_own_execution_delivery_record(self):
        for question in ('Что у меня сегодня важного?', 'Дошёл утренний обзор?'):
            with self.subTest(question=question):
                route = hook.topic_sources('yqecea', question)
                for required in ('morning briefing', 'job_id', 'execution_id', 'SQLite mode=ro',
                                 'delivery record by that execution_id', 'delivery_outcome',
                                 'configured destination or completed cron execution is not delivery proof',
                                 'Do not substitute another execution'):
                    self.assertIn(required, route)

    def test_completion_routes_capture_requirements_and_name_manifest_commands(self):
        cases = [('Проверь код параллельно субагентами.', 'delegations'),
                 ('Сверь мой список просьб.', 'reconciliation'),
                 ('Сравни мой список просьб.', 'reconciliation'),
                 ('Проверь код Sol 6.1 medium и Luna max.', 'delegations'),
                 ('Сравни код параллельно субагентами.', 'delegations'),
                 ('Верни файлы T3 run 6.', 'return_bundle'),
                 ('Подготовь handoff для внешнего агента.', 'package_dependencies'),
                 ('Объясни handoff для внешнего агента.', 'package_dependencies'),
                 ('Открой страницу и подготовь handoff для внешнего агента.', 'package_dependencies')]
        for n, (text, contract) in enumerate(cases):
            with self.subTest(text=text):
                self.calls.clear()
                context = self.invoke(text, session=f'contract{n}', turn=f'contract{n}')
                add = next(call for call in self.calls if call[0] == 'add')
                self.assertIn('--completion-requirements', add)
                requirements = Path(add[add.index('--completion-requirements') + 1])
                self.assertIn(contract, json.loads(requirements.read_text())['completion_contracts'])
                close = context.split('close it with one call, `', 1)[1].split('`', 1)[0]
                self.assertIn('--completion-manifest /abs/completion.json', close)
                self.assertIn('update task', context)
                self.assertIn('--completion-requirements /abs/requirements.json', context)
                self.assertIn('Captured with `', context)

    def test_real_hook_commands_enforce_handoff_completion_manifest(self):
        script = Path(__file__).with_name('proactive.py')
        def cli(*args):
            return subprocess.run([sys.executable, '-B', str(script), 'ledger', *args],
                                  env={**os.environ, 'HERMES_HOME': str(self.home)},
                                  text=True, capture_output=True, timeout=10)
        def real_ledger(path, *args):
            result = cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        payload = dict(profile='default', session_id='fixture',
                       extra=dict(user_message='Пришли файлы handoff для внешнего агента.', turn_id='fixture'))
        out = io.StringIO()
        with patch.object(hook, 'HOME', self.home), patch.object(hook, 'run', real_ledger), \
                patch.object(hook, 'run_kanban', self.kanban), \
                patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
            hook.main()
        context = json.loads(out.getvalue())['context']
        self.assertIn('Captured with `', context)
        add = shlex.split(context.split('Captured with `', 1)[1].split('`', 1)[0])[1:]
        created = cli(*add)
        self.assertEqual(created.returncode, 0, created.stderr)
        task = created.stdout.strip()
        self.assertEqual(json.loads(cli('show', task).stdout)['completion_contracts'], ['package_dependencies'])
        package = self.home / 'package.zip'
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('handoff.md', 'Source and instructions for the external agent')
        document = self.home / 'handoff.md'
        document.write_text('Open the attached canonical ZIP')
        manifest = self.home / 'completion.json'
        close_text = context.split('close it with one call, `', 1)[1].split('`', 1)[0]
        close = shlex.split(close_text)[1:]
        close[1] = task
        close = [part.replace('<proof>', 'verified').replace('/abs/each-sent-file', str(document))
                 .replace('/abs/package.zip', str(package)).replace('/abs/completion.json', str(manifest)) for part in close]
        without_manifest = close[:close.index('--completion-manifest')]
        rejected = cli(*without_manifest)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('--completion-manifest', rejected.stderr)
        self.assertEqual(json.loads(cli('show', task).stdout)['status'], 'open')
        manifest.write_text(json.dumps({'task_id': task, 'package_dependencies': [
            {'path': str(package), 'sha256': hashlib.sha256(package.read_bytes()).hexdigest()}]}))
        accepted = cli(*close)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(cli('show', task).stdout)['status'], 'done')
        self.assertIn('MEDIA:' + str(package), accepted.stdout)
        self.assertIn('MEDIA:' + str(manifest), accepted.stdout)

    def test_external_handoff_requires_verified_zip_and_final_media(self):
        for n, text in enumerate(('Подготовь всё для внешнего агента без доступа к моему ПК.',
                                  'Пришли файлы handoff для внешнего агента без доступа к моему ПК.',
                                  'Объясни handoff для внешнего агента без доступа к моему ПК.')):
            with self.subTest(text=text):
                self.calls.clear()
                context = self.invoke(text, session=f'handoff{n}', turn=f'handoff{n}')
                add = next(c for c in self.calls if c[0] == 'add')
                self.assertIn('проверенный ZIP', add[add.index('--outputs') + 1])
                self.assertIn('--artifact /abs/package.zip', context)
                self.assertIn('MEDIA:/abs/package.zip', context)
                self.assertIn('verify its entries against the manifest', context)
                self.assertIn('Do not defer the ZIP', context)

    def test_list_route_prevents_false_due_and_finishes_artifact_before_short_summary(self):
        context = self.invoke('Сверь мой список просьб')
        for required in ('Never invent a due time', 'update ID --clear-due',
                         'Complete and validate the per-ID artifact before the summary',
                         '1400 characters', 'point to the artifact', 'recorded_claim',
                         'sampled=false/unverified/open', 'source-linked observations'):
            self.assertIn(required, context)
        add = next(call for call in self.calls if call[0] == 'add')
        self.assertNotIn('--due', add)

    def test_request_list_reconciles_each_id_before_phone_summary(self):
        for n, question in enumerate(('Что из моего списка ещё не сделано?',
                                      'Сверь мой список с доказательствами и скажи, что реально закрыто.')):
            with self.subTest(question=question):
                context = self.invoke(question, session=f'list{n}', turn=f'list{n}')
                self.assertIn('one ID/evidence/remaining-step reconciliation', context)
                self.assertIn('before the phone summary', context)
                self.assertIn('every request ID', context)
                self.assertIn('missing evidence stays open', context)
                self.assertIn('Counts or categories alone', context)

    def test_browser_handoff_close_keeps_zip_proof_and_artifact(self):
        context = self.invoke('Открой страницу регистрации и подготовь handoff для внешнего агента.')
        close = context.split('close it with one call, `', 1)[1].split('`', 1)[0]
        self.assertIn('страница в браузере=route=', close)
        self.assertIn('проверенный ZIP для внешнего агента=<proof>', close)
        self.assertIn('--artifact /abs/package.zip', close)

    def test_handoff_retry_preserves_required_zip_without_duplicate_capture(self):
        text = 'Подготовь всё для внешнего агента без доступа к моему ПК.'
        first = self.invoke(text)
        retry = self.invoke(text)
        self.assertEqual(first, retry)
        self.assertEqual(len([call for call in self.calls if call[0] == 'add']), 1)
        self.assertIn('MEDIA:/abs/package.zip', retry)
        add = next(call for call in self.calls if call[0] == 'add')
        self.assertIn('проверенный ZIP', add[add.index('--outputs') + 1])

    def test_real_learning_verifier_rejects_overlap_buffer_and_late_work(self):
        verifier = Path(__file__).resolve().parents[2] / 'profiles/sqecea/scripts/learning-window.py'
        ledger = self.home / 'fixture-tasks.jsonl'
        ledger.write_text(json.dumps(dict(id='practice', status='open', due='2030-10-14T19:00:00+05:00')))
        calendar = self.home / 'calendar-fixture.json'
        calendar.write_text('[]')
        plan = self.home / 'fixture-plan.json'
        for start, end, expected in (('15:00', '16:00', 'overlaps_busy:Gym'),
                                     ('16:15', '17:15', 'inside_30min_buffer:Gym'),
                                     ('16:30', '17:30', None),
                                     ('18:30', '19:30', 'after_deadline')):
            with self.subTest(start=start):
                plan.write_text(json.dumps(dict(windows=[dict(task_id='practice',
                    start=f'2030-10-14T{start}:00+05:00', end=f'2030-10-14T{end}:00+05:00')],
                    busy=[dict(label='Gym', start='2030-10-14T14:00:00+05:00', end='2030-10-14T16:00:00+05:00')])))
                result = subprocess.run([sys.executable, str(verifier), '--verify', str(plan), '--ledger', str(ledger), '--calendar-fixture', str(calendar)],
                                        capture_output=True, text=True, timeout=5)
                outcome = json.loads(result.stdout)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(outcome.get('error'), expected)
                self.assertFalse(outcome['success'])
                if expected is None:
                    self.assertTrue(outcome['fixture_passed'])
                    self.assertEqual(outcome['calendar_source'], 'fixture')
                    self.assertEqual(outcome['status'], 'fixture-calendar')

    def prepare_profile_fixture_ledgers(self):
        for profile in ['sqecea', 'yqecea']:
            ledger = self.home / 'profiles' / profile / 'scripts/ledger'
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.touch()

    def test_konspekt_delivery_uses_content_contract_not_work_report(self):
        self.prepare_profile_fixture_ledgers()
        for profile in ['default', 'sqecea', 'yqecea']:
            with self.subTest(profile=profile):
                context = self.invoke('сделай конспект по теме массивы', profile=profile)
                self.assertIn('Конспект standard (Yusuf)', context)
                self.assertIn('Тақырып атауы\n\n1. Сұрақ-тақырып?', context)
                self.assertIn('Each file appears once as a MEDIA line', context)
                self.assertIn('internal IDs and traces stay in the ledger', context)
                self.assertIn('Follow the user', context)
                self.assertIn('Agent names: H/h = default/hqecea', context)
                self.assertNotIn('Final message: the result in the first sentence', context)
                self.assertNotIn('code goes in fences', context)
                self.assertNotIn('restating the goal', context)

    def test_ordinary_report_keeps_complete_original_delivery_line(self):
        self.prepare_profile_fixture_ledgers()
        for profile in ['default', 'sqecea', 'yqecea']:
            with self.subTest(profile=profile):
                context = self.invoke('Проверь сайт после обновления и верни результат. ' * 9, profile=profile)
                self.assertIn('- Final message: the result in the first sentence, then only what Yusuf needs to act. Each file appears once as a MEDIA line; internal IDs and traces stay in the ledger; code goes in fences.', context)
                self.assertIn('restating the goal', context)

    def test_long_public_konspekt_has_title_first_contract_without_generic_opener(self):
        self.prepare_profile_fixture_ledgers()
        message = 'Маған информатика сабағына "Операциялық жүйенің негізгі міндеттері" тақырыбы бойынша 100–120 сөздік конспект жаз. Нөмірленген тармақтармен, қазақша, мәтінді бірден осында жібер.\n\nТестовый запрос. Не читай личную переписку и не создавай cron/отправки. Если для проверки нужен файл, записывай только в /home/yqecea/coding-projects/hermes-pro/.artifacts/resume-20261014/activation-1/model-cases/S-konspekt-after-count/in. Финальный конспект дай текстом здесь.\n'
        self.assertGreater(len(message), 350)
        for profile in ['default', 'sqecea', 'yqecea']:
            with self.subTest(profile=profile):
                context = self.invoke(message, profile=profile)
                self.assertIn('Конспект standard (Yusuf)', context)
                self.assertIn('Тақырып атауы\n\n1. Сұрақ-тақырып?', context)
                self.assertIn('plain text in the chat', context)
                self.assertNotIn('restating the goal', context)

    def test_ordinary_long_request_keeps_generic_opener_for_all_profiles(self):
        self.prepare_profile_fixture_ledgers()
        message = 'Нужно разобраться с сайтом после обновления и проверить исправление перед публикацией. ' * 6
        for profile in ['default', 'sqecea', 'yqecea']:
            with self.subTest(profile=profile):
                self.assertIn('restating the goal', self.invoke(message, profile=profile))

    def test_short_konspekt_keeps_existing_contract_for_all_profiles(self):
        self.prepare_profile_fixture_ledgers()
        for profile in ['default', 'sqecea', 'yqecea']:
            with self.subTest(profile=profile):
                context = self.invoke('сделай конспект по теме массивы', profile=profile)
                self.assertIn('Конспект standard (Yusuf)', context)
                self.assertIn('Тақырып атауы', context)
                self.assertNotIn('restating the goal', context)

    def test_long_or_dictated_request_opens_with_a_goal_restatement(self):
        # Matt Pocock / Karpathy analysis 2026-10-13: a long dictated message is misread silently; restating the goal surfaces it.
        long = 'Слушай, короче, надо разобраться с сайтом, там после обновления что-то поехало, ' * 6
        self.assertIn('restating the goal', self.invoke(long))
        self.assertNotIn('restating the goal', self.invoke('Сделай отчёт по подпискам', turn='t2'))

    def test_studio_injects_separate_current_blocker_and_next_sections_before_contract(self):
        repo = self.home / 'repo'
        repo.mkdir()
        (repo / 'HANDOFF.md').write_text('# Handoff\n\nResume from [STATUS.md](STATUS.md).\n')
        (repo / 'STATUS.md').write_text('# Status 2026-10-12\n\n## Packaging\n\n' +
            ('Checks remain pending for packaging. ' * 60) +
            '\n\n## Signal\n\nMicrophone is silent: SIGNAL-MARKER.\n\n' +
            '## Next actions\n\n1. Capture speech in another application: CAPTURE-MARKER.\n2. Build and install the candidate.\n')
        index = self.home / 'PROJECTS.md'
        index.write_text(f'- Project: Active | Open: `{repo}`\n')
        with patch.object(hook, 'PROJECTS_INDEX', index):
            context = self.invoke('Что мешает студии?', session='studio')
        for expected in ('Dated excerpts, not verified current blockers:', 'Next-action evidence:', 'SIGNAL-MARKER',
                         'CAPTURE-MARKER', '2026-10-12', 'checkout freshness not checked', 'STATUS.md#signal',
                         'label time estimates as estimates'):
            self.assertIn(expected, context)
        self.assertLess(context.index('SIGNAL-MARKER'), context.index('Execution contract'))
        self.assertLess(context.index('CAPTURE-MARKER'), context.index('Execution contract'))

    def test_h03_answer_first_bounded_ranked_brief(self):
        names = ['Noise', 'Archive', 'Studio A', 'Studio B', 'Studio C', 'Studio D', 'Target']
        rows = []
        for n, name in enumerate(names):
            repo = self.home / name
            repo.mkdir()
            body = '# Status 2026-10-15\n\n## Scope\n\n' + 'Pending scope. ' * 200 + '\nScope ends.\n\n## Next\n\nNext step: inspect fixture.\n'
            (repo / 'STATUS.md').write_text(body)
            os.utime(repo / 'STATUS.md', (100000 + n, 100000 + n))
            rows.append(f'- Project: {name} | Open: `{repo}`\n')
        hook.PROJECTS_INDEX.write_text(''.join(rows))
        (self.home / 'work').mkdir()
        (self.home / 'work/STUDIO_PLAN.md').write_text('| Область | Результат |\n|---|---|\n' +
            ''.join(f'| {name} | milestone |\n' for name in names[2:6]))
        context = self.invoke('Что мешает продвинуть студию сегодня?')
        self.assertLessEqual(context.count('  Dated excerpts, not verified current blockers:'), 3)
        self.assertEqual(context.count('  Dated excerpts, not verified current blockers:'), 3)
        for name in ('Studio D', 'Studio C', 'Studio B'):
            self.assertIn(f'- {name}:', context)
            section = context.split(f'- {name}:', 1)[1].split('\n- ', 1)[0]
            self.assertIn('  Dated excerpts', section)
        self.assertLess(context.index('- Studio D:'), context.index('- Studio C:'))
        self.assertIn('Answer first within the current turn budget', context)
        self.assertIn('ledger task', context)
        self.assertIn('unverified', context)
        self.assertIn('label time estimates as estimates', context)
        pointers = re.findall(r'\[([^\]]+STATUS.md[^\]]*)\] read before claiming', context)
        self.assertTrue(pointers)
        for pointer in pointers:
            self.assertRegex(pointer, r':5-6$')
        self.assertIn('read_file offset=5 limit=2', context)
        self.assertNotIn('using current project documents before the phone summary', context)
        self.assertLessEqual(len(context), 20000)
        named = self.invoke('Что мешает проекту Target сегодня?', turn='t2')
        self.assertIn('  Dated excerpts', named.split('- Target:', 1)[1].split('\n- ', 1)[0])
        self.assertLess(named.index('- Target:'), named.index('- Studio D:'))

    def test_project_paragraph_keeps_active_v2_and_retired_v1_scope(self):
        repo = self.home / 'repo'
        repo.mkdir()
        paragraph = ('Review the active V2 target from this repository. '
                     'Oysana V1 has been deleted; no local V1 checkout remains.')
        (repo / 'STATUS.md').write_text('# Status 2026-10-12\n\n## Scope\n\n' + paragraph + '\n')
        hook.PROJECTS_INDEX.write_text(f'- Project: Active | Open: `{repo}`\n')
        facts = hook.project_facts()
        self.assertTrue(paragraph in facts or
                        (f'{repo}/STATUS.md:5' in facts and 'read before claiming' in facts), facts)

    def test_project_long_paragraph_requires_read_instead_of_clipping(self):
        repo = self.home / 'repo'
        repo.mkdir()
        paragraph = 'Microphone pending historical blocker. ' + 'Scope detail. ' * 180 + 'V2 is active today.'
        (repo / 'STATUS.md').write_text('# Status 2026-10-12\n\n## Scope\n\n' + paragraph + '\n')
        hook.PROJECTS_INDEX.write_text(f'- Project: Active | Open: `{repo}`\n')
        facts = hook.project_facts()
        self.assertNotIn('Microphone pending historical blocker.', facts)
        self.assertIn(f'{repo}/STATUS.md:5', facts)
        self.assertIn('read before claiming', facts)

    def test_project_budget_stops_between_paragraphs_and_requires_next_read(self):
        repo = self.home / 'repo'
        repo.mkdir()
        first = 'Pending check. ' + 'A' * 1000 + ' First scope ends.'
        second = 'Pending check. ' + 'B' * 1000 + ' Second scope ends.'
        (repo / 'STATUS.md').write_text('# Status\n\n## Scope\n\n' + first + '\n\n' + second + '\n')
        hook.PROJECTS_INDEX.write_text(f'- Project: Active | Open: `{repo}`\n')
        facts = hook.project_facts()
        self.assertIn(first, facts)
        self.assertNotIn('BBB', facts)
        self.assertIn(f'{repo}/STATUS.md:7', facts)
        excerpt = facts.split('Dated excerpts, not verified current blockers: ', 1)[1].split('\n', 1)[0]
        self.assertLessEqual(len(excerpt), 1800)
        self.assertIn('read before claiming', excerpt)

    def test_long_next_action_and_fallback_require_source_read(self):
        repo = self.home / 'repo'
        repo.mkdir()
        hook.PROJECTS_INDEX.write_text(f'- Project: Active | Open: `{repo}`\n')
        for doc, heading, paragraph in (
            ('STATUS.md', 'Next actions', 'Next step: ' + 'scope detail ' * 150),
            ('AGENTS.md', 'Workflow', 'When dictation produces no text, ' + 'scope detail ' * 150)):
            with self.subTest(doc=doc):
                (repo / 'STATUS.md').write_text('# Status\n\nNo excerpt.\n')
                (repo / doc).write_text('# Source\n\n## ' + heading + '\n\n' + paragraph + '\n')
                facts = hook.project_facts()
                action = facts.split('Next-action evidence: ', 1)[1]
                self.assertIn(f'{repo}/{doc}:5', action)
                self.assertIn('read before claiming', action)
                self.assertNotIn('scope detail', action)
                self.assertLessEqual(len(action), 1400)

    def test_project_read_limit_cannot_split_a_source_paragraph(self):
        repo = self.home / 'repo'
        repo.mkdir()
        paragraph = 'Pending historical check. ' + 'Context. ' * 23000 + 'V2 is active.'
        (repo / 'STATUS.md').write_text('# Status\n\n' + paragraph + '\n')
        hook.PROJECTS_INDEX.write_text(f'- Project: Active | Open: `{repo}`\n')
        facts = hook.project_facts()
        self.assertNotIn('Pending historical check.', facts)
        self.assertIn(f'{repo}/STATUS.md:3', facts)
        self.assertIn('read before claiming', facts)

    def test_project_facts_keep_signal_context_and_russian_next_sequence(self):
        repo = self.home / 'repo'
        repo.mkdir()
        (repo / 'STATUS.md').write_text('# Status\n\nТекущий статус на 12 октября 2026.\n\n' +
            '## Current qualification\n\nAfter transcript timeouts the microphone was silent. Direct ALSA capture stayed near its noise floor while the owner spoke.\n\n' +
            '## Состояние проверки\n\nПоследовательность закрытия: исправить расхождения → пройти проверки → собрать APK → проверить установленный APK.\n')
        (repo / 'AGENTS.md').write_text('## Required workflow\n\nWhen dictation produces no text, check the selected microphone and a real speech signal in another application before changing provider code.\n')
        index = self.home / 'PROJECTS.md'
        index.write_text(f'- Project: Active | Open: `{repo}`\n')
        with patch.object(hook, 'PROJECTS_INDEX', index):
            facts = hook.project_facts()
        for value in ('Direct ALSA capture stayed near its noise floor', '12 октября 2026',
                      'Next-action evidence:', 'Последовательность закрытия:', 'пройти проверки → собрать APK'):
            self.assertIn(value, facts)
        (repo / 'STATUS.md').write_text('# Status\n\nPhysical run 2026-09-26: the microphone was silent.\n')
        with patch.object(hook, 'PROJECTS_INDEX', index):
            facts = hook.project_facts()
        self.assertIn('document date not stated', facts)
        self.assertIn('AGENTS.md', facts)
        self.assertIn('check the selected microphone and a real speech signal', facts)

    def test_project_blocker_past_the_opening_is_injected(self):
        # Judge batch 6 2026-10-13 H02: the VoiceBridge blocker (silent microphone) sat past the 350-char opening and was lost.
        repo = self.home / 'repos/vb'
        repo.mkdir(parents=True)
        filler = 'Packaging and docs were refreshed for the tester kit. ' * 12
        (repo / 'STATUS.md').write_text(f'# VB\n\n{filler}\n\nThe owner\'s USB\nmicrophone was SILENT-MARKER in another application.\n')
        index = self.home / 'PROJECTS.md'
        index.write_text(f'- Project: VB | Open: `{repo}`\n')
        with patch.object(hook, 'PROJECTS_INDEX', index):
            facts = hook.project_facts()
        self.assertIn("The owner's USB microphone was SILENT-MARKER", facts)
        self.assertIn('checkout freshness not checked', facts)
        # A handoff that defers to another file is followed to that file's blocker.
        (repo / 'STATUS.md').unlink()
        (repo / 'HANDOFF.md').write_text('# VB\n\n> Resume from [OPEN.md](OPEN.md).\n')
        (repo / 'OPEN.md').write_text('# Open\n\nMigration check is still pending: MIGRATION-MARKER.\n')
        with patch.object(hook, 'PROJECTS_INDEX', index):
            self.assertIn('MIGRATION-MARKER', hook.project_facts())

    def test_rules_table_names_only_existing_tests(self):
        # RULES.md pairs each rule with its enforcer; a renamed or deleted test must not leave a stale row.
        here = Path(__file__).resolve().parent
        support = here / '_fixture_support' if (here / '_fixture_support').is_dir() else here.parent
        rules = (support / 'RULES.md').read_text()
        sources = ''.join(f.read_text() for f in [*here.glob('test_*.py'),
                                                   *Path.home().joinpath('.hermes/plugins').glob('*/tests/test_*.py'),
                                                   *Path.home().joinpath('.hermes/profiles').glob('*/scripts/test_*.py')])
        named = set(re.findall(r'`(test_[a-z_]+)`', rules))
        self.assertTrue(named)
        self.assertEqual({t for t in named if f'def {t}(' not in sources}, set())

    def test_index_carries_wiki_lint_warnings(self):
        # Karpathy LLM Wiki lint: stale or missing index pages are named next to the index itself.
        (self.home / 'work').mkdir()
        (self.home / 'work/STUDIO_PLAN.md').write_text('plan')
        (self.home / 'state').mkdir()
        (self.home / 'state/wiki-lint.json').write_text(json.dumps(
            {'stale': ['/x/OLD_PAGE.md'], 'missing': ['GONE_PAGE.md'], 'stale_days': 21}))
        context = self.invoke('Что по проектам сейчас?')
        self.assertIn('OLD_PAGE.md', context)
        self.assertIn('GONE_PAGE.md', context)

    def test_contract_forbids_offer_endings(self):
        # Acceptance 2026-10-13 G02/H03/S14/S17: replies ended with "Делать?" instead of the prepared brief.
        self.assertIn('never ends by offering work you could already do', self.invoke('Что по проектам?'))

    def test_quick_day_plan_stays_text(self):
        # Acceptance 2026-10-13 Y13: "fit the gym into tomorrow" built a visual and hit the time budget.
        self.assertIn('quick practical answer', self.invoke('Как вписать зал в завтрашний день?'))

    def test_judge_batch3_rules_reach_the_brief(self):
        # Acceptance rerun 2026-10-13: S14 overlapped a calendar event, Y06 trusted old billing, G12 claimed
        # undispatched parallel work, G02/H03 asked Yusuf to pick instead of doing the favoured option.
        (self.home / 'work').mkdir(exist_ok=True)
        (self.home / 'work/STUDIO_PLAN.md').write_text('plan')
        h = self.invoke('Что по проектам?')
        self.assertIn('overlaps any calendar event is not free', h)
        self.assertIn('pick the one the index or his goals favour', h)
        self.assertIn('T3 task receipt (taskId) per child', h)
        ledger = self.home / 'profiles/yqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.touch()
        self.assertIn('never proof of a current charge', self.invoke('Какие подписки?', turn='t2', profile='yqecea'))

    def test_my_list_points_to_the_requests_file(self):
        # Acceptance 2026-10-13 G01: "finish my list" was answered from ledger counts.
        context = self.invoke('Что из моего списка ещё не сделано?')
        self.assertIn(str(self.home / 'shared/REQUESTS.md'), context)

    def test_t3_wait_uses_the_real_ledger_syntax(self):
        context = self.invoke('Что ты делаешь?')
        self.assertIn("ledger wait ID --kind internal --on 'T3 run <runId>' --until", context)
        self.assertNotIn('thread/run in --note', context)

    def test_t3_contract_binds_kanban_cards_and_waits_until_terminal(self):
        # Owner correction 2026-10-29: a card parked "waiting on T3" is never woken —
        # kanban fires subscriptions on card terminal events, not T3 thread state.
        context = self.invoke('Что ты делаешь?')
        for required in ('T3-Thread:', 'T3-State: waiting', 't3_thread_wait',
                         'never wakes the owner', 'kanban --board default comment ID',
                         'T3-State: ingested', 'pending-T3 scan rescues it on the next turn'):
            self.assertIn(required, context)

    def test_pending_t3_card_surfaces_ingestion_directive(self):
        self.ktasks = [dict(id='t_00000001', title='OM acceptance waits T3', status='blocked',
                            assignee='default',
                            body='WAIT for this existing run T3 mcp:dd35945e-fefa-405c-aaae-46d00b87770e '
                                 'dispatched run:thread:mcp%3Add35945e:ordinal:1.'),
                       dict(id='t_00000002', title='Ordinary blocked card', status='blocked',
                            assignee='default', body='parked on owner input, no thread'),
                       dict(id='t_00000003', title='Done card', status='done',
                            assignee='default',
                            body='--- task-meta ---\nT3-Thread: mcp:eeeeeeee-0000-0000-0000-000000000000\n')]
        context = self.invoke('Что ты делаешь?')
        self.assertIn('Pending T3 ingestion', context)
        self.assertIn('t_00000001', context)
        self.assertIn('mcp:dd35945e-fefa-405c-aaae-46d00b87770e', context)
        self.assertIn('run:thread:mcp%3Add35945e:ordinal:1', context)
        self.assertIn('t3_thread_read', context)
        self.assertIn('unblock', context)
        ingestion = context.split('Pending T3 ingestion', 1)[1]
        self.assertNotIn('t_00000002', ingestion)
        self.assertNotIn('t_00000003', ingestion)

    def test_return_brief_orders_verbatim_proof_block_once(self):
        """Owner rule 07.11.2026: the ingestion brief carries exactly one line
        ordering the T3 report's PROOF block to the owner verbatim."""
        self.ktasks = [dict(id='t_00000009', title='Waits T3', status='blocked', assignee='default',
                            body='--- task-meta ---\nT3-Thread: mcp:ffffffff-0000-0000-0000-000000000000\n')]
        context = self.invoke('Что ты делаешь?')
        self.assertEqual(context.count('PROOF из отчёта T3'), 1)
        self.assertIn('дословно', context.split('PROOF из отчёта T3', 1)[1].splitlines()[0])
        direct = hook.t3_ingestion_brief(
            [{'id': 't_x', 'title': 'x', 'thread': 'mcp:t', 'run': 'r'}], limit=4)
        self.assertEqual(direct.count('PROOF из отчёта T3'), 1)

    def test_ingested_and_other_status_cards_stay_quiet(self):
        self.ktasks = [dict(id='t_00000004', title='Ingested', status='blocked', assignee='default',
                            body='--- task-meta ---\nT3-Thread: mcp:aaaaaaaa-0000-0000-0000-000000000000\nT3-State: ingested\n'),
                       dict(id='t_00000005', title='Running worker owns the wait', status='running',
                            assignee='default',
                            body='--- task-meta ---\nT3-Thread: mcp:bbbbbbbb-0000-0000-0000-000000000000\n'),
                       dict(id='t_00000006', title='Other assignee', status='blocked', assignee='sqecea',
                            body='WAIT for this existing run T3 mcp:cccccccc-0000-0000-0000-000000000000')]
        # pending_t3_cards asks kanban only for assignee=default rows; the sqecea row
        # arrives only because the fixture returns the whole seeded list, so the filter
        # must still exclude it explicitly.
        context = self.invoke('Что ты делаешь?')
        self.assertNotIn('Pending T3 ingestion', context)
        for thread in ('mcp:aaaaaaaa', 'mcp:bbbbbbbb', 'mcp:cccccccc'):
            self.assertNotIn(thread, context)

    def test_owner_holds_scheduled_and_active_waits_never_prompt_auto_unblock(self):
        binding='T3-Thread: mcp:aaaaaaaa-0000-0000-0000-000000000000\nT3-State: waiting\n'
        self.ktasks=[dict(id='t_owner',title='Owner decision',status='blocked',assignee='default',block_kind='needs_input',body=binding),
                     dict(id='t_timed',title='Native time owner',status='scheduled',assignee='default',body=binding),
                     dict(id='t_active',title='Live native worker',status='blocked',assignee='default',current_run_id=77,body=binding)]
        context=self.invoke('Что ты делаешь?')
        self.assertNotIn('Pending T3 ingestion',context)
        self.assertNotIn('mcp:aaaaaaaa',context)

    def test_kanban_failure_emits_no_directive_and_never_breaks_the_turn(self):
        self.kfail = True
        context = self.invoke('Что ты делаешь?')
        self.assertNotIn('Pending T3 ingestion', context)
        self.assertIn('T3-Thread:', context)  # the contract text still teaches the binding

    def test_pending_t3_scan_is_h_only(self):
        self.ktasks = [dict(id='t_00000007', title='S parked', status='blocked', assignee='sqecea',
                            body='WAIT for this existing run T3 mcp:dddddddd-0000-0000-0000-000000000000')]
        ledger = self.home / 'profiles/sqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.touch()
        context = self.invoke('Что ты делаешь?', profile='sqecea')
        self.assertNotIn('Pending T3 ingestion', context)
        # The board listing for S covers S's own cards only — the ingestion
        # scan itself stays H-only through the profile gate around pending_t3_cards.
        lists = [call for call in self.kcalls if call[0] == 'list']
        self.assertTrue(all('sqecea' in call for call in lists), lists)

    def test_delegation_route_requires_child_tools_for_every_original_role(self):
        context = self.invoke('Раздай задачи: Sol 6.1 medium, Luna max, Sol 6.1 xhigh')
        self.assertNotIn('call T3 delegate_task', context)
        for required in ('t3_thread_send', 't3_thread_wait', 't3_thread_read',
                         'dispatch.tool=t3_thread_send', 'returned.tool=t3_thread_read',
                         'stable clientRequestId', 'existing coordinator', 'original requested roles'):
            self.assertIn(required, context)

    def test_t3_return_looks_up_run_before_asking_for_ids(self):
        context = self.invoke('Верни итог и файлы T3 run в этот чат')
        lookup = context.split('T3 return lookup:', 1)[1]
        order = ['stored IDs in the ledger/WORK_TRACKER', 't3_thread_list', 't3_thread_search', 't3_thread_read']
        self.assertEqual(sorted(order, key=lookup.index), order)
        self.assertIn('empty or ambiguous', lookup)
        self.assertIn('say which lookups ran', lookup)

    def test_external_demo_dispatch_uses_t3_route(self):
        for text in ('Authorized external implementation; demo dispatch only, no deployment.',
                     'Use the external agent for this authorized demo.',
                     'Разрешаю внешнюю реализацию демо без деплоя.',
                     'Разрешаю демо-диспетчеризацию внешнему агенту.'):
            with self.subTest(text=text):
                context = self.invoke(text, turn=text)
                self.assertIn('External implementation route:', context)
                for tool in ('t3_thread_send', 't3_thread_wait', 't3_thread_read'):
                    self.assertIn(tool, context)

    def test_explicit_parallel_models_route_to_t3_catalog_and_receipts(self):
        context = self.invoke('Раздай три задачи параллельно: Sol medium делает, Luna ищет, Sol xhigh ревьюит')
        duties = context.split('Work duties:', 1)[1]
        for required in ['explicit subagents', 'parallel tasks', 'named models/efforts',
                         'first open skill t3-work-bridge', "T3 agent's delegate_task",
                         'orchestrator_capabilities', 'target.providerInstanceId',
                         'target.model', 'target.options', 'requested models/efforts verbatim',
                         'taskId', 't3_thread_send', 't3_thread_wait', 't3_thread_read']:
            self.assertIn(required, duties)

    def test_owner_delegation_rule_small_self_medium_t3(self):
        # Owner rule 2026-10-25: H does small edits itself; only medium-and-larger coding goes to T3.
        context = self.invoke('Исправь опечатку в README проекта')
        duties = context.split('Work duties:', 1)[1]
        for required in ('small code or doc edits yourself', 'medium-and-larger coding', 'visible T3 thread'):
            self.assertIn(required, duties)
        self.assertNotIn('Send the goal into a visible T3 thread', duties)
        self.assertIn('does research, planning, ops and small edits itself', context)

    def test_existing_turns_receive_all_profile_aliases(self):
        context = self.invoke('Кто такие h s y?')
        for alias in ['H/h = default/hqecea', 'S/s = sqecea', 'Y/y = yqecea']:
            self.assertIn(alias, context)
        self.assertEqual(self.tasks, [])

    def test_h_shared_repository_question_carries_brief_outputs(self):
        self.invoke('https://github.com/emilkowalski/skills\nвсе скиллы крутые, но у меня вроде break ui нету?')
        self.assertEqual(len(self.ktasks), 1)
        body = self.ktasks[0]['body']
        self.assertIn('разбор инструмента', body)
        self.assertIn('подтверждение доставки', body)
        self.assertIn('без внедрения', body)

    def test_repository_intake_bounds_and_repeated_turn(self):
        post = 'Tool collection\n' + ('Details about techniques. ' * 190) + '\ngithub.com/cth9191/animate'
        self.invoke(post)
        self.invoke(post)
        self.assertEqual(len(self.ktasks), 1)
        self.assertIn('github.com/cth9191/animate', self.ktasks[0]['body'])
        for text in ['Только запомни https://github.com/example/tool',
                     'Не изучай https://github.com/example/tool',
                     'Всё хватит https://github.com/example/tool',
                     'Остановись https://github.com/example/tool',
                     'Не продолжай https://github.com/example/tool',
                     'https://github.com/example/tool\nВсё хватит',
                     'Introducing @scriptc/threads\nНе продолжай',
                     'Например: https://github.com/example/tool',
                     'Вот пример: https://github.com/example/tool',
                     'For example https://github.com/example/tool',
                     '> https://github.com/example/tool',
                     '```\nhttps://github.com/example/tool\n```',
                     'Как открыть https://example.org?',
                     'https://notgithub.com/example/tool']:
            with self.subTest(text=text):
                self.assertFalse(hook.is_tool_brief(text, 'default'))
        self.assertTrue(hook.is_tool_brief('https://github.com/example/tool', 'sqecea'))
        self.assertTrue(hook.is_tool_brief('https://github.com/example/tool', 'yqecea'))

    def test_named_package_announcement_carries_source_without_url(self):
        self.invoke('Introducing @𝚜𝚌𝚛𝚒𝚙𝚝𝚌/𝚝𝚑𝚛𝚎𝚊𝚍𝚜\nZero-copy object sharing for regular TypeScript')
        self.assertEqual(len(self.ktasks), 1)
        self.assertIn('@𝚜𝚌𝚛𝚒𝚙𝚝𝚌/𝚝𝚑𝚛𝚎𝚊𝚍𝚜', self.ktasks[0]['body'])
        self.assertFalse(hook.is_tool_brief('Here is @alice/messages from our conversation', 'default'))

    def test_questions_examples_and_stops(self):
        for text in ['Можешь проверить сайт?', 'Can you fix this?', 'Как настроить сайт?',
                     'Мне нужно понять, как настроить сайт', 'Я хочу сравнить варианты',
                     'Например: проверь сайт сейчас', '"проверь сайт сейчас"',
                     'Вот пример: «сделай это сейчас»', '```\nfix the site now\n```',
                     '> проверь сайт сейчас', 'Остановись', 'всё хватит',
                     'Не отправь это сообщение', 'Не надо проверять сайт']:
            with self.subTest(text=text):
                self.assertFalse(hook.is_request(text))

    def test_explicit_request_after_code(self):
        self.assertTrue(hook.is_request('```\nexample only\n```\nИсправь ошибку'))

    def test_no_invented_deadline(self):
        self.invoke('Проверь доступность моего сайта')
        create = next(c for c in self.kcalls if c[0] == 'create')
        self.assertNotIn('--due', create)
        self.assertNotIn('due', self.ktasks[0])

    def test_same_turn_does_not_repeat(self):
        self.invoke('Проверь доступность моего сайта')
        self.invoke('Проверь доступность моего сайта')
        self.assertEqual(sum(c[0] == 'create' for c in self.kcalls), 1)
        self.assertEqual(sum(c[0] == 'comment' for c in self.kcalls), 0)

    def test_turn_id_is_scoped_to_session(self):
        text = 'Gateway message origin (JSON data): {"platform":"telegram","chat_id":"chat"}\nDo not guess a reply destination when these fields are insufficient.\nПроверь доступность моего сайта'
        self.invoke(text)
        self.invoke(text, session='s2')
        self.assertEqual(sum(c[0] == 'comment' for c in self.kcalls), 1)
        self.assertEqual(sum(c[0] == 'create' for c in self.kcalls), 1)

    def test_destination_scopes_fuzzy_matching(self):
        self.tasks = [dict(id='existing', title='Проверь доступность моего сайта',
                           dest='telegram:another:topic', status='open')]
        self.invoke('Проверь доступность моего сайта')
        self.assertTrue(any(c[0] == 'create' for c in self.kcalls))
        self.assertFalse(any(c[0] == 'comment' for c in self.kcalls))

    def test_unknown_origins_share_the_work_card(self):
        # The board is the single work list: the same request asked again is a repeat
        # note on the one open card, wherever it was first captured.
        self.invoke('Проверь доступность моего сайта')
        self.invoke('Проверь доступность моего сайта', session='s2')
        self.assertEqual(len(self.ktasks), 1)
        self.assertEqual(sum(c[0] == 'comment' for c in self.kcalls), 1)

    def test_distinct_arguments_create_distinct_tasks(self):
        for first, second in [('Создай отчёт за май', 'Создай отчёт за июнь'),
                              ('Найди 3 статьи', 'Найди 5 статей'),
                              ('Найди "apple"', 'Найди "orange"'),
                              ('Проверь README.md', 'Проверь readme.md')]:
            with self.subTest(first=first, second=second):
                self.ktasks = []
                self.invoke(first, turn='first-' + first)
                self.invoke(second, turn='second-' + second)
                self.assertEqual(len(self.ktasks), 2)
                self.assertEqual(self.ktasks[0]['title'], first)

    def test_long_similar_requests_do_not_merge(self):
        prefix = 'Найди ' + 'полезные статьи ' * 12
        self.invoke(prefix + 'для первого проекта', turn='first')
        self.invoke(prefix + 'для второго проекта', turn='second')
        self.assertEqual(len(self.ktasks), 2)

    def test_profile_bounds(self):
        with patch.object(hook, 'HOME', self.home):
            self.assertEqual(hook.ledger_path('hqecea'), self.home / 'scripts/ledger')
            for profile in ['../other', '/tmp/other', 'unknown']:
                with self.subTest(profile=profile):
                    self.assertEqual(self.invoke('Проверь сайт', profile=profile), '')
        self.assertEqual(self.calls, [])

    def test_existing_state_preserved(self):
        path = self.home / 'state/turn-brief.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'repeats': {'old': 4}, 'extension': {'keep': True}}))
        self.invoke('Проверь доступность моего сайта')
        state = json.loads(path.read_text())
        self.assertEqual(state['extension'], {'keep': True})
        self.assertEqual(state['repeats']['old'], 4)

    def test_questions_do_not_create_state(self):
        self.invoke('Как настроить сайт?')
        self.assertFalse((self.home / 'state').exists())

    def test_atomic_failure_preserves_prior_file(self):
        path = self.home / 'state/turn-brief.json'
        path.parent.mkdir()
        path.write_text('{"keep": true}')
        with patch.object(hook.os, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                hook.write_state(path, {'keep': False})
        self.assertEqual(json.loads(path.read_text()), {'keep': True})
        self.assertEqual(list(path.parent.glob('.turn-brief-*')), [])

    def test_corrupt_state_is_preserved(self):
        path = self.home / 'state/turn-brief.json'
        path.parent.mkdir()
        path.write_text('not JSON')
        with self.assertRaises(ValueError):
            self.invoke('Проверь сайт')
        self.assertEqual(path.read_text(), 'not JSON')
        self.assertEqual(self.calls, [])

    def test_concurrent_calls_capture_once(self):
        # The subprocess sets HOME on the imported module, never on a live profile.
        worker = (
            'import importlib.util,sys; from pathlib import Path; '
            f's=importlib.util.spec_from_file_location("hook", {str(Path(__file__).with_name("turn-brief.py"))!r}); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            f'm.HOME=Path({str(self.home)!r}); m.main()'
        )
        ledger = self.home / 'scripts/ledger'
        ledger.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n'
                          'p=Path(__file__).parent.parent/"calls.jsonl"\n'
                          'with p.open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                          'print("[]" if sys.argv[1]=="list" else "task1")\n')
        ledger.chmod(0o700)
        payload = json.dumps(dict(profile='default', session_id='s',
                                  extra=dict(turn_id='t', user_message='Проверь доступность моего сайта')))
        child_env = dict(os.environ, HERMES_KANBAN_CLI=write_kanban_shim(self.home))
        processes = [subprocess.Popen([sys.executable, '-B', '-c', worker], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      env=child_env) for _ in range(4)]
        for process in processes:
            output, error = process.communicate(payload, timeout=10)
            self.assertEqual(process.returncode, 0, error)
            json.loads(output)
        calls = [json.loads(line) for line in (self.home / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum(c[0] == 'add' for c in calls), 0)
        self.assertEqual(sum(c[0] == 'update' for c in calls), 0)
        self.assertEqual(sum(c[0] == 'create' for c in kanban_calls(self.home)), 1)

    def test_immediate_request_lands_on_the_board_not_the_ledger(self):
        # The board is the single work list: a plain ask becomes a card; the ledger sees nothing.
        context = self.invoke('Проверь сайт')
        self.assertEqual(len(self.ktasks), 1)
        self.assertEqual(self.ktasks[0]['status'], 'blocked')
        self.assertEqual(self.ktasks[0]['assignee'], 'default')
        self.assertIn('Ожидаемый результат:', self.ktasks[0]['body'])
        self.assertIn('Следующий шаг:', self.ktasks[0]['body'])
        self.assertEqual(self.tasks, [])
        self.assertIn('Captured kanban card', context)
        self.assertNotIn('Captured ledger task', context)

    def test_ordinary_talk_captures_nothing_anywhere(self):
        for text in ('привет', 'спасибо', 'Сколько будет 2 + 2?', 'Как дела?', 'Расскажи анекдот'):
            with self.subTest(text=text):
                self.invoke(text, turn='talk-' + text)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.ktasks, [])

    def test_time_bound_and_contract_work_stays_in_the_ledger(self):
        for text in ('нужно сделать ревью PR на этой неделе',
                     'Разбери мои рабочие задачи параллельно и верни итог.',
                     'Подготовь всё для внешнего агента, чтобы он понял без доступа к моему ПК.'):
            with self.subTest(text=text):
                self.calls.clear(); self.kcalls.clear()
                context = self.invoke(text, session='bound-' + text[:12], turn='bound-' + text[:12])
                self.assertTrue(any(c[0] == 'add' for c in self.calls))
                self.assertFalse(any(c[0] == 'create' for c in self.kcalls))
                self.assertIn('Captured ledger task', context)

    def test_kanban_refusal_captures_nothing_and_keeps_the_turn(self):
        self.kfail = True
        context = self.invoke('Проверь сайт')
        self.assertNotIn('Captured kanban card', context)
        self.assertNotIn('Captured ledger task', context)
        self.assertEqual(self.tasks, [])
        self.assertIn('Execution contract', context)


    def test_invalid_payload_fails_open(self):
        process = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('turn-brief.py'))],
                                 input='not JSON', capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout), {'context': ''})

    def test_tool_link_brief_has_executable_visual_and_adoption_workflow(self):
        # Owner rule 2026-10-25: the H tool brief keeps its workflow; the visual page needs an explicit ask.
        context = self.invoke('Что даёт https://github.com/vojtaholik/good-css?')
        for required in ['visual-output', 'references/tool-brief.md', 'TOOLS.md', 'scratch folder',
                         'adopt-tool', 'dry-run']:
            self.assertIn(required, context)
        self.assertEqual(len(self.ktasks), 1)

    def test_contract_covers_eval_gaps(self):
        contract = self.invoke('Объясни тему https://github.com/example/tool и нарисуй схему')
        for needed in ['owning profile', 'inspect desktop AND mobile PNGs', 'ledger wait ID --kind internal', 'first sentence',
                       'scratch folder (always allowed)', 'hand it to H']:
            self.assertIn(needed, contract)
        self.assertNotIn('visual-output/scripts/finish.sh', contract)
        for question in ['Что задали на завтра?', 'Какие подписки спишутся на неделе?']:
            self.assertFalse(hook.is_request(question))

    def test_contract_has_scope_and_no_forced_work(self):
        contract = self.invoke('https://github.com/example/tool')
        for removed in ['at least three', 'one line proposing', 'not with text',
                        'one script installs it for every harness']:
            self.assertNotIn(removed, contract)
        for needed in ['Hermes-only', 'stop', 'profile', 'question']:
            self.assertIn(needed, contract)

    def test_natural_work_requests_are_captured(self):
        # Enterprise G7 2026-10-25: natural phrasing does not always open with the imperative.
        for text in ('в проекте /tmp/h-bench-o добавь в README.md строку про лицензию MIT',
                     'нужно сделать ревью PR на этой неделе',
                     'ты меня бесишь, /tmp/h-bench-o/run.py опять падает — почини',
                     'создай NOTES.md в проекте',
                     'в понедельник обнови зависимости проекта',
                     'надо бы проверить почему падает сборка',
                     'пришли мне отчёт по проекту',
                     'запусти тесты в проекте oysana',
                     'посмотри что с логами',
                     'добавь в README строку про лицензию MIT'):
            with self.subTest(text=text):
                self.assertTrue(hook.is_request(text))

    def test_greetings_and_questions_stay_uncaptured(self):
        # Enterprise G7 2026-10-25: seven greetings/questions, zero ledger captures.
        for text in ('привет', 'привет, как дела', 'спасибо', 'что задали на завтра?',
                     'сколько места занимают мои кодинг-проекты в ~/coding-projects',
                     'мне нужно понять, как настроить сайт',
                     'я хочу сравнить варианты'):
            with self.subTest(text=text):
                self.assertFalse(hook.is_request(text))

    def test_owner_promise_before_a_verb_is_not_a_request(self):
        # Enterprise WP6 2026-10-26: the ten-word window captured Yusuf's own hedged promises.
        for text in ('Постараюсь завтра отправить документы', 'Попробую завтра сделать отчёт',
                     'завтра отправлю; отправь копию', 'Завтра отправлю отчёт; отправь копию'):
            with self.subTest(text=text):
                self.assertFalse(hook.is_request(text))
        for text in ('Пожалуйста, составь отчёт по продажам', 'Юсуф, подготовь список дел'):
            with self.subTest(text=text):
                self.assertTrue(hook.is_request(text))

    def test_h_reply_contract_demands_three_line_head(self):
        # Enterprise G6: line 1 done/not done, line 2 how to see it, line 3 one decision or «ничего».
        context = self.invoke('Проверь сайт')
        for marker in ('the first three short lines', 'done or not done',
                       'how Yusuf sees or checks it', 'one decision left to him or «ничего»',
                       'Never answer only «Задача закрыта.»'):
            self.assertIn(marker, context)
        self.assertNotIn('the first three short lines', self.invoke('привет', turn='greeting'))

    def test_captured_task_close_precedes_the_final_reply(self):
        # Enterprise G7: a task finished in this turn is closed before the answer is written.
        context = self.invoke('Исправь опечатку в проекте')
        captured = context.split('Captured kanban card', 1)[1]
        self.assertIn('close it once', captured)
        self.assertIn('before you write the answer', captured)

    def test_future_horizon_request_stays_open(self):
        # A request for later is captured but must not be closed or started in this turn.
        context = self.invoke('нужно сделать ревью PR на этой неделе')
        captured = context.split('Captured ledger task', 1)[1]
        self.assertIn('for later', captured)
        self.assertNotIn('Finished in this reply', captured)

    def test_h_visual_only_when_explicitly_requested(self):
        # Enterprise WP1: an H visual page only on an explicit ask in the same message.
        for text in ('Объясни тему циклов с урока', 'Составь план на неделю',
                     'Почему небо голубое?', 'Сравни supabase и firebase'):
            with self.subTest(text=text):
                self.assertFalse(hook.visual_required(text, 'default'))
        for text in ('Нарисуй схему DNS', 'Визуализируй архитектуру',
                     'Объясни тему циклов и нарисуй схему', 'Покажи визуал'):
            with self.subTest(text=text):
                self.assertTrue(hook.visual_required(text, 'default'))
        self.assertFalse(hook.visual_required(
            'сделай в /tmp/h-bench-o маленькое приложение-счётчик на html с кнопками плюс/минус и тестами',
            'default'))
        # Study and personal profiles keep the existing topic-explanation behaviour.
        self.assertTrue(hook.visual_required('Объясни тему массивы', 'sqecea'))
        self.assertTrue(hook.visual_required('Объясни тему массивы', 'yqecea'))

    def test_live_battery_g7_prompts_are_captured(self):
        # The exact G7 battery wording (enterprise/evidence/WP1/LIVE-SCENARIOS.md).
        for text in ('в проекте /tmp/h-ent/wp1-x добавь в README.md строку про лицензию MIT',
                     'нужно сделать ревью PR на этой неделе',
                     '/tmp/h-ent/wp1-x/run.py опять падает, почини',
                     'у нас /tmp/h-ent/wp1-x/notes.txt с ошибками, исправь опечатки',
                     'надо проверить тесты в /tmp/h-ent/wp1-x',
                     'помоги добавить сумму чисел в /tmp/h-ent/wp1-x/calc.py',
                     'сделай короткое описание /tmp/h-ent/wp1-x/app.py',
                     'проверь что всё работает в /tmp/h-ent/wp1-x',
                     'подготовь список проблем в /tmp/h-ent/wp1-x/review.py',
                     'обнови заголовок в /tmp/h-ent/wp1-x/README.md'):
            with self.subTest(text=text):
                self.assertTrue(hook.is_request(text))
        for text in ('привет', 'спасибо', 'как дела', 'что такое MCP',
                     'сколько будет два плюс два', 'как работает git commit',
                     'чем Python отличается от JavaScript'):
            with self.subTest(text=text):
                self.assertFalse(hook.is_request(text))

    def test_attributed_and_negated_speech_is_not_a_request(self):
        # Someone else's instruction quoted as text, and a negated ask, own no ledger task.
        for text in ('он написал сделай отчёт', 'мне сказали проверить сайт',
                     'она попросила удалить файл', 'не нужно сделать ревью',
                     'не нужно проверить сайт'):
            with self.subTest(text=text):
                self.assertFalse(hook.is_request(text))
        # The same verbs as direct asks still capture.
        for text in ('сделай отчёт', 'проверь сайт'):
            with self.subTest(text=text):
                self.assertTrue(hook.is_request(text))

    def test_future_subject_is_not_a_deferral(self):
        # A future time inside the work's subject is not scheduling the work itself.
        for text in ('почини сейчас расчёт прогноза на завтра',
                     'подготовь план на следующей неделе',
                     'сделай отчёт на следующей неделе'):
            with self.subTest(text=text):
                context = self.invoke(text, turn=text)
                captured = context.split('Captured kanban card', 1)[1]
                self.assertIn('close it once', captured)
                self.assertNotIn('for later', captured)
        context = self.invoke('нужно сделать ревью PR на этой неделе', turn='week')
        captured = context.split('Captured ledger task', 1)[1]
        self.assertIn('for later', captured)

    def test_na_horizon_dates_the_deliverable_not_the_work(self):
        # "на <horizon>" dates the artifact; a bare adverb or an explicit cue still schedules.
        for text, want in (
            ('создай в /tmp/x/p9/todo.md список из трёх дел на завтра: молоко, звонок маме, полить цветы', False),
            ('составь список дел на завтра', False),
            ('подготовь отчёт на завтра', False),
            ('сделай план на завтра', False),
            ('собери меню на неделю', False),
            ('завтра в 10 напомни позвонить в банк', True),
            ('напомни завтра позвонить маме', True),
            ('в понедельник проверь, прошли ли тесты', True),
        ):
            with self.subTest(text=text):
                self.assertEqual(want, hook.deferred_request(text))

    def test_h_tool_link_without_visual_ask_has_no_page_requirement(self):
        # WP1(d): an H tool brief keeps its workflow but owes no visual page unless asked.
        context = self.invoke('Что даёт https://github.com/acme/tool?')
        self.assertIn('references/tool-brief.md', context)
        self.assertIn('scratch folder', context)
        self.assertNotIn('mandatory primary method', context)
        self.assertNotIn('--check-finish', context)
        self.assertNotIn('/abs/page.desktop.png', context)
        # The same link in a study profile keeps the visual requirement.
        ledger = self.home / 'profiles/sqecea/scripts/ledger'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.touch()
        context = self.invoke('Что даёт https://github.com/acme/tool?', profile='sqecea', turn='s1')
        self.assertIn('--check-finish', context)

    def test_t3_bridge_skill_keeps_thresholds_and_honest_failure(self):
        # Enterprise G5: small stays with H, medium/design/review goes to T3, failures are named.
        here = Path(__file__).resolve()
        skill = here.parents[2] / 't3-work-bridge' / 'SKILL.md'
        if not skill.is_file():
            skill = Path.home() / '.hermes/skills/t3-work-bridge/SKILL.md'
        text = skill.read_text()
        self.assertIn('up to two files', text)
        self.assertIn('three or more files', text)
        self.assertNotIn('touches more than one file', text)
        self.assertIn('A written brief is not a sent task.', text)
        self.assertIn('Confirm its actual run or acknowledgment and owner before reporting delegation', text)


class KonspektHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.turn = 0
        self.study = self.home / 'profiles/sqecea'
        ledger = self.study / 'scripts/ledger'
        ledger.parent.mkdir(parents=True)
        ledger.write_text('#!/bin/sh\nexec ' + shlex.join([
            sys.executable, '-B', str(Path(__file__).with_name('proactive.py')),
            '--home', str(self.study), 'ledger']) + ' "$@"\n')
        ledger.chmod(0o700)

    def invoke(self, message):
        worker = (
            'import importlib.util,sys; from pathlib import Path; '
            f's=importlib.util.spec_from_file_location("hook", {str(Path(__file__).with_name("turn-brief.py"))!r}); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            f'm.HOME=Path({str(self.home)!r}); m.main()'
        )
        self.turn += 1
        payload = dict(profile='sqecea', session_id='fixture',
                       extra=dict(turn_id=str(self.turn), user_message=message))
        env = {key: value for key, value in os.environ.items() if not key.startswith('HERMES_')}
        env.setdefault('HERMES_KANBAN_CLI', write_kanban_shim(self.home))
        result = subprocess.run([sys.executable, '-B', '-c', worker], input=json.dumps(payload),
                                capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['context']
        return context, kanban_cards(self.home)

    def assert_standard(self, context):
        for required in ['Конспект standard (Yusuf)', 'Kazakh', '100–120 words',
                         'plain text in the chat', 'a visual or file never replaces it',
                         '1. Сұрақ-тақырып?', '- пункт;', '- соңғы пункт.',
                         'python3 ~/.hermes/profiles/sqecea/scripts/konspekt-check.py FILE']:
            self.assertIn(required, context)

    def test_konspekt_requires_chat_text_without_visual_rule(self):
        context, cards = self.invoke('сделай конспект по теме массивы')
        self.assert_standard(context)
        self.assertNotIn('open skill `visual-output`', context)
        self.assertNotIn('Keep prose to captions', context)
        self.assertIn('Ожидаемый результат: конспект текстом в чат', cards[-1]['body'])

    def test_konspekt_with_visual_keeps_both_outputs(self):
        context, cards = self.invoke('конспект и визуально по Python PostgreSQL')
        self.assert_standard(context)
        self.assertIn('open skill `visual-output`', context)
        self.assertIn('then still send the конспект text', context)
        self.assertIn('Ожидаемый результат: конспект текстом в чат;визуал', cards[-1]['body'])

    def test_konspekt_case_and_forms_require_text(self):
        for message in ['сделай КОНСПЕКТ по теме массивы', 'напиши конспекты по теме массивы',
                        'объясни тему в формате конспекта']:
            with self.subTest(message=message):
                context, cards = self.invoke(message)
                self.assert_standard(context)
                self.assertNotIn('Keep prose to captions', context)
                self.assertIn('Ожидаемый результат: конспект текстом в чат', cards[-1]['body'])

    def test_explicit_visual_words_keep_chat_text(self):
        for visual in ['визуал', 'визуально', 'нарисуй', 'схема', 'HTML', 'png', 'страница']:
            with self.subTest(visual=visual):
                context, cards = self.invoke('сделай конспект и ' + visual)
                self.assert_standard(context)
                self.assertIn('open skill `visual-output`', context)
                self.assertIn('Ожидаемый результат: конспект текстом в чат;визуал', cards[-1]['body'])

    def test_topic_name_and_text_only_scope_do_not_request_visual(self):
        for message in ('сделай конспект по теме HTML', 'сделай конспект без визуала',
                        'сделай конспект только текстом по теме визуальные редакторы'):
            with self.subTest(message=message):
                context, cards = self.invoke(message)
                self.assert_standard(context)
                self.assertNotIn('mandatory primary method', context)
                self.assertNotIn('--check-finish', context)
                self.assertIn('Ожидаемый результат: конспект текстом в чат', cards[-1]['body'])

    def test_topic_explanation_keeps_existing_visual_behavior(self):
        context, cards = self.invoke('объясни тему массивы')
        self.assertIn('open skill `visual-output`', context)
        self.assertIn('Keep prose to captions', context)
        self.assertNotIn('Конспект standard (Yusuf)', context)
        self.assertIn('Ожидаемый результат: визуал', cards[-1]['body'])



ORIGIN_ENV_KEYS = ('HERMES_SESSION_SOURCE', 'HERMES_KANBAN_TASK')
STATE_FILE = 'state/turn-brief.json'


class InternalOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'scripts').mkdir()
        (self.home / 'scripts/ledger').touch()
        for name, path in [('HOME', self.home), ('PROJECTS_INDEX', self.home / 'PROJECTS.md'),
                           ('SUBS_FILE', self.home / 'subs.jsonl')]:
            fixture = patch.object(hook, name, path)
            fixture.start()
            self.addCleanup(fixture.stop)
        self.calls = []
        self.tasks = []
        self.ktasks = []
        self.kcalls = []

    def kanban(self, *args):
        self.kcalls.append(args)
        if getattr(self, 'kfail', None):
            return False, "kanban: refused (fixture)"
        if args[0] == 'list':
            rows = self.ktasks
            if '--assignee' in args:
                rows = [r for r in rows if r.get('assignee', 'default') == args[args.index('--assignee') + 1]]
            return True, json.dumps(rows)
        if args[0] == 'create':
            card_id = 'k' + str(len(self.ktasks) + 1)
            card = dict(id=card_id, title=args[1],
                        body=args[args.index('--body') + 1],
                        status='blocked', created_by='turn-brief')
            for flag in ('--assignee',):
                if flag in args:
                    card['assignee'] = args[args.index(flag) + 1]
            self.ktasks.append(card)
            return True, json.dumps({'id': card_id})
        if args[0] == 'comment':
            card = next((c for c in self.ktasks if c['id'] == args[1]), None)
            if card is None:
                return False, 'unknown card'
            card.setdefault('comments', []).append(args[2])
            return True, ""
        return True, ""

    def ledger(self, path, *args):
        self.calls.append(args)
        if args[0] == 'list':
            return json.dumps(self.tasks)
        if args[0] == 'add':
            task_id = 'task' + str(len(self.tasks) + 1)
            self.tasks.append(dict(id=task_id, title=args[args.index('--title') + 1],
                                   dest=args[args.index('--dest') + 1],
                                   source=args[args.index('--source') + 1], status='open'))
            return task_id
        return args[1]

    def session_row(self, source=None, created_source=None, session_key=None,
                    parent_session_id=None, session='s1', profile='default'):
        home = self.home if profile in ('', 'default', 'hqecea') else self.home / 'profiles' / profile
        conn = sqlite3.connect(home / 'state.db')
        conn.execute('CREATE TABLE IF NOT EXISTS sessions '
                     '(id TEXT PRIMARY KEY, source TEXT, created_source TEXT, '
                     'session_key TEXT, parent_session_id TEXT)')
        conn.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)',
                     (session, source, created_source, session_key, parent_session_id))
        conn.commit()
        conn.close()

    def invoke(self, text, session='s1', turn='t1', profile='default', platform=None,
               parent_session_id=None, env=None):
        extra = dict(user_message=text, turn_id=turn)
        if platform is not None:
            extra['platform'] = platform
        payload = dict(profile=profile, session_id=session, extra=extra)
        if parent_session_id is not None:
            payload['parent_session_id'] = parent_session_id
        out = io.StringIO()
        with patch.dict(os.environ, env or {}) as environ, \
                patch.object(hook, 'HOME', self.home), patch.object(hook, 'run', self.ledger), \
                patch.object(hook, 'run_kanban', self.kanban), \
                patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(out):
            for key in ORIGIN_ENV_KEYS:
                if key not in (env or {}):
                    environ.pop(key, None)
            hook.main()
        return json.loads(out.getvalue())['context']

    def added(self):
        return [c for c in self.calls if c[0] == 'add']

    def added_cards(self):
        return [c for c in self.kcalls if c[0] == 'create']

    def test_internal_subagent_prompt_captures_no_owner_task(self):
        context = self.invoke('Нарисуй схему архитектуры и пришли отчёт',
                              platform='subagent', parent_session_id='parent9')
        self.assertEqual(self.added(), [])
        self.assertEqual(self.added_cards(), [])
        self.assertNotIn('Captured', context)
        self.assertIn('Internal turn', context)

    def test_internal_visual_prompt_locks_no_task_contract(self):
        context = self.invoke('Объясни тему циклов и нарисуй схему',
                              platform='subagent', parent_session_id='parent9')
        self.assertEqual(self.added(), [])
        self.assertEqual(self.added_cards(), [])
        self.assertFalse(any('визуал' in arg or 'visual-output' in arg
                             for call in self.calls for arg in call), self.calls)
        self.assertNotIn('Captured', context)
        self.assertFalse((self.home / STATE_FILE).exists())

    def test_owner_cli_capture_still_lands(self):
        context = self.invoke('Проверь сайт', platform='cli')
        self.assertEqual(len(self.added_cards()), 1)
        self.assertEqual(len(self.ktasks), 1)
        self.assertIn('k1', context)

    def test_owner_telegram_capture_still_lands(self):
        self.invoke('Проверь сайт', platform='telegram', session='tg1')
        self.assertEqual(len(self.added_cards()), 1)

    def test_kanban_worker_env_captures_no_duplicate(self):
        context = self.invoke('Проверь сайт', platform='cli',
                              env={'HERMES_SESSION_SOURCE': 'kanban',
                                   'HERMES_KANBAN_TASK': 't_fixture'})
        self.assertEqual(self.added(), [])
        self.assertEqual(self.added_cards(), [])
        self.assertFalse((self.home / STATE_FILE).exists())
        self.assertIn('kanban', context)

    def test_owner_compressed_session_with_parent_still_captures(self):
        self.session_row(source='cli', parent_session_id='parent9')
        context = self.invoke('Проверь сайт', platform='cli', parent_session_id='parent9')
        self.assertEqual(len(self.added_cards()), 1)
        self.assertIn('k1', context)

    def test_repeated_internal_prompts_leave_state_untouched(self):
        for turn in ('t1', 't2'):
            self.invoke('Нарисуй схему и пришли отчёт', platform='subagent',
                        parent_session_id='p', turn=turn)
        self.assertEqual([c[0] for c in self.calls], ['list', 'list'])
        self.assertFalse((self.home / STATE_FILE).exists())

    def test_session_row_marks_internal_when_payload_is_silent(self):
        self.session_row(source='subagent')
        context = self.invoke('Проверь сайт', platform='cli', session='s1')
        self.assertEqual(self.added(), [])
        self.assertIn('Internal turn', context)

    def test_unknown_session_source_stays_owner_capable(self):
        self.session_row(source='unknown')
        self.invoke('Проверь сайт', platform='cli')
        self.assertEqual(len(self.added_cards()), 1)

    def test_no_session_row_stays_owner_capable(self):
        self.invoke('Проверь сайт', platform='cli', session='never-recorded')
        self.assertEqual(len(self.added_cards()), 1)

    def test_owner_api_and_recovered_surfaces_still_capture(self):
        for source in ('api_server', 'recovered'):
            with self.subTest(source=source):
                self.tasks=[];self.calls=[];self.ktasks=[];self.kcalls=[]
                self.session_row(source=source, session=source)
                self.invoke('Проверь сайт', platform=source, session=source)
                self.assertEqual(len(self.added_cards()),1)

    def test_internal_long_code_task_does_not_force_visual_format(self):
        (self.home/'profiles/sqecea/scripts').mkdir(parents=True)
        (self.home/'profiles/sqecea/scripts/ledger').touch()
        context=self.invoke('Подготовь программу C# и проверь её. '+('Сохрани исходник и результат исполнения. '*15),
                            profile='sqecea',platform='subagent')
        self.assertEqual(self.added(),[])
        self.assertNotIn('Keep prose to captions',context)
        self.assertNotIn('Before delivering a visual',context)

    def test_internal_turn_does_not_inherit_unrelated_due_owner_work(self):
        self.tasks=[{'id':'other','title':'UNRELATED_OWNER_TASK','status':'open',
                     'due':'2020-01-01T10:00:00+05:00','next':'unrelated','outputs':[]}]
        context=self.invoke('Проверь исходный код',platform='subagent')
        self.assertNotIn('UNRELATED_OWNER_TASK',context)
        self.assertEqual(self.added(),[])

    def test_internal_boundary_and_owner_capture_for_all_profiles(self):
        for profile in ('default','sqecea','yqecea'):
            with self.subTest(profile=profile):
                home=self.home if profile=='default' else self.home/'profiles'/profile
                (home/'scripts').mkdir(parents=True,exist_ok=True)
                (home/'scripts/ledger').touch()
                self.tasks=[];self.calls=[];self.ktasks=[];self.kcalls=[]
                self.invoke('Проверь исходный код',profile=profile,platform='subagent')
                self.assertEqual(self.added(),[])
                self.assertEqual(self.added_cards(),[])
                self.invoke('Проверь исходный код',session='owner',profile=profile,platform='cli')
                self.assertEqual(len(self.added_cards()),1)
                assignee = {'default':'default','sqecea':'sqecea','yqecea':'yqecea'}[profile]
                self.assertEqual(self.ktasks[0]['assignee'], assignee)

    def test_internal_worker_keeps_t3_guidance_without_scanning_other_cards(self):
        with patch.object(hook, 'pending_t3_cards', return_value=[
                dict(id='t_unrelated', title='UNRELATED_OWNER_KANBAN', thread='thread:other')]) as scan:
            context=self.invoke('Проверь исходный код',platform='subagent')
        scan.assert_not_called()
        self.assertNotIn('UNRELATED_OWNER_KANBAN',context)
        self.assertIn('After handing work to T3',context)

    def test_internal_continue_never_guesses_legacy_owner_task(self):
        self.tasks=[dict(id='legacy1',title='UNRELATED_LEGACY_OWNER',status='open',outputs=['visual png'])]
        context=self.invoke('Продолжи проверять исходный код',platform='subagent')
        self.assertNotIn('UNRELATED_LEGACY_OWNER',context)
        self.assertNotIn('Before delivering a visual',context)

    def test_owner_continue_preserves_legacy_task_contract(self):
        self.tasks=[dict(id='legacy1',title='LEGACY_OWNER',status='open',outputs=['visual png'])]
        context=self.invoke('Продолжи проверять исходный код',platform='cli')
        self.assertIn('LEGACY_OWNER',context)
        self.assertIn('Before delivering a visual',context)

    def test_internal_explicit_task_keeps_its_contract(self):
        self.tasks=[dict(id='legacy1',title='EXPLICIT_TASK',status='open',outputs=['visual png'])]
        context=self.invoke('Продолжи legacy1',platform='subagent')
        self.assertIn('EXPLICIT_TASK',context)
        self.assertIn('Before delivering a visual',context)


if __name__ == '__main__':
    unittest.main()


class TopicTaskVisibilityTests(unittest.TestCase):
    """Real stdin hook + native ledger, using the existing size fixture harness."""
    @staticmethod
    def normalize_fixture_paths(value):
        root = re.escape(tempfile.gettempdir().rstrip(os.sep))
        value = re.sub(r'\b\d\d:\d\d (?=Asia/Almaty)', 'HH:MM ', value)
        return re.sub(root + r'/tmp[^/ ;:)]+', '<fixture-home>', value)

    def test_fixture_path_normalization_preserves_contract_differences(self):
        root = tempfile.gettempdir().rstrip(os.sep)
        left = f'{root}/tmpfirst/profiles/sqecea/scripts/ledger mode=safe'
        right = f'{root}/tmpsecond/profiles/sqecea/scripts/ledger mode=safe'
        normalize = self.normalize_fixture_paths
        self.assertEqual(normalize(left), normalize(right))
        self.assertNotEqual(normalize(left), normalize(right.replace('mode=safe', 'mode=unsafe')))
        self.assertEqual(normalize('/outside/preserved'), '/outside/preserved')

    def invoke_topic(self, profile, text, tasks, topic=True):
        from test_turn_brief_size import invoke
        if topic:
            text = ('Gateway message origin (JSON data): '
                    '{"platform":"telegram","chat_id":"fixture","thread_id":"9499"} Do not guess a reply destination when these fields are insufficient.\n' + text)
        return invoke(Path(__file__).with_name('turn-brief.py'), profile, text, tasks)

    def fixture_tasks(self):
        return [dict(id=f'old-{i}', title=f'Stale waiting {i}', status='waiting',
                     dest='telegram:fixture:other') for i in range(48)] + [
            dict(id='current-db', title='Database homework', status='open',
                 dest='telegram:fixture:9499', next='Prepare assignment')]

    def selected(self, context):
        return re.findall(r'selected task_id=([^ ]+)', context)

    def test_current_topic_after_48_waiting_rows_visible_without_keywords(self):
        context = self.invoke_topic('sqecea', 'что по домашке', self.fixture_tasks())
        self.assertIn('current-db', self.selected(context))
        self.assertLessEqual(len(self.selected(context)), 7)
        self.assertLessEqual(len(context), 20000)

    def test_current_topic_precedes_backlog_on_legacy_keyword(self):
        context = self.invoke_topic('sqecea', 'Что сегодня?', self.fixture_tasks())
        self.assertEqual(self.selected(context)[0], 'current-db')
        self.assertLessEqual(len(self.selected(context)), 7)

    def test_legacy_selection_exact_for_non_topic_s_and_y(self):
        tasks = self.fixture_tasks()
        for profile, topic in [('sqecea', False), ('yqecea', True)]:
            for scratch in (False, True):
                with self.subTest(profile=profile, scratch=scratch):
                    rows = tasks + ([dict(id='scratch', title='Scratch', status='open', due='2030-01-01')] if scratch else [])
                    context = self.invoke_topic(profile, 'Что сегодня?', rows, topic)
                    self.assertEqual(self.selected(context), (['scratch'] if scratch else []) + [f'old-{i}' for i in range(6)])

    def test_topic_greetings_trivial_and_stop_do_not_grow(self):
        for text in ('привет', 'спасибо', 'Сколько будет 2 + 2?', 'Стоп'):
            with self.subTest(text=text):
                context = self.invoke_topic('sqecea', text, self.fixture_tasks())
                self.assertEqual(self.selected(context), [])
                self.assertNotIn('current-db', context)
                baseline = self.invoke_topic('sqecea', text, self.fixture_tasks(), topic=False)
                normalize = self.normalize_fixture_paths
                self.assertEqual(normalize(context), normalize(baseline))

    def test_h_and_non_topic_s_keep_keyword_trigger(self):
        for profile, topic in [('default', True), ('sqecea', False), ('yqecea', True)]:
            with self.subTest(profile=profile):
                context = self.invoke_topic(profile, 'что по домашке', self.fixture_tasks(), topic)
                self.assertEqual(self.selected(context), [])


class OwnerRequestQuoteTests(unittest.TestCase):
    """The hook keeps the owner's own words so a handoff never depends on a model paraphrase."""
    LONG = 'Сделай фильтр цен в проекте: учти пороги, не меняй формат экспорта и проверь мобильную версию. ' * 3

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'scripts').mkdir()
        (self.home / 'scripts/ledger').touch()
        self.saved = self.home / 'state/last-owner-request.json'

    def invoke(self, text, profile='default', session='s1', turn='t1', sender=''):
        payload = dict(profile=profile, session_id=session,
                       extra=dict(user_message=text, turn_id=turn, platform='cli', sender_id=sender))
        ledger = lambda path, *args: '[]' if args and args[0] == 'list' else 'task1'
        with patch.object(hook, 'HOME', self.home), patch.object(hook, 'run', ledger), \
                patch.object(hook, 'run_kanban', lambda *a: (True, '')), \
                patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(io.StringIO()):
            hook.main()

    def test_long_owner_message_is_saved_verbatim_and_private(self):
        self.invoke(self.LONG)
        data = json.loads(self.saved.read_text())
        self.assertEqual(data['text'], self.LONG.strip())
        self.assertFalse(data['truncated'])
        self.assertEqual(self.saved.stat().st_mode & 0o777, 0o600)

    def test_small_talk_does_not_overwrite_the_request(self):
        self.invoke(self.LONG)
        self.invoke('ок, спасибо', turn='t2')
        self.assertEqual(json.loads(self.saved.read_text())['text'], self.LONG.strip())

    def test_other_profiles_and_untrusted_senders_are_not_saved(self):
        self.invoke(self.LONG, profile='sqecea')
        self.invoke(self.LONG, sender='12345')
        self.assertFalse(self.saved.exists())

    def test_oversized_message_is_capped_and_flagged(self):
        self.invoke('Сделай отчёт. ' + 'я ' * hook.OWNER_REQUEST_LIMIT)
        data = json.loads(self.saved.read_text())
        self.assertEqual(len(data['text']), hook.OWNER_REQUEST_LIMIT)
        self.assertTrue(data['truncated'])

    def test_unwritable_state_never_breaks_the_hook(self):
        (self.home / 'state').write_text('not a directory')
        hook.remember_owner_request(self.home / 'state', self.LONG, 's1')
        self.assertFalse(self.saved.exists())
