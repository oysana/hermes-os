import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(os.environ.get('COMPLETION_SOURCE', Path(__file__).with_name('proactive.py')))

class CompletionDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='completion-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / '.hermes/profiles/sqecea'
        self.env = dict(os.environ, HOME=str(self.root), HERMES_KANBAN_CLI='/bin/false')

    def run_ledger(self, *args):
        return subprocess.run([sys.executable, '-B', str(SOURCE), '--home', str(self.home),
                               'ledger', *args], env=self.env, capture_output=True, text=True)

    def add(self, outputs):
        result = self.run_ledger('add', '--title', 'Synthetic assignment', '--outputs', outputs,
                                 '--dest', 'telegram:fixture:9499')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def state(self, ident):
        return json.loads(self.run_ledger('show', ident).stdout)

    def test_document_claim_without_file_stays_open(self):
        ident = self.add('проверенное решение и документ для сдачи')
        result = self.run_ledger('done', ident, '--evidence',
            'проверенное решение и документ для сдачи=пока не подготовлено')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state(ident)['status'], 'open')

    def test_topic_answer_without_receipt_stays_open(self):
        ident = self.add('ответ в теме Базы данных')
        result = self.run_ledger('done', ident, '--evidence', 'ответ в теме Базы данных=ещё не доставлено')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state(ident)['status'], 'open')

    def test_file_and_receipt_complete_declared_preparation_outputs(self):
        ident = self.add('документ для сдачи;ответ в теме Базы данных')
        artifact = self.root / 'synthetic-document.txt'
        artifact.write_text('Synthetic nonempty artifact; this test checks custody, not document contents.')
        result = self.run_ledger('done', ident, '--evidence',
            'документ для сдачи=fixture file;ответ в теме Базы данных=fixture receipt',
            '--artifact', str(artifact), '--receipt', 'telegram:fixture:message-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.state(ident)
        self.assertEqual(record['status'], 'done')
        self.assertEqual(record['artifacts'], [str(artifact)])
        self.assertEqual(record['delivery']['message_id'], 'telegram:fixture:message-1')

    def test_plain_learning_text_without_delivery_promise_can_complete(self):
        ident = self.add('объяснение алгоритма')
        result = self.run_ledger('done', ident, '--evidence', 'объяснение алгоритма=fixture explanation')
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__':
    unittest.main()
