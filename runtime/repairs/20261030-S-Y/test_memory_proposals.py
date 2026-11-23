import io
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import proactive as p
import delivery as d


class MemoryProposalNoticeTests(unittest.TestCase):
    def test_unreadable_proposal_does_not_break_other_notifications(self):
        self.put('unreadable.md', 'synthetic proposal')
        p.atomic(self.home / 'state/proactive-queue.jsonl',
                 [dict(id='ordinary-fixture', weight='today', text='Обычное напоминание', delivered=False)], True)
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('synthetic refusal')):
            output = self.sweep()
        self.assertIn('Обычное напоминание', output)
        self.assertNotIn('Файлы с предложениями', output)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.now = p.iso('2026-10-09T12:00:00+05:00')
        self.policy = self.home / 'policy.json'
        self.policy_data = {
            'quiet_start': '23:00', 'quiet_end': '07:00', 'daily_cap': 3,
            'exceptions': ['prayer'], 'exception_jobs': {}, 'deadline_jobs': [],
            'stdout_budgeted_jobs': ['sweep'],
        }
        p.atomic(self.policy, self.policy_data)
        self.proposals = self.home / 'state/memory-proposals'
        self.proposals.mkdir(parents=True)
        self.enterContext(patch.object(p, 'ROOT', self.home))
        self.enterContext(patch.object(p, 'POLICY', self.policy))
        self.enterContext(patch.object(p, 'clock', return_value=self.now))

    def put(self, name, content):
        (self.proposals / name).write_text(content)

    def sweep(self, now=None):
        out = io.StringIO()
        p.sweep(self.home, now or self.now, self.policy_data, out)
        return out.getvalue()

    def settle_accepted(self, output):
        job = {'id': 'sweep', 'execution_id': 'synthetic-execution', 'deliver': 'telegram:fixture'}

        def accepted():
            job['_proactive_receipts'] = [{'target': 'telegram:fixture', 'message_id': 'synthetic-receipt'}]

        d.guard_delivery(self.home, job, output, accepted)

    def test_notice_describes_file_count_and_makes_no_acceptance_offer(self):
        self.put('multi.md', '# Candidate\n## one\n## two\n## three\n')
        self.put('other.md', '# Candidate\n')
        output = self.sweep()
        self.assertIn('2', output)
        self.assertIn('файл', output.lower())
        self.assertNotIn('прими', output.lower())

    def test_accepted_snapshot_is_not_announced_again_on_later_days(self):
        self.put('candidate.md', '# Candidate\n')
        first = self.sweep()
        self.settle_accepted(first)
        later = self.sweep(self.now + timedelta(days=1))
        self.assertEqual(later, '')

    def test_same_content_under_a_new_archive_filename_is_not_new_work(self):
        self.put('2026-10-09-execution-a.md', '# Candidate\n')
        first = self.sweep()
        self.settle_accepted(first)
        self.put('2026-10-10-execution-b.md', '# Candidate\n')
        self.assertEqual(self.sweep(self.now + timedelta(days=1)), '')

    def test_new_or_changed_snapshot_is_announced_once(self):
        self.put('candidate.md', '# Candidate\n')
        first = self.sweep()
        self.settle_accepted(first)
        self.put('candidate.md', '# Candidate changed\n')
        changed = self.sweep(self.now + timedelta(days=1))
        self.assertTrue(changed)
        self.settle_accepted(changed)
        # The same accepted fingerprint does not create a later-day reminder.
        self.assertEqual(self.sweep(self.now + timedelta(days=2)), '')

    def test_uncertain_transport_stays_fenced(self):
        self.put('candidate.md', '# Candidate\n')
        output = self.sweep()
        job = {'id': 'sweep', 'execution_id': 'uncertain-execution', 'deliver': 'telegram:fixture'}

        def uncertain():
            raise RuntimeError('synthetic transport uncertainty')

        with self.assertRaises(RuntimeError):
            d.guard_delivery(self.home, job, output, uncertain)
        self.assertEqual(p.read(self.home / 'state/notification-batch.json', {})['status'], 'uncertain')
        self.assertEqual(self.sweep(self.now + timedelta(days=1)), '')

    def test_proven_no_attempt_remains_retryable(self):
        self.put('candidate.md', '# Candidate\n')
        output = self.sweep()
        job = {'id': 'sweep', 'execution_id': 'no-attempt-execution', 'deliver': 'telegram:fixture'}

        def no_attempt():
            job['_proactive_no_attempt'] = True

        d.guard_delivery(self.home, job, output, no_attempt)
        self.assertEqual(p.read(self.home / 'state/notification-batch.json', {})['status'], 'not-sent')
        self.assertTrue(self.sweep())


if __name__ == '__main__':
    unittest.main()
