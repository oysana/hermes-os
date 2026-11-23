"""Fixture tests for scripts/api-keys/install_api_keys.py.

Run inside the selected Hermes interpreter (tests/api-keys/run.py via
run-selected.py) so hermes_cli/agent/tools imports resolve. Every test builds a
disposable <tmp>/.hermes root — nothing touches the real ~/.hermes.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "api-keys"))
import install_api_keys  # noqa: E402

from agent.secret_scope import (  # noqa: E402
    build_profile_secret_scope, invalidate_env_file_cache, load_env_file)
import hermes_cli.config as hermes_config  # noqa: E402

OPENAI_NEW = "sk-test-openai-NEWVALUE0000001"
OPENAI_OLD = "sk-test-openai-OLDVALUE9999999"
GEMINI_NEW = "AIza-test-gemini-NEWVALUE00002"


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._env_before = dict(os.environ)
        self.tmp = Path(tempfile.mkdtemp(prefix="api-keys-test-"))
        self.root = self.tmp / ".hermes"
        (self.root / "profiles" / "sqecea").mkdir(parents=True)
        (self.root / "profiles" / "yqecea").mkdir(parents=True)
        self.env_h = self.root / ".env"
        self.env_s = self.root / "profiles" / "sqecea" / ".env"
        self.env_y = self.root / "profiles" / "yqecea" / ".env"
        self.key_files = self.tmp / "keys"
        self.key_files.mkdir()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        invalidate_env_file_cache()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def key_file(self, name: str, content: str) -> Path:
        path = self.key_files / name
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = install_api_keys.main(list(argv))
        invalidate_env_file_cache()
        return code, out.getvalue(), err.getvalue()

    def base_args(self, *extra: str) -> list[str]:
        return ["--hermes-root", str(self.root), *extra]

    def openai_args(self, *extra: str) -> list[str]:
        return self.base_args("--openai-key-file",
                              str(self.key_file("openai.key", OPENAI_NEW)), *extra)


class TestValidation(_Base):
    def test_multiline_file_rejected_nothing_written(self) -> None:
        bad = self.key_file("openai.key", f"{OPENAI_NEW}\nsecond-line")
        code, _, err = self.run_main(
            *self.base_args("--openai-key-file", str(bad)))
        self.assertEqual(code, 2)
        self.assertIn("more than one line", err)
        self.assertFalse(self.env_h.exists() and OPENAI_NEW in self.env_h.read_text())

    def test_bad_values_rejected(self) -> None:
        for label, content in (
            ("empty", "   \n"),
            ("whitespace", "sk-part one-two-three"),
            ("quoted", f'"{OPENAI_NEW}"'),
            ("dotenv", f"OPENAI_API_KEY={OPENAI_NEW}"),
            ("nonascii", OPENAI_NEW + "ключ"),
            ("short", "sk-1"),
        ):
            with self.subTest(label=label):
                bad = self.key_file(f"{label}.key", content)
                code, _, _ = self.run_main(
                    *self.base_args("--openai-key-file", str(bad)))
                self.assertEqual(code, 2, label)

    def test_missing_file_rejected(self) -> None:
        code, _, _ = self.run_main(
            *self.base_args("--openai-key-file", str(self.tmp / "absent.key")))
        self.assertEqual(code, 2)

    def test_no_keys_is_usage_error(self) -> None:
        code, _, _ = self.run_main(*self.base_args())
        self.assertEqual(code, 2)


class TestWrites(_Base):
    def test_updates_existing_and_preserves_unrelated(self) -> None:
        for env in (self.env_h, self.env_s, self.env_y):
            _write(env, "# keep me\n"
                        f"OPENAI_API_KEY={OPENAI_OLD}\n"
                        "OTHER_TOKEN=zzz-untouched\n"
                        "export FOO=bar\n", 0o640)
        code, out, _ = self.run_main(
            *self.openai_args("--gemini-key-file",
                             str(self.key_file("gemini.key", GEMINI_NEW))))
        self.assertEqual(code, 0, out)
        for env in (self.env_h, self.env_s, self.env_y):
            parsed = load_env_file(env)
            self.assertEqual(parsed["OPENAI_API_KEY"], OPENAI_NEW)
            self.assertEqual(parsed["GEMINI_API_KEY"], GEMINI_NEW)
            self.assertEqual(parsed["OTHER_TOKEN"], "zzz-untouched")
            self.assertEqual(parsed["FOO"], "bar")
            self.assertIn("# keep me", env.read_text())

    def test_fills_empty_value(self) -> None:
        _write(self.env_h, "OPENAI_API_KEY=\n")
        code, out, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0, out)
        self.assertEqual(load_env_file(self.env_h)["OPENAI_API_KEY"], OPENAI_NEW)
        self.assertEqual(load_env_file(self.env_s)["OPENAI_API_KEY"], OPENAI_NEW)

    def test_duplicate_lines_collapsed(self) -> None:
        _write(self.env_s,
               f"export OPENAI_API_KEY={OPENAI_OLD}\n"
               f"OPENAI_API_KEY={OPENAI_OLD}2\n")
        code, out, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0, out)
        lines = [l for l in self.env_s.read_text().splitlines()
                 if "OPENAI_API_KEY" in l]
        self.assertEqual(len(lines), 1, self.env_s.read_text())
        self.assertEqual(load_env_file(self.env_s)["OPENAI_API_KEY"], OPENAI_NEW)

    def test_symlinked_env_writes_through_and_keeps_link(self) -> None:
        target = self.tmp / "real.env"
        _write(target, "OPENAI_API_KEY=\nOTHER=keep\n", 0o600)
        self.env_y.symlink_to(target)
        code, _, _ = self.run_main(*self.openai_args("--profile", "yqecea"))
        self.assertEqual(code, 0)
        self.assertTrue(self.env_y.is_symlink())
        parsed = load_env_file(target)
        self.assertEqual(parsed["OPENAI_API_KEY"], OPENAI_NEW)
        self.assertEqual(parsed["OTHER"], "keep")

    def test_new_env_file_created_0600(self) -> None:
        code, _, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(self.env_s.stat().st_mode), 0o600)

    def test_existing_file_mode_preserved(self) -> None:
        _write(self.env_s, "OPENAI_API_KEY=\n", 0o640)
        code, _, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(self.env_s.stat().st_mode), 0o640)

    def test_config_yaml_mirror_scrubbed(self) -> None:
        _write(self.env_s, f"OPENAI_API_KEY={OPENAI_OLD}\n")
        _write(self.root / "profiles" / "sqecea" / "config.yaml",
               f"model:\n  api_key: {OPENAI_OLD}\n"
               f"providers:\n  openai-api:\n    api_key: {OPENAI_OLD}\n")
        code, out, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0, out)
        text = (self.root / "profiles" / "sqecea" / "config.yaml").read_text()
        self.assertNotIn(OPENAI_OLD, text)
        self.assertIn(OPENAI_NEW, text)

    def test_credential_pool_materialized(self) -> None:
        code, out, _ = self.run_main(*self.openai_args("--profile", "sqecea"))
        self.assertEqual(code, 0, out)
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(self.root / "profiles" / "sqecea"))
        try:
            from agent.credential_pool import load_pool

            pool = load_pool("openai-api")
            self.assertIsNotNone(pool)
            self.assertTrue(pool.has_credentials())
        finally:
            reset_hermes_home_override(token)


class TestFailures(_Base):
    def test_missing_profile_home_is_explicit_failure(self) -> None:
        import shutil

        shutil.rmtree(self.root / "profiles" / "yqecea")
        code, out, _ = self.run_main(*self.openai_args())
        self.assertEqual(code, 1)
        self.assertIn("yqecea", out)
        self.assertIn("error", out)
        self.assertEqual(load_env_file(self.env_h)["OPENAI_API_KEY"], OPENAI_NEW)
        self.assertEqual(load_env_file(self.env_s)["OPENAI_API_KEY"], OPENAI_NEW)


class TestDryRun(_Base):
    def test_dry_run_changes_nothing(self) -> None:
        _write(self.env_h, f"OPENAI_API_KEY={OPENAI_OLD}\n")
        before = self.env_h.read_text()
        code, out, _ = self.run_main(*self.openai_args("--dry-run"))
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertEqual(self.env_h.read_text(), before)
        self.assertFalse(self.env_s.exists())


class TestVerify(_Base):
    def test_verify_confirms_scope_resolution(self) -> None:
        code, out, _ = self.run_main(*self.openai_args("--verify"))
        self.assertEqual(code, 0, out)
        self.assertIn("scope=ok", out)

    def test_verify_negative_when_not_written(self) -> None:
        code, out, _ = self.run_main(*self.openai_args("--dry-run", "--verify"))
        self.assertEqual(code, 1)
        self.assertIn("MISS", out)

    def test_scope_matches_gateway_reader(self) -> None:
        code, _, _ = self.run_main(*self.openai_args())
        self.assertEqual(code, 0)
        for home in (self.root, self.root / "profiles" / "sqecea",
                     self.root / "profiles" / "yqecea"):
            scope = build_profile_secret_scope(home)
            self.assertEqual(scope.get("OPENAI_API_KEY"), OPENAI_NEW)


class TestSecretHygiene(_Base):
    def test_no_secret_in_any_output(self) -> None:
        for argv in (
            self.openai_args("--verify"),
            self.openai_args("--json", "--verify"),
            self.base_args("--openai-key-file", str(self.key_file("ok.key", OPENAI_NEW)),
                           "--gemini-key-file", str(self.key_file("g.key", GEMINI_NEW))),
        ):
            code, out, err = self.run_main(*argv)
            self.assertEqual(code, 0, err)
            for blob in (out, err):
                self.assertNotIn(OPENAI_NEW, blob)
                self.assertNotIn(GEMINI_NEW, blob)
                self.assertNotIn(OPENAI_OLD, blob)


class TestInteractive(_Base):
    def test_getpass_collects_missing_keys(self) -> None:
        import unittest.mock as mock

        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("getpass.getpass", side_effect=[OPENAI_NEW, ""]):
            code, out, _ = self.run_main(*self.base_args("--interactive"))
        self.assertEqual(code, 0, out)
        self.assertEqual(load_env_file(self.env_h)["OPENAI_API_KEY"], OPENAI_NEW)
        self.assertNotIn("GEMINI_API_KEY", load_env_file(self.env_h))


class TestLiveVerifySeam(_Base):
    def _stub_probe(self, glyph: str):
        import hermes_cli.doctor_connectivity as dc

        def fake(*_a, **_kw):
            return types.SimpleNamespace(lines=[[glyph, "X API", ""]])

        return fake

    def test_live_probe_ok(self) -> None:
        import hermes_cli.doctor_connectivity as dc
        import unittest.mock as mock

        with mock.patch.object(dc, "_probe_apikey_provider",
                               self._stub_probe("\033[32m✓\033[0m")):
            code, out, _ = self.run_main(*self.openai_args("--verify-live", "--json"))
        self.assertEqual(code, 0, out)
        self.assertIn('"status": "ok"', out)

    def test_live_probe_provider_refusal_fails(self) -> None:
        import hermes_cli.doctor_connectivity as dc
        import unittest.mock as mock

        with mock.patch.object(dc, "_probe_apikey_provider",
                               self._stub_probe("\033[31m✗\033[0m")):
            code, out, _ = self.run_main(*self.openai_args("--verify-live"))
        self.assertEqual(code, 1)


class TestWrapperPassthrough(unittest.TestCase):
    def test_wrapper_execs_run_selected_with_installer(self) -> None:
        import subprocess

        tmp = Path(tempfile.mkdtemp(prefix="api-keys-wrap-"))
        shim = tmp / "run_selected_shim.py"
        shim.write_text(
            "import sys\nprint('ARGV=' + '|'.join(sys.argv[1:]))\nsys.exit(7)\n")
        env = dict(os.environ, HERMES_RUN_SELECTED=str(shim))
        proc = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "api-keys" / "add-api-keys.sh"),
             "--dry-run", "--verify"],
            capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertIn("install_api_keys.py", proc.stdout)
        self.assertIn("--dry-run", proc.stdout)
        self.assertIn("--verify", proc.stdout)


if __name__ == "__main__":
    unittest.main()
