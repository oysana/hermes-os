import subprocess
import sys
import unittest
from pathlib import Path


class LearningOverflowTests(unittest.TestCase):
    def test_native_capture_recall_and_source_boundaries(self):
        script = Path(__file__).resolve().parents[2] / 'runtime/repairs/20261115-memory-continuity/test_overflow.py'
        result = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
