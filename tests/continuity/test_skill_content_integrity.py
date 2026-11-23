import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'runtime/repairs/20261115-skill-integrity/tool_result_shaping.py'
spec = importlib.util.spec_from_file_location('skill_integrity_shaping', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SkillContentIntegrity(unittest.TestCase):
    def test_loaded_instructions_remain_complete(self):
        content = '\n'.join(f'Instruction {i}: keep its complete meaning.' for i in range(400))
        result = json.dumps({'success': True, 'name': 'fixture', 'content': content})
        self.assertEqual(module.shape_result_for_context('skill_view', result), result)

    def test_large_reference_remains_valid_and_complete(self):
        result = json.dumps({'content': 'reference ' * 10000, 'path': 'references/guide.md'})
        self.assertEqual(json.loads(module.shape_result_for_context('skill_view', result)), json.loads(result))

    def test_terminal_logs_still_shaped(self):
        result = '\n'.join(f'log {i}: details' for i in range(400))
        original = module._shaping_config
        module._shaping_config = lambda: dict(max_lines=150,max_chars=64000,dedupe=True,strip_ansi=False)
        try:
            self.assertIn('omitted — chars', module.shape_result_for_context('terminal', result))
        finally:
            module._shaping_config = original
