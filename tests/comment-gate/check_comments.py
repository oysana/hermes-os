#!/usr/bin/env python3
"""
Hermes Q03 Comment Gate
Forbids new workaround comments without an external reference.
"""

import argparse
import glob
import json
import os
import re
import sys
import tokenize

HERMES_PATTERNS = [
    "shared/proactive/*.py",
    "shared/communications/*.py",
    "shared/plugins/*/*.py",
    "profiles/*/plugins/*/*.py",
    "profiles/*/scripts/*.py",
    "profiles/*/scripts/*.sh",
    "scripts/*.py",
    "scripts/*.sh",
]

# Flags: TODO, FIXME, HACK, XXX, workaround, костыл, временно, temporary, kludge
FLAG_PATTERN = re.compile(
    r"(?i)(?:\b(?:TODOs?|FIXMEs?|HACKs?|XXX|workarounds?|temporary|temporarily|kludges?)\b|костыл|временно)"
)

# Allowed external references:
# 1. URL: http:// or https://
# 2. Issue ref: #123 or owner/repo#123
# 3. Ticket: [A-Z]+-\d+ (e.g. HERMES-123, JIRA-42)
REF_PATTERN = re.compile(
    r"https?://\S+|(?:[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)?#\d+\b|\b[A-Z]+-\d+\b"
)


def extract_python_comments(filepath):
    """Extract Python comments using tokenize (ignoring strings/docstrings)."""
    comments = []
    try:
        with open(filepath, "rb") as f:
            tokens = tokenize.tokenize(f.readline)
            for tok in tokens:
                if tok.type == tokenize.COMMENT:
                    line_no = tok.start[0]
                    text = tok.string.strip()
                    comments.append((line_no, text))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Fall back to line-based parsing if tokenize encounters a syntax error
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        comments.append((line_no, stripped))
        except Exception:
            pass
    except Exception:
        pass
    return comments


def extract_shell_comments(filepath):
    """
    Extract shell comments: lines whose first non-space char is '#'
    (skipping line 1 shebang).
    """
    comments = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, 1):
                stripped = line.strip()
                if line_no == 1 and stripped.startswith("#!"):
                    continue
                if stripped.startswith("#"):
                    comments.append((line_no, stripped))
    except Exception:
        pass
    return comments


def is_flagged_comment(text):
    """Return True if comment matches flag pattern and lacks an external reference."""
    if not FLAG_PATTERN.search(text):
        return False
    if REF_PATTERN.search(text):
        return False
    return True


def collect_files(root_dir):
    """Collect all files matching Hermes patterns under root_dir."""
    files = []
    seen = set()
    for pattern in HERMES_PATTERNS:
        full_pattern = os.path.join(root_dir, pattern)
        for path in glob.glob(full_pattern):
            if "__pycache__" in path:
                continue
            # The vendored OMH plugin bundle is upstream code refreshed by
            # `omh update`; its comments are not authored in this repo.
            if os.sep + os.path.join("plugins", "omh") + os.sep in path:
                continue
            norm = os.path.normpath(path)
            if norm not in seen and os.path.isfile(norm):
                seen.add(norm)
                files.append(norm)
    return sorted(files)


def scan_file(filepath):
    """Scan a single file and return flagged comments: list of (line_no, text)."""
    if filepath.endswith(".py"):
        raw_comments = extract_python_comments(filepath)
    elif filepath.endswith(".sh"):
        raw_comments = extract_shell_comments(filepath)
    else:
        raw_comments = []

    flagged = []
    for line_no, text in raw_comments:
        if is_flagged_comment(text):
            flagged.append((line_no, text))
    return flagged


def scan_tree(root_dir):
    """Scan entire root_dir. Returns list of (filepath, line_no, text)."""
    files = collect_files(root_dir)
    results = []
    for fpath in files:
        file_flagged = scan_file(fpath)
        for line_no, text in file_flagged:
            results.append((fpath, line_no, text))
    return results


def load_baseline(baseline_path, root_dir):
    """Load baseline comments as a set of (path, text)."""
    if not os.path.exists(baseline_path):
        return set()
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load baseline {baseline_path}: {e}", file=sys.stderr)
        return set()

    baseline_set = set()
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                p = item.get("path", "")
                t = item.get("text") or item.get("comment", "")
                if p and t:
                    baseline_set.add((p, t))
                    if root_dir:
                        baseline_set.add((os.path.normpath(os.path.join(root_dir, p)), t))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                baseline_set.add((item[0], item[1]))
                if root_dir:
                    baseline_set.add((os.path.normpath(os.path.join(root_dir, item[0])), item[1]))
    elif isinstance(data, dict):
        for p, val in data.items():
            if isinstance(val, list):
                for t in val:
                    baseline_set.add((p, t))
                    if root_dir:
                        baseline_set.add((os.path.normpath(os.path.join(root_dir, p)), t))
            elif isinstance(val, str):
                baseline_set.add((p, val))
                if root_dir:
                    baseline_set.add((os.path.normpath(os.path.join(root_dir, p)), val))
    return baseline_set


def write_baseline(baseline_path, flagged_items, root_dir):
    """Save flagged items to baseline.json as list of {path, text}."""
    entries = []
    seen = set()
    for fpath, _, text in flagged_items:
        try:
            rel_path = os.path.relpath(fpath, root_dir)
        except ValueError:
            rel_path = fpath
        key = (rel_path, text)
        if key not in seen:
            seen.add(key)
            entries.append({"path": rel_path, "text": text})

    entries.sort(key=lambda x: (x["path"], x["text"]))
    os.makedirs(os.path.dirname(os.path.abspath(baseline_path)), exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return len(entries)


def check_comments(root_dir, baseline_path, write_baseline_mode=False):
    """
    Main check logic.
    Returns (exit_code, violations, total_flagged_count).
    """
    flagged_items = scan_tree(root_dir)

    if write_baseline_mode:
        count = write_baseline(baseline_path, flagged_items, root_dir)
        print(f"Wrote {count} flagged comment(s) to baseline: {baseline_path}")
        return 0, [], count

    baseline_set = load_baseline(baseline_path, root_dir)
    violations = []

    for fpath, line_no, text in flagged_items:
        try:
            rel_path = os.path.relpath(fpath, root_dir)
        except ValueError:
            rel_path = fpath

        # Match against baseline using relative path, exact path, or normalized path
        if (
            (rel_path, text) not in baseline_set
            and (fpath, text) not in baseline_set
            and (os.path.normpath(fpath), text) not in baseline_set
        ):
            violations.append((fpath, line_no, text))

    for fpath, line_no, text in violations:
        print(f"{fpath}:{line_no}: new workaround comment without an external reference: {text}")

    exit_code = 1 if violations else 0
    return exit_code, violations, len(flagged_items)


def main():
    default_baseline = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "baseline.json"
    )
    parser = argparse.ArgumentParser(
        description="Gate checking for new workaround comments without external references."
    )
    parser.add_argument(
        "--root",
        default=os.path.expanduser("~/.hermes"),
        help="Hermes root directory (default: ~/.hermes)",
    )
    parser.add_argument(
        "--baseline",
        default=default_baseline,
        help=f"Path to baseline.json (default: {default_baseline})",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate baseline.json with all currently flagged comments",
    )

    args = parser.parse_args()
    root_dir = os.path.abspath(os.path.expanduser(args.root))
    baseline_path = os.path.abspath(os.path.expanduser(args.baseline))

    exit_code, violations, _ = check_comments(
        root_dir=root_dir,
        baseline_path=baseline_path,
        write_baseline_mode=args.write_baseline,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
