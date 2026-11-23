#!/usr/bin/env python3
"""on_session_end: capture what the owner taught in the finished session.

Why this exists: Hermes' own learning path (background review) only fires on the
10th user turn or the 15th tool iteration, and it writes through tools that can
refuse (OMH-owned skills, a full MEMORY.md). In a 1-3 message exchange nothing is
ever saved, so the same correction has to be repeated next time.

This hook runs at every session end (CLI and gateway), reads the finished
transcript from state.db, and appends the owner's own durable statements to the
store that is injected into every future turn:

  correction / preference about how to work  -> ~/.hermes/memories/USER.md
  durable environment / project fact         -> ~/.hermes/memories/MEMORY.md
  a research artifact produced this session  -> ~/.hermes/work/research/INDEX.md

Deterministic on purpose: no model call, no paraphrase, the owner's words
verbatim. Conservative: only messages carrying an explicit marker, at most 3
entries per session, secrets are never stored, near-duplicates are skipped, and
a store that is out of budget is reported instead of silently truncated.

Contract: reads one JSON object on stdin, always prints `{}`, never raises.
Every decision lands in ~/.hermes/state/learning/journal-YYYYMM.jsonl so the
behaviour can be audited without reading the transcript.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).resolve()
DB = HOME / "state.db"
MEMORIES = HOME / "memories"
STATE = HOME / "state" / "learning"
RESEARCH_INDEX = HOME / "work" / "research" / "INDEX.md"

# --- what counts as a durable statement -------------------------------------
# Explicit markers: the owner said this should outlive the moment.
FACT_MARKERS = (
    "запомни", "на будущее", "на будущее:", "имей в виду", "учти на будущее",
    "это правило", "запиши в память", "не забывай", "remember this",
)
# Corrections about HOW work is done (behaviour, format, order, tone).
BEHAVIOUR_WORDS = (
    "отчёт", "отчет", "ответ", "формат", "пиши", "показывай", "объясняй",
    "answer", "answers", "response", "short", "format", "reply",
    "спрашивай", "делай", "сначала", "потом", "коротко", "подробно", "язык",
    "тон", "структур", "повторя", "переспрашивай", "вопрос",
)
CORRECTION_OPENERS = (
    "нет,", "нет ", "не так", "неправильно", "не надо", "больше не",
    "я же просил", "я же говорил", "опять", "перестань", "хватит",
    "исправь", "не делай",
)
PREFERENCE_MARKERS = (
    "всегда", "никогда", "мне удобнее", "предпочитаю", "мне нужно чтобы",
    "мне нужно, чтобы", "я хочу чтобы", "я хочу, чтобы", "лучше делай",
)

# --- privacy: never store secrets -------------------------------------------
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(sk-[a-z0-9_-]{8,}|ghp_[a-z0-9]{10,}|github_pat_[a-z0-9_]{10,})"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"(?i)\bxox[abprs]-[a-z0-9-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|пароль|api[_-]?key|apikey|token|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),  # bare long opaque token
    re.compile(r"(?i)bearer\s+[a-z0-9._-]{12,}"),
)

MAX_ENTRIES_PER_SESSION = 3
MAX_ENTRY_CHARS = 240


def _log(record: dict) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        path = STATE / f"journal-{time.strftime('%Y%m')}.jsonl"
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _limits() -> tuple[int, int]:
    """(memory_chars, user_chars) from config.yaml, with Hermes' own defaults."""
    # The gateway runs this hook under Hermes' managed Python, which has no PyYAML: the old
    # `import yaml` failed there, the error was swallowed and Telegram sessions fell back to 4000,
    # so every correction was refused as store_full. Read the two integers with the stdlib instead
    # and fall back to the configured defaults, never to a smaller number.
    mem, user = 12000, 6000
    try:
        text = (HOME / "config.yaml").read_text(encoding="utf-8")
        block = re.search(r"(?ms)^memory:[ \t]*\n(.*?)(?=^\S|\Z)", text)
        if block:
            for name in ("memory_char_limit", "user_char_limit"):
                found = re.search(rf"(?m)^[ \t]+{name}:[ \t]*(\d+)\b", block.group(1))
                if found:
                    if name == "memory_char_limit":
                        mem = int(found.group(1))
                    else:
                        user = int(found.group(1))
    except Exception:
        pass
    return mem, user


