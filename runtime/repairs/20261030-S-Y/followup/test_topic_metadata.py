import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('topic_diag', Path(__file__).with_name('tg-personal.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TopicMetadataTest(unittest.TestCase):
    def test_only_read_requests_no_message_body_inspection(self):
        from telethon.tl.functions.messages import GetPeerDialogsRequest, GetForumTopicsRequest
        from telethon.tl.types import InputPeerUser

        class Response:
            dialogs = [SimpleNamespace(unread_count=3, read_inbox_max_id=9)]
            topics = [SimpleNamespace(id=17, unread_count=0, read_inbox_max_id=20)]
            count = 1

            @property
            def messages(self):
                raise AssertionError('Message bodies must never be read')

        class Client:
            calls = []

            async def __call__(self, request):
                self.calls.append(request)
                if type(request) not in (GetPeerDialogsRequest, GetForumTopicsRequest):
                    raise AssertionError('Only read-only metadata RPCs allowed')
                return Response()

        client = Client()
        result = asyncio.run(module.topic_read_metadata(client, InputPeerUser(123, 0)))
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(result['complete'])
        self.assertFalse(result['changes'])
        self.assertEqual(result['dialogs'][0]['unread_count'], 3)
        self.assertEqual(result['topics'][0]['unread_count'], 0)
        self.assertNotIn('text', str(result))


class WatchIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_reader_follows_only_s_topic_read_events(self):
        class Client:
            callbacks = []

            async def get_entity(self, name):
                self.name = name
                return SimpleNamespace(id=777, bot=True)

            async def get_input_entity(self, entity):
                return 'exact-s-peer'

            def on(self, event):
                def register(callback):
                    self.callbacks.append(callback)
                    return callback
                return register

        client = Client()
        reconcile = AsyncMock(return_value={'applied': False})
        with patch('topic_read_sync.reconcile', reconcile), patch.object(module, 'record_read_sync') as audit:
            await module.enable_s_topic_read_sync(client)
            self.assertEqual(client.name, '@sqecea_bot')
            self.assertEqual(reconcile.await_count, 1)
            callback = client.callbacks[0]
            await callback(SimpleNamespace(peer=SimpleNamespace(user_id=888), top_msg_id=7))
            await callback(SimpleNamespace(peer=SimpleNamespace(user_id=777), top_msg_id=None))
            self.assertEqual(reconcile.await_count, 1)
            await callback(SimpleNamespace(peer=SimpleNamespace(user_id=777), top_msg_id=9497))
            self.assertEqual(reconcile.await_count, 2)
            reconcile.assert_awaited_with(client, 'exact-s-peer', apply=True)
            self.assertEqual([call.args[0] for call in audit.call_args_list], ['startup', 'topic_read'])

    async def test_unavailable_s_does_not_break_personal_reader(self):
        client = SimpleNamespace(get_entity=AsyncMock(side_effect=ValueError('synthetic unavailable')))
        with patch('sys.stderr'):
            await module.enable_s_topic_read_sync(client)


class AuditReceiptTests(unittest.TestCase):
    def test_receipt_is_private_append_only_and_omits_message_content(self):
        import tempfile, json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sync.jsonl'
            with patch.object(module, 'READ_SYNC_AUDIT', path):
                module.record_read_sync('startup', {'safe': True, 'applied': False, 'unread_count': 2,
                                                   'messages': [{'text': 'private fixture'}], 'reason': 'private fixture'})
                module.record_read_sync('topic_read', {'safe': True, 'applied': True, 'read_through': 11, 'unread_after': [0]})
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['event'] for r in records], ['startup', 'topic_read'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private fixture', path.read_text())
            self.assertEqual(records[1]['unread_after'], [0])

    def test_symlink_is_not_followed_and_logging_failure_is_nonfatal(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'untouched'; target.write_text('keep')
            link = Path(directory) / 'link'; link.symlink_to(target)
            with patch.object(module, 'READ_SYNC_AUDIT', link):
                module.record_read_sync('topic_read', {'safe': True})
            self.assertEqual(target.read_text(), 'keep')
            with patch.object(module.os, 'open', side_effect=PermissionError('fixture')):
                module.record_read_sync('topic_read', {})
