#!/usr/bin/env python3
"""Offline contract tests for scripts/merge-guard.py.

The live proof (real gh + real repo + real 403) ran on the supported hook path;
these cases pin the policy offline: target detection, composite/ambiguous
refusal, green-at-head evidence semantics, and protection legibility mapping.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "merge_guard", ROOT / "scripts" / "merge-guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


MG = _load()


def fake_run(results):
    """results: list of CompletedProcess-like returns consumed in order."""
    it = iter(results)
    def _run(argv, cwd=None):
        return next(it)
    return _run


def cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class TestTargetDetection(unittest.TestCase):
    def test_ordinary_command_not_gated(self):
        self.assertIsNone(MG.merge_target("gh pr view 14"))
        self.assertIsNone(MG.merge_target("git status"))
        self.assertIsNone(MG.merge_target("gh pr checks 14"))

    def test_pr_merge_detected(self):
        self.assertEqual(MG.merge_target("gh pr merge 14 --squash"),
                         ("oysana/hermes-pro", "14"))

    def test_gh_api_merge_detected(self):
        self.assertEqual(
            MG.merge_target("gh api repos/x/y/pulls/3/merge -X PUT -f x=1"),
            ("x/y", None))

    def test_composite_refused(self):
        self.assertEqual(MG.merge_target("gh pr merge 14 | tee log"), "composite")
        self.assertEqual(MG.merge_target("cd x && gh pr merge 2"), "composite")

    def test_other_repo_not_gated(self):
        self.assertEqual(
            MG.merge_target("gh pr merge 9 -R example/other"), ("example/other", "9"))

    def test_quoted_merge_text_is_inert(self):
        # Harmless text that only reaches stdout or a commit message must pass:
        # the blocked 2026-11-15 native proof showed `echo 'gh pr merge …'`
        # falsely gated.
        self.assertIsNone(MG.merge_target("echo 'gh pr merge 15 --squash'"))
        self.assertIsNone(MG.merge_target('echo "gh pr merge 15"'))
        self.assertIsNone(MG.merge_target("echo 'gh pr merge 15' | tee log"))
        self.assertIsNone(MG.merge_target('git commit -m "docs: gh pr merge steps"'))
        self.assertIsNone(MG.merge_target("printf 'gh api pulls/3/merge'"))

    def test_evaluated_merge_still_gated(self):
        # A quoted payload is real when something evaluates it.
        self.assertEqual(MG.merge_target("sh -c 'gh pr merge 15'"),
                         ("oysana/hermes-pro", "15"))
        self.assertEqual(MG.merge_target("bash -c 'gh pr merge 15 --squash'"),
                         ("oysana/hermes-pro", "15"))
        self.assertEqual(MG.merge_target("eval 'gh pr merge 15'"),
                         ("oysana/hermes-pro", "15"))
        self.assertEqual(MG.merge_target("echo 'gh pr merge 15' | xargs -I{} {}"),
                         "composite")
        self.assertIsNone(MG.merge_target('echo "eval gh pr merge 15"'))


class TestGreenGate(unittest.TestCase):
    def _run_dir(self, root, name, checkout, log):
        d = Path(root) / ".artifacts" / "verification" / name
        d.mkdir(parents=True)
        (d / "checkout.txt").write_text(checkout)
        (d / "check.log").write_text(log)

    def test_green_at_exact_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_dir(tmp, "run-1", "abc123",
                          "== lane-a\nok  lane-a\n== lane-b\nok  lane-b\n"
                          "all checks passed\n")
            self.assertTrue(MG.green_gate_at_head(Path(tmp), "abc123"))

    def test_failed_lane_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_dir(tmp, "run-1", "abc123",
                          "== lane-a\nok  lane-a\n== lane-b\nFAIL lane-b\n"
                          "FAILED: lane-b\n")
            self.assertFalse(MG.green_gate_at_head(Path(tmp), "abc123"))

    def test_inner_red_suite_does_not_reject_green_gate(self):
        # A lane proving its red case prints test-level FAIL lines yet the
        # lane itself passes — the honest all-green log must be accepted.
        with tempfile.TemporaryDirectory() as tmp:
            self._run_dir(tmp, "run-1", "abc123",
                          "== lane-a\nFAIL: test_red_case\n"
                          "FAILED (failures=1)\nok  lane-a\n"
                          "== lane-b\nok  lane-b\nall checks passed\n")
            self.assertTrue(MG.green_gate_at_head(Path(tmp), "abc123"))

    def test_unmatched_step_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_dir(tmp, "run-1", "abc123",
                          "== lane-a\nok  lane-a\n== lane-b\n"
                          "all checks passed\n")
            self.assertFalse(MG.green_gate_at_head(Path(tmp), "abc123"))

    def test_wrong_head_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_dir(tmp, "run-1", "other", "all checks passed\n")
            self.assertFalse(MG.green_gate_at_head(Path(tmp), "abc123"))

    def test_missing_evidence_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(MG.green_gate_at_head(Path(tmp), "abc123"))


class TestProtectionLegibility(unittest.TestCase):
    def setUp(self):
        self._orig = MG.run

    def tearDown(self):
        MG.run = self._orig

    def test_requirements(self):
        MG.run = fake_run([cp(stdout='{"required_status_checks": {"contexts": ["ci"]}}')])
        self.assertEqual(MG.protection_legible("r", "b"), "requirements")

    def test_not_found_means_none(self):
        MG.run = fake_run([cp(stderr="gh: Not Found (HTTP 404)", rc=1)])
        self.assertEqual(MG.protection_legible("r", "b"), "none")

    def test_403_means_unknown(self):
        MG.run = fake_run([cp(stderr="gh: Upgrade to GitHub Pro (HTTP 403)", rc=1)])
        self.assertEqual(MG.protection_legible("r", "b"), "unknown")


class TestCommandParsing(unittest.TestCase):
    def test_hook_event_json(self):
        event = json.dumps({"tool_name": "exec",
                            "tool_input": {"command": "gh pr merge 14"}})
        self.assertEqual(MG.merge_target(
            MG.command_of.__self__ if False else
            json.loads(event)["tool_input"]["command"]), ("oysana/hermes-pro", "14"))

    def test_argv_command(self):
        self.assertEqual(MG.command_of(["--command", "gh pr merge 1"]),
                         "gh pr merge 1")
        self.assertEqual(MG.command_of(["--command=gh pr merge 1"]),
                         "gh pr merge 1")


if __name__ == "__main__":
    unittest.main()
