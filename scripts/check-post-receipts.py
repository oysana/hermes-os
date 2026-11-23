#!/usr/bin/env python3
"""Outcome: every owner-shared X post is read and summarized, forever.

Converts the recurring one-time goal "read the posts I send" into a
standing outcome (owner 22.11: «я давно не видел разборы/саммари того что
я кидаю»). Self-maintaining: on each run it scans the profiles' state.db
read-only for status URLs the owner sent, appends unseen ones to
research/post-receipts.jsonl as "queued", then fails when any post has
waited longer than MAX_AGE_H without reaching a terminal state
(read/summarized/applied/deferred/rejected). Only URLs are extracted;
message bodies are never read.

Terminal states: receipted, summarized, applied, deferred, rejected.
"queued"/"read" older than MAX_AGE_H fails; fresher passes as INFO.
Exit 1 on failure. Missing DB or registry dir is a skip, not a failure
(CI and fresh checkouts have neither file).
"""
import json, re, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "research" / "post-receipts.jsonl"
DBS = [Path.home() / ".hermes" / "state.db",
       Path.home() / ".hermes" / "profiles" / "sqecea" / "state.db",
       Path.home() / ".hermes" / "profiles" / "yqecea" / "state.db"]
MAX_AGE_H = 48
# Posts shared before the outcome existed are a known backlog, not a
# violation: they register as "legacy" (terminal for the gate, reported
# as backlog). Anything newer must be receipted within MAX_AGE_H.
CUTOFF = 1795356000.0  # 2026-11-22T19:00:00+05:00, outcome creation
STATUS_RE = re.compile(r"https?://(?:www\.)?x\.com/([A-Za-z0-9_]+)/status/(\d+)")
TERMINAL = {"receipted", "summarized", "applied", "deferred", "rejected", "legacy"}


def load_registry():
    rows = {}
    if REGISTRY.exists():
        for line in REGISTRY.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows[row["id"]] = row  # append-only file: last row wins
    return rows


def scan_owner_posts():
    found = {}
    for db in DBS:
        if not db.exists():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for ts, content in con.execute(
                    "SELECT timestamp, content FROM messages WHERE role='user' "
                    "AND content LIKE '%x.com%'"):
                for author, sid in STATUS_RE.findall(content or ""):
                    if sid not in found or ts < found[sid][0]:
                        found[sid] = (ts, author)
        except sqlite3.Error:
            continue
        finally:
            con.close()
    return found


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdb = Path(td) / "s.db"
        con = sqlite3.connect(tdb)
        con.execute("CREATE TABLE messages(timestamp REAL, role TEXT, content TEXT)")
        con.execute("INSERT INTO messages VALUES (?,?,?)",
                    (time.time() - 3 * 86400, "user", "see https://x.com/a/status/1"))
        con.commit(); con.close()
        global DBS, REGISTRY
        DBS = [tdb]
        REGISTRY = Path(td) / "reg.jsonl"
        REGISTRY.write_text(json.dumps({"id": "1", "state": "queued",
                                        "received_at": time.time() - 3 * 86400}) + "\n")
        assert run() == 1, "old queued post must fail"
        REGISTRY.write_text(json.dumps({"id": "1", "state": "receipted",
                                        "received_at": time.time() - 3 * 86400}) + "\n")
        assert run() == 0, "receipted post must pass"
        fresh = Path(td) / "empty.db"  # no unseen posts: registry-only eval
        DBS = [fresh]
        REGISTRY.unlink()
    print("check-post-receipts selftest OK")
    return 0


def run():
    rows = load_registry()
    found = scan_owner_posts()
    if not found and not any(db.exists() for db in DBS):
        print("check-post-receipts: no profile state.db, skip")
        return 0
    new = [{"id": sid, "author": author, "received_at": ts,
            "state": "queued" if ts >= CUTOFF else "legacy"}
           for sid, (ts, author) in sorted(found.items()) if sid not in rows]
    if new:
        with REGISTRY.open("a") as f:
            for row in new:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows.update({r["id"]: r for r in new})
    now, red, info, legacy = time.time(), [], [], 0
    for sid, row in sorted(rows.items()):
        state = row.get("state", "queued")
        if state == "legacy":
            legacy += 1
            continue
        if state in TERMINAL:
            continue
        age_h = (now - row.get("received_at", now)) / 3600
        (red if age_h > MAX_AGE_H else info).append(
            f"{sid} @{row.get('author','?')} {state} {age_h:.0f}h")
    for line in red:
        print("RED  unreceipted post:", line)
    for line in info:
        print("INFO pending:", line)
    done = len(rows) - len(red) - len(info) - legacy
    print(f"post-receipts: {done} terminal, {len(info)} pending, "
          f"{len(red)} overdue, {legacy} legacy backlog")
    return 1 if red else 0


def main():
    return selftest() if "--selftest" in sys.argv else run()


if __name__ == "__main__":
    sys.exit(main())
