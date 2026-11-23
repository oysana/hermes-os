#!/usr/bin/env python3
"""Synthetic behavior tests for the proof_check.py candidate parser.

All fixtures are self-contained; T3 executions are passed to judge() in memory.
No runtime reads: run PROOF_CHECK_SOURCE=/path/to/proof_check.py to select the
module under test (default: the installed native script).
"""
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

TARGET = Path(os.environ.get("PROOF_CHECK_SOURCE", Path.home() / ".hermes/scripts/proof_check.py"))
_spec = importlib.util.spec_from_file_location("proof_check_under_test", TARGET)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def report(command, rc="0", output="ok (лог: /tmp/x.log)", files="src/a.py", gaps=True):
    """PROOF block; gaps=True puts a blank line after «Итог», the shape that truncated."""
    sep = "\n\n" if gaps else "\n"
    return (
        "PROOF\n"
        "Итог: проверка пройдена" + sep +
        "Как проверил: " + command + "\n"
        "Код возврата: " + rc + "\n"
        "Что показала проверка: " + output + "\n"
        "PR: нет | коммит: ab12cd3 | CI: нет CI\n"
        "Доказательство: /tmp/x.log\n"
        "Что изменено: " + files + "\n"
        "Почему сломалось: не исправление\n"
        "Не проверено: ничего\n"
    )


def missing(text, executions=(), **kw):
    _mode, _rows, miss = pc.judge(text, executions=list(executions), **kw)
    return miss


class BlankSeparatedFields(unittest.TestCase):
    """New behavior: a blank line does not truncate the block; keys still bound it."""

    def test_fields_after_blank_line_parse(self):
        miss = missing(report("bash check.sh"), executions=[("bash check.sh", 0, "")])
        self.assertEqual([], miss)

    def test_blank_lines_between_every_field(self):
        text = report("`gh pr view 141 --json state,mergeCommit -q .state`").replace("\nК", "\n\nК")
        miss = missing(text, executions=[("gh pr view 141 --json state,mergeCommit -q .state", 0, "")])
        self.assertEqual([], miss)

    def test_trailing_prose_after_blank_ignored(self):
        text = report("bash check.sh") + "\nРиск: низкий, описание свободным текстом.\n"
        fields = pc.parse_block(text)
        self.assertEqual("src/a.py", fields.get("files"))
        self.assertNotIn("низкий", fields.get("root", ""))

    def test_field_value_continues_on_next_line(self):
        miss = missing(report("bash check.sh", output="строка один\nстрока два", gaps=False),
                       executions=[("bash check.sh", 0, "")])
        self.assertEqual([], miss)


class ShellSyntaxIsData(unittest.TestCase):
    """Commas, quotes and telemetry inside «как проверил» keep shell meaning."""

    def test_comma_inside_json_argument_is_one_claim(self):
        claims = pc.claim_commands("gh pr view 141 --json state,mergeCommit -q .state")
        self.assertEqual(["gh pr view 141 --json state,mergeCommit -q .state"], claims)

    def test_backticked_comma_arguments_not_split(self):
        text = report("`gh pr view 141 --json state,mergeCommit -q .state`, `gh pr view 143 --json state,mergeCommit -q .state`", gaps=False)
        miss = missing(text, executions=[
            ("gh pr view 141 --json state,mergeCommit -q .state", 0, ""),
            ("gh pr view 143 --json state,mergeCommit -q .state", 0, ""),
        ])
        self.assertEqual([], miss)

    def test_quoted_semicolon_is_one_claim(self):
        claims = pc.claim_commands('python3 -c "print(1); print(2)"')
        self.assertEqual(1, len(claims))
        self.assertIn('print(1); print(2)', claims[0])
        miss = missing(report('python3 -c "print(1); print(2)"', gaps=False),
                       executions=[('python3 -c "print(1); print(2)', 0, "")])  # norm_cmd strips edge quotes
        self.assertEqual([], miss)

    def test_echo_telemetry_is_not_a_claim(self):
        self.assertEqual(
            ["./scripts/run.sh > /tmp/l 2>&1"],
            pc.claim_commands("./scripts/run.sh > /tmp/l 2>&1; echo EXIT=$?"))

    def test_ambiguous_russian_prose_is_not_silently_dropped(self):
        self.assertTrue(missing(report("bash check.sh, grep-проверка вывода", gaps=False),
                                executions=[("bash check.sh", 0, "")]))


