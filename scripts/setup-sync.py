#!/usr/bin/env python3
"""setup-sync — deterministic engine behind "setup as code" (owner 23.11.2026).

Subcommands: apply | pull | drift | verify. Locking, journaling and restart
policy live in the thin wrappers (scripts/setup-apply.sh, setup-pull.sh,
setup-lock.sh); this file only knows repo<->live file semantics.

Layout:
    setup/manifest.tsv        rows: kind<TAB>home_rel<TAB>flags
    setup/home/<home_rel>     repo-side copy (populated by `pull`, installed by `apply`)
    <root>/.setup-sync/       runtime: state.json, backups/<ts>/, journal.log
    <root>                    = SETUP_ROOT env, default $HOME

`file` rows: repo content wins on apply.
`dir` rows: apply copies repo files in; live extras are kept by default and
reported as EXTRA-LIVE drift; `--prune` deletes them (and live files whose
manifest row was removed since the previous apply).
`json-state` rows (volatile=cron): per-record config fields come from the
repo, runtime fields (volatile set) are carried over from live by record id;
live-only records are kept and flagged. Pull strips volatile fields so repo
stores configuration, not scheduler state.
"""
import argparse, filecmp, hashlib, json, os, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = Path(os.environ.get("SETUP_REPO", SCRIPT_DIR.parent)).resolve()
ROOT = Path(os.environ.get("SETUP_ROOT", str(Path.home()))).resolve()
MANIFEST = REPO / "setup" / "manifest.tsv"
TREE = REPO / "setup" / "home"
RUNTIME = ROOT / ".setup-sync"

CRON_VOLATILE = {
    "last_run_at", "last_status", "failure_streak", "next_run_at",
    "fire_claim", "monitor_state", "last_dispatch", "last_delivery_error",
    "last_delivery_unverified", "last_failure", "last_error", "state",
    "canonical_completions", "model_snapshot", "provider_snapshot",
}
DIR_EXCLUDE = {"__pycache__", ".DS_Store"}


class Row:
    def __init__(self, kind, home_rel, flags):
        self.kind, self.home_rel, self.flags = kind, home_rel, flags
        self.repo = TREE / home_rel
        self.live = ROOT / home_rel

    def __repr__(self):
        return f"{self.kind} {self.home_rel}"


def manifest_rows():
    rows = []
    for line in MANIFEST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        kind, home_rel = parts[0], parts[1]
        flags = parts[2].split(",") if len(parts) > 2 else []
        rows.append(Row(kind, home_rel, flags))
    return rows


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_files(root):
    if not root.is_dir():
        return set()
    out = set()
    for p in root.rglob("*"):
        if p.is_file() and not any(x in DIR_EXCLUDE for x in p.parts):
            out.add(p.relative_to(root))
    return out


# --- json-state (cron jobs.json) ---------------------------------------------

def _jobs(doc):
    if isinstance(doc, list):
        return doc, None
    if isinstance(doc, dict) and isinstance(doc.get("jobs"), list):
        return doc["jobs"], doc
    return None, None


def normalize_jobs(text):
    doc = json.loads(text)
    jobs, _ = _jobs(doc)
    if jobs is None:
        return doc
    norm = []
    for j in jobs:
        if isinstance(j, dict):
            norm.append({k: v for k, v in j.items() if k not in CRON_VOLATILE})
        else:
            norm.append(j)
    norm.sort(key=lambda j: json.dumps(j, sort_keys=True))
    return norm


def jobs_equal(repo_path, live_path):
    try:
        return normalize_jobs(repo_path.read_text()) == normalize_jobs(live_path.read_text())
    except Exception:
        return False


def merge_jobs(repo_path, live_path):
    """Return merged doc: config from repo, volatile fields carried from live
    by job id; live-only jobs appended (flagged by drift afterwards)."""
    repo_doc = json.loads(repo_path.read_text())
    repo_jobs, _ = _jobs(repo_doc)
    live_doc = json.loads(live_path.read_text()) if live_path.exists() else {}
    live_jobs, _ = _jobs(live_doc) if live_doc else ([], None)
    live_by_id = {j.get("id"): j for j in (live_jobs or []) if isinstance(j, dict) and j.get("id")}
    repo_ids = set()
    merged_jobs, kept_extra = [], []
    for j in (repo_jobs or []):
        if isinstance(j, dict) and j.get("id") in live_by_id:
            live = live_by_id[j["id"]]
            merged = dict(j)
            for f in CRON_VOLATILE:
                if f in live:
                    merged[f] = live[f]
            merged_jobs.append(merged)
        else:
            merged_jobs.append(j)
        if isinstance(j, dict):
            repo_ids.add(j.get("id"))
    for j in (live_jobs or []):
        if isinstance(j, dict) and j.get("id") not in repo_ids:
            kept_extra.append(j)
    merged_jobs += kept_extra
    if isinstance(repo_doc, dict):
        out = dict(repo_doc)
        out["jobs"] = merged_jobs
    else:
        out = merged_jobs
    return out, kept_extra


