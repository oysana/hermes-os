#!/usr/bin/env python3
"""Select which check.sh lanes cover a change set — dev-loop helper.

Source adoption: corpus post @o_kwasniewski 2116237813582582072 (e2e CI
filters by PR-diff, "run when unsure" conservative default), verdict TEST in
research/x-posts-20261112/review.jsonl index 12 and lane-notes/NOTES.md V7.
Owner rule it serves: "run only the checks that cover the change" during
development; the full scripts/check.sh gate still runs once before install.

Deterministic baseline only — no model. Unmapped non-documentation paths are
"unsure": the script tells you to run the full gate, it never says "safe to
skip" for a path it does not understand. The lane list is read from
scripts/check.sh, and the selftest fails when a lane there has no mapping here.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERMES = Path.home() / ".hermes"
ACCEPTANCE = "proactivity-completion-20261012/prd-wave1/acceptance/"

# lane name (as in check.sh) -> path fragments that select it. A changed path
# selects a lane when a fragment is a substring of its normalized form
# (repo-relative, "/.hermes/<rel>" under ~/.hermes, else absolute). A fragment
# starting with "^" must match at the start instead.
LANES = {
    "plan-coverage": ("PLAN.md", "REQUESTS.md", "plan-coverage.py"),
    "file-size": ("^scripts/", "^tests/", "^runtime/", "check-file-size"),
    "laws": (".bend", "check-laws"),
    "native-memory": ("tests/native-memory", "hermes_state", "session_search_tool"),
    "acceptance-runner": (ACCEPTANCE, "profiles/yqecea/scripts/mail-radar.py", "verify-harness.py"),
    "konspekt": ("konspekt", "profiles/sqecea/scripts/"),
    "communications": ("shared/communications/", "profiles/yqecea/scripts/gmail-watch.sh", "gws-helpers"),
    "y-watchers": ("tests/y-watchers", "profiles/yqecea/scripts/"),
    "smartnation-intake": ("smartnation",),
    "acceptance-runner": (ACCEPTANCE, "profiles/yqecea/scripts/mail-radar.py", "verify-harness.py"),
    "acceptance-dry-run": (ACCEPTANCE,),
    "acceptance-boundaries": ("tests/acceptance-runner", ACCEPTANCE + "run_suite.py"),
    "video-pipeline": ("video-pipeline", "vo_video", "visual-output"),
    "harness": ("verify-harness.py", "run-selected.py", "CHARACTER.md", "SOUL", "config.yaml", "turn-brief"),
    "group-addressing": ("group-addressing", "addressing.py"),
    "comment-gate": ("comment-gate", "check_comments"),
    "post-audit": ("tests/post-audit", "check-post-decisions", "x-posts-"),
    "post-receipts": ("check-post-receipts", "post-receipts.jsonl"),
    "doc-paths": ("check-doc-paths", "AGENTS.md", "README.md", "shared/RULES.md", "check-rollback-links"),
    "admission-native": ("tests/admission", "scripts/admission.py", "run_active_work_verb"),
    "continuity": ("tests/continuity", "proof_check.py", "memory-audit.py", "apply-memory-proposals.py",
                   "t3-brief.py", "t3-watchdog.py", "20261115-skill-integrity", "20261115-return-brief",
                   "20261111-memory-audit", "20261115-memory-continuity"),
    "runtime-state-contracts": ("tests/runtime-state", "runtime-state-check"),
    "browser-access": ("browser-access", "helium-cdp-relay"),
    "runtime-state": ("runtime-state-check", "/skills/", "plugins/", "config.yaml"),
    "check-select": ("check-select", "scripts/check.sh"),
    "task-state": ("task-state-check",),
    "task-state-live": ("task-state-check", "kanban"),
    "merge-guard": ("merge-guard",),
    "agents-tools": ("tests/agents-tools", ".agents/bin/research-find"),
    "agents-update": ("tests/agents-update", ".agents/bin/agents-update"),
    "api-keys": ("api-keys",),
    "t3-harnesses": ("check-t3-harnesses", "tests/harness-native", "runtime/harness-config", "harness-usage",
                     "check-rollback-links", "compaction-floor", "t3-pstack/models.md", "ROLLBACK.md"),
    "outer-inner": ("tests/outer-inner", "t3-mcp-call", "t3_mcp_client", "route-policy"),
    "setup-as-code": ("setup/", "setup-sync", "setup-apply", "setup-pull", "setup-lock",
                      "setup-gateway-restart", "setup-as-code"),
    "custom-ratchet": ("custom-ratchet", "/.hermes/plugins/", "/.hermes/scripts/", ".agents/lib/"),
}
# Lanes that check every file of a kind; hitting only these does not mean a path is understood.
GENERIC = {"file-size"}

# Files that gate nothing: prose, evidence, corpus. Checked after the lanes, so a
# lane can still claim a file under these prefixes (the acceptance runner lives in .artifacts/).
NO_LANE_SUFFIXES = (".md", ".txt", ".html", ".jsonl")
NO_LANE_PREFIXES = (".artifacts/", "research/", "docs/", "verification/", "tests/technique-audit")


def gate_lanes() -> list[str]:
    """Lane names in check.sh order: the gate is the source of truth."""
    return re.findall(r"^step (\S+) ", (REPO / "scripts/check.sh").read_text(), re.M)


def normalize(path: str, cwd: Path) -> str:
    """Reduce a changed path to a form lane fragments match."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = cwd / p
    for base in (REPO, HERMES):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        return f"/{base.name}/{rel}" if base == HERMES else str(rel)
    return str(p)