class OwnExitEvidence(unittest.TestCase):
    """Pins that hold on installed and candidate alike, plus the echo-marker fixes."""

    def test_comma_separated_commands_each_verified(self):
        claims = pc.claim_commands("bash a.sh, bash b.sh")
        self.assertEqual(["bash a.sh", "bash b.sh"], claims)

    def test_multiple_claims_all_must_verify(self):
        miss = missing(report("bash a.sh, bash b.sh", gaps=False),
                       executions=[("bash a.sh", 0, "")])
        self.assertTrue(any("b.sh" in m and "не выполнялась" in m for m in miss), miss)

    def test_command_plus_echo_marker_verified(self):
        miss = missing(report("./scripts/run.sh > /tmp/l 2>&1; echo EXIT=$?", gaps=False),
                       executions=[("./scripts/run.sh > /tmp/l 2>&1; echo EXIT=$?", 0, "EXIT=0\nExited with code 0")])
        self.assertEqual([], miss)

    def test_failed_validation_then_successful_echo_is_mismatch(self):
        miss = missing(report("./scripts/run.sh; echo E=$?", gaps=False),
                       executions=[("./scripts/run.sh; echo E=$?", 0, "E=1")])
        self.assertTrue(any("кодом 1" in m for m in miss), miss)

    def test_marker_written_to_file_stays_unrecorded(self):
        miss = missing(report("./scripts/run.sh >> /tmp/g.log 2>&1; echo \"GATES_EXIT=$?\" >> /tmp/g.log", gaps=False),
                       executions=[("./scripts/run.sh >> /tmp/g.log 2>&1; echo \"GATES_EXIT=$?\" >> /tmp/g.log", 0, "")])
        self.assertTrue(any("не записал код возврата" in m and "run.sh" in m for m in miss), miss)

    def test_no_recorded_code_rejected(self):
        miss = missing(report("./scripts/run.sh > /tmp/l 2>&1", gaps=False),
                       executions=[("./scripts/run.sh > /tmp/l 2>&1", None, "")])
        self.assertTrue(any("не записал код возврата" in m for m in miss), miss)

    def test_masked_by_pipe_rejected(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("bash check.sh 2>&1 | tail -5", 0, "")])
        self.assertTrue(any("конвейером" in m for m in miss), miss)

    def test_masked_by_fallback_rejected(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("bash check.sh || echo failed", 0, "")])
        self.assertTrue(any("конвейером" in m for m in miss), miss)

    def test_wrong_command_rejected(self):
        miss = missing(report("npm test", gaps=False), executions=[("pytest", 0, "")])
        self.assertTrue(any("не выполнялась" in m for m in miss), miss)

    def test_recorded_nonzero_rejected(self):
        miss = missing(report("python3 -m unittest t", gaps=False),
                       executions=[("python3 -m unittest t > /tmp/o 2>&1", 1, "Exited with code 1")])
        self.assertTrue(any("кодом 1" in m for m in miss), miss)

    def test_claimed_nonzero_rejected(self):
        miss = missing(report("bash check.sh", rc="1", gaps=False),
                       executions=[("bash check.sh", 1, "")])
        self.assertTrue(any("не ноль" in m for m in miss), miss)

    def test_fake_echo_line_does_not_verify_command(self):
        miss = missing(report("bash check.sh", gaps=False), executions=[("echo EXIT=0", 0, "EXIT=0")])
        self.assertTrue(any("не выполнялась" in m for m in miss), miss)


