"""Run the api-keys fixture suite inside the selected Hermes interpreter.

Entry: ``python3 -B ~/.hermes/shared/proactive/run-selected.py tests/api-keys/run.py``
(same bootstrap the check.sh y-watchers/harness lanes use).
"""
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parent
suite = unittest.defaultTestLoader.discover(str(root), pattern='test_*.py', top_level_dir=str(root))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