def strip_jobs(text):
    doc = json.loads(text)
    jobs, _ = _jobs(doc)
    if jobs is None:
        return doc
    for j in jobs:
        if isinstance(j, dict):
            for f in CRON_VOLATILE:
                j.pop(f, None)
    return doc


# --- compare ------------------------------------------------------------------

def compare_row(row):
    """One status per row: CLEAN | CHANGED | MISSING-REPO | MISSING-LIVE |
    EXTRA-LIVE (dir extras, json-state live-only records)."""
    statuses = []
    if row.kind != "dir" and not row.repo.exists() and not row.live.exists():
        return statuses  # row declared but not adopted anywhere yet
    if row.kind == "dir":
        repo_f, live_f = dir_files(row.repo), dir_files(row.live)
        changed = [str(r) for r in sorted(repo_f & live_f) if sha(row.repo / r) != sha(row.live / r)]
        missing_live = [str(r) for r in sorted(repo_f - live_f)]
        missing_repo = [str(r) for r in sorted(live_f - repo_f)]
        statuses += [("CHANGED", p) for p in changed]
        statuses += [("MISSING-LIVE", p) for p in missing_live]
        statuses += [("EXTRA-LIVE", p) for p in missing_repo]
    elif row.kind == "json-state":
        if not row.repo.exists():
            statuses.append(("MISSING-REPO", row.home_rel))
        elif not row.live.exists():
            statuses.append(("MISSING-LIVE", row.home_rel))
        elif not jobs_equal(row.repo, row.live):
            statuses.append(("CHANGED", row.home_rel))
        else:
            try:
                live_doc = json.loads(row.live.read_text())
                live_jobs, _ = _jobs(live_doc)
                repo_doc = json.loads(row.repo.read_text())
                repo_jobs, _ = _jobs(repo_doc)
                repo_ids = {j.get("id") for j in (repo_jobs or []) if isinstance(j, dict)}
                for j in (live_jobs or []):
                    if isinstance(j, dict) and j.get("id") not in repo_ids:
                        statuses.append(("EXTRA-LIVE", f"{row.home_rel}: job {j.get('id')}"))
            except Exception:
                pass
    else:
        if not row.repo.exists():
            statuses.append(("MISSING-REPO", row.home_rel))
        elif not row.live.exists():
            statuses.append(("MISSING-LIVE", row.home_rel))
        elif sha(row.repo) != sha(row.live):
            statuses.append(("CHANGED", row.home_rel))
    return statuses


def cmd_drift(_args):
    bad = False
    for row in manifest_rows():
        for status, detail in compare_row(row):
            print(f"{status:14} {row.kind:10} {detail}")
            bad = True
    if not bad:
        print("drift: clean")
    return 1 if bad else 0


# --- verify --------------------------------------------------------------------

def verify_rows():
    """Parse checks for everything the repo side declares; returns failures."""
    failures = []
    for row in manifest_rows():
        if row.kind == "dir":
            continue
        src = row.repo
        if not src.exists():
            if row.live.exists():
                failures.append(f"{row.home_rel}: live file not adopted into repo")
            continue
        name = src.name
        try:
            if name.endswith((".yaml", ".yml")):
                import yaml
                yaml.safe_load(src.read_text())
            elif name.endswith(".json"):
                doc = json.loads(src.read_text())
                if row.kind == "json-state" and _jobs(doc)[0] is None:
                    failures.append(f"{row.home_rel}: no jobs list found")
            elif name.endswith(".toml"):
                import tomllib
                tomllib.loads(src.read_text())
        except Exception as e:
            failures.append(f"{row.home_rel}: parse error: {e}")
    return failures


def cmd_verify(_args):
    failures = verify_rows()
    for f in failures:
        print(f"FAIL {f}")
    if failures:
        return 1
    print(f"verify: {len(manifest_rows())} manifest rows parse clean")
    return 0


# --- apply / pull --------------------------------------------------------------

