import importlib.util
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('TG_ADDRESSING_SOURCE') or Path.home() / '.hermes/shared/communications/addressing.py')
spec = importlib.util.spec_from_file_location('addressing_under_test', SOURCE)
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)

class AddressingTests(unittest.TestCase):
    def test_public_scalar_contract(self):
        for case in json.loads((ROOT / 'cases.json').read_text()):
            with self.subTest(case=case['id'], text=case['text'][:100]):
                self.assertEqual(parser.addressed_question(case['text']), case['expected'])

    def test_native_question_gate_and_bound_are_preserved(self):
        self.assertEqual(parser.addressed_question('Какая API?', True), True)
        self.assertEqual(parser.addressed_question('Спасибо', True), False)
        self.assertEqual(parser.addressed_question(' ' * 4000 + '?', True), False)
        self.assertEqual(parser.addressed_question('Как дела?'), False)
        self.assertEqual(parser.addressed_question('«Кто видел https://example.com/?to=@yusuf?»', True), True)

if __name__ == '__main__':
    unittest.main()