def matches(fragment: str, path: str) -> bool:
    return path.startswith(fragment[1:]) if fragment.startswith("^") else fragment in path


def changed_files(repo: Path, base: str | None) -> list[str]:
    git = ["git", "-c", "core.quotepath=off"]
    cmds = [git + ["diff", "--name-only", "--diff-filter=ACMRD"] + ([base] if base else ["HEAD"]),
            git + ["diff", "--cached", "--name-only", "--diff-filter=ACMRD"],
            git + ["ls-files", "--others", "--exclude-standard"]]
    out = set()
    for cmd in cmds:
        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        out.update(x for x in res.stdout.splitlines() if x.strip())
    return sorted(out)


def select(paths: list[str], cwd: Path) -> tuple[set[str], list[str]]:
    lanes, unsure = set(), []
    for raw in paths:
        path = normalize(raw, cwd)
        hits = {lane for lane, frags in LANES.items() if any(matches(f, path) for f in frags)}
        lanes |= hits
        if hits - GENERIC:
            continue
        if any(path.startswith(pre) or f"/{pre}" in path for pre in NO_LANE_PREFIXES):
            continue
        if not path.endswith(NO_LANE_SUFFIXES):
            unsure.append(raw)
    return lanes, unsure


def _selftest() -> int:
    acceptance = ".artifacts/hermes-bridge-20261011/live-repair/" + ACCEPTANCE
    cases = [
        (["scripts/browser-access.py"], {"browser-access", "file-size"}, 0),
        ([str(HERMES / "scripts/route-policy-check.py")], {"outer-inner"}, 0),
        (["tests/continuity/test_watchdog.py"], {"continuity", "file-size"}, 0),
        (["README.md", "research/x-posts-20261112/REPORT.md"], set(), 0),
        (["weird/binary.bin"], set(), 1),
        (["scripts/check-select.py"], {"check-select"}, 0),
        # 16.11: the acceptance runner sits under .artifacts/ and was never selected.
        ([acceptance + "fixture_runner.py"], {"acceptance-runner", "acceptance-dry-run"}, 0),
        ([str(HERMES / "profiles/yqecea/scripts/mail-radar.py")], {"acceptance-runner", "y-watchers"}, 0),
        ([str(HERMES / "profiles/yqecea/scripts/gmail-watch.sh")], {"communications", "y-watchers"}, 0),
        ([str(HERMES / "profiles/yqecea/config.yaml")], {"harness", "runtime-state"}, 0),
        ([str(Path.home() / ".config/t3-pstack/models.md")], {"t3-harnesses"}, 0),
        # A file only the size gate reads is not understood: run the full gate.
        (["runtime/integration-health/integration-health.py"], {"file-size"}, 1),
        ([".artifacts/wave2-20261116/checks-repo/REPORT.md"], set(), 0),
    ]
    failures = 0
    in_gate = set(gate_lanes())
    if in_gate != set(LANES):
        failures += 1
        print(f"selftest FAIL lanes not mapped both ways (check.sh vs LANES): {sorted(in_gate ^ set(LANES))}")
    for paths, want, want_unsure in cases:
        got, unsure = select(paths, REPO)
        if want - got or bool(unsure) != bool(want_unsure):
            failures += 1
            print(f"selftest FAIL {paths}: lanes={sorted(got)} want>={sorted(want)} unsure={unsure}")
    if not failures:
        print(f"check-select selftest: {len(in_gate)} lanes mapped, {len(cases)} cases ok")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="git ref to diff against (default: HEAD plus worktree)")
    ap.add_argument("--files", nargs="*", help="explicit changed paths instead of git diff")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    files = args.files if args.files is not None else changed_files(REPO, args.base)
    lanes, unsure = select(files, Path.cwd() if args.files is not None else REPO)
    payload = {"files": files, "lanes": [lane for lane in gate_lanes() if lane in lanes], "unsure": unsure}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for lane in gate_lanes():
            print(f"{'run ' if lane in lanes else 'skip'} {lane}")
        if unsure:
            print("\nUNSURE (no lane mapping — run full scripts/check.sh):")
            print("\n".join(f"  {u}" for u in unsure))
    return 0


if __name__ == "__main__":
    sys.exit(main())
