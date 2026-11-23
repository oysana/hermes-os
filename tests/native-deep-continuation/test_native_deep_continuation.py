"""Native deep-continuation lane: model-authored durable goal registration.

Proves (green on the candidate overlay, red on bare installed source) that the
``todo_list`` ``goal`` field writes the same ``goal:<session_id>`` SessionDB row
/goal writes — so a synthetic short deep request survives the turn boundary
with its contract. Ordinary planning alone does not activate the goal judge.

Source selection: ``NDC_ROOT`` (default = this repo's candidate dir) overlays
``hermes_cli/goals.py``, ``tools/todo_tool.py`` and
``agent/inline_tool_executors.py``; ``HERMES_SOURCE_FIXTURE`` supplies every
other module unmodified. Set ``NDC_ROOT=installed`` for the baseline run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURE = os.environ.get('HERMES_SOURCE_FIXTURE', '/home/yqecea/.hermes/hermes-agent')
if FIXTURE not in sys.path:
    sys.path.insert(0, FIXTURE)

PACKET = Path(__file__).resolve().parents[1].parent / 'runtime' / 'repairs' \
    / '20261119-native-deep-continuation'
# NDC_ROOT='installed' forces bare installed source (the red arm); unset = candidate;
# any other path is a partial overlay — missing files fall back to the fixture per file.
_ndc = os.environ.get('NDC_ROOT')
NDC_ROOT = Path(FIXTURE) if _ndc == 'installed' else Path(_ndc or PACKET / 'candidate')

_OVERLAY = (
    ('hermes_cli.goals', 'hermes_cli/goals.py'),
    ('tools.todo_tool', 'tools/todo_tool.py'),
    ('agent.inline_tool_executors', 'agent/inline_tool_executors.py'),
)


def _load_source():
    """Load the three packet modules, overlaid from NDC_ROOT when present, else installed."""
    import agent  # noqa: F401 — parents must exist before spec-loading children
    import hermes_cli
    import tools
    loaded = {}
    for full, rel in _OVERLAY:
        path = NDC_ROOT / rel
        if not path.exists():
            path = Path(FIXTURE) / rel
        sys.modules.pop(full, None)
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        parent, leaf = full.rsplit('.', 1)
        setattr(sys.modules[parent], leaf, mod)
        loaded[full] = mod
    return loaded['hermes_cli.goals'], loaded['tools.todo_tool'], loaded['agent.inline_tool_executors']


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HERMES_HOME + a real SessionDB session; returns (home, db, session_id, modules)."""
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT', raising=False)
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(str(home))
    goals, todo, executors = _load_source()
    goals._DB_CACHE.clear()
    from hermes_state import SessionDB
    db = SessionDB(home / 'state.db')
    db.create_session('sess-owner', source='telegram')
    try:
        yield home, db, 'sess-owner', goals, todo, executors
    finally:
        db.close()
        set_hermes_home_override(None)


GOAL = {'objective': 'produce two verified artifacts',
        'contract': {'outcome': 'artifact A and B exist on disk',
                     'verification': 'both files present and non-empty',
                     'stop_when': 'before asking the user questions'}}


def _register(todo, store, sid, goal=GOAL):
    return json.loads(todo.todo_tool(goal=goal, store=store, session_id=sid, platform="telegram"))


# ── core: the durable bridge exists and survives the boundary ─────────

def test_two_phase_registration_persists_durable_goal(env):
    """Synthetic short deep request: the model registers a contract and starts a
    two-phase plan; the goal row is durable, contract-bearing and post-turn-visible."""
    _home, _db, sid, goals, todo, _ex = env
    store = todo.TodoStore()
    res = json.loads(todo.todo_tool(
        todos=[{'id': '1', 'content': 'write artifact A', 'status': 'in_progress'},
               {'id': '2', 'content': 'write artifact B', 'status': 'pending'}],
        goal=GOAL, store=store, session_id=sid, platform="telegram"))
    assert res['goal']['ok'] is True
    row = goals.load_goal(sid)
    assert row is not None and row.status == 'active'
    assert row.goal == GOAL['objective']
    assert row.contract.stop_when == 'before asking the user questions'
    # a fresh manager (the post-turn hook's view) sees the same active goal
    assert goals.GoalManager(session_id=sid).is_active()


