"""Fixture tests for the native-control admission candidate.

Runs against the REAL gateway.status/control_socket code paths and real
sqlite stores in a disposable home; the gateway itself is emulated only by a
real AF_UNIX listener at ``<home>/gateway.sock`` answering canned
``active-work`` envelopes (the peer is the unit under test's counterparty,
not the unit). The end-to-end case runs BOTH admission scripts as real
processes under the launcher-selected interpreter on the same fixture home —
run-selected.py's own bootstrap cannot run against a fixture home because
``hermes_bootstrap`` provisions any unknown data root.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/yqecea/.hermes/hermes-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util

_ADMISSION_SOURCE = Path(os.environ.get("SOURCE_ADMISSION", str(Path(__file__).resolve().parents[2] / "scripts/admission.py")))
_spec = importlib.util.spec_from_file_location("qualified_admission", _ADMISSION_SOURCE)
admission = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(admission)

REPO = Path(__file__).resolve().parents[2]
OLD_HELPER = Path(__file__).resolve().parent / "fixtures/admission-recency-baseline.py"
NEW_HELPER = _ADMISSION_SOURCE
SILENCE = object()


def _json_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _json_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_keys(item)


class _ControlSocket(threading.Thread):
    """Minimal AF_UNIX listener answering each request line with one canned envelope."""

    def __init__(self, home, result):
        super().__init__(daemon=True)
        self._result = result
        path = Path(home) / "gateway.sock"
        path.unlink(missing_ok=True)
        self._srv = socket.socket(socket.AF_UNIX)
        self._srv.bind(str(path))
        self._srv.listen(8)
        self._stop = threading.Event()
        self.start()

    def run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                conn.settimeout(5)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
                if self._result is SILENCE:
                    time.sleep(4)
                    return
                payload = self._result if isinstance(self._result, dict) and "ok" in self._result else {
                    "ok": True, "protocol": 1, "id": 1, "result": self._result}
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            except OSError:
                pass

    def close(self):
        self._stop.set()
        self._srv.close()


def _write_profile(home, *, sessions=(), messages=(), executions=(), jobs=()):
    home = Path(home)
    (home / "cron").mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(home / "state.db")) as db:
        db.execute("DROP TABLE IF EXISTS sessions")
        db.execute("DROP TABLE IF EXISTS messages")
        db.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, session_key TEXT, "
                   "started_at REAL, ended_at REAL, last_activity_at REAL)")
        db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id INTEGER, timestamp REAL)")
        db.execute("CREATE INDEX idx_messages_session ON messages(session_id)")
        db.executemany("INSERT INTO sessions(session_key, started_at, ended_at, last_activity_at) "
                       "VALUES (?,?,?,?)", sessions)
        db.executemany("INSERT INTO messages(session_id, timestamp) VALUES (?,?)", messages)
        db.commit()
    with closing(sqlite3.connect(home / "cron/executions.db")) as db:
        db.execute("DROP TABLE IF EXISTS executions")
        db.execute("CREATE TABLE executions(id TEXT, job_id TEXT, status TEXT)")
        db.executemany("INSERT INTO executions VALUES (?,?,?)", executions)
        db.commit()
    (home / "cron/jobs.json").write_text(json.dumps({"jobs": list(jobs)}), encoding="utf-8")


def _build_root(tmp, *, pid, fresh=True, gateway_state="running", active_agents=0,
                restart_requested=False, served=("default", "sqecea", "yqecea"),
                platforms=None, profiles=None):
    root = Path(tmp) / ".hermes"
    root.mkdir(parents=True, exist_ok=True)
    updated = datetime.now(timezone.utc) - (timedelta(seconds=1) if fresh else timedelta(seconds=600))
    status = {"gateway_state": gateway_state, "active_agents": active_agents, "pid": pid,
              "updated_at": updated.isoformat(), "restart_requested": restart_requested,
              "served_profiles": list(served),
              "platforms": platforms if platforms is not None else {
                  "telegram": {"state": "connected"},
                  "sqecea:telegram": {"state": "connected"},
                  "yqecea:telegram": {"state": "connected"}}}
    (root / "gateway_state.json").write_text(json.dumps(status), encoding="utf-8")
    profiles = profiles or {}
    for name, home in (("default", root), ("sqecea", root / "profiles/sqecea"),
                       ("yqecea", root / "profiles/yqecea")):
        _write_profile(home, **profiles.get(name, {}))
    return root


class AdmissionCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pid = os.getpid()
        self._saved_home = os.environ.get("HERMES_HOME")
        self._socket = None
        self.addCleanup(self._restore_env)
        self.addCleanup(self._close_socket)

    def _restore_env(self):
        if self._saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._saved_home

    def _close_socket(self):
        if self._socket is not None:
            self._socket.close()

    def _evaluate(self, *, answer="__default__", **kwargs):
        self._close_socket()
        self._socket = None
        root = _build_root(self._tmp.name, pid=self.pid, **kwargs)
        os.environ["HERMES_HOME"] = str(root)
        if answer == "__none__":
            (root / "gateway.sock").unlink(missing_ok=True)
        else:
            result = {"active_agents": 0, "active_work": [], "pid": self.pid} \
                if answer == "__default__" else answer
            self._socket = _ControlSocket(root, result)
        return root, admission.evaluate(root)

    def test_recent_idle_admitted_where_old_veto_would_refuse(self):
        recent = time.time() - 30
        _, record = self._evaluate(profiles={
            "default": {"sessions": [("agent:main", recent - 60, None, recent)]}})
        self.assertTrue(record["idle_admitted"], record["blocked_reasons"])
        item = record["profiles"]["default"]
        self.assertEqual(item["open_gateway_session_metadata_count"], 1)
        self.assertLess(item["seconds_since_last_activity"], 120)
        # The retired predicate demonstrably fires on this exact fixture.
        self.assertTrue(item["open_gateway_session_metadata_count"]
                        and item["seconds_since_last_activity"] < 120)
        self.assertEqual(record["control_active_work"]["active_work_count"], 0)

    def test_delegation_unit_refused_with_zero_agent_count(self):
        unit = {"kind": "delegation", "delegation_id": "d-1", "goal": "SENTINEL-GOAL-SECRET",
                "pid": self.pid, "elapsed_s": 12.5}
        _, record = self._evaluate(
            answer={"active_agents": 0, "active_work": [unit], "pid": self.pid})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control reports live gateway work", record["blocked_reasons"])
        self.assertEqual(record["control_active_work"]["active_work_kind_counts"], {"delegation": 1})
        self.assertNotIn("SENTINEL-GOAL-SECRET", json.dumps(record))

    def test_chat_and_cron_units_refused(self):
        for unit in ({"kind": "chat", "session": "SENTINEL-SESSION", "pid": self.pid},
                     {"kind": "cron", "job_id": "SENTINEL-JOB", "pid": self.pid},
                     {"kind": "managed", "pid": self.pid},
                     {"kind": "api", "pid": self.pid},
                     {"kind": "deferred", "pid": self.pid}):
            with self.subTest(kind=unit["kind"]):
                _, record = self._evaluate(
                    answer={"active_agents": 1, "active_work": [unit], "pid": self.pid})
                self.assertFalse(record["idle_admitted"])
                self.assertIn("control reports live gateway work", record["blocked_reasons"])
                self.assertNotIn("SENTINEL", json.dumps(record))

    def test_cron_execution_active_refused(self):
        _, record = self._evaluate(profiles={
            "default": {"executions": [("exec-1", "job-1", "running")]}})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("default: cron active or overdue/due-now", record["blocked_reasons"])

    def test_cron_due_now_refused(self):
        due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        _, record = self._evaluate(profiles={
            "default": {"jobs": [{"id": "job-due", "enabled": True, "next_run_at": due,
                                  "state": "idle"}]}})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("default: cron active or overdue/due-now", record["blocked_reasons"])

    def test_platform_unready_refused(self):
        _, record = self._evaluate(platforms={
            "telegram": {"state": "connected"}, "sqecea:telegram": {"state": "connected"},
            "yqecea:telegram": {"state": "starting"}})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("yqecea: metadata/readiness gate failed", record["blocked_reasons"])

    def test_stale_status_refused(self):
        _, record = self._evaluate(fresh=False)
        self.assertFalse(record["idle_admitted"])
        self.assertIn("gateway not freshly idle", record["blocked_reasons"])

    def test_old_heartbeat_count_does_not_override_current_idle_answer(self):
        _, record = self._evaluate(active_agents=2)
        self.assertTrue(record["idle_admitted"], record["blocked_reasons"])
        self.assertEqual(record["active_agents"], 2)
        self.assertEqual(record["control_active_work"]["active_agents"], 0)

    def test_current_work_refused_even_when_heartbeat_says_idle(self):
        _, record = self._evaluate(active_agents=0, answer={
            "active_agents": 1, "active_work": [{"kind": "managed"}], "pid": self.pid})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control reports live gateway work", record["blocked_reasons"])

    def test_malformed_heartbeat_count_is_not_idle(self):
        for value in [False, None, "0", -1]:
            _, record = self._evaluate(active_agents=value)
            self.assertFalse(record["idle_admitted"])


    def test_control_pid_mismatch_refused(self):
        _, record = self._evaluate(
            answer={"active_agents": 0, "active_work": [], "pid": self.pid + 424242})
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control active-work answered by a different pid", record["blocked_reasons"])

    def test_control_exception_refuses_without_private_error_text(self):
        from unittest.mock import patch
        with patch("gateway.control_socket.query_gateway_control", side_effect=OSError("private sentinel")):
            _, record = self._evaluate()
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control active-work query failed: OSError", record["blocked_reasons"])
        self.assertNotIn("private sentinel", json.dumps(record))

    def test_control_unanswered_refused(self):
        _, record = self._evaluate(answer="__none__")
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control active-work unanswered", record["blocked_reasons"])

    def test_control_unresponsive_refused(self):
        _, record = self._evaluate(answer=SILENCE)
        self.assertFalse(record["idle_admitted"])
        self.assertIn("control active-work unanswered", record["blocked_reasons"])

    def test_control_malformed_refused(self):
        pid = self.pid
        bad = [
            {"ok": False, "error": "unknown verb: 'active-work'"},
            {"active_agents": "0", "active_work": [], "pid": pid},
            {"active_agents": True, "active_work": [], "pid": pid},
            {"active_agents": 0, "active_work": {}, "pid": pid},
            {"active_agents": 0, "active_work": ["oops"], "pid": pid},
            {"active_agents": 0, "active_work": [{"kind": 7}], "pid": pid},
            {"active_agents": 0, "active_work": [], "pid": True},
            {"active_agents": 0, "active_work": []},
        ]
        for answer in bad:
            with self.subTest(answer=answer):
                _, record = self._evaluate(answer=answer)
                self.assertFalse(record["idle_admitted"])
                self.assertTrue(any(r.startswith("control active-work")
                                    for r in record["blocked_reasons"]), record["blocked_reasons"])

    def test_no_unit_fields_leak(self):
        units = [{"kind": "chat", "session": "SENT-SESSION", "model": "SENT-MODEL",
                  "current_tool": "SENT-TOOL", "goal": "SENT-GOAL", "prompt": "SENT-PROMPT",
                  "text": "SENT-TEXT", "body": "SENT-BODY", "pid": self.pid}]
        _, record = self._evaluate(
            answer={"active_agents": 1, "active_work": units, "pid": self.pid})
        blob = json.dumps(record)
        for sentinel in ("SENT-SESSION", "SENT-MODEL", "SENT-TOOL", "SENT-GOAL", "SENT-PROMPT",
                         "SENT-TEXT", "SENT-BODY"):
            self.assertNotIn(sentinel, blob)
        for key in _json_keys(record):
            self.assertNotIn(key, {"goal", "session", "prompt", "text", "body",
                                   "delegation_id", "current_tool"})

    def test_end_to_end_old_refuses_new_admits(self):
        """Same fixture home: old helper refuses on the retired recency veto while
        the candidate admits, both run as real processes via run-selected.py."""
        sleeper = subprocess.Popen(["sleep", "300"])
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        recent = time.time() - 30
        root = _build_root(self._tmp.name, pid=sleeper.pid, profiles={
            "default": {"sessions": [("agent:main", recent - 60, None, recent)]}})
        self._socket = _ControlSocket(
            root, {"active_agents": 0, "active_work": [], "pid": sleeper.pid})
        # run-selected.py's verbatim mechanism: the launcher-selected command with its
        # runpy tail replacement. Env overrides CANNOT go to the subprocess env — the
        # bootstrap's prepare_launch(HERMES_HOME) provisions any unknown data root — so
        # the code flips HOME/HERMES_HOME in-process AFTER deps activate from the real root.
        command = json.loads(subprocess.check_output(
            ["hermes", "--print-runtime-command"], text=True))
        code_index = command.index("-c") + 1
        code = command[code_index]
        end = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
        self.assertTrue(code.endswith(end))
        command[code_index] = code[:-len(end)] + (
            f"os.environ.__setitem__('HERMES_HOME',{str(root)!r});"
            f"os.environ.__setitem__('HOME',{self._tmp.name!r});"
            "sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent)); "
            "runpy.run_path(sys.argv.pop(1), run_name='__main__')")
        env = {key: value for key, value in os.environ.items() if key != "HERMES_HOME"}

        def _run(script):
            return subprocess.run(command + [str(script)], env=env,
                                  capture_output=True, text=True, timeout=120)

        old_recent = _run(OLD_HELPER)
        self.assertEqual(old_recent.returncode, 1, old_recent.stderr)
        old_record = json.loads(old_recent.stdout)
        self.assertFalse(old_record["idle_admitted"])
        self.assertEqual(old_record["profiles"]["default"],
                         {"metadata_error_type": "ValueError"})
        self.assertEqual(old_record["blocked_reasons"], ["default: metadata/readiness gate failed"])

        new_recent = _run(NEW_HELPER)
        self.assertEqual(new_recent.returncode, 0, new_recent.stderr)
        new_record = json.loads(new_recent.stdout)
        self.assertTrue(new_record["idle_admitted"], new_record["blocked_reasons"])
        self.assertLess(new_record["profiles"]["default"]["seconds_since_last_activity"], 120)
        self.assertEqual(new_record["control_active_work"]["pid"], sleeper.pid)

        # Old helper on the identical home once activity ages past 120s: admits —
        # isolating the veto as the only changed gate.
        with closing(sqlite3.connect(root / "state.db")) as db:
            db.execute("UPDATE sessions SET last_activity_at=?", (time.time() - 600,))
            db.commit()
        old_aged = _run(OLD_HELPER)
        self.assertEqual(old_aged.returncode, 0, old_aged.stderr)
        self.assertTrue(json.loads(old_aged.stdout)["idle_admitted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
