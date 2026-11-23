"""Real native SQLite lifecycle with synthetic T3 metadata; no live board/network/messages."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

HERE = Path('/home/yqecea/coding-projects/hermes-pro/.artifacts/session-recovery-20261030/H-return-repair')
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('candidate_return_sweep', HERE / 't3-return-sweep.py')
sweep = importlib.util.module_from_spec(spec);spec.loader.exec_module(sweep)
THREAD = 'mcp:11111111-2222-4333-8444-555555555555'
RUN = 'run:thread:mcp%3A11111111-2222-4333-8444-555555555555:ordinal:1'
BODY = f'T3-Thread: {THREAD}\nT3-Run: {RUN}\nT3-State: waiting\n'


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / '.hermes';home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home));monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(sweep, 'ROOT', home)
    import hermes_constants
    token=hermes_constants.set_hermes_home_override(home)
    try:
        kb.init_db()
        yield home / 'kanban.db'
    finally:
        hermes_constants.reset_hermes_home_override(token)


def waiting(board, *, body=BODY, kind='transient'):
    with kbc.connect_closing(board) as conn:
        task = kb.create_task(conn, title='Owned synthetic T3 result', body=body, assignee='default')
        assert kb.claim_task(conn, task, claimer='default') is not None
        assert kb.block_task(conn, task, reason='waiting on T3', kind=kind)
    return task


def result(thread=THREAD, run=RUN):
    return {'thread': thread, 'run': run, 'status': 'completed'}


def status(board, task):
    with kbc.connect_closing(board) as conn:return kb.get_task(conn, task).status


def comment(board, task, text, author='user'):
    with kbc.connect_closing(board) as conn:kb.add_comment(conn, task, author, text)


def test_owner_stop_is_preserved(board):
    task = waiting(board);comment(board, task, 'СТОП. Не продолжать работу.')
    assert sweep.sweep(board, lookup=lambda _: result()) == []
    assert status(board, task) == 'blocked'


def test_owner_resume_after_stop_allows_current_wait(board):
    task = waiting(board);comment(board, task, 'СТОП. Не продолжать работу.')
    comment(board, task, 'продолжай')
    assert len(sweep.sweep(board, lookup=lambda _: result())) == 1
    assert status(board, task) == 'ready'


@pytest.mark.parametrize('state', ['ingested', 'INGESTED', 'done', 'paused', 'hold'])
def test_recorded_closed_or_paused_wait_never_requeues(board, state):
    task = waiting(board, body=BODY.replace('waiting', state))
    assert sweep.sweep(board, lookup=lambda _: result()) == []
    assert status(board, task) == 'blocked'


def test_unrelated_later_comment_is_not_ingestion_proof(board):
    task = waiting(board);comment(board, task, 'Результат ещё не прочитан и не доставлен.')
    assert len(sweep.sweep(board, lookup=lambda _: result())) == 1
    with kbc.connect_closing(board) as conn:
        rows=kb.list_comments(conn, task)
        receipts=[row for row in rows if row.author=='t3-return-sweep']
        assert len(receipts)==1 and RUN in receipts[0].body
        assert '"ingested": false' in receipts[0].body and '"delivered": false' in receipts[0].body


def test_pause_arriving_during_remote_probe_is_checked_before_transition(board):
    task = waiting(board)
    def remote(_):
        comment(board, task, 'T3-Control: hold')
        return result()
    assert sweep.sweep(board, lookup=remote) == []
    assert status(board, task) == 'blocked'


def test_new_binding_arriving_during_remote_probe_is_not_completed_by_old_run(board):
    task = waiting(board)
    def remote(_):
        with kbc.connect_closing(board) as conn:
            assert kb.edit_task(conn, task, body=BODY.replace(':ordinal:1', ':ordinal:2'))
        return result()
    assert sweep.sweep(board, lookup=remote) == []
    assert status(board, task) == 'blocked'


def test_native_owner_wait_kind_wins_over_resume_text(board):
    task = waiting(board, kind='needs_input');comment(board, task, 'T3-Control: resume')
    assert sweep.sweep(board, lookup=lambda _: result()) == []
    assert status(board, task) == 'blocked'


def test_wrong_terminal_run_does_not_resume_bound_wait(board):
    task=waiting(board)
    assert sweep.sweep(board, lookup=lambda _:result(run=RUN.replace(':ordinal:1', ':ordinal:2'))) == []
    assert status(board, task)=='blocked'


def test_dry_run_does_not_write_events_comments_or_status(board):
    task=waiting(board)
    with kbc.connect_closing(board) as conn:
        before=(len(kb.list_events(conn,task)),len(kb.list_comments(conn,task)))
    assert len(sweep.sweep(board,dry_run=True,lookup=lambda _:result()))==1
    with kbc.connect_closing(board) as conn:
        assert before==(len(kb.list_events(conn,task)),len(kb.list_comments(conn,task)))
        assert kb.get_task(conn,task).status=='blocked'


def test_unblock_and_receipt_rollback_together(board):
    task=waiting(board)
    with kbc.connect_closing(board) as conn:
        before=len(kb.list_events(conn,task))
        with pytest.raises(RuntimeError,match='synthetic rollback'):
            with kb.write_txn(conn):
                assert kb.unblock_task(conn,task)
                kb.add_comment(conn,task,'t3-return-sweep','synthetic receipt')
                raise RuntimeError('synthetic rollback')
        assert kb.get_task(conn,task).status=='blocked'
        assert len(kb.list_events(conn,task))==before
        assert kb.list_comments(conn,task)==[]


def test_receipt_failure_cannot_commit_unblock(board,monkeypatch):
    task=waiting(board)
    with monkeypatch.context() as patcher:
        patcher.setattr(kb,'add_comment',lambda *a,**kw:(_ for _ in ()).throw(OSError('synthetic receipt failure')))
        with pytest.raises(OSError,match='synthetic receipt failure'):
            sweep.enqueue_result(board,task,result())
    assert status(board,task)=='blocked'


@pytest.mark.parametrize('values', [
    {'status':'interrupted'}, {'status':'cancelled'}, {'settled':False},
    {'activeRunId':RUN}, {'latestRunId':None},
])
def test_incomplete_or_cancelled_remote_state_never_wakes(board,monkeypatch,values):
    thread={'status':'completed','settled':True,'activeRunId':None,'latestRunId':RUN};thread.update(values)
    monkeypatch.setattr(sweep.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'thread':thread})))
    assert sweep.t3_state(THREAD) is None


def test_dispatcher_worker_cannot_bypass_orchestrator_only_unblock(board,monkeypatch):
    task=waiting(board)
    monkeypatch.setenv('HERMES_KANBAN_TASK',task)
    with pytest.raises(PermissionError,match='orchestrator-only'):
        sweep.enqueue_result(board,task,result())
    assert status(board,task)=='blocked'


def test_other_profile_cannot_resume_h_board(board,tmp_path):
    import hermes_constants
    task=waiting(board)
    token=hermes_constants.set_hermes_home_override(tmp_path/'other-profile')
    try:
        with pytest.raises(PermissionError,match='owning profile scope'):
            sweep.enqueue_result(board,task,result())
    finally:
        hermes_constants.reset_hermes_home_override(token)
    assert status(board,task)=='blocked'


def test_unattributed_legacy_stop_is_conservative_and_untrusted_resume_does_not_clear_it(board):
    task=waiting(board)
    comment(board,task,'СТОП. Не продолжать работу.',author='legacy-operator')
    comment(board,task,'продолжай',author='worker')
    assert sweep.sweep(board,lookup=lambda _:result())==[]
    assert status(board,task)=='blocked'


@pytest.mark.parametrize('payload',[None,[],1,'invalid',{'thread':None}])
def test_malformed_remote_metadata_is_not_a_completion(board,monkeypatch,payload):
    monkeypatch.setattr(sweep.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(payload)))
    assert sweep.t3_state(THREAD) is None
