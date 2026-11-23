#!/usr/bin/env python3
"""Custom code around H may only shrink.

Owner 18.11: «мы снова ушли в луп где не понятно самим что добавляем, слишком много кастома и всё
ещё не полностью используем Hermes and omh». Counts non-test lines of our own runtime code (every
plugin H enables except OMH and marketplace installs, plus ~/.hermes/scripts and
~/.agents/lib) and fails when the total is above the recorded
baseline. A shrink lowers the baseline automatically; growth needs `--allow-growth "<owner's
verbatim words>"`, which is logged next to the baseline.
  custom-ratchet.py [--allow-growth QUOTE]
"""
import argparse, hashlib, json, sys, time
from pathlib import Path

import yaml

HOME = Path.home() / ".hermes"
BASELINE = Path(__file__).resolve().parent / "custom-ratchet.json"
NATIVE = {"omh"}
# Published dependency carrier named by the installed catalog LCM-X README.
LCM_COMPANION = {
    "source": "https://github.com/electricsheephq/lcm-x.git#companions/hermes-lcm-x-local-embeddings",
    "revision": "6260c31fbce11e4cd542878f1f6a37410d678ae8",
    "files": {
        "__init__.py": "b701529c1ccea7e887b9391690579d4a16dd4a29e760f84e23b5f2167ae4c753",
        "plugin.yaml": "6a795e4929507c086ec2bab636323b7ae80902f14cfb53436564af9c6f1976ea",
        "README.md": "4adf574cd2e8a755a4341362bffc6e929937361e503389c2c981b148e3b899f9",
    },
}


def maintained_companion(root: Path, metadata: dict) -> bool:
    if root.name != "hermes-lcm-x-local-embeddings":
        return False
    record = metadata.get(root.name) or {}
    if not (record.get("source") == LCM_COMPANION["source"]
            and record.get("revision") == LCM_COMPANION["revision"] and record.get("pinned") is True):
        return False
    files = {f.relative_to(root).as_posix(): f for f in root.rglob("*")
             if f.is_file() and "__pycache__" not in f.relative_to(root).parts}
    return files.keys() == LCM_COMPANION["files"].keys() and all(
        hashlib.sha256(files[name].read_bytes()).hexdigest() == digest
        for name, digest in LCM_COMPANION["files"].items())


def counted_files():
    enabled = (yaml.safe_load((HOME / "config.yaml").read_text()).get("plugins") or {}).get("enabled") or []
    # A marketplace install carries .hermes-catalog.json: maintained upstream, not ours.
    roots = [HOME / "plugins" / p for p in enabled
             if p not in NATIVE and not (HOME / "plugins" / p / ".hermes-catalog.json").exists()]
    roots += [HOME / "scripts", Path.home() / ".agents" / "lib"]
    metadata_path = HOME / "plugins" / ".install-metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    for root in roots:
        if maintained_companion(root, metadata):
            continue
        for f in root.rglob("*"):
            parts = set(f.relative_to(root).parts)
            if not f.is_file() or parts & {"tests", "__pycache__", "backups", "fixtures"} or ".bak" in f.name:
                continue
            if f.suffix in {".py", ".sh", ".bend"} or f.parent.name == "bin":
                yield root.name, f


def measure() -> dict:
    out: dict = {}
    for root, f in counted_files():
        try:
            out[root] = out.get(root, 0) + sum(1 for _ in f.open(errors="ignore"))
        except OSError:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-growth")
    a = ap.parse_args()
    now = measure()
    total = sum(now.values())
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {"total": total, "log": []}
    parts = "  ".join(f"{k}={v}" for k, v in sorted(now.items(), key=lambda x: -x[1]))
    if total > base["total"] and not a.allow_growth:
        print(f"custom code grew {base['total']} -> {total} lines: delete something or use a native "
              f"Hermes/OMH feature instead. Growth only with --allow-growth \"<owner's verbatim words>\".\n{parts}")
        return 1
    if total != base["total"] or not BASELINE.exists():
        base["log"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "from": base["total"], "to": total,
                            "owner": a.allow_growth or ""})
        base["total"] = total
        BASELINE.write_text(json.dumps(base, ensure_ascii=False, indent=1) + "\n")
    print(f"custom code {total} lines (baseline {base['total']})\n{parts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
