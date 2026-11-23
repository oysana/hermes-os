import importlib.util
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'scripts/runtime-state-check.py'
spec = importlib.util.spec_from_file_location('runtime_state', SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class FixtureInputsTests(unittest.TestCase):
    def test_sample_strings_and_output_paths_are_not_required_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in runtime.CHECK_INPUTS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            sample = root / 'tests/example.py'
            sample.write_text("sample = '.artifacts/public-letter-v1.md'\n")
            output = root / 'scripts/probe.sh'
            output.parent.mkdir(exist_ok=True)
            output.write_text('mkdir -p .artifacts/new-probe/output\n')
            self.assertEqual(runtime.check_required_inputs(root), [])

    def test_deleted_or_directory_input_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in runtime.CHECK_INPUTS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            required = root / runtime.CHECK_INPUTS[0]
            required.unlink()
            self.assertEqual(len(runtime.check_required_inputs(root)), 1)
            self.assertIn(str(required), runtime.check_required_inputs(root)[0])
            required.mkdir()
            self.assertEqual(len(runtime.check_required_inputs(root)), 1)


if __name__ == '__main__':
    unittest.main()