def test_stubbed_judge_continues_and_partial_progress_is_durable(env):
    """Turn ends 'stop' mid-work: evaluate_after_turn fires the (stubbed) judge,
    a continue verdict burns the budget and the partial progress stays durably saved."""
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    with patch.object(goals, 'judge_goal',
                      return_value=('continue', 'phase 2 still pending', False, None, False)):
        decision = goals.GoalManager(session_id=sid).evaluate_after_turn('artifact A written')
    assert decision['should_continue'] is True
    assert decision['continuation_prompt']
    # "process died here": a brand-new manager re-loads the spent turn + partial state
    fresh = goals.GoalManager(session_id=sid)
    assert fresh.state.turns_used == 1
    assert fresh.state.last_verdict == 'continue'
    assert fresh.is_active()


def test_done_verdict_ends_the_goal_without_continuation(env):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    with patch.object(goals, 'judge_goal',
                      return_value=('done', 'both artifacts verified', False, None, False)):
        decision = goals.GoalManager(session_id=sid).evaluate_after_turn('A and B verified')
    assert decision['should_continue'] is False
    assert goals.load_goal(sid).status == 'done'


# ── no automatic goal / judge for ordinary work ────────────────────────

def test_simple_question_registers_nothing_and_judge_never_fires(env):
    """A plain todo plan (or a turn with no todo call at all) writes no goal row;
    the post-turn hook exits 'inactive' without ever touching the judge."""
    _home, _db, sid, goals, todo, _ex = env
    store = todo.TodoStore()
    res = json.loads(todo.todo_tool(
        todos=[{'id': '1', 'content': 'answer a simple question', 'status': 'completed'}],
        store=store))
    assert 'goal' not in res
    assert goals.load_goal(sid) is None
    with patch.object(goals, 'judge_goal') as judge:
        decision = goals.GoalManager(session_id=sid).evaluate_after_turn('here is the answer')
    judge.assert_not_called()
    assert decision['verdict'] == 'inactive' and decision['should_continue'] is False


# ── idempotency / admission replay ────────────────────────────────────

def test_reregister_same_objective_preserves_budget(env):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    mgr = goals.GoalManager(session_id=sid)
    mgr._state.turns_used = 3
    mgr._save()
    res = _register(todo, todo.TodoStore(), sid, goal={'objective': GOAL['objective']})
    assert res['goal']['ok'] is True
    row = goals.load_goal(sid)
    assert row.turns_used == 3  # replay did not reset the spent budget


def test_different_objective_refused_not_replaced(env):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    res = _register(todo, todo.TodoStore(), sid, goal={'objective': 'an unrelated other goal'})
    assert res['goal']['ok'] is False
    assert goals.load_goal(sid).goal == GOAL['objective']


def test_contract_fields_outside_the_schema_refused(env):
    _home, _db, sid, goals, todo, _ex = env
    res = _register(todo, todo.TodoStore(), sid,
                    goal={'objective': 'x', 'contract': {'deadline': 'tomorrow'}})
    assert res['goal']['ok'] is False
    assert goals.load_goal(sid) is None


# ── stop / pause are owner-only and never revived by a tool call ──────

def test_paused_goal_not_revived_by_registration(env):
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    goals.GoalManager(session_id=sid).pause('owner-paused')
    res = _register(todo, todo.TodoStore(), sid)
    assert res['goal']['ok'] is False
    assert goals.load_goal(sid).status == 'paused'
    with patch.object(goals, 'judge_goal') as judge:
        decision = goals.GoalManager(session_id=sid).evaluate_after_turn('more work')
    judge.assert_not_called()
    assert decision['should_continue'] is False


