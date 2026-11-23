import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('tg_personal', Path(__file__).with_name('tg-personal.py'))
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)
ARTIFACTS = Path('/home/yqecea/.hermes/.artifacts/hermes-bridge-20261011/live-repair/communications-next')

class Client:
    def __init__(self):
        self.files = []
        self.failure = False
    async def upload_file(self, file, **kwargs):
        return file.read()
    async def send_file(self, entity, file, **kwargs):
        self.files.append((entity, file.read() if hasattr(file, 'read') else file, kwargs))
        if self.failure:
            raise TimeoutError()
        return SimpleNamespace(id=42)

class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS, prefix='telegram-fixture-')
        self.base = Path(self.temp.name)
        tg.RECEIPTS = self.base / 'receipts.sqlite3'
        self.file = self.base / 'original.html'
        self.file.write_bytes(b'<h1>fixture</h1>')
        self.args = SimpleNamespace(file=str(self.file), operation_id='fixture-1', caption=None,
                                    guard_state=None, chat='123')
        self.client = Client()
        self.peer = tg.types.InputPeerUser(123, 456)
    def tearDown(self):
        self.temp.cleanup()
    def send(self):
        return asyncio.run(tg.send_file(self.client, self.peer, self.args))
    def test_native_original_document_and_durable_receipt_prevent_replay(self):
        first = self.send()
        second = self.send()
        self.assertEqual(first['message_id'], 42)
        self.assertEqual(second['message_id'], 42)
        self.assertTrue(second['replayed_receipt'])
        self.assertEqual(len(self.client.files), 1)
        self.assertEqual(self.client.files[0][1], b'<h1>fixture</h1>')
        self.assertTrue(self.client.files[0][2]['force_document'])
        self.assertIsNone(self.client.files[0][2]['parse_mode'])
        self.assertEqual(tg.RECEIPTS.stat().st_mode & 0o777, 0o600)

    def test_unknown_network_outcome_is_durable_and_never_retried(self):
        self.client.failure = True
        with self.assertRaises(TimeoutError):
            self.send()
        self.client.failure = False
        result = self.send()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(self.client.files), 1)
    def test_real_sdk_upload_names_pinned_descriptor_before_network(self):
        native = tg.TelegramClient(tg.MemorySession(), 12345, 'fixture-api-hash')
        native._call = AsyncMock(return_value=True)
        class NativeUploadClient:
            async def upload_file(self, file, **kwargs):
                return await native.upload_file(file, **kwargs)
            async def send_file(self, entity, file, **kwargs):
                uploaded = file if isinstance(file, tg.types.InputFile) else await native.upload_file(file)
                self.name = uploaded.name
                return SimpleNamespace(id=42)
        self.client = NativeUploadClient()
        self.assertTrue(self.send()['sent'])
        self.assertEqual(self.client.name, 'original.html')
        self.assertEqual(native._call.await_count, 1)
    def test_same_operation_cannot_send_changed_file_or_recipient(self):
        self.send()
        self.file.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'OPERATION_ID_CONFLICT'):
            self.send()
        self.assertEqual(len(self.client.files), 1)
    def test_symlinks_empty_files_and_directories_fail_before_network(self):
        link = self.base / 'link'
        link.symlink_to(self.file)
        for path in [link, self.base]:
            self.args.file = str(path)
            with self.assertRaises((OSError, ValueError)):
                self.send()
        self.file.write_bytes(b'')
        self.args.file = str(self.file)
        with self.assertRaises(ValueError):
            self.send()
        self.assertEqual(self.client.files, [])
    def test_pagination_and_uncaptioned_media_are_exposed(self):
        class Reader:
            def iter_messages(self, entity, **kwargs):
                self.kwargs = kwargs
                async def rows():
                    yield SimpleNamespace(id=9, date=None, raw_text='', out=False, voice=True,
                                          file=SimpleNamespace(name='voice.ogg', mime_type='audio/ogg', size=20))
                return rows()
        reader = Reader()
        result = asyncio.run(tg.read_page(reader, self.peer, SimpleNamespace(limit=20, before_id=10)))
        self.assertEqual(reader.kwargs, {'limit': 20, 'offset_id': 10})
        self.assertEqual(result[0]['media'], {'name':'voice.ogg','mime_type':'audio/ogg','size':20,'voice':True})
    def test_download_exact_media_privately_without_overwrite(self):
        class Reader:
            async def get_messages(self, entity, ids):
                self.target = entity, ids
                return SimpleNamespace(id=ids, file=SimpleNamespace(name='item.bin', mime_type='application/octet-stream', size=4))
            async def download_media(self, message, file):
                file.write(b'data')
        reader = Reader()
        destination = self.base / 'download.bin'
        args = SimpleNamespace(message_id=7, output=str(destination))
        result = asyncio.run(tg.download_file(reader, self.peer, args))
        self.assertEqual(result['bytes'], 4)
        self.assertEqual(reader.target, (self.peer, 7))
        self.assertEqual(destination.read_bytes(), b'data')
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            asyncio.run(tg.download_file(reader, self.peer, args))
        self.assertEqual(destination.read_bytes(), b'data')
    def test_failed_download_removes_only_new_owned_partial(self):
        class Reader:
            async def get_messages(self, entity, ids):
                return SimpleNamespace(id=ids, file=SimpleNamespace(size=4))
            async def download_media(self, message, file):
                file.write(b'd')
                raise TimeoutError()
        destination = self.base / 'partial.bin'
        with self.assertRaises(TimeoutError):
            asyncio.run(tg.download_file(Reader(), self.peer, SimpleNamespace(message_id=7, output=str(destination))))
        self.assertFalse(destination.exists())

class CatchupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_history_uses_persisted_id_age_bound_and_live_handler(self):
        import sqlite3, time
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state.sqlite'
            db=sqlite3.connect(state)
            db.execute('CREATE TABLE intake_cursors(platform TEXT,chat TEXT,id TEXT,ts REAL,peer TEXT)')
            db.execute("INSERT INTO intake_cursors VALUES('telegram','123','10',?,?)",(time.time()-600,'{"kind":"InputPeerUser","id":123,"access_hash":456}'))
            db.commit();db.close()
            now=time.time()
            class Reader:
                async def iter_dialogs(self, limit=None):
                    yield SimpleNamespace(id=123, input_entity='peer-123',
                                          message=SimpleNamespace(id=13),
                                          date=SimpleNamespace(timestamp=lambda: now-60))
                def iter_messages(self, peer, **kwargs):
                    self.kwargs=kwargs
                    async def rows():
                        for mid,age in [(13,60),(12,120),(11,1000000)]:
                            yield SimpleNamespace(id=mid,date=SimpleNamespace(timestamp=lambda age=age:now-age))
                    return rows()
            reader=Reader();seen=[]
            async def incoming(event, *, catchup=False):
                seen.append((event.id,catchup))
            await tg.catch_up(reader,str(state),incoming,now=now)
            self.assertEqual(seen,[(12,True),(13,True)])
            self.assertEqual(reader.kwargs['min_id'],10)
            self.assertEqual(reader.kwargs['limit'],100)

class FirstStartCatchupTests(unittest.IsolatedAsyncioTestCase):
    def state(self, tmp, rows):
        import sqlite3
        state=Path(tmp)/'state.sqlite'; db=sqlite3.connect(state)
        db.execute('CREATE TABLE intake_cursors(platform TEXT,chat TEXT,id TEXT,ts REAL,peer TEXT)')
        db.executemany("INSERT INTO intake_cursors VALUES('telegram',?,?,?,NULL)",rows)
        db.commit();db.close();return state

    def reader(self, now, ages):
        class Reader:
            async def iter_dialogs(self, limit=None):
                yield SimpleNamespace(id=777,input_entity='peer-777',
                                      message=SimpleNamespace(id=ages[0][0]),
                                      date=SimpleNamespace(timestamp=lambda:now-ages[0][1]))
            def iter_messages(self, peer, **kwargs):
                async def rows():
                    for mid,age in ages:
                        yield SimpleNamespace(id=mid,date=SimpleNamespace(timestamp=lambda age=age:now-age))
                return rows()
        return Reader()

    async def test_quiet_dialogs_make_no_history_requests(self):
        now=1000000
        class Reader:
            async def iter_dialogs(self, limit=None):
                # Cursor already saw latest; latest predates stop; latest is too old;
                # empty dialog; quiet saved peer must not bypass the dialog filter.
                for chat,mid,ts in [(555,9,now-60),(777,2,now-600),
                                    (888,3,now-90000),(999,None,None),(123,10,now-60)]:
                    yield SimpleNamespace(id=chat,input_entity=f'peer-{chat}',
                                          message=SimpleNamespace(id=mid) if mid else None,
                                          date=SimpleNamespace(timestamp=lambda ts=ts:ts) if ts else None)
            def iter_messages(self, peer, **kwargs):
                raise AssertionError('quiet dialog requested history')
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as tmp:
            state=self.state(tmp,[('555','9',now-600),('123','10',now-600)])
            import sqlite3
            db=sqlite3.connect(state)
            try:
                db.execute("UPDATE intake_cursors SET peer=? WHERE chat='123'",
                           ('{"kind":"InputPeerUser","id":123,"access_hash":456}',))
                db.commit()
            finally:
                db.close()
            await tg.catch_up(Reader(),str(state),AsyncMock(),now=now)

    async def test_first_activation_without_any_cursor_replays_nothing(self):
        import time
        now=time.time();seen=[]
        async def incoming(event, *, catchup=False): seen.append(event.id)
        with tempfile.TemporaryDirectory() as tmp:
            await tg.catch_up(self.reader(now,[(2,60),(1,3600)]),str(self.state(tmp,[])),incoming,now=now)
        self.assertEqual(seen,[])

    async def test_chat_without_cursor_replays_only_after_watcher_stopped(self):
        import time
        now=time.time();seen=[]
        async def incoming(event, *, catchup=False): seen.append(event.id)
        with tempfile.TemporaryDirectory() as tmp:
            # The watcher last processed a message 10 minutes ago in another chat.
            state=self.state(tmp,[('555','9',now-600)])
            await tg.catch_up(self.reader(now,[(3,60),(2,1200),(1,3600)]),str(state),incoming,now=now)
        self.assertEqual(seen,[3])


class WatchStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpc_failure_preserves_cursors_and_releases_queued_live_updates(self):
        import contextlib, io, json, sqlite3, time
        from telethon.errors import FloodWaitError, RPCError
        connect = sqlite3.connect
        for stage in ('dialogs', 'history', 'sender'):
            for error in (FloodWaitError(None, capture=37), RPCError(None, 'FIXTURE')):
                with self.subTest(stage=stage, error=type(error).__name__):
                    now=time.time()
                    message=SimpleNamespace(id=12,chat_id=777,sender_id=2,
                        date=SimpleNamespace(timestamp=lambda:now-60),raw_text='fixture',
                        out=False,is_private=True,is_group=False,mentioned=False,chat=None,
                        get_sender=AsyncMock(return_value=SimpleNamespace(bot=False)),
                        get_input_chat=AsyncMock(return_value=tg.types.InputPeerUser(777,456)))
                    class WatchClient:
                        connect=AsyncMock()
                        disconnect=AsyncMock()
                        is_user_authorized=AsyncMock(return_value=True)
                        get_me=AsyncMock(return_value=SimpleNamespace(id=1))
                        def on(self, event):
                            def register(handler):
                                self.handler=handler
                                return handler
                            return register
                        async def iter_dialogs(self, limit=None):
                            self.live=asyncio.create_task(self.handler(SimpleNamespace(message=message)))
                            await asyncio.sleep(0)
                            self.queued=not self.live.done()
                            yield SimpleNamespace(id=777,input_entity='first',
                                message=message,date=message.date)
                            if stage == 'dialogs':
                                raise error
                            yield SimpleNamespace(id=888,input_entity='second',
                                message=message,date=message.date)
                        async def iter_messages(self, peer, **kwargs):
                            if peer == 'second':
                                raise error
                            yield message
                        async def run_until_disconnected(self):
                            self.listening=True
                            await asyncio.wait_for(self.live, timeout=1)
                    if stage == 'sender':
                        message.get_sender.side_effect=[error,SimpleNamespace(bot=False)]
                    client=WatchClient()
                    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as tmp:
                        state=FirstStartCatchupTests.state(self,tmp,[('555','9',now-600)])
                        before=state.read_bytes()
                        # Replace credential I/O with fixtures; no auth/session file is opened.
                        class SessionDB:
                            def execute(self, query): return self
                            def fetchone(self): return (2,'fixture.invalid',443,b'fixture-key')
                            def close(self): pass
                        def fixture_connect(path, **kwargs):
                            if str(state) in path:
                                return connect(path, **kwargs)
                            return SessionDB()
                        output=io.StringIO();stderr=io.StringIO()
                        try:
                            with patch.object(Path,'read_text',return_value='{"api_id":12345,"api_hash":"fixture"}'), \
                                 patch.object(tg.sqlite3,'connect',side_effect=fixture_connect), \
                                 patch.object(tg,'TelegramClient',return_value=client) as factory, \
                                 contextlib.redirect_stdout(output),contextlib.redirect_stderr(stderr):
                                await tg.run(SimpleNamespace(action='watch',intake_state=str(state)))
                        finally:
                            if hasattr(client,'live') and not client.live.done():
                                client.live.cancel()
                                await asyncio.gather(client.live,return_exceptions=True)
                        self.assertIs(factory.call_args.kwargs['receive_updates'],True)
                        self.assertTrue(client.queued)
                        self.assertTrue(client.listening)
                        self.assertEqual(state.read_bytes(),before)
                        rows=[json.loads(line) for line in output.getvalue().splitlines()]
                        self.assertEqual(rows[0],{'watch_ready':True})
                        self.assertEqual([(row['id'],row['catchup']) for row in rows[1:]],[('12',False)])
                        self.assertEqual([json.loads(line) for line in stderr.getvalue().splitlines()],
                                         [{'error':type(error).__name__,'seconds':getattr(error,'seconds',0)}])


if __name__ == '__main__':
    unittest.main()
