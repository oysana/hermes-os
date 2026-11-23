"""Drive the public gateway hooks and native session authority using only an in-memory transport."""
import asyncio
import json
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path.home() / '.hermes/hermes-agent'))

HOOK = Path(__file__).resolve().parents[2] / 'runtime/outer-loop/owner-continuity'
OWNER = 'OWNER_CHAT_ID'


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setenv('HERMES_TEST_ISOLATION', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', OWNER)
    from gateway import run_api
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.run import GatewayRunner
    from gateway.session_ingress_context import register_transport_home
    from tests.gateway.test_command_bypass_active_session import _StubAdapter

    monkeypatch.setattr('gateway.run._hermes_home', tmp_path)
    monkeypatch.setattr('hermes_state.DEFAULT_DB_PATH', tmp_path / 'state.db')
    if (HOOK / 'handler.py').exists():
        shutil.copytree(HOOK, tmp_path / 'hooks' / HOOK.name)
    platform_config = PlatformConfig(enabled=True, token='fixture', typing_indicator=False)
    runner = GatewayRunner(GatewayConfig(multiplex_profiles=False, sessions_dir=tmp_path / 'sessions',
                                        platforms={Platform.TELEGRAM: platform_config}))
    monkeypatch.setattr(run_api, '_ACTIVE_RUNNER', runner)
    adapter = _StubAdapter(platform_config, Platform.TELEGRAM)
    replies = []

    async def send(chat_id, content, **kwargs):
        assert str(chat_id) == OWNER
        if content.startswith(('H ', 'Задача')):
            assert kwargs['metadata'] == {'thread_id': '9267'}
        replies.append(content)
        return SendResult(success=True, message_id=str(len(replies)))

    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._wire_adapter_handlers(adapter)
    register_transport_home(runner, None, tmp_path)
    source = adapter.build_source(chat_id=OWNER, chat_type='dm', user_id=OWNER)
    yield runner, source, replies
    runner.session_store.close_all_db_handles()


@pytest.mark.asyncio
@pytest.mark.parametrize('paused_on_boot', (False, True))
async def test_restart_continues_saved_telegram_message_without_discard(gateway, tmp_path, monkeypatch, paused_on_boot):
    from agent.estop import engage, disengage
    from gateway.platforms.event import MessageEvent
    from gateway.session_authority import initialize_session_authority
    from gateway.session_ingress_context import native_callback
    from gateway.session_results import execution_result
    from hermes_state_runtime import claim_session_input, get_session_admission
    from tests.waiting import async_wait_until

    runner, source, replies = gateway
    first = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='first')
    monkeypatch.setattr(first, '_schedule', lambda ref: None)
    head = MessageEvent(text='lost input', source=source, message_id='head')
    follower = MessageEvent(text='saved input', source=source, message_id='follower')
    with native_callback(runner, head, tmp_path):
        receipt = await first.admit_native(head)
    with native_callback(runner, follower, tmp_path):
        queued = await first.admit_native(follower)
    sid = receipt.ref.session_id
    claimed = claim_session_input(first.db, epoch=first.epoch, session_id=sid)
    assert claimed['admission_id'] == receipt.admission_id
    second = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='second')
    monkeypatch.setattr(runner, 'session_authority', second)
    assert get_session_admission(second.db, admission_id=receipt.admission_id)['status'] == 'unknown'
    executed = []

    async def answer(event):
        executed.append(event.text)
        execution_result.get()['result'] = {'final_response': 'fixture reply', 'completed': True, 'messages': []}
        return 'fixture reply'

    monkeypatch.setattr(runner, '_handle_message', answer)
    if paused_on_boot:
        factories = []
        monkeypatch.setattr('agent.estop._candidate_sentinel_paths', lambda: [tmp_path / 'ESTOP'])
        monkeypatch.setattr(runner, '_running', True)
        monkeypatch.setattr(runner, '_spawn_supervised', lambda factory, _name: factories.append(factory))
        [handle] = runner.hooks._active()._resolve_handlers('gateway:startup')
        monkeypatch.setitem(handle.__globals__, 'STATUS_POLL', 0.001)
        engage()
    await runner.hooks.emit('gateway:startup', {'platforms': ['telegram']})
    if paused_on_boot:
        assert get_session_admission(second.db, admission_id=receipt.admission_id)['status'] == 'unknown'
        assert not executed
        assert disengage()
        watcher = asyncio.create_task(factories[0]())
        try:
            await async_wait_until(lambda: sid in second.sessions and second.sessions[sid].task is not None,
                                   what='recovery after the pause is lifted')
        finally:
            runner._running = False
            await asyncio.wait_for(watcher, 2)
    if sid in second.sessions and second.sessions[sid].task is not None:
        await asyncio.wait_for(second.sessions[sid].task, 10)
    lost = get_session_admission(second.db, admission_id=receipt.admission_id)
    next_row = get_session_admission(second.db, admission_id=queued.admission_id)
    assert (lost['status'], lost['outcome']) == ('terminal', 'interrupted')
    assert (next_row['status'], next_row['outcome']) == ('terminal', 'completed')
    assert executed == ['saved input']
    assert replies == ['fixture reply']
    assert runner.session_store.peek_session_id(runner._session_key_for_source(source)) == sid


