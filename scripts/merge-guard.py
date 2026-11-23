#!/usr/bin/env python3
"""Merge guard for the hermes-pro contract: no mutating merge on a red/unknown gate.

Supported invocation path: a PreToolUse hook on ``exec`` (``.devin/hooks.v1.json``),
the same shape the project's other pre-tool gates use. The guard inspects the
shell command; only a mutating merge targeting this repository is gated —
everything else passes immediately.

A merge is admitted only when BOTH hold:

1. Full-gate green at the PR's exact head: some ``.artifacts/verification/run-*/``
   under the repository root records ``checkout.txt`` == ``gh pr view``
   ``headRefOid`` AND a ``check.log`` whose steps all completed with the final
   ``all checks passed`` line. A lane subset, an older head, or a log with any
   FAILED lane does not qualify.
2. Branch protection is legible: the protection endpoint answers (200 = the
   listed requirements must be covered by the green gate; 404 = explicitly
   none). A 403 (free plan, private repo — the feature is not readable, so
   requirements are unknown) blocks the merge unless the owner explicitly opts
   out by creating ``~/.hermes/state/allow-merge-unknown-protection``.

A merge command the guard cannot isolate (piped/chained composition, missing PR,
unreadable API) is refused rather than guessed: the failure mode that fired the
blocked card was an under-verified merge, not an over-strict one.

Exit codes follow the hook contract: 0 = allow, 2 = block (reason on stderr).
Direct CLI use for testing: ``merge-guard.py --command 'gh pr merge 1'``.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = os.environ.get("MERGE_GUARD_REPO", "oysana/hermes-pro")
FLAG = Path.home() / ".hermes/state/allow-merge-unknown-protection"
TIMEOUT = int(os.environ.get("MERGE_GUARD_TIMEOUT", "25"))

# Mutating merge forms through gh; a merge hidden inside shell composition is
# ambiguous — its repo/context cannot be verified, so it is refused outright.
MERGE_FORMS = (
    re.compile(r"\bgh\s+pr\s+merge\b"),
    re.compile(r"\bgh\s+api\b[^|;]*?(?:pulls/\d+/merge|/merges\b|-X\s*PUT[^|;]*pulls)"),
    re.compile(r"\bgh\s+repo\s+merge\b"),
)
COMPOSITE = re.compile(r"[|&;`]|>\s*[^ >]|\$\(")
# A quoted payload only mutates when something evaluates it: sh -c, eval,
# xargs. Everywhere else it is inert text (echo/printf arguments, commit
# messages, docs) and must not be classified as a merge.
EVAL_WRAPPERS = re.compile(r"\b(?:ba|z|k|c)?sh\s+-c\b|\beval\b|\bxargs\b")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _effective_command(command: str) -> str:
    stripped = QUOTED.sub("", command)
    return command if EVAL_WRAPPERS.search(stripped) else stripped


def deny(reason: str) -> int:
    print(f"merge-guard: БЛОК — {reason}", file=sys.stderr)
    return 2


def run(argv, cwd=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)


def command_of(argv: list) -> str:
    """The shell command under inspection, from a hook event or --command."""
    for i, a in enumerate(argv):
        if a == "--command" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--command="):
            return a.split("=", 1)[1]
    try:
        import select
        if sys.stdin.isatty() or not select.select([sys.stdin], [], [], 0.2)[0]:
            return ""
        stdin_text = sys.stdin.read()
    except Exception:
        return ""
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return ""
    tool = event.get("tool_input") or event.get("args") or {}
    return str(tool.get("command") or "")


def merge_target(command: str):
    """(repo, pr_ref, base_hint) when the command mutates a merge, else None."""
    effective = _effective_command(command)
    if COMPOSITE.search(command) and any(p.search(effective) for p in MERGE_FORMS):
        return "composite"
    if not any(p.search(effective) for p in MERGE_FORMS):
        return None
    command = effective
    repo = REPO
    m = (re.search(r"(?:-R|--repo)[=\s]+([\w.-]+/[\w.-]+)", command)
         or re.search(r"\bgh\s+api\b[^|;]*?\brepos/([\w.-]+/[\w.-]+)", command))
    if m:
        repo = m.group(1)
    elif not (Path.cwd() / ".git").exists():
        # Not inside a checkout and no -R: gh resolves the repo from cwd;
        # outside a checkout that is undefined — refuse rather than guess.
        return "ambiguous"
    pr = re.search(r"\bgh\s+pr\s+merge\s+(?:#\s*)?(\d+)", command)
    return repo, (pr.group(1) if pr else None)


def repo_root() -> Path:
    top = run(["git", "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        raise RuntimeError("not inside a git checkout")
    return Path(top.stdout.strip())


def pr_facts(repo: str, pr_ref):
    argv = ["gh", "pr", "view"]
    if pr_ref:
        argv.append(pr_ref)
    argv += ["--json", "number,headRefOid,baseRefName,state", "-R", repo]
    out = run(argv)
    if out.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def green_gate_at_head(root: Path, head: str) -> bool:
    """A verification run under .artifacts/verification proves the FULL gate
    green at exactly `head`: checkout.txt == head and check.log has no failed
    lane and ends in `all checks passed`."""
    runs = root / ".artifacts" / "verification"
    if not runs.is_dir():
        return False
    for run_dir in sorted(runs.iterdir()):
        checkout = run_dir / "checkout.txt"
        log = run_dir / "check.log"
        if not (checkout.is_file() and log.is_file()):
            continue
        if checkout.read_text(errors="replace").strip() != head:
            continue
        text = log.read_text(errors="replace")
        # Lane-level failures only: `FAIL <lane>` and the closing
        # `FAILED: <lanes>` line. Test-level `FAIL:`/`FAILED (` output inside
        # a passing lane (a deliberate red-suite proof) must not disqualify.
        if re.search(r"(?m)^FAILED:\s", text) or re.search(r"(?m)^FAIL\s+\S", text):
            continue
        starts = re.findall(r"(?m)^==\s+(\S+)", text)
        oks = set(re.findall(r"(?m)^ok\s\s+(\S+)", text))
        if (re.search(r"(?im)all checks passed", text)
                and starts and all(s in oks for s in starts)):
            return True
    return False


def protection_legible(repo: str, base: str) -> str:
    """'requirements' | 'none' | 'unknown' from the protection endpoint."""
    out = run(["gh", "api", f"repos/{repo}/branches/{base}/protection"])
    if out.returncode == 0:
        try:
            body = json.loads(out.stdout)
        except json.JSONDecodeError:
            return "unknown"
        return "requirements" if body else "none"
    if "Not Found" in out.stderr or '"status": "404"' in out.stderr:
        return "none"
    return "unknown"


def guard(repo: str, pr_ref, root: Path) -> int:
    facts = pr_facts(repo, pr_ref)
    head, base, number = facts["headRefOid"], facts["baseRefName"], facts["number"]
    if not green_gate_at_head(root, head):
        return deny(
            f"PR #{number}@{head[:12]}: нет записанного ПОЛНОГО зелёного "
            f"scripts/check.sh на этом head под .artifacts/verification/ — "
            f"прогони gate на точной ревизии и сохрани check.log + checkout.txt")
    level = protection_legible(repo, base)
    if level == "unknown":
        if FLAG.exists():
            return 0
        return deny(
            f"защита ветки {base} нечитаема (403/план): требования неизвестны — "
            f"публикация остаётся заблокированной; осознанный обход владельца: "
            f"touch {FLAG}")
    return 0


def main() -> int:
    command = command_of(sys.argv[1:])
    target = merge_target(command)
    if target is None:
        return 0
    if target == "composite":
        return deny("merge скрыт в конвейере/цепочке команд — контекст "
                    "и репозиторий не проверить; запусти merge одной командой")
    if target == "ambiguous":
        return deny("merge вне checkout'а без -R: целевой репозиторий не определён")
    repo, pr_ref = target
    if repo.lower() != REPO.lower():
        return 0
    try:
        root = repo_root()
        return guard(repo, pr_ref, root)
    except Exception as exc:
        return deny(f"не удалось проверить условия merge ({exc})")


if __name__ == "__main__":
    sys.exit(main())
