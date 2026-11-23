import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts' / 'check-post-decisions.py'
SPEC = importlib.util.spec_from_file_location('check_post_decisions', SOURCE)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def review_row(index, status='отложено'):
    return {'index': index, 'id': f'100{index}', 'status': status}


class DecisionCoverageTests(unittest.TestCase):
    def test_live_corpus_has_a_decision_per_deferred_post(self):
        result = subprocess.run([sys.executable, '-B', str(SOURCE), '--root', str(ROOT)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual(report['deferred'], report['decisions'])
        self.assertGreater(report['deferred'], 0)
        self.assertEqual(report['adopt'] + report['reject'], report['decisions'])

    def test_deferred_row_without_decision_fails(self):
        errors, _ = CHECKER.validate([review_row(1)], [])
        self.assertTrue(any('no adopt/reject decision' in e for e in errors), errors)

    def test_adopt_needs_card_and_acceptance(self):
        errors, _ = CHECKER.validate([review_row(1)], [
            {'index': 1, 'decision': 'adopt', 'card': 'not-a-card', 'acceptance': ''}])
        self.assertEqual(len(errors), 2, errors)

    def test_reject_needs_reason(self):
        errors, _ = CHECKER.validate([review_row(1)], [{'index': 1, 'decision': 'reject'}])
        self.assertTrue(any('reason' in e for e in errors), errors)

    def test_duplicate_decision_fails(self):
        decisions = [{'index': 1, 'decision': 'reject', 'reason': 'x'},
                     {'index': 1, 'decision': 'reject', 'reason': 'y'}]
        errors, _ = CHECKER.validate([review_row(1)], decisions)
        self.assertTrue(any('duplicate' in e for e in errors), errors)

    def test_decision_on_non_deferred_row_fails(self):
        errors, _ = CHECKER.validate([review_row(1, status='внедрено')],
                                     [{'index': 1, 'decision': 'reject', 'reason': 'x'}])
        self.assertTrue(any('not deferred' in e for e in errors), errors)

    def test_full_fixture_passes(self):
        rows = [review_row(1), review_row(2, status='внедрено'), review_row(3, status='отклонено')]
        decisions = [{'index': 1, 'decision': 'adopt', 'card': 't_4475af99', 'acceptance': 'stage-4'}]
        errors, summary = CHECKER.validate(rows, decisions)
        self.assertEqual(errors, [])
        self.assertEqual(summary['adopt'], 1)


if __name__ == '__main__':
    unittest.main()
