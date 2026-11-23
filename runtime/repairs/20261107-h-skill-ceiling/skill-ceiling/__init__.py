"""skill-ceiling — refuse SKILL.md growth past 25,000 chars.

Background review (agent/background_review.py) edits skills only through the
``skill_manage`` tool, so one ``pre_tool_call`` veto covers both interactive and
curator edits. Any write whose resulting SKILL.md would exceed ``CAP`` chars AND
be longer than before is blocked; shrinking or equal-size writes always pass,
and supporting files (references/, scripts/, ...) are never gated — the refusal
tells the writer to move detail there instead.

The veto applies to the flat call shape and to the atomic ``operations`` batch,
simulated sequentially so a multi-step batch can't creep past the cap.

Fail-open: any internal error allows the call and appends a JSON line to
``<hermes home>/logs/skill-ceiling.jsonl``, so a broken hook never stalls work.

Emergency kill switch (documented): set env ``HERMES_SKILL_CEILING=off`` or
``touch ~/.hermes/plugins/skill-ceiling/OFF`` — either disables the veto.
"""

import json
import os
import time
from pathlib import Path

CAP = 25_000

_REFUSAL = (
    "SKILL.md ceiling: правка даст {new} знаков при лимите {cap} (было {prior}). "
    "перенеси подробности в references/ и сократи SKILL.md"
)


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _disabled() -> bool:
    if os.environ.get("HERMES_SKILL_CEILING", "").strip().lower() in ("off", "0", "disabled"):
        return True
    return (Path(__file__).with_name("OFF")).exists()


def _log(event: dict) -> None:
    try:
        path = _home() / "logs" / "skill-ceiling.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **event}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _find_skill_md(name: str) -> Path | None:
    """SKILL.md path for ``name`` (bare or ``category/name``), or None."""
    if not name:
        return None
    try:
        from tools.skill_manager_tool import _find_skill
        hit = _find_skill(name)
        return Path(hit["path"]) / "SKILL.md" if hit else None
    except Exception:
        pass
    bare = Path(name).name
    root = _home() / "skills"
    try:
        for md in root.rglob("SKILL.md"):
            if md.parent.name == bare:
                return md
    except OSError:
        pass
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _apply_patch(content: str, old: str, new: str, replace_all: bool):
    """Result of skill_manage's patch, or None when it can't be determined
    (no unique match — the tool itself will then fail the call anyway)."""
    try:
        from tools.fuzzy_match import fuzzy_find_and_replace
        new_content, _count, _strategy, err = fuzzy_find_and_replace(
            content, old, new, replace_all)
        return None if err else new_content
    except Exception:
        pass
    n = content.count(old)
    if n == 0 or (n > 1 and not replace_all):
        return None
    return content.replace(old, new) if replace_all else content.replace(old, new, 1)


def _violation(virtual: dict, key: str, new_text: str):
    """``(prior, new)`` when the write breaks the ceiling, else None; passing
    writes still update ``virtual`` so batch ops accumulate correctly."""
    prior = virtual.get(key)
    if prior is None:
        prior = _read(Path(key))
    if len(new_text) > CAP and len(new_text) > len(prior):
        return len(prior), len(new_text)
    virtual[key] = new_text
    return None


def _check_op(op: dict, virtual: dict):
    """One flat-shape op -> refusal message or None. ``virtual`` maps
    ``str(skill_md_path)`` to pending content for sequential batch steps."""
    action = op.get("action")
    name = op.get("name") or ""
    file_path = op.get("file_path") or ""
    targets_root_md = not file_path or Path(file_path).name == "SKILL.md"

    if action == "create":
        if _find_skill_md(name) is not None:
            return None  # create on an existing skill errors inside the tool
        content = op.get("content") or ""
        if len(content) > CAP:
            return _REFUSAL.format(new=len(content), cap=CAP, prior=0)
        return None

    if action in ("edit",) or (action == "patch" and op.get("content")):
        if not targets_root_md:
            return None
        md = _find_skill_md(name)
        if md is None:
            return None
        v = _violation(virtual, str(md), op.get("content") or "")
        return _REFUSAL.format(new=v[1], cap=CAP, prior=v[0]) if v else None

    if action == "patch":
        md = _find_skill_md(name)
        if md is None or not targets_root_md:
            return None
        target = md if not file_path else md.parent / file_path
        key = str(target)
        current = virtual.get(key)
        if current is None:
            current = _read(target)
        new_content = _apply_patch(
            current, op.get("old_string") or "", op.get("new_string") or "",
            bool(op.get("replace_all")))
        if new_content is None:
            return None
        v = _violation(virtual, key, new_content)
        return _REFUSAL.format(new=v[1], cap=CAP, prior=v[0]) if v else None

    if action == "write_file" and Path(file_path).name == "SKILL.md":
        md = _find_skill_md(name)
        if md is None:
            return None
        v = _violation(virtual, str(md), op.get("file_content") or "")
        return _REFUSAL.format(new=v[1], cap=CAP, prior=v[0]) if v else None

    return None


def pre_tool_call(tool_name: str = "", args=None, **_):
    if tool_name != "skill_manage" or not isinstance(args, dict):
        return None
    try:
        if _disabled():
            return None
        virtual: dict = {}
        ops = args.get("operations")
        if isinstance(ops, list) and ops:
            for i, op in enumerate(ops):
                if not isinstance(op, dict):
                    continue
                msg = _check_op({"name": op.get("name") or args.get("name") or "", **op},
                                virtual)
                if msg:
                    _log({"event": "blocked", "op_index": i,
                          "skill": op.get("name") or args.get("name")})
                    return {"action": "block", "message": f"operations[{i}]: {msg}"}
            return None
        msg = _check_op(args, virtual)
        if msg:
            _log({"event": "blocked", "skill": args.get("name"),
                  "action": args.get("action")})
            return {"action": "block", "message": msg}
        return None
    except Exception as e:
        _log({"event": "hook_error", "error": repr(e)})
        return None


def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool_call)