class RecordedSegmentMatching(unittest.TestCase):
    """Recorded input is segmented like shell: compound, multiline and wrapped
    executions relate back to a verbatim claim; negatives keep their verdict."""

    def test_compound_cd_and_redirect_claim_verified(self):
        miss = missing(report("bash scripts/check.sh > /tmp/check.log 2>&1", gaps=False),
                       executions=[("cd /tmp/work && bash scripts/check.sh > /tmp/check.log 2>&1; echo CHECK_RC=$?", 0, "CHECK_RC=0")])
        self.assertEqual([], miss)

    def test_multiline_recorded_input_verified(self):
        miss = missing(report("bash scripts/check.sh > /tmp/check.log 2>&1", gaps=False),
                       executions=[("cd /tmp/work\nbash scripts/check.sh > /tmp/check.log 2>&1\necho CHECK_RC=$?", 0, "CHECK_RC=0")])
        self.assertEqual([], miss)

    def test_multiline_claim_field_splits_into_commands(self):
        text = report("cd /tmp/work\nbash scripts/check.sh > /tmp/check.log 2>&1\necho CHECK_RC=$?", gaps=False)
        miss = missing(text, executions=[("cd /tmp/work && bash scripts/check.sh > /tmp/check.log 2>&1; echo CHECK_RC=$?", 0, "CHECK_RC=0")])
        self.assertEqual([], miss)

    def test_env_assignment_prefix_verified(self):
        for recorded in ("result=ok python3 t.py", "RESULT=ok python3 t.py", "cd /x && A=1 B=two python3 t.py"):
            with self.subTest(recorded=recorded):
                self.assertEqual([], missing(report("python3 t.py", gaps=False),
                                             executions=[(recorded, 0, "")]))

    def test_timeout_and_wrapper_prefixes_verified(self):
        for recorded in ("timeout 600 bash scripts/check.sh", "timeout -k 5 600 bash scripts/check.sh",
                         "nice -n 10 bash scripts/check.sh", "env A=1 bash scripts/check.sh"):
            with self.subTest(recorded=recorded):
                self.assertEqual([], missing(report("bash scripts/check.sh", gaps=False),
                                             executions=[(recorded, 0, "")]))

    def test_bash_c_script_is_matched_inside(self):
        miss = missing(report("python3 t.py", gaps=False),
                       executions=[("/bin/bash -lc 'python3 t.py'", 0, "")])
        self.assertEqual([], miss)

    def test_fabricated_command_still_missing(self):
        miss = missing(report("npm test", gaps=False), executions=[("ls -la", 0, "")])
        self.assertTrue(any("не выполнялась" in m and "npm test" in m for m in miss), miss)

    def test_command_substitution_assignment_is_not_invocation(self):
        miss = missing(report("mktemp -d", gaps=False),
                       executions=[("t=$(mktemp -d) && bash scripts/check.sh", 0, "")])
        self.assertTrue(any("не выполнялась" in m for m in miss), miss)

    def test_heredoc_body_is_not_a_command(self):
        miss = missing(report("rm -rf /tmp/x", gaps=False),
                       executions=[("cat > /tmp/f <<'EOF'\nrm -rf /tmp/x\nEOF", 0, "")])
        self.assertTrue(any("не выполнялась" in m for m in miss), miss)

    def test_verbatim_heredoc_claim_verified(self):
        miss = missing(report("python3 - <<'EOF'\nimport sys\nsys.exit(0)\nEOF", gaps=False),
                       executions=[("python3 - <<'EOF'\nimport sys\nsys.exit(0)\nEOF", 0, "")])
        self.assertEqual([], miss)

    def test_wrong_recorded_exit_still_mismatch(self):
        miss = missing(report("python3 -m unittest t", gaps=False),
                       executions=[("cd /x && python3 -m unittest t > /tmp/o 2>&1", 1, "Exited with code 1")])
        self.assertTrue(any("кодом 1" in m for m in miss), miss)

    def test_pipe_inside_bash_c_still_masked(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("/bin/bash -lc \"bash check.sh 2>&1 | tail -5\"", 0, "")])
        self.assertTrue(any("конвейером" in m for m in miss), miss)

    def test_outer_pipe_masks_bash_c_inner(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("/bin/bash -lc 'bash check.sh' | tail -3", 0, "")])
        self.assertTrue(any("конвейером" in m for m in miss), miss)

    def test_semicolon_follower_stays_unrecorded(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("bash check.sh; echo done", 0, "")])
        self.assertTrue(any("не записал код возврата" in m for m in miss), miss)

    def test_background_command_stays_unrecorded(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("bash check.sh > /tmp/l 2>&1 &", 0, "")])
        self.assertTrue(any("не записал код возврата" in m for m in miss), miss)

    def test_if_condition_exit_absorbed(self):
        miss = missing(report("bash check.sh", gaps=False),
                       executions=[("if bash check.sh; then echo ok; fi", 0, "ok")])
        self.assertTrue(any("не записал код возврата" in m for m in miss), miss)


