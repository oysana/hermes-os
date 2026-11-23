import json
from pathlib import Path
import shlex
import sys
from unittest.mock import patch

import pytest

from test_native_deep_continuation import GOAL, env, _register


@pytest.mark.parametrize('terminal', ['cleared', 'done'])
def test_terminal_replay_cannot_start_a_new_turn(env, terminal):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    manager = goals.GoalManager(sid)
    if terminal == 'cleared':
        manager.clear()
    else:
        manager.mark_done('artifacts checked')
    before = goals.load_goal(sid).to_json()
    result = _register(todo, todo.TodoStore(), sid)
    assert result['goal'].get('status') == terminal
    assert goals.load_goal(sid).to_json() == before


def test_registration_does_not_rewrite_owner_contract(env):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    before = goals.load_goal(sid).to_json()
    result = _register(todo, todo.TodoStore(), sid,
                       {'objective': GOAL['objective'], 'contract': {'verification': 'weaker'}})
    assert result['goal']['ok'] is False
    assert goals.load_goal(sid).to_json() == before


def test_owner_pause_committed_before_registration_stays_paused(env, monkeypatch):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    db = goals._get_session_db()
    original = db._execute_write
    inserted = False

    def owner_pause_then_write(fn, *args, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            goals.GoalManager(sid).pause('owner-paused')
        return original(fn, *args, **kwargs)

    monkeypatch.setattr(db, '_execute_write', owner_pause_then_write)
    result = _register(todo, todo.TodoStore(), sid)
    assert result['goal']['ok'] is False
    assert goals.load_goal(sid).status == 'paused'


def test_invalid_plan_cannot_leave_a_running_goal(env):
    _home, _db, sid, goals, todo, _ex = env
    result = json.loads(todo.todo_tool(todos=[{'id': 'bad', 'content': '', 'status': 'pending'}],
                                      goal=GOAL, store=todo.TodoStore(), session_id=sid, platform="telegram"))
    assert 'error' in result
    assert goals.load_goal(sid) is None


def test_refused_commit_cannot_report_success(env):
    _home, _db, sid, goals, todo, _ex = env
    db = goals._get_session_db()
    with db._lock:
        db._conn.execute('PRAGMA query_only=ON')
    try:
        result = _register(todo, todo.TodoStore(), sid)
        assert result['goal']['ok'] is False
        assert goals.load_goal(sid) is None
    finally:
        with db._lock:
            db._conn.execute('PRAGMA query_only=OFF')


@pytest.mark.parametrize('profile,role', [('default', 'engineering'),
                                        ('sqecea', 'education'), ('yqecea', 'personal')])
def test_two_verified_phases_and_stop_before_questions(env, profile, role):
    home, _db, sid, goals, todo, _ex = env
    first, second = home/'phase-a.txt', home/'phase-b.txt'
    _register(todo, todo.TodoStore(), sid,
              {'objective': f'Complete {role} preparation before any owner questions',
               'contract': GOAL['contract']})
    manager = goals.GoalManager(sid)
    code = f'from pathlib import Path; assert Path({str(first)!r}).read_text() == {role!r}; assert Path({str(second)!r}).read_text() == {profile!r}'
    manager.add_gate(shlex.quote(sys.executable)+' -c '+shlex.quote(code))
    first.write_text(role)
    with patch.object(goals, 'judge_goal') as judge:
        decision = manager.evaluate_after_turn('phase A saved; phase B unfinished')
    judge.assert_not_called()
    assert decision['should_continue'] is True
    assert decision['verdict'] == 'gate_failed'
    assert first.read_text() == role and not second.exists()
    second.write_text(profile)
    fresh = goals.GoalManager(sid)
    with patch.object(goals, 'judge_goal', return_value=(
            'blocked', 'Preparation saved; the next phase needs owner answers', False, None, False)):
        stopped = fresh.evaluate_after_turn('Both artifacts checked; stopped before questions')
    assert stopped['should_continue'] is False
    saved = goals.load_goal(sid)
    assert saved.status == 'paused'
    assert 'owner answers' in saved.last_reason
    assert saved.turns_used == 2
    assert all(gate.last_exit_code == 0 for gate in saved.gates)
    assert first.read_text() == role and second.read_text() == profile
    assert not (home/'questions-sent.txt').exists()
