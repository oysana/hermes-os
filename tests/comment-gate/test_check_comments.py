#!/usr/bin/env python3
"""
Unit tests for check_comments.py.
All tests use temporary directories and pass paths via --root so they never read real files.
"""

import os
import subprocess
import sys
import tempfile
import unittest

# Import the module under test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_comments


class TestCheckComments(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name
        self.baseline_path = os.path.join(self.root, "baseline.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_file(self, rel_path, content):
        full_path = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return full_path

    def _run_cli(self, args):
        cmd = [
            sys.executable,
            "-B",
            check_comments.__file__,
            "--root",
            self.root,
            "--baseline",
            self.baseline_path,
        ] + args
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc

    def test_string_containing_todo_ignored(self):
        """String literals containing TODO or other flags are ignored (not comments)."""
        content = (
            'val = "TODO: this is a string literal"\n'
            'desc = \'FIXME: another string literal\'\n'
            '"""\n'
            'HACK: docstring should not be treated as a comment\n'
            '"""\n'
        )
        self._create_file("shared/proactive/test_code.py", content)

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(violations), 0)
        self.assertEqual(total_flagged, 0)

        # Also verify CLI execution
        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_python_todo_flagged(self):
        """Python comment matching '# TODO' without an external reference is flagged."""
        self._create_file(
            "shared/proactive/worker.py",
            "x = 1\n# TODO need to refactor this logic\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(violations), 1)
        self.assertEqual(total_flagged, 1)
        self.assertEqual(violations[0][1], 2)  # line 2
        self.assertIn("# TODO need to refactor this logic", violations[0][2])

        # CLI execution
        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("worker.py:2: new workaround comment without an external reference: # TODO need to refactor this logic", proc.stdout)

    def test_todo_with_url_allowed(self):
        """'# TODO https://x' is allowed because it has an external URL reference."""
        self._create_file(
            "shared/proactive/worker.py",
            "# TODO https://x\n# FIXME: http://example.com/ticket/123\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(violations), 0)
        self.assertEqual(total_flagged, 0)

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 0)

    def test_hack_with_issue_ref_allowed(self):
        """'# HACK see owner/repo#12' is allowed because of the issue reference."""
        self._create_file(
            "shared/proactive/worker.py",
            "# HACK see owner/repo#12\n# TODO see #456\n# WORKAROUND [HERMES-789] until upstream fix\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(violations), 0)
        self.assertEqual(total_flagged, 0)

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 0)

    def test_baseline_entry_passes(self):
        """An existing flagged comment recorded in baseline.json passes the check."""
        self._create_file(
            "shared/proactive/legacy.py",
            "# TODO legacy workaround\n",
        )

        # First verify it fails without baseline
        exit_code, violations, _ = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(violations), 1)

        # Regenerate baseline
        proc_wb = self._run_cli(["--write-baseline"])
        self.assertEqual(proc_wb.returncode, 0)
        self.assertTrue(os.path.exists(self.baseline_path))

        # Check passes with baseline
        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(violations), 0)
        self.assertEqual(total_flagged, 1)

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 0)

    def test_same_text_in_new_file_fails(self):
        """The same flagged text in a new file fails even if present in baseline for another file."""
        self._create_file(
            "shared/proactive/file_a.py",
            "# TODO identical comment text\n",
        )

        # Baseline file_a.py
        check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
            write_baseline_mode=True,
        )

        # Add file_b.py with identical comment text
        self._create_file(
            "shared/proactive/file_b.py",
            "# TODO identical comment text\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(violations), 1)
        self.assertEqual(total_flagged, 2)

        # Only file_b.py should be in violations
        self.assertIn("file_b.py", violations[0][0])
        self.assertNotIn("file_a.py", violations[0][0])

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("file_b.py", proc.stdout)
        self.assertNotIn("file_a.py", proc.stdout)

    def test_shell_comment_flagged(self):
        """Shell lines whose first non-space character is '#' are flagged if matching."""
        self._create_file(
            "scripts/deploy.sh",
            "#!/usr/bin/env bash\n   # TODO fix deployment kludge\necho done\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][1], 2)
        self.assertEqual(violations[0][2], "# TODO fix deployment kludge")

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("deploy.sh:2: new workaround comment without an external reference: # TODO fix deployment kludge", proc.stdout)

    def test_shebang_ignored(self):
        """The shebang on line 1 is ignored even if it matches keywords or pattern."""
        self._create_file(
            "scripts/run.sh",
            "#!/usr/bin/env bash TODO\necho 'hello world'\n",
        )
        self._create_file(
            "shared/proactive/script.py",
            "#!/usr/bin/env python3\nprint('hello world')\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(violations), 0)
        self.assertEqual(total_flagged, 0)

        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 0)

    def test_russian_keywords_flagged(self):
        """Russian workaround keywords ('костыл', 'временно') are flagged without ref."""
        self._create_file(
            "shared/proactive/service.py",
            "# костыль для safari\n# временно отключено\n",
        )

        exit_code, violations, total_flagged = check_comments.check_comments(
            root_dir=self.root,
            baseline_path=self.baseline_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(violations), 2)
        self.assertEqual(total_flagged, 2)


if __name__ == "__main__":
    unittest.main()
