#!/usr/bin/env python3
"""Canonical task-state contract for the kanban board — read-only gate.

Source adoption: DeepSeek dsh-superagent-prototype core/ticket.py — one enum
as the system's mode ("неявные машины состояний — дефект", owner rule
07.11.2026); verdict TEST in source-lane FINDINGS-architecture §1.3: "ввести
один state-enum для task_runs … проверить на существующих переходах".

This is the safe staged candidate, deliberately separate from activation:
it makes the implicit machine explicit and enforces it on real data, but
does not change any live writer. Migrating writers to import these symbols
is the activation step — deferred on idle admission and owner decision.

Contract enforced on the live kanban.db:
  1. every task.status ∈ TASK_STATES;
  2. a reactivating event after the task's last terminal event is a
     violation. Late `worker_result` after completion is allowed: it is the
     at-least-once late-delivery record (input read-tracking semantic from
     the same source), it does not reopen the task.
"""
import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes/hermes-agent"))
from hermes_cli.kanban_workflow import DEFAULT_STATUSES as TASK_STATES  # noqa: E402
TERMINAL_EVENTS = ("completed", "archived")
REACTIVATING_EVENTS = ("claimed", "blocked", "unblocked", "worker_bound", "spawned")

SCHEMA = """
CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT, title TEXT);
CREATE TABLE task_events(id INTEGER PRIMARY KEY, task_id TEXT, run_id INT,
                         kind TEXT, payload TEXT, created_at INT);
"""


def violations(db_path: Path) -> list[str]:
    out = []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    for status, in db.execute("SELECT DISTINCT status FROM tasks"):
        if status not in TASK_STATES:
            out.append(f"off-enum task status: {status!r}")
    term = ("SELECT task_id, MAX(id) FROM task_events "
            f"WHERE kind IN ({','.join('?' * len(TERMINAL_EVENTS))}) GROUP BY task_id")
    reac = (f"SELECT e.task_id, e.kind, t.status FROM task_events e "
            f"JOIN ({term}) term ON term.task_id = e.task_id "
            "JOIN tasks t ON t.id = e.task_id "
            "WHERE e.id > term.[MAX(id)] AND "
            f"e.kind IN ({','.join('?' * len(REACTIVATING_EVENTS))})")
    for task_id, kind, status in db.execute(reac, TERMINAL_EVENTS + REACTIVATING_EVENTS):
        out.append(f"reactivation after terminal: {task_id} event {kind!r} (status {status})")
    db.close()
    return out


def _selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "kanban.db"
        db = sqlite3.connect(db_path)
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO tasks VALUES(?,?,?)", [
            ("ok1", "done", "t"), ("ok2", "blocked", "t"), ("bad1", "in_progress", "t"),
            ("bad2", "done", "t"), ("ok3", "done", "t"),
        ])
        db.executemany("INSERT INTO task_events(task_id,kind) VALUES(?,?)", [
            ("ok1", "claimed"), ("ok1", "completed"), ("ok1", "worker_result"),
            ("bad1", "created"), ("bad2", "completed"), ("bad2", "claimed"),
            ("ok3", "completed"), ("ok3", "commented"),
        ])
        db.commit(); db.close()
        got = violations(db_path)
    want_bad_status = any("in_progress" in v for v in got)
    want_reac = any("bad2" in v and "claimed" in v for v in got)
    want_clean = not any(("ok1" in v or "ok3" in v) for v in got)
    if want_bad_status and want_reac and want_clean and len(got) == 2:
        print("task-state-check selftest: enum + terminal-finality + late-result-allow ok")
        return 0
    print(f"selftest FAIL: {got}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(Path.home() / ".hermes/kanban.db"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    problems = violations(Path(args.db))
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"task-state-check: contract holds ({len(TASK_STATES)} states, terminal finality, late results allowed)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
