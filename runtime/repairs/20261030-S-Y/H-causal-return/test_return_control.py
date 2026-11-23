"""Real native SQLite lifecycle with synthetic T3 wait metadata; no live board/network/messages."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

HERE = Path('/home/yqecea/coding-projects/hermes-pro/.artifacts/session-recovery-20261030/H-completion-semantics')
sys.path.insert(0, str(HERE))
SOURCE = Path(os.environ.get('HERMES_RETURN_SWEEP_SOURCE', str(HERE / 't3-return-sweep.py')))
spec = importlib.util.spec_from_file_location('candidate_return_sweep', SOURCE)
sweep = importlib.util.module_from_spec(spec);spec.loader.exec_module(sweep)
THREAD = 'mcp:11111111-2222-4333-8444-555555555555'
RUN = 'run:thread:mcp%3A11111111-2222-4333-8444-555555555555:ordinal:35'
RESULT_RUN = 'run:thread:mcp%3A11111111-2222-4333-8444-555555555555:ordinal:38'
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


def repark(board, task):
    """Worker re-parks the unchanged wait untyped; it stays resumable-blocked for the sweep."""
    with kbc.connect_closing(board) as conn:
        assert kb.block_task(conn, task, reason='still waiting on T3')
        assert kb.get_task(conn, task).status == 'blocked'


def result(thread=THREAD, run=RUN, waited=None):
    observed = {'thread': thread, 'run': run, 'status': 'completed'}
    if waited is not None:
        observed['waited_run'] = waited
    return observed


def wait_payload(*, thread=THREAD, run_id=RUN, status='idle', timed_out=False, result_run='unset'):
    """The real t3_thread_wait output shape; result_run 'unset' emits the field."""
    return {'threadId': thread, 'runId': run_id, 'status': status, 'timedOut': timed_out,
            'next': None, 'resultRun': result_run}


def handler_payload(payload):
    return json.dumps({'result': '', 'structuredContent': payload})


def status(board, task):
    with kbc.connect_closing(board) as conn:return kb.get_task(conn, task).status


def comment(board, task, text, author='user'):
    with kbc.connect_closing(board) as conn:kb.add_comment(conn, task, author, text)


def receipts(board, task):
    with kbc.connect_closing(board) as conn:
        return [row for row in kb.list_comments(conn, task) if row.author == 't3-return-sweep']


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


def test_causal_result_run_may_differ_from_the_bound_run(board):
    """The proven live case: card binds run 35 whose continuation children end in run 38."""
    task = waiting(board)
    actions = sweep.sweep(board, lookup=lambda _: result(run=RESULT_RUN, waited=RUN))
    assert len(actions) == 1 and actions[0]['run'] == RESULT_RUN and actions[0]['waited_run'] == RUN
    assert status(board, task) == 'ready'
    rows = receipts(board, task)
    assert len(rows) == 1
    assert f'"run": "{RESULT_RUN}"' in rows[0].body and f'"bound_run": "{RUN}"' in rows[0].body
    assert '"ingested": false' in rows[0].body and '"delivered": false' in rows[0].body


def test_same_queued_result_is_not_readmitted_while_the_card_parks_unchanged(board):
    task = waiting(board)
    observed = result(run=RESULT_RUN, waited=RUN)
    assert len(sweep.sweep(board, lookup=lambda _: observed)) == 1
    repark(board, task)
    assert sweep.sweep(board, lookup=lambda _: observed) == []
    assert len(receipts(board, task)) == 1


def test_a_new_result_run_for_the_same_wait_is_not_deduped(board):
    task = waiting(board)
    first = result(run=RESULT_RUN, waited=RUN)
    assert len(sweep.sweep(board, lookup=lambda _: first)) == 1
    repark(board, task)
    later = result(run=RESULT_RUN.replace(':ordinal:38', ':ordinal:41'), waited=RUN)
    assert len(sweep.sweep(board, lookup=lambda _: later)) == 1
    assert len(receipts(board, task)) == 2


def test_typed_repark_lands_in_native_triage_outside_the_sweep(board):
    """The native block-recurrence bound, not the sweep, owns re-admission escalation."""
    task = waiting(board)
    assert len(sweep.sweep(board, lookup=lambda _: result())) == 1
    with kbc.connect_closing(board) as conn:
        assert kb.block_task(conn, task, reason='still waiting', kind='transient')
        assert kb.get_task(conn, task).status == 'triage'
    assert sweep.sweep(board, lookup=lambda _: result()) == []


def test_unrelated_later_comment_is_not_ingestion_proof(board):
    task = waiting(board);comment(board, task, 'Результат ещё не прочитан и не доставлен.')
    assert len(sweep.sweep(board, lookup=lambda _: result())) == 1
    rows = receipts(board, task)
    assert len(rows) == 1 and RUN in rows[0].body
    assert '"ingested": false' in rows[0].body and '"delivered": false' in rows[0].body


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
            assert kb.edit_task(conn, task, body=BODY.replace(':ordinal:35', ':ordinal:2'))
        return result()
    assert sweep.sweep(board, lookup=remote) == []
    assert status(board, task) == 'blocked'


def test_new_binding_arriving_during_causal_probe_is_not_completed_by_causal_result(board):
    task = waiting(board)
    def remote(_):
        with kbc.connect_closing(board) as conn:
            assert kb.edit_task(conn, task, body=BODY.replace(':ordinal:35', ':ordinal:40'))
        return result(run=RESULT_RUN, waited=RUN)
    assert sweep.sweep(board, lookup=remote) == []
    assert status(board, task) == 'blocked'


def test_native_owner_wait_kind_wins_over_resume_text(board):
    task = waiting(board, kind='needs_input');comment(board, task, 'T3-Control: resume')
    assert sweep.sweep(board, lookup=lambda _: result()) == []
    assert status(board, task) == 'blocked'


def test_result_for_an_unbound_waited_run_does_not_resume_the_bound_wait(board):
    task = waiting(board)
    assert sweep.sweep(board, lookup=lambda _: result(run=RESULT_RUN, waited=RESULT_RUN)) == []
    assert status(board, task) == 'blocked'


def test_dry_run_does_not_write_events_comments_or_status(board):
    task = waiting(board)
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


def test_native_probe_failure_surfaces_stable_code_without_payload(board, monkeypatch, capsys):
    task = waiting(board)
    monkeypatch.setattr(sweep, '_shutdown_native_t3', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['return-sweep', '--dry-run'])
    assert sweep.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {'event': 't3_return_probe_failed', 'task_id': task,
                                    'code': 'mcp_unavailable'}
    assert status(board, task) == 'blocked'


def test_unavailable_probe_does_not_block_another_valid_return(board, capsys):
    first=waiting(board);second=waiting(board)
    calls=0
    def lookup(binding):
        nonlocal calls
        calls+=1
        if calls==1: raise OSError('PRIVATE_SDK_PAYLOAD')
        return result()
    errors=[]
    actions=sweep.sweep(board, lookup=lookup, probe_errors=errors)
    assert len(actions)==1 and len(errors)==1 and errors[0]['code']=='mcp_unavailable'
    assert sorted([status(board,first),status(board,second)])==['blocked','ready']
    assert 'PRIVATE_SDK_PAYLOAD' not in capsys.readouterr().err


# --- Native causal-wait probe (the t3_thread_wait boundary) -------------------

def native_probe(payload, monkeypatch=None, *, calls=None):
    """NativeWait wired to a fake registered handler; no MCP transport."""
    if calls is None:
        calls = []
    def handler(args):
        calls.append(dict(args))
        return handler_payload(payload)
    return sweep.NativeWait(handler=handler), calls


def test_wait_requests_the_bound_run_with_delegated_settlement(board):
    probe, calls = native_probe(wait_payload(result_run={'runId': RESULT_RUN, 'status': 'completed'}))
    observed = probe.result({'thread': THREAD, 'run': RUN})
    assert calls == [{'threadId': THREAD, 'runId': RUN, 'timeoutMs': sweep.WAIT_TIMEOUT_MS,
                      'untilDelegatedWorkSettled': True}]
    assert observed == {'thread': THREAD, 'run': RESULT_RUN, 'status': 'completed', 'waited_run': RUN}


def test_wait_without_a_bound_run_refuses_before_transport(board):
    probe, calls = native_probe(wait_payload(result_run={'runId': RESULT_RUN, 'status': 'completed'}))
    with pytest.raises(sweep.T3ProbeError, match='binding_run_missing'):
        probe.result({'thread': THREAD, 'run': ''})
    assert calls == []


def test_missing_run_binding_is_visible_and_never_queues_latest(board):
    task = waiting(board, body=f'T3-Thread: {THREAD}\nT3-State: waiting\n')
    errors=[]
    assert sweep.sweep(board, lookup=lambda _: result(), probe_errors=errors)==[]
    assert errors == [{'event':'t3_return_probe_failed','task_id':task,'code':'binding_run_missing'}]
    assert status(board,task)=='blocked'


@pytest.mark.parametrize('payload', [
    wait_payload(timed_out=True, status='running', result_run=None),
    wait_payload(status='running', result_run=None),
    wait_payload(status='running', result_run={'runId': RESULT_RUN, 'status': 'running'}),
    wait_payload(result_run={'runId': RESULT_RUN, 'status': 'interrupted'}),
    wait_payload(result_run={'runId': RESULT_RUN, 'status': 'cancelled'}),
])
def test_unfinished_causal_work_never_wakes_the_bound_card(board, payload):
    probe, _ = native_probe(payload)
    assert probe.result({'thread': THREAD, 'run': RUN}) is None


def test_delegated_answer_run_is_the_causal_result_not_the_latest_run(board):
    """Organizational settled stays false while run 38 carries the final answer for bound run 35."""
    probe, _ = native_probe(wait_payload(run_id=RUN, status='idle',
                                         result_run={'runId': RESULT_RUN, 'status': 'completed'}))
    observed = probe.result({'thread': THREAD, 'run': RUN})
    assert observed['run'] == RESULT_RUN and observed['waited_run'] == RUN


def test_wait_result_string_payload_parses(board):
    """The observed live wire shape: the wait object arrives as a JSON string under `result`."""
    probe = sweep.NativeWait(handler=lambda args: json.dumps(
        {'result': json.dumps(wait_payload(result_run={'runId': RESULT_RUN, 'status': 'completed'}))}))
    observed = probe.result({'thread': THREAD, 'run': RUN})
    assert observed['run'] == RESULT_RUN and observed['waited_run'] == RUN


def test_real_shape_timed_out_completed_thread_does_not_wake(board):
    """Live shape seen on both bound threads: completed + unsettled delegated work -> still waiting."""
    probe = sweep.NativeWait(handler=lambda args: json.dumps(
        {'result': json.dumps({'threadId': THREAD, 'runId': RUN,
                               'status': 'completed', 'timedOut': True})}))
    assert probe.result({'thread': THREAD, 'run': RUN}) is None


def test_failed_causal_result_is_still_ingestible(board):
    probe, _ = native_probe(wait_payload(result_run={'runId': RESULT_RUN, 'status': 'failed'}))
    observed = probe.result({'thread': THREAD, 'run': RUN})
    assert observed['status'] == 'failed'


def test_unrelated_newer_run_is_never_substituted_for_the_bound_run(board):
    probe, _ = native_probe(wait_payload(run_id=RESULT_RUN.replace(':ordinal:38', ':ordinal:40'),
                                       status='completed', result_run=None))
    assert probe.result({'thread': THREAD, 'run': RUN}) is None


@pytest.mark.parametrize('payload', [
    None, [], 'invalid',
    {'result': 'not-json'},
    {'structuredContent': wait_payload(timed_out='yes')},
    {'structuredContent': wait_payload(status='bogus')},
    {'structuredContent': wait_payload(thread='mcp:99999999-2222-4333-8444-555555555555')},
    {'structuredContent': wait_payload(run_id=None, result_run=None)},
    {'structuredContent': wait_payload(result_run={'runId': '', 'status': 'completed'})},
    {'structuredContent': wait_payload(result_run={'runId': RESULT_RUN, 'status': 'bogus'})},
])
def test_malformed_wait_metadata_is_not_a_completion(board, payload):
    probe, _ = native_probe(payload)
    with pytest.raises(sweep.T3ProbeError, match='metadata_invalid'):
        probe.result({'thread': THREAD, 'run': RUN})


def test_tool_error_payload_surfaces_stable_code(board):
    probe = sweep.NativeWait(handler=lambda args: json.dumps({'error': 'PRIVATE_ERROR_PAYLOAD'}))
    with pytest.raises(sweep.T3ProbeError, match='tool_error'):
        probe.result({'thread': THREAD, 'run': RUN})


@pytest.mark.parametrize('raised,code', [(TimeoutError(), 'wait_timeout'),
                                         (RuntimeError('PRIVATE_ERROR_PAYLOAD'), 'mcp_unavailable')])
def test_handler_failure_surfaces_stable_code_without_payload(board, raised, code):
    def handler(args):
        raise raised
    probe = sweep.NativeWait(handler=handler)
    with pytest.raises(sweep.T3ProbeError, match=code):
        probe.result({'thread': THREAD, 'run': RUN})


def test_unregistered_wait_tool_is_a_stable_probe_error(board, monkeypatch):
    monkeypatch.setattr(sweep, '_t3_wait_handler', lambda: None)
    with pytest.raises(sweep.T3ProbeError, match='mcp_unavailable'):
        sweep.NativeWait()


def test_noncallable_wait_entry_is_a_stable_probe_error(board, monkeypatch):
    monkeypatch.setattr(sweep, '_t3_wait_handler', lambda: 'not-a-handler')
    with pytest.raises(sweep.T3ProbeError, match='mcp_unavailable'):
        sweep.NativeWait()


def test_sweep_builds_native_probe_once_and_shuts_the_loop_down(board, monkeypatch):
    first, second = waiting(board), waiting(board)
    calls, closed = [], []
    def handler(args):
        calls.append(dict(args))
        return handler_payload(wait_payload(result_run={'runId': RESULT_RUN, 'status': 'completed'}))
    monkeypatch.setattr(sweep, '_t3_wait_handler', lambda: handler)
    monkeypatch.setattr(sweep, '_shutdown_native_t3', lambda: closed.append(True))
    actions = sweep.sweep(board)
    assert len(actions) == 2 and len(calls) == 2 and closed == [True]
    assert all(call.get('runId') == RUN for call in calls)
    assert status(board, first) == 'ready' and status(board, second) == 'ready'


def test_native_probe_construction_failure_is_a_stable_code_per_candidate(board, monkeypatch):
    waiting(board);waiting(board)
    def denied():
        raise OSError('PRIVATE_TRANSPORT_DETAIL')
    monkeypatch.setattr(sweep, '_t3_wait_handler', denied)
    monkeypatch.setattr(sweep, '_shutdown_native_t3', lambda: None)
    errors = []
    assert sweep.sweep(board, probe_errors=errors) == []
    assert [e['code'] for e in errors] == ['mcp_unavailable', 'mcp_unavailable']


def test_sweep_without_waiting_cards_never_opens_the_native_probe(board, monkeypatch):
    monkeypatch.setattr(sweep, '_t3_wait_handler',
                        lambda: (_ for _ in ()).throw(AssertionError('probe opened with no candidates')))
    assert sweep.sweep(board) == []