def test_active_children_hold_parks_continuation(env):
    """A goal parked on actual live children burns no turn and calls no judge."""
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    mgr = goals.GoalManager(session_id=sid)
    mgr.wait_on(os.getpid(), reason='worker running')  # a real live pid: self
    with patch.object(goals, 'judge_goal') as judge:
        decision = mgr.evaluate_after_turn('delegated work still running')
    judge.assert_not_called()
    assert decision['verdict'] == 'waiting' and decision['should_continue'] is False
    assert goals.load_goal(sid).waiting_on_pid == os.getpid()


# ── scope and refusal boundaries ──────────────────────────────────────

def test_profile_scopes_are_isolated(env, tmp_path, monkeypatch):
    """H/S/Y separation: a goal row under one profile home is invisible under another."""
    _home, _db, sid, goals, todo, _ex = env
    _register(todo, todo.TodoStore(), sid)
    assert goals.load_goal(sid) is not None
    other = tmp_path / 'profile-s' / '.hermes'
    other.mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(other))
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(str(other))
    goals._DB_CACHE.clear()
    from hermes_state import SessionDB
    db_s = SessionDB(other / 'state.db')
    try:
        db_s.create_session(sid, source='telegram')
        assert goals.load_goal(sid) is None
        res = _register(todo, todo.TodoStore(), sid, goal={'objective': 'profile S work'})
        assert res['goal']['ok'] is True
        assert goals.load_goal(sid).goal == 'profile S work'
    finally:
        db_s.close()


def test_missing_session_id_and_empty_objective_refused(env):
    _home, _db, sid, _goals, todo, _ex = env
    store = todo.TodoStore()
    assert json.loads(todo.todo_tool(goal=GOAL, store=store, session_id='', platform='telegram'))['goal']['ok'] is False
    assert json.loads(todo.todo_tool(
        goal={'objective': '   '}, store=store, session_id=sid, platform="telegram"))['goal']['ok'] is False


def test_delegated_child_refused(env, monkeypatch):
    _home, _db, sid, goals, todo, _ex = env
    monkeypatch.setenv('HERMES_DELEGATED_CHILD_CONTEXT', '1')
    res = _register(todo, todo.TodoStore(), sid)
    assert res['goal']['ok'] is False
    assert goals.load_goal(sid) is None


def test_dropped_persistence_reported_not_claimed(env, monkeypatch):
    """save_goal degrades silently; registration must not claim durability it lacks."""
    _home, _db, sid, goals, todo, _ex = env
    monkeypatch.setattr(goals, '_get_session_db', lambda: None)
    res = _register(todo, todo.TodoStore(), sid)
    assert res['goal']['ok'] is False
    assert 'persist' in res['goal']['error']


# ── the interface the model sees is unchanged ─────────────────────────

def test_plain_calls_and_executor_wiring(env):
    """todo_list without goal returns the historical shape; the inline executor
    passes goal + the agent's session_id through."""
    _home, _db, sid, goals, todo, executors = env
    store = todo.TodoStore()
    plain = json.loads(todo.todo_tool(
        todos=[{'id': '1', 'content': 'only phase', 'status': 'pending'}], store=store))
    assert set(plain) == {'todos', 'revision', 'summary'}

    class _Agent:
        session_id = sid
        platform = "telegram"
        _todo_store = todo.TodoStore()

    ctx = type('Ctx', (), {'tool_call_id': 'tc1', 'effective_task_id': 't1'})()
    out = json.loads(executors._RAW_INLINE_TOOL_EXECUTORS['todo_list'](
        _Agent(), {'goal': {'objective': 'executor path goal'}}, ctx))
    assert out['goal']['ok'] is True
    assert goals.load_goal(sid).goal == 'executor path goal'


def test_registration_keeps_native_configured_budget(env, monkeypatch):
    _home, _db, sid, goals, todo, _ex = env
    import hermes_cli.config as config
    monkeypatch.setattr(config, 'load_config', lambda: {'goals': {'max_turns': 73}})
    res = _register(todo, todo.TodoStore(), sid)
    assert res['goal']['ok'] is True
    assert goals.load_goal(sid).max_turns == 73