def _cp(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def backup_manifest(rows, backup_dir):
    for row in rows:
        dst = backup_dir / row.home_rel
        if row.kind == "dir":
            if row.live.is_dir():
                shutil.copytree(row.live, dst, ignore=shutil.ignore_patterns(*DIR_EXCLUDE))
        elif row.live.exists():
            _cp(row.live, dst)


def restore_backup(rows, backup_dir):
    for row in rows:
        src = backup_dir / row.home_rel
        if row.kind == "dir":
            if src.is_dir():
                if row.live.exists():
                    shutil.rmtree(row.live)
                shutil.copytree(src, row.live)
        elif src.exists():
            _cp(src, row.live)


def apply_rows(rows, prune):
    applied_paths, kept, warnings = [], [], []
    for row in rows:
        if row.kind == "dir":
            for rel in sorted(dir_files(row.repo)):
                _cp(row.repo / rel, row.live / rel)
                applied_paths.append(str(row.live / rel))
            extras = sorted(dir_files(row.live) - dir_files(row.repo))
            if extras and prune:
                for rel in extras:
                    (row.live / rel).unlink()
                    warnings.append(f"PRUNED {row.home_rel}/{rel}")
            elif extras:
                kept += [f"{row.home_rel}/{r}" for r in extras]
        elif row.kind == "json-state":
            if not row.repo.exists():
                warnings.append(f"MISSING-REPO {row.home_rel} (skip)")
                continue
            if row.live.exists():
                merged, extra = merge_jobs(row.repo, row.live)
            else:
                merged, extra = json.loads(row.repo.read_text()), []
            row.live.parent.mkdir(parents=True, exist_ok=True)
            tmp = row.live.with_suffix(row.live.suffix + ".tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
            tmp.replace(row.live)
            applied_paths.append(str(row.live))
            kept += [f"{row.home_rel}: job {j.get('id')}" for j in extra]
        else:
            if not row.repo.exists():
                warnings.append(f"MISSING-REPO {row.home_rel} (skip)")
                continue
            _cp(row.repo, row.live)
            applied_paths.append(str(row.live))
    return applied_paths, kept, warnings


def prune_orphans(rows, state, prune):
    """Remove live files whose manifest row vanished since the last apply."""
    if not prune:
        return []
    current = {r.home_rel for r in rows}
    gone = [p for p in state.get("applied_rows", []) if p not in current]
    removed = []
    for rel in gone:
        live = ROOT / rel
        if live.is_dir():
            shutil.rmtree(live)
        elif live.exists():
            live.unlink()
        removed.append(rel)
    return removed


def cmd_apply(args):
    rows = manifest_rows()
    failures = verify_rows()
    if failures:
        for f in failures:
            print(f"verify-repo FAIL {f}")
        return 2
    RUNTIME.mkdir(parents=True, exist_ok=True)
    backup_dir = RUNTIME / "backups" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-{os.getpid()}")
    backup_manifest(rows, backup_dir)
    try:
        applied, kept, warnings = apply_rows(rows, args.prune)
    except Exception as e:
        restore_backup(rows, backup_dir)
        print(f"apply error: {e}")
        print(f"rollback: restored from {backup_dir}")
        return 3
    # post-apply verify: applied content must match repo (json-state compares
    # config fields only; EXTRA-LIVE is reported by drift, not a verify failure)
    bad = []
    for row in rows:
        for status, detail in compare_row(row):
            if status in ("CHANGED", "MISSING-LIVE"):
                bad.append(f"{status} {row.home_rel}:{detail}")
    if bad:
        for b in bad:
            print(f"post-verify FAIL {b}")
        restore_backup(rows, backup_dir)
        print(f"rollback: restored from {backup_dir}")
        return 3
    removed = prune_orphans(rows, json.loads((RUNTIME / "state.json").read_text())
                            if (RUNTIME / "state.json").exists() else {}, args.prune)
    (RUNTIME / "state.json").write_text(json.dumps({
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_rows": [r.home_rel for r in rows],
    }, indent=2))
    for w in warnings:
        print(w)
    for r in removed:
        print(f"PRUNED-ORPHAN {r}")
    for k in kept:
        print(f"KEPT-LIVE-EXTRA {k} (drift will flag; pull+commit to keep, --prune to remove)")
    reload_rows = [r.home_rel for r in rows if "systemctl-reload" in r.flags]
    if reload_rows:
        print("note: systemd unit rows managed — run `systemctl --user daemon-reload` after apply")
    print(f"apply: {len(applied)} files installed, backup at {backup_dir}")
    return 0


def cmd_pull(_args):
    rows = manifest_rows()
    pulled = 0
    for row in rows:
        if row.kind == "dir":
            for rel in sorted(dir_files(row.live)):
                _cp(row.live / rel, row.repo / rel)
                pulled += 1
            extras = sorted(dir_files(row.repo) - dir_files(row.live))
            for rel in extras:  # live deleted it -> delete from repo
                (row.repo / rel).unlink()
                pulled += 1
        elif row.kind == "json-state":
            if not row.live.exists():
                continue
            row.repo.parent.mkdir(parents=True, exist_ok=True)
            doc = strip_jobs(row.live.read_text())
            row.repo.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
            pulled += 1
        else:
            if row.live.exists():
                _cp(row.live, row.repo)
                pulled += 1
    print(f"pull: {pulled} repo files updated from live")
    return 0


def main():
    ap = argparse.ArgumentParser(description="setup-sync engine")
    ap.add_argument("cmd", choices=["apply", "pull", "drift", "verify"])
    ap.add_argument("--prune", action="store_true",
                    help="delete live extras and orphaned rows (repo wins fully)")
    args = ap.parse_args()
    fn = {"apply": cmd_apply, "pull": cmd_pull, "drift": cmd_drift, "verify": cmd_verify}[args.cmd]
    sys.exit(fn(args))


if __name__ == "__main__":
    main()
