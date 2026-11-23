"""Offline native gateway handoff; no provider or messenger transport is used."""
import asyncio
import json
import shlex
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from test_native_deep_continuation import GOAL, env


@pytest.mark.parametrize('profile,role', [
    ('default', 'engineering'), ('sqecea', 'education'), ('yqecea', 'personal')])
@pytest.mark.parametrize('deep', [True, False])
def test_native_turn_boundary_drains_once_and_releases_chat(env, monkeypatch, profile, role, deep):
    home, _db, sid, goals, todo, executors = env
    from hermes_constants import set_hermes_home_override
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource, build_session_key

    profile_home = home / profile
    profile_home.mkdir()
    set_hermes_home_override(str(profile_home))
    # Warm the real profile-scoped goal store before entering the event loop.
    goals._get_session_db().create_session(sid, source='telegram')
    first, second = profile_home/'phase-a.txt', profile_home/'phase-b.txt'
    handled, sent, judgements = [], [], []

    class Adapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__(PlatformConfig(enabled=True, token='fixture'), Platform.TELEGRAM)

        async def connect(self):
            return True

        async def disconnect(self):
            pass

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            assert chat_id == 'fixture-self'
            sent.append(content)
            return SimpleNamespace(success=True, message_id=str(len(sent)))

        async def send_typing(self, chat_id, metadata=None):
            pass

        async def get_chat_info(self, chat_id):
            return {}

    source = SessionSource(platform=Platform.TELEGRAM, chat_id='fixture-self',
                           user_id='fixture-owner', chat_type='dm', profile=profile)
    key = build_session_key(source)
    entry = SessionEntry(session_key=key, session_id=sid, platform=Platform.TELEGRAM,
                         chat_type='dm', created_at=datetime.now(), updated_at=datetime.now())
    adapter = Adapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True)})
    # Only environment/transport dependencies are replaced; evaluation, enqueue
    # and adapter drain below are the installed native implementations.
    runner._delivery_adapter_for = lambda src: adapter
    runner._session_key_for_source = lambda src: key
    async def off_loop(fn):
        return await asyncio.to_thread(fn)
    runner._run_in_executor_with_context = off_loop
    monkeypatch.setattr(goals, 'gather_background_processes', lambda **kw: [])
    monkeypatch.setattr(goals, 'count_active_delegations', lambda *a: 0)

    def stop_before_questions(*args, **kwargs):
        judgements.append('blocked')
        return ('blocked', 'Preparation verified; owner answers needed next', False, None, False)
    monkeypatch.setattr(goals, 'judge_goal', stop_before_questions)

    async def handler(event):
        handled.append(event.text)
        assert len(handled) <= 2, 'duplicate continuation executed'
        if not deep:
            response = '4'
        elif len(handled) == 1:
            agent = SimpleNamespace(session_id=sid, platform="telegram", _todo_store=todo.TodoStore())
            args = {'goal': {'objective': f'Prepare {role} artifacts; stop before questions',
                             'contract': GOAL['contract']}}
            # The real inline tool is the model's boundary. Re-delivery must
            # preserve this registration rather than create another goal.
            execute = executors._RAW_INLINE_TOOL_EXECUTORS['todo_list']
            assert json.loads(execute(agent, args, executors.InlineToolContext(effective_task_id='')))['goal']['ok']
            assert json.loads(execute(agent, args, executors.InlineToolContext(effective_task_id='')))['goal']['ok']
            code = (f'from pathlib import Path; assert Path({str(first)!r}).read_text()=={role!r}; '
                    f'assert Path({str(second)!r}).read_text()=={profile!r}')
            goals.GoalManager(sid).add_gate(shlex.quote(sys.executable)+' -c '+shlex.quote(code))
            first.write_text(role)
            response = 'Phase A saved; phase B pending'
        else:
            assert first.read_text() == role
            second.write_text(profile)
            response = 'Preparation complete; paused before owner questions'
        await runner._post_turn_goal_continuation(
            session_entry=entry, source=source, final_response=response)
        return response

    async def run():
        adapter.set_message_handler(handler)
        event = MessageEvent(text='Prepare in two phases' if deep else '2+2?',
                             message_type=MessageType.TEXT, source=source)
        assert adapter._start_session_processing(event, key)
        # Wait for native spawned drain tasks, never inject a second inbound event.
        async with asyncio.timeout(10):
            while len(sent) < (2 if deep else 1) or key in adapter._active_sessions:
                await asyncio.sleep(0.01)
        assert key not in adapter._pending_messages
        assert key not in adapter._active_sessions
    asyncio.run(run())
    if deep:
        assert len(handled) == 2
        assert sent[0] == 'Phase A saved; phase B pending'
        assert sent.count('Preparation complete; paused before owner questions') == 1
        assert len(sent) == 4  # two responses and two native goal-status notices
        assert judgements == ['blocked']  # first turn failed its executable gate
        state = goals.load_goal(sid)
        assert state.status == 'paused' and state.turns_used == 2
        assert state.gates[0].last_exit_code == 0
        assert first.read_text() == role and second.read_text() == profile
    else:
        assert handled == ['2+2?'] and sent == ['4']
        assert judgements == [] and goals.load_goal(sid) is None
    print(f'{profile}: turns={len(handled)} delivered={len(sent)} goal={"paused" if deep else "absent"} chat_released=true')
