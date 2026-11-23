import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('LEARNING_SOURCE', Path(__file__).with_name('learn-on-end.py')))
SPEC = importlib.util.spec_from_file_location('learning_under_test', SOURCE)
learning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learning)
sys.path.insert(0, '/home/yqecea/.hermes/plugins')
from omh.memory_session_lens import publish_session_lens, surface_flags
from omh.memory_principals import build_principal_context
from omh.tools.memory_tool import omh_memory_handler


class OverflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'hermes'
        self.omh = Path(self.temp.name) / 'omh'
        (self.home / 'plugins').mkdir(parents=True)
        (self.home / 'plugins/omh').symlink_to('/home/yqecea/.hermes/plugins/omh')
        (self.home / 'memories').mkdir()
        for name in ('MEMORY.md', 'USER.md'):
            (self.home / 'memories' / name).write_text('old memory preserved')
        self.home.joinpath('config.yaml').write_text('memory:\n  memory_char_limit: 20\n  user_char_limit: 20\n')
        self.db = self.home / 'state.db'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_name TEXT, tool_calls TEXT, display_kind TEXT, _compressed_summary INTEGER)')
            conn.execute('CREATE TABLE sessions(id TEXT, started_at REAL, git_repo_root TEXT, cwd TEXT)')
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('owner', 9999999999, '', ''))
        self.patches = [patch.object(learning, k, v) for k,v in {'HOME':self.home,'DB':self.db,'MEMORIES':self.home/'memories','STATE':self.home/'state/learning'}.items()]
        self.patches += [patch.dict(os.environ, {'HERMES_HOME':str(self.home),'OMH_HOME':str(self.omh)}), patch.object(learning,'_index_research',return_value=None)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def lens(self, surface='owner_direct'):
        shared, writes = surface_flags(surface)
        fields = {}
        if surface == 'direct':
            fields['principal_context'] = build_principal_context(self.omh, profile_ref='fixture',
                surface_ref='fixture-dm', session_ref='owner', turn_ref='test', actor_kind='human',
                host_identity='fixture-other-person')
        if surface == 'channel':
            fields['channel_scope'] = {'kind':'channel','ref':'channel:v1:' + 'a'*64}
        publish_session_lens(self.omh, 'owner', {'surface_class':surface,'shared_surface':shared,
            'writes_enabled':writes,'homes':[str(self.omh)], **fields}, reset=True)

    def message(self, text, display=None):
        with sqlite3.connect(self.db) as conn:
            conn.execute('INSERT INTO messages VALUES(1,?,?,?,?,?,?,0)', ('owner','user',text,None,None,display))

    def test_full_file_stays_intact_and_new_lesson_is_recalled(self):
        self.lens()
        self.message('Предпочитаю подробные ответы на русском языке.')
        result=learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'],1,result)
        self.assertEqual(result['decisions'][0]['store'],'omh')
        for name in ('MEMORY.md','USER.md'):
            self.assertEqual((self.home/'memories'/name).read_text(),'old memory preserved')
        record_id = result['decisions'][0]['omh']['record_id']
        recall=json.loads(omh_memory_handler({'action':'recall','query':record_id,'limit':6},session_id='owner'))
        self.assertIn('Предпочитаю подробные ответы',json.dumps(recall,ensure_ascii=False))
        again=learning.run({'session_id':'owner'})
        self.assertEqual(again['decisions'][0]['omh']['status'],'already_remembered')
        self.assertEqual(again['stored'],0)

    def test_reversal_of_owner_preference_is_not_discarded_as_similar(self):
        self.assertFalse(learning._too_similar('Не пиши подробный отчёт о каждой проверке',
            ['2026-11-15: Всегда пиши подробный отчёт о каждой проверке']))
        self.assertTrue(learning._too_similar('Предпочитаю короткий ответ',
            ['2026-11-15: Предпочитаю короткий ответ']))

    def test_notification_is_not_an_owner_lesson(self):
        self.lens()
        self.message('Всегда пиши отчёт о каждой внутренней проверке.', 'internal_notification')
        result=learning.run({'session_id':'owner'})
        self.assertEqual(result['result'],'empty')

    def test_missing_lens_does_not_claim_saved(self):
        self.message('Предпочитаю подробные ответы на русском языке.')
        result=learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'],0)
        self.assertEqual(result['decisions'][0]['omh']['status'],'refused')

    def test_background_cannot_write_owner_memory(self):
        self.lens('background')
        self.message('Предпочитаю подробные ответы на русском языке.')
        result=learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'],0)
        self.assertEqual(result['decisions'][0]['omh']['status'],'refused')

    def test_non_owner_cannot_write_legacy_file_even_when_it_has_room(self):
        self.home.joinpath('config.yaml').write_text('memory:\n  memory_char_limit: 10000\n  user_char_limit: 10000\n')
        self.message('Предпочитаю подробные ответы на русском языке.')
        for surface in ('background', 'shared', 'direct', 'channel'):
            with self.subTest(surface=surface):
                self.lens(surface)
                path = self.home/'memories/USER.md'
                path.write_text('old memory preserved')
                result = learning.run({'session_id':'owner'})
                self.assertEqual(path.read_text(), 'old memory preserved', result)
                self.assertEqual(result['stored'], 1 if surface == 'direct' else 0, result)
                if surface == 'direct':
                    self.assertEqual(result['decisions'][0]['store'], 'omh')
                    foreign_id = result['decisions'][0]['omh']['record_id']
                    self.lens()
                    recall = json.loads(omh_memory_handler({'action':'recall','query':foreign_id},session_id='owner'))
                    self.assertNotIn('Предпочитаю подробные ответы', json.dumps(recall,ensure_ascii=False))
                    self.assertNotIn(foreign_id, [row.get('record_id') for row in recall.get('stored_records',[])])

    def test_unknown_surface_cannot_write_legacy_file_with_room(self):
        self.home.joinpath('config.yaml').write_text('memory:\n  memory_char_limit: 10000\n  user_char_limit: 10000\n')
        self.message('Предпочитаю подробные ответы на русском языке.')
        result = learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'], 0)
        self.assertEqual((self.home/'memories/USER.md').read_text(), 'old memory preserved')

    def test_owner_still_appends_legacy_file_when_it_has_room(self):
        self.lens()
        self.home.joinpath('config.yaml').write_text('memory:\n  memory_char_limit: 10000\n  user_char_limit: 10000\n')
        self.message('Предпочитаю подробные ответы на русском языке.')
        result = learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'], 1)
        self.assertIn('Предпочитаю подробные ответы', (self.home/'memories/USER.md').read_text())

    def test_operator_console_is_an_owner_surface(self):
        self.lens('operator_console')
        self.assertTrue(learning._owner_file_write_allowed('owner'))

    def test_configured_omh_home_takes_precedence_over_environment(self):
        configured = self.home.parent/'configured-omh'
        self.home.joinpath('config.yaml').write_text(
            'plugins:\n  entries:\n    omh:\n      settings:\n        omh_home: '+str(configured)+'\n')
        publish_session_lens(configured, 'owner', {'surface_class':'owner_direct','shared_surface':False,
            'writes_enabled':True,'homes':[str(configured)]}, reset=True)
        self.assertTrue(learning._owner_file_write_allowed('owner'))

    def test_profile_without_omh_keeps_existing_file_capture(self):
        (self.home/'plugins/omh').unlink()
        self.home.joinpath('config.yaml').write_text('memory:\n  memory_char_limit: 10000\n  user_char_limit: 10000\n')
        self.message('Предпочитаю подробные ответы на русском языке.')
        result = learning.run({'session_id':'owner'})
        self.assertEqual(result['stored'], 1)
        self.assertIn('Предпочитаю подробные ответы', (self.home/'memories/USER.md').read_text())


if __name__ == '__main__':
    unittest.main()