def _read_transcript(session_id: str, limit: int = 80) -> list[dict]:
    if not DB.exists() or not session_id:
        return []
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "select role, content, tool_name, tool_calls from messages "
                "where session_id=? AND COALESCE(display_kind,'') IN ('','steer') "
                "AND COALESCE(_compressed_summary,0)=0 order by id desc limit ?",
                (session_id, limit),
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return []
    out = []
    for role, content, tool_name, tool_calls in reversed(rows):
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            continue
        out.append({"role": role, "text": content.strip(),
                    "tool_name": tool_name, "tool_calls": tool_calls})
    return out


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _too_similar(candidate: str, existing: list[str]) -> bool:
    normalized = _norm(candidate)
    if not normalized:
        return True
    for line in existing:
        without_stamp = re.sub(r"^\d{4}-\d{2}-\d{2}:\s*", "", line.strip())
        if _norm(without_stamp) == normalized:
            return True
    return False


def _has_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _classify(text: str) -> str | None:
    """Return 'user' (how to work), 'memory' (durable fact) or None."""
    low = text.lower().replace("ё", "е")
    if len(text) < 15 or len(text) > 1200:
        return None
    if any(marker in low for marker in FACT_MARKERS):
        return "memory"
    looks_like_task = any(word in low for word in ("файл", "папк", "запусти", "поставь",
                                                   "установи", "скачай", "открой"))
    behaviour = any(word in low for word in BEHAVIOUR_WORDS)
    correction = any(low.strip().startswith(opener) for opener in CORRECTION_OPENERS)
    preference = any(marker in low for marker in PREFERENCE_MARKERS)
    if (correction or preference) and behaviour and not looks_like_task:
        return "user"
    if preference and len(text) < 220 and not looks_like_task:
        return "user"
    return None


TASK_VERBS = (
    "сделай", "создай", "проверь", "запусти", "найди", "ресёрч", "исследуй",
    "посмотри", "открой", "установи", "напиши код", "почини", "разберись",
    "build", "create", "run ", "check ", "fix ", "install",
)


def _sentences(text: str) -> list[str]:
    """One statement per sentence: a message often mixes a rule with a task request."""
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _condense(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(нет|не так|неправильно|опять|слушай|окей|ок)[,.\s]+", "", text,
                  flags=re.IGNORECASE)
    if len(text) > MAX_ENTRY_CHARS:
        cut = text[:MAX_ENTRY_CHARS]
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
        text = cut.rstrip(", ") + "…"
    return text[0].upper() + text[1:] if text else text


