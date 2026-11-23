"""Run Y's existing offline watcher interfaces using the selected Hermes interpreter."""
from pathlib import Path
import os,sys,unittest
root=Path(os.environ.get('HERMES_Y_SCRIPTS_CHECK_ROOT',str(Path.home()/'.hermes/profiles/yqecea/scripts'))).resolve()
sys.path.insert(0,str(root))
suite=unittest.defaultTestLoader.discover(str(root),pattern='test_*.py',top_level_dir=str(root))
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