@pytest.mark.asyncio
@pytest.mark.parametrize('topic', (None, '9265'))
async def test_telegram_turn_uses_changed_config_with_same_session(gateway, tmp_path, monkeypatch, topic):
    from gateway.config import ChannelOverride, Platform
    from gateway.session_authority import initialize_session_authority
    from gateway.session_messaging_picks import messaging_route_info

    runner, source, replies = gateway
    unrelated = ChannelOverride(system_prompt='unchanged')
    runner.config.platforms[Platform.TELEGRAM].channel_overrides['other'] = unrelated
    if topic:
        source = runner.adapters[Platform.TELEGRAM].build_source(
            chat_id=OWNER, chat_type='dm', user_id=OWNER, thread_id=topic)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    monkeypatch.setattr(runner, 'session_authority', authority)
    ref = authority.register(source)
    live = authority.sessions[ref.session_id]
    before, after = uuid.uuid4().hex, uuid.uuid4().hex
    config = tmp_path / 'config.yaml'

    def write_route(value):
        settings = {'model': {'default': value}}
        if topic:
            settings['model']['default'] = 'fixture-default'
            settings['telegram'] = {'channel_overrides': {topic: {'model': value}}}
        config.write_text(json.dumps(settings))

    write_route(before)
    runner.session_store.set_model_override(live.route, {'model': before})
    runner._session_state(live.route).conversation.model_override = {'model': before}

    context = {'platform': 'telegram', 'user_id': OWNER, 'chat_id': OWNER, 'session_id': ref.session_id}
    await runner.hooks.emit('agent:start', context)
    assert runner.session_store.get_model_override(live.route) is not None
    write_route(after)
    await runner.hooks.emit('agent:start', context)

    assert messaging_route_info(runner, live)['model'] == after, 'the previous route still wins'
    assert runner.session_store.get_model_override(live.route) is None
    assert runner.session_store.peek_session_id(live.route) == ref.session_id
    assert runner._session_model_override(live.route) is None
    assert runner.config.platforms[Platform.TELEGRAM].channel_overrides['other'] is unrelated
    assert replies == []

    new_pick = uuid.uuid4().hex
    runner.session_store.set_model_override(live.route, {'model': new_pick})
    write_route(uuid.uuid4().hex)
    await runner.hooks.emit('agent:start', context)
    assert messaging_route_info(runner, live)['model'] == new_pick, 'a newer explicit pick was lost'


@pytest.mark.asyncio
async def test_long_work_notifies_once_and_idle_is_quiet(gateway):
    from gateway.hooks import _load_hook_dir
    from gateway.run import _AGENT_PENDING_SENTINEL
    from gateway.session import _session_key_namespace

    runner, source, replies = gateway
    _, _, handle, _ = _load_hook_dir(HOOK)
    report = handle.__globals__['report_progress']
    key = f"{_session_key_namespace('default')}:telegram:dm:{OWNER}"
    work = runner._session_state(key).turn

    assert await report(runner, 'default', False) is False
    work.agent = _AGENT_PENDING_SENTINEL
    work.started_ts = time.time()
    assert await report(runner, 'default', False) is False
    work.started_ts -= 301
    assert await report(runner, 'default', False) is True
    assert await report(runner, 'default', True) is True
    assert replies == ['H ещё работает. Результат пока не готов.']
    work.agent = None
    assert await report(runner, 'default', True) is False


@pytest.mark.asyncio
async def test_t3_progress_reads_only_the_status_of_the_goals_board(gateway):
    from contextlib import closing
    from gateway.hooks import _load_hook_dir
    from hermes_cli.kanban_db import kanban_db_path

    runner, source, replies = gateway
    _, _, handle, _ = _load_hook_dir(HOOK)
    report = handle.__globals__['report_progress']
    db = kanban_db_path(board='goals')
    db.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE tasks (status TEXT, started_at INTEGER)')
        conn.execute("INSERT INTO tasks VALUES ('running', ?)", (int(time.time()) - 301,))
        conn.commit()
        assert await report(runner, 'default', False) is True
        assert await report(runner, 'default', True) is True
        assert replies == ['Задача в T3 ещё в работе. Результат пока не готов.']
        conn.execute("UPDATE tasks SET status = 'done'")
        conn.commit()
        assert await report(runner, 'default', True) is False