def _existing_lines(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []
    return [part.strip() for part in raw.split("§") if part.strip()]


def _append_entry(path: Path, entry: str, limit: int) -> tuple[bool, str]:
    """Append one `§`-separated entry; refuse rather than truncate when full."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    separator = "" if not current else ("\n§\n" if not current.endswith("\n§\n") else "")
    addition = separator + entry + "\n"
    if len(current) + len(addition) > limit:
        return False, f"store_full ({len(current)}+{len(addition)}>{limit})"
    # one backup per day, before the first write of that day
    try:
        backup = path.with_suffix(path.suffix + f".bak-{time.strftime('%Y%m%d')}")
        if not backup.exists():
            backup.write_text(current, encoding="utf-8")
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".learn-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(current + addition)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True, "stored"


def _index_research(session_started: float, cwd: str) -> dict | None:
    """Record a research artifact produced during this session (pointer only)."""
    roots = [Path(cwd) / ".artifacts" / "research", HOME / "work" / "research"]
    newest: Path | None = None
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file() or path.suffix.lower() not in (".md", ".markdown", ".txt"):
                    continue
                if path.stat().st_mtime < session_started - 5:
                    continue
            except OSError:
                continue
            if newest is None or path.stat().st_mtime > newest.stat().st_mtime:
                newest = path
    if newest is None:
        return None
    try:
        relative = str(newest).replace(str(Path.home()), "~")
        RESEARCH_INDEX.parent.mkdir(parents=True, exist_ok=True)
        existing = RESEARCH_INDEX.read_text(encoding="utf-8") if RESEARCH_INDEX.exists() else (
            "# Research index (one line per review — newest first)\n\n")
        if relative in existing:
            return {"action": "skipped", "reason": "already_indexed", "path": relative}
        title = newest.stem.replace("-", " ").replace("_", " ")
        line = f"- {time.strftime('%Y-%m-%d')} — {title} → {relative}\n"
        RESEARCH_INDEX.write_text(existing.rstrip() + "\n" + line, encoding="utf-8")
        return {"action": "indexed", "path": relative}
    except Exception as exc:
        return {"action": "error", "reason": str(exc)[:120]}


def _session_repo(session_id: str, payload_cwd: str) -> str:
    """Git root of the finished session, or '' when it ran outside a repository (or in $HOME)."""
    root = ""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("select git_repo_root, cwd from sessions where id=?", (session_id,)).fetchone()
        finally:
            con.close()
        if row:
            root = row[0] or row[1] or ""
    except Exception:
        pass
    root = root or payload_cwd
    try:
        path = Path(root).resolve()
    except Exception:
        return ""
    if not path.is_dir() or path in (Path.home(), Path("/"), Path("/tmp")) or not (path / ".git").exists():
        return ""
    return str(path)


def _project_capture(repo: str, entry: str) -> str:
    """Also file a durable fact in OMH project memory (native, auto-safe, local). Returns a short status."""
    import subprocess
    try:
        ident = subprocess.run(["omh", "memory", "project-identity", "show"], cwd=repo,
                               capture_output=True, text=True, timeout=20)
        ref = json.loads(ident.stdout).get("identity", "")
        if not ref:
            return "no_identity"
        done = subprocess.run(
            ["omh", "memory", "capture", "--type", "fact", "--scope-kind", "project", "--scope-ref", ref,
             "--source", "learn-on-end", "--tag", "owner-rule", "--retention-class", "durable", entry],
            cwd=repo, capture_output=True, text=True, timeout=30)
        return "captured" if done.returncode == 0 else f"capture_rc{done.returncode}"
    except Exception as exc:
        return f"error:{type(exc).__name__}"


def _owner_file_write_allowed(session_id: str) -> bool:
    plugin_root = HOME / "plugins"
    if not (plugin_root / "omh" / "tools" / "memory_tool.py").is_file():
        return True
    try:
        import importlib
        sys.path.insert(0, str(plugin_root))
        try:
            lenses = importlib.import_module("omh.memory_session_lens")
            home = importlib.import_module("omh.runtime_paths").default_omh_home()
            lens = lenses.read_session_lens(home, session_id)
            return lens is not None and lenses.lens_surface_class(lens) in lenses.OWNER_SURFACE_CLASSES
        finally:
            sys.path.remove(str(plugin_root))
    except Exception:
        return False


def _overflow_capture(session_id: str, entry: str, store: str) -> dict:
    plugin_root = HOME / "plugins"
    if not (plugin_root / "omh" / "tools" / "memory_tool.py").is_file():
        return {"status": "unavailable", "reason": "omh_plugin_missing"}
    try:
        import importlib
        sys.path.insert(0, str(plugin_root))
        try:
            handler = importlib.import_module("omh.tools.memory_tool").omh_memory_handler
            result = json.loads(handler({
                "action": "capture", "summary": entry,
                "record_type": "lesson" if store == "user" else "fact",
                "scope": "user", "retention_class": "durable",
                "tags": ["owner-correction", "learning-overflow"],
            }, session_id=session_id))
        finally:
            sys.path.remove(str(plugin_root))
        return {key: result[key] for key in ("status", "reason", "record_id", "candidate_id") if key in result}
    except Exception as exc:
        return {"status": "error", "reason": type(exc).__name__}


def run(payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "").strip()
    platform = str(payload.get("profile") or payload.get("platform") or "")
    platform = str((payload.get("extra") or {}).get("platform") or platform or "")
    if not session_id:
        return {"result": "no_session"}

    STATE.mkdir(parents=True, exist_ok=True)
    transcript = _read_transcript(session_id)
    users = [m for m in transcript if m["role"] == "user"]
    if not users:
        return {"result": "empty"}

    mem_limit, user_limit = _limits()
    user_path = MEMORIES / "USER.md"
    mem_path = MEMORIES / "MEMORY.md"
    existing = {"user": _existing_lines(user_path), "memory": _existing_lines(mem_path)}

    decisions = []
    stored = 0
    repo = _session_repo(session_id, str(payload.get("cwd") or ""))
    file_write_allowed = _owner_file_write_allowed(session_id)
    for message in users:
        if stored >= MAX_ENTRIES_PER_SESSION:
            break
        text = message["text"]
        if text.startswith("[") and text.endswith("]"):
            continue  # hidden/system-shaped message
        sentence = next(
            (s for s in _sentences(text) if _classify(s) is not None), None
        )
        if sentence is None:
            continue
        text = sentence
        store = _classify(text)
        if store is None:
            continue
        if _has_secret(text):
            decisions.append({"store": store, "action": "skipped", "reason": "secret_pattern",
                              "text": text[:80] + "…"})
            continue
        if any(verb in text.lower() for verb in TASK_VERBS) and not any(
            marker in text.lower() for marker in ("всегда", "никогда", "пиши", "сначала", "формат")
        ):
            continue  # a task request, not a durable rule
        entry = _condense(text)
        if _too_similar(entry, existing["user"] + existing["memory"]):
            decisions.append({"store": store, "action": "skipped", "reason": "duplicate",
                              "text": entry[:120]})
            continue
        stamped = f"{time.strftime('%Y-%m-%d')}: {entry}"
        if file_write_allowed:
            ok, reason = _append_entry(user_path if store == "user" else mem_path,
                                       stamped, user_limit if store == "user" else mem_limit)
        else:
            ok, reason = False, "owner_file_scope_refused"
        destination = store
        overflow = None
        if not ok and (reason.startswith("store_full") or reason == "owner_file_scope_refused"):
            overflow = _overflow_capture(session_id, entry, store)
            ok = overflow.get("status") in ("remembered", "already_remembered")
            if ok:
                destination, reason = "omh", "stored" if overflow["status"] == "remembered" else "duplicate"
        decisions.append({"store": destination, "action": "stored" if ok and reason != "duplicate" else "skipped",
                          "reason": reason, "text": stamped[:160]})
        if overflow is not None:
            decisions[-1]["omh"] = overflow
        if ok:
            existing[store].append(stamped)
            if reason != "duplicate":
                stored += 1
            if destination != "omh" and store == "memory" and repo:
                decisions[-1]["project_memory"] = _project_capture(repo, entry)

    started = 0.0
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("select started_at from sessions where id=?", (session_id,)).fetchone()
        finally:
            con.close()
        started = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass
    research = _index_research(started, str(payload.get("cwd") or Path.cwd()))

    result = {
        "result": "ok",
        "session_id": session_id,
        "platform": platform,
        "user_messages": len(users),
        "stored": stored,
        "decisions": decisions,
        "research": research,
        "sizes": {
            "USER.md": user_path.stat().st_size if user_path.exists() else 0,
            "MEMORY.md": mem_path.stat().st_size if mem_path.exists() else 0,
            "limits": {"USER.md": user_limit, "MEMORY.md": mem_limit},
        },
    }
    return result


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        record = run(payload)
    except Exception as exc:  # never break the session end
        record = {"result": "error", "error": f"{type(exc).__name__}: {exc}"}
    _log(record)
    print("{}")


if __name__ == "__main__":
    main()