class Contract(unittest.TestCase):
    def test_exports(self):
        for name in ("judge", "thread_text", "run_executions", "brief_scope", "block_lines",
                     "payload_text", "TEMPLATE", "executions_after", "evidence", "claim_commands",
                     "own_exit", "parse_block", "legacy_fields", "resolve_refs", "selftest"):
            self.assertTrue(hasattr(pc, name), name)

    def test_native_selftest_green(self):
        self.assertEqual(0, pc.selftest())


class IndependentReviewRegressions(unittest.TestCase):
    def test_backticked_rc_remains_a_scalar(self):
        self.assertEqual([], missing(report('bash check.sh', rc='`0`'),
                                     executions=[('bash check.sh', 0, '')]))

    def test_markdown_never_hides_failed_validation(self):
        for command, executions in [
            ('`python check.py; echo EXIT=$?`', [('python check.py; echo EXIT=$?', 0, 'EXIT=1')]),
            ('`bash fail.sh; bash ok.sh`', [('bash fail.sh; bash ok.sh', 0, '')])]:
            with self.subTest(command=command):
                self.assertTrue(missing(report(command), executions=executions))

    def test_unicode_commands_are_never_dropped(self):
        for command in ['./check-тест.sh', 'python3 test-проверка.py']:
            with self.subTest(command=command):
                for execution in [[], [(command, 1, '')]]:
                    self.assertTrue(missing(report('bash ok.sh; ' + command),
                        executions=[('bash ok.sh', 0, '')] + execution))
                self.assertEqual([], missing(report(command), executions=[(command, 0, '')]))

    def test_telemetry_prefix_cannot_hide_work(self):
        for command in ['echo EXIT=$? | python3 verify.py', 'echo EXIT=$?$(python3 verify.py)']:
            with self.subTest(command=command):
                self.assertTrue(missing(report('bash ok.sh; ' + command),
                                        executions=[('bash ok.sh', 0, '')]))

    def test_escaped_quote_does_not_hide_separator(self):
        command = r'printf \"; false; echo EXIT=$?'
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('EXIT=1', result.stdout)
        self.assertTrue(missing(report(command), executions=[(command, result.returncode, result.stdout)]))

    def test_unicode_subshell_without_record_is_not_annotation(self):
        self.assertTrue(missing(report('bash ok.sh; (./check-тест.sh)'),
                                executions=[('bash ok.sh', 0, '')]))

    def test_failed_unicode_subshell_is_not_annotation(self):
        self.assertTrue(missing(report('bash ok.sh; (./check-тест.sh)'),
            executions=[('bash ok.sh', 0, ''), ('(./check-тест.sh)', 1, 'Exited with code 1')]))

    def test_key_spacing_does_not_change_markdown_semantics(self):
        command = '`python check.py; echo EXIT=$?`'
        executions = [('python check.py; echo EXIT=$?', 0, 'EXIT=1')]
        text = report(command)
        self.assertTrue(missing(text, executions=executions))
        self.assertTrue(missing(text.replace('Как проверил: ', 'Как проверил:'), executions=executions))


if __name__ == "__main__":
    unittest.main(verbosity=2)
