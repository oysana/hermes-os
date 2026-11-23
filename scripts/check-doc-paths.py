#!/usr/bin/env python3
"""Outcome: docs stay accurate to whatever is on main.

Verifies that concrete path references inside the human/agent docs exist:
repo-relative `scripts/`, `tests/`, `docs/`, `research/`, `verification/`
tokens are checked against the repo; `~/.hermes/` tokens are checked on
this host only (skipped when ~/.hermes is absent, e.g. CI). Backticked
paths, quoted paths and bare tokens are all caught; URLs, globs with *,
and template placeholders are ignored.

Exit 1 on any missing reference. Usage: check-doc-paths.py [--selftest]
"""
import os, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = [REPO / "AGENTS.md", REPO / "README.md",
        Path.home() / ".hermes" / "shared" / "RULES.md"]
REPO_TOKEN = re.compile(r"(?<![\w./-])((?:scripts|tests|docs|research|verification|runtime|features)/[\w./@+-]+)")
HOME_TOKEN = re.compile(r"~?/?\.hermes/([\w./@+-]+)")
NOISE = ("<", ">", "{", "}", "…")


def refs(docs=DOCS):
    for doc in docs:
        if not doc.exists():
            continue
        for line in doc.read_text().splitlines():
            if "http" in line:
                line = re.sub(r"https?://\S+", "", line)
            for m in REPO_TOKEN.finditer(line):
                yield doc, "repo", m.group(1).rstrip(".,;:'\")`|")
            for m in HOME_TOKEN.finditer(line):
                yield doc, "home", m.group(1).rstrip(".,;:'\")`|")


def check(docs=DOCS, repo=REPO, home=None):
    missing = []
    hermes = (home or Path.home()) / ".hermes"
    for doc, kind, rel in refs(docs):
        if any(n in rel for n in NOISE) or "*" in rel:
            continue
        if kind == "repo":
            if not (repo / rel).exists():
                missing.append(f"{doc.name}: {rel}")
        elif hermes.exists() and not (hermes / rel).exists():
            missing.append(f"{doc.name}: ~/.hermes/{rel}")
    return missing


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "scripts").mkdir()
        (td / "scripts/ok.py").write_text("")
        doc = td / "DOC.md"
        doc.write_text("run `scripts/ok.py` and `scripts/gone.py`; "
                       "guard is ~/.hermes/scripts/guard.py\n")
        home = td / "home"
        (home / ".hermes/scripts").mkdir(parents=True)
        bad = check(docs=[doc], repo=td, home=home)
        assert len(bad) == 2 and "gone.py" in bad[0] and "guard.py" in bad[1], bad
        (home / ".hermes/scripts/guard.py").write_text("")
        assert check(docs=[doc], repo=td, home=home) == ["DOC.md: scripts/gone.py"]
    print("check-doc-paths selftest OK")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    missing = check()
    for m in missing:
        print("MISSING", m)
    print(f"doc-paths: {len(missing)} missing references")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
