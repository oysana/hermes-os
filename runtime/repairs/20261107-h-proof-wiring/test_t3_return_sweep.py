"""Regression tests for T3 return sweep ingestion guard.

Owner requirement (verbatim): add a check `ingested=true or an explicit reason`,
otherwise the card must NOT close.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path.home() / '.hermes'
sys.path.insert(0, str(ROOT / 'hermes-agent'))
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
import hermes_constants

SWEEP_PATH = ROOT / 'scripts/t3-return-sweep.py'
spec = importlib.util.spec_from_file_location('t3_return_sweep', SWEEP_PATH)
sweep_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep_mod)

THREAD = 'mcp:launch:11111111222233334444555566667777'
RUN = 'run:thread:mcp%3Alaunch%3A11111111222233334444555566667777:ordinal:1'
BODY = f"""H отправил работу в T3; эта карточка — приёмник возврата.

--- task-meta ---
T3-Thread: {THREAD}
T3-Run: {RUN}
T3-State: waiting
"""


# kanban_home() resolves get_default_hermes_root()/HERMES_KANBAN_* env, never the
# hermes-home override, so a bare init_db() would migrate the real ~/.hermes/kanban.db.
# Ambient worker/delegation pins leak in too: the descendant fence opens the missing
# temp DB read-only, and HERMES_KANBAN_TASK makes the sweep refuse its own card.
_FIXTURE_ENV = (
    'HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_WORKSPACES_ROOT',
    'HERMES_KANBAN_ATTACHMENTS_ROOT', 'HERMES_KANBAN_TASK', 'HERMES_KANBAN_RUN_ID',
    'HERMES_KANBAN_CLAIM_LOCK', 'HERMES_DELEGATED_CHILD_CONTEXT',
)


class TestT3ReturnSweepIngestionGuard(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / '.hermes'
        self.home.mkdir()
        self.db_path = self.home / 'kanban.db'
        self.token = hermes_constants.set_hermes_home_override(self.home)
        self.old_root = sweep_mod.ROOT
        sweep_mod.ROOT = self.home
        self.old_env = {name: os.environ.get(name) for name in _FIXTURE_ENV}
        for name in _FIXTURE_ENV:
            os.environ.pop(name, None)
        os.environ['HERMES_KANBAN_HOME'] = str(self.home)
        kb.init_db(self.db_path)

    def tearDown(self):
        sweep_mod.ROOT = self.old_root
        hermes_constants.reset_hermes_home_override(self.token)
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp_dir.cleanup()

    def create_card(self, body=BODY):
        with kbc.connect_closing(self.db_path) as conn:
            task_id = kb.create_task(
                conn,
                title='Test T3 Return Card',
                body=body,
                assignee='default',
                created_by='t3-return',
            )
            kb.block_task(conn, task_id, reason='initial_status')
            return task_id

    def get_task(self, task_id):
        with kbc.connect_closing(self.db_path) as conn:
            return kb.get_task(conn, task_id)

    def get_comments(self, task_id):
        with kbc.connect_closing(self.db_path) as conn:
            return kb.list_comments(conn, task_id)

    def test_ingested_false_leaves_card_open_in_blocked_status(self):
        """Negative case: T3 return with ingested=false leaves card open and does NOT close."""
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed', 'ingested': False}

        unclosed = []
        actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            lookup=lambda _: observed,
            unclosed_reasons=unclosed,
        )

        # Actions must be empty (card was NOT completed)
        self.assertEqual(actions, [])
        self.assertEqual(len(unclosed), 1)
        self.assertEqual(unclosed[0]['reason'], 'not ingested and no explicit reason')

        # Card must remain open in blocked status
        task = self.get_task(task_id)
        self.assertEqual(task.status, 'blocked')
        self.assertIsNone(task.completed_at)

        # Receipt comment was still idempotently recorded with ingested=false
        comments = self.get_comments(task_id)
        receipt_comments = [c for c in comments if c.author == 't3-return-sweep']
        self.assertEqual(len(receipt_comments), 1)
        head = receipt_comments[0].body.split('\n', 1)[0]
        self.assertTrue(head.startswith('T3-Return: '))
        receipt = json.loads(head[len('T3-Return: '):])
        self.assertFalse(receipt['ingested'])
        self.assertFalse(receipt['delivered'])

    def test_ingested_true_in_observed_closes_card_normally(self):
        """Positive case 1: T3 return with ingested=true in observed closes normally."""
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed', 'ingested': True}

        actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            lookup=lambda _: observed,
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['action'], 'completed_return')
        self.assertEqual(actions[0]['task_id'], task_id)

        # Card must now be closed with status done
        task = self.get_task(task_id)
        self.assertEqual(task.status, 'done')
        self.assertIsNotNone(task.completed_at)
        self.assertIn(RUN, task.result)

        # Receipt comment recorded ingested=true
        comments = self.get_comments(task_id)
        receipt_comments = [c for c in comments if c.author == 't3-return-sweep']
        self.assertEqual(len(receipt_comments), 1)
        head = receipt_comments[0].body.split('\n', 1)[0]
        receipt = json.loads(head[len('T3-Return: '):])
        self.assertTrue(receipt['ingested'])

    def test_ingested_true_via_comment_closes_normally(self):
        """Positive case 2: Task comment 'T3-State: ingested' satisfies the guard and closes normally."""
        task_id = self.create_card()
        # Origin session recorded evidence of ingestion in a comment
        with kbc.connect_closing(self.db_path) as conn:
            kb.add_comment(conn, task_id, 'origin-session', 'Verified run artifacts. T3-State: ingested')

        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            lookup=lambda _: observed,
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['action'], 'completed_return')
        task = self.get_task(task_id)
        self.assertEqual(task.status, 'done')

    def test_explicit_reason_in_observed_closes_with_reason(self):
        """Explicit reason case: T3 return with ingested=false but explicit_reason closes card."""
        task_id = self.create_card()
        observed = {
            'thread': THREAD,
            'run': RUN,
            'status': 'completed',
            'ingested': False,
            'explicit_reason': 'waived by owner: manual verification in external sheet',
        }

        actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            lookup=lambda _: observed,
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['action'], 'completed_return')
        task = self.get_task(task_id)
        self.assertEqual(task.status, 'done')
        self.assertIn('waived by owner', task.result)

    def test_explicit_reason_in_comment_closes_with_reason(self):
        """Explicit reason in task comment (T3-Reason:) closes card."""
        task_id = self.create_card()
        with kbc.connect_closing(self.db_path) as conn:
            kb.add_comment(conn, task_id, 'owner', 'T3-Reason: superseded by task t_70e25ca9')

        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            lookup=lambda _: observed,
        )

        self.assertEqual(len(actions), 1)
        task = self.get_task(task_id)
        self.assertEqual(task.status, 'done')
        self.assertIn('superseded by task t_70e25ca9', task.result)

    def test_two_stage_lifecycle_leaves_open_then_closes_on_ingestion(self):
        """Full lifecycle: Pass 1 parks with ingested=false; Pass 2 closes once ingested comment added."""
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}

        # Pass 1: Observed completed T3 run, not ingested
        actions_pass1 = sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed)
        self.assertEqual(actions_pass1, [])
        task_after_p1 = self.get_task(task_id)
        self.assertEqual(task_after_p1.status, 'blocked')
        comments_p1 = self.get_comments(task_id)
        self.assertEqual(len(comments_p1), 1)

        # Worker inspects run, downloads artifacts, adds ingestion comment
        with kbc.connect_closing(self.db_path) as conn:
            kb.add_comment(conn, task_id, 'worker', 'Deliveries accepted. T3-State: ingested')

        # Pass 2: Sweep sees the ingested comment, now completes the card
        actions_pass2 = sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed)
        self.assertEqual(len(actions_pass2), 1)
        task_after_p2 = self.get_task(task_id)
        self.assertEqual(task_after_p2.status, 'done')

        # No duplicate receipt comments were created
        sweep_comments = [c for c in self.get_comments(task_id) if c.author == 't3-return-sweep']
        self.assertEqual(len(sweep_comments), 1)

    def wake_events(self, task_id):
        """Synthetic 'blocked' events the sweep emitted for this card (creation's
        'initial_status' blocked event carries actor='t3-return', not the run)."""
        with kbc.connect_closing(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='blocked' ORDER BY id",
                (task_id,)).fetchall()
        out = []
        for (payload,) in rows:
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if data.get('actor') == 't3-return-sweep':
                out.append(data)
        return out

    def test_parked_card_emits_single_wake_event(self):
        """A receipted card that cannot close wakes its subscriber via ONE
        claimable 'blocked'(needs_input) event — claimed by TERMINAL_KINDS,
        waking via _WAKE_KINDS, delivered as 'result' (needs_input is the only
        non-diagnostic blocked payload kind)."""
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}

        actions = sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed)
        self.assertEqual(actions, [])
        self.assertEqual(self.get_task(task_id).status, 'blocked')

        events = self.wake_events(task_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'needs_input')
        self.assertEqual(events[0]['t3_run'], RUN)
        self.assertEqual(events[0]['t3_thread'], THREAD)
        self.assertEqual(events[0]['run_status'], 'completed')
        self.assertIn(RUN, events[0]['reason'])

        # Pass 2 with the receipt already queued: no second wake event.
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed)
        self.assertEqual(len(self.wake_events(task_id)), 1)

        # A rebound NEWER run earns its own receipt and its own wake.
        run2 = RUN.replace('ordinal:1', 'ordinal:2')
        with kbc.connect_closing(self.db_path) as conn:
            kb.edit_task(conn, task_id, body=BODY.replace(RUN, run2))
        observed2 = dict(observed, run=run2)
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed2)
        events = self.wake_events(task_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]['t3_run'], run2)

    def test_terminal_states_receipted_and_woken(self):
        """interrupted/cancelled/rolled_back runs are terminal too: they get a
        receipt and the wake event instead of parking silently."""
        for status in ('interrupted', 'cancelled', 'rolled_back'):
            with self.subTest(status=status):
                task_id = self.create_card()
                observed = {'thread': THREAD, 'run': RUN, 'status': status}
                unclosed = []
                actions = sweep_mod.sweep(
                    self.db_path, task_id=task_id, lookup=lambda _: observed,
                    unclosed_reasons=unclosed)
                self.assertEqual(actions, [])
                self.assertEqual(unclosed[0]['reason'], 'not ingested and no explicit reason')
                self.assertEqual(self.get_task(task_id).status, 'blocked')
                events = self.wake_events(task_id)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['run_status'], status)

    def test_no_wake_event_when_card_closes(self):
        """An ingested/reasoned return closes on 'completed' — that event itself
        wakes; the parked-card wake must not double it."""
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed', 'ingested': True}
        actions = sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.get_task(task_id).status, 'done')
        self.assertEqual(self.wake_events(task_id), [])

    def test_dry_run_reflects_guard_without_modifying_database(self):
        """Dry run only reports completed_return when eligible to close, and never modifies DB."""
        # Un-ingested card
        task_id = self.create_card()
        observed_no_ingest = {'thread': THREAD, 'run': RUN, 'status': 'completed', 'ingested': False}
        dry_actions = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            dry_run=True,
            lookup=lambda _: observed_no_ingest,
        )
        self.assertEqual(dry_actions, [])
        self.assertEqual(len(self.get_comments(task_id)), 0)

        # Ingested card
        observed_ingested = {'thread': THREAD, 'run': RUN, 'status': 'completed', 'ingested': True}
        dry_actions_ingested = sweep_mod.sweep(
            self.db_path,
            task_id=task_id,
            dry_run=True,
            lookup=lambda _: observed_ingested,
        )
        self.assertEqual(len(dry_actions_ingested), 1)
        self.assertTrue(dry_actions_ingested[0]['dry_run'])
        # Database was NOT modified
        self.assertEqual(self.get_task(task_id).status, 'blocked')
        self.assertEqual(len(self.get_comments(task_id)), 0)


class TestT3ReturnSweepProofGate(unittest.TestCase):
    """Owner rule 07.11.2026: a completed run is judged by proof_check; an
    accepted block rides the wake verbatim, a rejected report steers the same
    thread for rework and the card never closes as accepted."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / '.hermes'
        self.home.mkdir()
        self.db_path = self.home / 'kanban.db'
        self.token = hermes_constants.set_hermes_home_override(self.home)
        self.old_root = sweep_mod.ROOT
        sweep_mod.ROOT = self.home
        self.old_env = {name: os.environ.get(name) for name in _FIXTURE_ENV}
        for name in _FIXTURE_ENV:
            os.environ.pop(name, None)
        os.environ['HERMES_KANBAN_HOME'] = str(self.home)
        kb.init_db(self.db_path)

    def tearDown(self):
        sweep_mod.ROOT = self.old_root
        hermes_constants.reset_hermes_home_override(self.token)
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp_dir.cleanup()

    def create_card(self, body=BODY):
        with kbc.connect_closing(self.db_path) as conn:
            task_id = kb.create_task(
                conn, title='Test T3 Return Card', body=body,
                assignee='default', created_by='t3-return')
            kb.block_task(conn, task_id, reason='initial_status')
            return task_id

    def get_task(self, task_id):
        with kbc.connect_closing(self.db_path) as conn:
            return kb.get_task(conn, task_id)

    def get_comments(self, task_id):
        with kbc.connect_closing(self.db_path) as conn:
            return kb.list_comments(conn, task_id)

    def wake_events(self, task_id):
        with kbc.connect_closing(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='blocked' ORDER BY id",
                (task_id,)).fetchall()
        out = []
        for (payload,) in rows:
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if data.get('actor') == 't3-return-sweep':
                out.append(data)
        return out

    ACCEPTED = {'verdict': 'accepted', 'missing': [],
                'block': 'PROOF\nкоманда: bash check.sh\nкод возврата: 0\nне проверено: ничего'}
    REJECTED = {'verdict': 'rejected', 'missing': ['код возврата', 'не проверено'], 'block': ''}

    def test_accepted_proof_block_rides_wake_verbatim(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed,
                        proof=lambda *_: dict(self.ACCEPTED))
        events = self.wake_events(task_id)
        self.assertEqual(len(events), 1)
        self.assertIn(self.ACCEPTED['block'], events[0]['reason'])

    def test_rejected_steers_thread_and_card_stays_open(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        steered = []
        actions = sweep_mod.sweep(
            self.db_path, task_id=task_id, lookup=lambda _: observed,
            proof=lambda *_: dict(self.REJECTED),
            steer=lambda binding, obs, missing: steered.append((binding['thread'], list(missing))) or True)
        self.assertEqual(actions[0]['action'], 'steered')
        self.assertEqual(steered, [(THREAD, ['код возврата', 'не проверено'])])
        self.assertEqual(self.get_task(task_id).status, 'blocked')
        self.assertEqual(self.wake_events(task_id), [])
        comments = self.get_comments(task_id)
        heads = [c.body.split('\n', 1)[0] for c in comments if c.author == 't3-return-sweep']
        self.assertEqual(len(heads), 1)
        self.assertTrue(heads[0].startswith('T3-Steer: '))
        marker = json.loads(heads[0][len('T3-Steer: '):])
        self.assertEqual(marker['run'], RUN)
        self.assertIn('код возврата', marker['missing'])

    def test_rejected_steer_sent_once_per_run(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        calls = []
        steer = lambda b, o, m: calls.append(o['run']) or True
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed,
                        proof=lambda *_: dict(self.REJECTED), steer=steer)
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed,
                        proof=lambda *_: dict(self.REJECTED), steer=steer)
        self.assertEqual(calls, [RUN])
        self.assertEqual(self.get_task(task_id).status, 'blocked')

    def test_rejected_steer_failure_falls_back_to_wake_with_missing(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        actions = sweep_mod.sweep(
            self.db_path, task_id=task_id, lookup=lambda _: observed,
            proof=lambda *_: dict(self.REJECTED), steer=lambda *_: False)
        self.assertEqual(actions, [])
        events = self.wake_events(task_id)
        self.assertEqual(len(events), 1)
        self.assertIn('код возврата', events[0]['reason'])

    def test_rejected_never_closes_even_when_ingested(self):
        task_id = self.create_card()
        with kbc.connect_closing(self.db_path) as conn:
            kb.add_comment(conn, task_id, 'origin', 'T3-State: ingested')
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        actions = sweep_mod.sweep(
            self.db_path, task_id=task_id, lookup=lambda _: observed,
            proof=lambda *_: dict(self.REJECTED), steer=lambda *_: True)
        self.assertEqual(self.get_task(task_id).status, 'blocked')
        self.assertNotEqual([a.get('action') for a in actions], ['completed_return'])

    def test_proof_not_consulted_for_failed_runs(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'failed'}
        seen = []
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed,
                        proof=lambda *_: seen.append(1) or dict(self.REJECTED),
                        steer=lambda *_: True)
        self.assertEqual(seen, [])

    def test_unavailable_proof_keeps_park_path(self):
        task_id = self.create_card()
        observed = {'thread': THREAD, 'run': RUN, 'status': 'completed'}
        sweep_mod.sweep(self.db_path, task_id=task_id, lookup=lambda _: observed,
                        proof=lambda *_: {'verdict': 'unavailable', 'missing': ['x'], 'block': ''})
        self.assertEqual(len(self.wake_events(task_id)), 1)


if __name__ == '__main__':
    unittest.main()
