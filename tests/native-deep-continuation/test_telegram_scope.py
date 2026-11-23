import json
from types import SimpleNamespace

import pytest

from test_native_deep_continuation import GOAL, env


@pytest.mark.parametrize('platform', ['cli', 'local', 'whatsapp', 'slack', 'desktop', ''])
def test_new_goal_activation_is_telegram_only(env, platform):
    _home, _db, sid, goals, todo, executors = env
    agent = SimpleNamespace(session_id=sid, platform=platform, _todo_store=todo.TodoStore())
    execute = executors._RAW_INLINE_TOOL_EXECUTORS['todo_list']
    ctx = executors.InlineToolContext(effective_task_id='')
    result = json.loads(execute(agent, {'goal': GOAL, 'platform': 'telegram'}, ctx))
    assert result['goal']['ok'] is False  # model arguments cannot forge agent.platform
    assert goals.load_goal(sid) is None
    assert 'goal' not in json.loads(execute(agent, {'todos': []}, ctx))
    # This patch restricts only the new tool argument, not native /goal.
    goals.GoalManager(sid).set('Existing native command')
    assert goals.load_goal(sid).status == 'active'


def test_registry_path_requires_trusted_platform_metadata(env):
    _home, _db, sid, goals, todo, _executors = env
    from tools.registry import registry
    handler = registry.get_entry('todo_list').handler
    result = json.loads(handler({'goal': GOAL, 'platform': 'telegram'},
                                store=todo.TodoStore(), session_id=sid))
    assert result['goal']['ok'] is False
    assert goals.load_goal(sid) is None
    result = json.loads(handler({'goal': GOAL}, store=todo.TodoStore(), session_id=sid,
                                platform='telegram'))
    assert result['goal']['ok'] is True
