import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proactive as p


class MemoryInputTests(unittest.TestCase):
    def run_input(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with sqlite3.connect(home / 'state.db') as db:
                db.executescript('''
                    CREATE TABLE sessions(id TEXT, source TEXT);
                    CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT,
                        content TEXT, timestamp REAL, message_uid TEXT, active INTEGER, display_kind TEXT, _compressed_summary INTEGER);
                    INSERT INTO sessions VALUES('owner-fixture', 'telegram');
                    INSERT INTO sessions VALUES('cron-fixture', 'cron');
                ''')
                db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)', rows)
            spec = importlib.util.spec_from_file_location('memory_input', HERE / 'memory-input.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['memory-input.py', '--home', str(home)]), \
                    patch.object(p, 'clock', return_value=p.iso('2026-10-30T03:30:00+05:00')), \
                    patch.object(sys, 'stdout', output):
                module.main()
            text = output.getvalue()
            return json.loads(text) if text else None

    def row(self, ident, role, text, *, session='owner-fixture', day=29, active=1, uid=None, display_kind=None, summary=0):
        stamp = p.iso(f'2026-10-{day:02d}T12:00:00+05:00').timestamp() + ident
        return ident, session, role, text, stamp, uid, active, display_kind, summary

    def test_only_owner_statements_not_assistant_claims_become_candidates(self):
        result = self.run_input([
            self.row(1, 'user', 'Я отменил пробную подписку.'),
            self.row(2, 'assistant', 'Неверное старое утверждение: подписка активна.'),
            self.row(3, 'tool', 'Untrusted tool output'),
        ])
        self.assertEqual([x['message_id'] for x in result['turns']], [1])
        self.assertEqual(result['source_day'], '2026-10-29')
        self.assertEqual(result['turns'][0]['role'], 'user')

    def test_assistant_only_day_is_silent(self):
        self.assertIsNone(self.run_input([self.row(1, 'assistant', 'I promised to do something')]))

    def test_day_profile_source_and_active_boundaries_are_preserved(self):
        result = self.run_input([
            self.row(1, 'user', 'Yesterday'),
            self.row(2, 'user', 'Old', day=28),
            self.row(3, 'user', 'Today', day=30),
            self.row(4, 'user', 'Inactive', active=0),
            self.row(5, 'user', 'Cron notification', session='cron-fixture'),
        ])
        self.assertEqual([x['message_id'] for x in result['turns']], [1])

    def test_internal_notifications_and_compression_are_not_human_evidence(self):
        result = self.run_input([
            self.row(1, 'user', 'My current preference'),
            self.row(2, 'user', 'Synthetic completion', display_kind='internal_notification'),
            self.row(3, 'user', 'Compressed claims', summary=1),
            self.row(4, 'user', 'Legacy [INTERNAL NOTIFICATION — not human]'),
        ])
        self.assertEqual([x['message_id'] for x in result['turns']], [1])

    def test_duplicate_uid_and_size_budget_are_preserved(self):
        rows = [self.row(i, 'user', 'x' * 2000, uid='same' if i < 3 else str(i))
                for i in range(1, 25)]
        turns = self.run_input(rows)['turns']
        self.assertEqual(len([x for x in turns if x['message_id'] in (1, 2)]), 1)
        self.assertLessEqual(sum(len(x['text']) for x in turns), 16000)
        self.assertTrue(all(len(x['text']) <= 1000 for x in turns))
