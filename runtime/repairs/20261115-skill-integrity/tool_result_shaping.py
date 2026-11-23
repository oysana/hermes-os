"""Boundary shaping for tool results entering message history.

Parse, don't validate: every result that becomes a ``role: "tool"`` message
passes through :func:`shape_result_for_context` before persistence. Requested
skill instructions retain their full content because skill_view has no pagination
and its deduplication assumes the previous result is complete. Other raw
command output (bash/grep/git/test stdout, file dumps, MCP payloads) can never
enter history unbounded.  Shaping is *structural*, not heuristic:

* ANSI/control sequences are stripped (they cost tokens and can smuggle
  invisible instructions to tokenizers).
* Runs of identical consecutive lines collapse to one line plus a ``×N``
  counter — log floods dedupe to their informative head.
* Anything over the line cap keeps head+tail with an explicit omission
  marker carrying byte offsets, so the model knows exactly what was cut and
  how to page for more (``read_file`` offset / a narrower command).

JSON results are shaped per string field and re-serialized, never cut
mid-structure.  Non-string results (multimodal envelopes) pass through.
Thresholds come from ``tool_budget.*`` (same block as the spillover budget);
defaults match the boundary contract (150 lines).
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_LINES = 150
# A single line has no line cap to hit: the char budget bounds one-line or
# few-line floods (minified files, base64, a lone 500KB log) the same way.
DEFAULT_MAX_CHARS = 64_000
# String fields smaller than this never pay the shaping pass.
_MIN_SHAPE_CHARS = 2_048

_MARKER = (
    "\n… [{omitted} {unit} omitted — chars {start}-{end} of {total}. "
    "Page the source (read_file offset, a narrower command, or grep) for the rest.]\n"
)
_DEDUPED = "   ×{n} identical lines omitted"


def _shaping_config() -> dict:
    """``tool_budget`` shaping knobs via the sanctioned readonly loader; cached
    per hermes home (one process serves many profiles)."""
    try:
        from hermes_constants import hermes_home_key
        key = hermes_home_key()
    except Exception:
        key = ""
    return _config_cached(key)


@functools.lru_cache(maxsize=8)
def _config_cached(_home_key: str) -> dict:
    cfg: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly
        data = load_config_readonly()
        block = data.get("tool_budget") if isinstance(data, dict) else None
        if isinstance(block, dict):
            cfg = block
    except Exception:
        cfg = {}
    return {
        "max_lines": _pos_int(cfg.get("result_max_lines"), DEFAULT_MAX_LINES),
        "max_chars": _pos_int(cfg.get("result_max_chars"), DEFAULT_MAX_CHARS),
        "dedupe": cfg.get("result_dedupe", True) is not False,
        "strip_ansi": cfg.get("result_strip_ansi", True) is not False,
    }


def _pos_int(value: Any, fallback: int) -> int:
    try:
        v = int(value)
        return v if v > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _strip_ansi(text: str) -> str:
    try:
        from tools.ansi_strip import strip_ansi
        return strip_ansi(text)
    except Exception:
        return text


def _dedupe_lines(lines: list[str]) -> list[str]:
    """Collapse consecutive identical lines: ``line`` ×N keeps one copy + a
    counter row, so a 400-line flood contributes its distinct lines only."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i + 1
        while j < n and lines[j] == lines[i]:
            j += 1
        out.append(lines[i])
        if j - i > 1:
            out.append(_DEDUPED.format(n=j - i - 1))
        i = j
    return out


def _cap_chars(text: str, max_chars: int) -> str:
    """Char head+tail with the same byte-offset marker as the line cap — the
    bound that a 1-line 500KB result needs and a line count never reaches."""
    if len(text) <= max_chars:
        return text
    head_n = max_chars // 2
    tail_n = max_chars - head_n
    marker = _MARKER.format(unit="chars", omitted=len(text) - head_n - tail_n,
                            start=head_n, end=len(text) - tail_n, total=len(text))
    return text[:head_n] + marker + text[len(text) - tail_n:]


def shape_raw_text(text: str, *, max_lines: int, dedupe: bool, strip_ansi: bool,
                   max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Head+tail line cap with a byte-offset omission marker, then a char cap."""
    if strip_ansi:
        text = _strip_ansi(text)
    lines = text.split("\n")
    if dedupe:
        lines = _dedupe_lines(lines)
    if len(lines) <= max_lines:
        return _cap_chars("\n".join(lines), max_chars)
    head_n = max_lines // 2
    tail_n = max_lines - head_n
    head, tail = lines[:head_n], lines[-tail_n:]
    omitted = len(lines) - head_n - tail_n
    start_char = len("\n".join(head)) + 1
    end_char = len(text) - len("\n".join(tail))
    marker = _MARKER.format(unit="lines", omitted=omitted, start=start_char, end=end_char, total=len(text))
    return _cap_chars("\n".join(head) + marker + "\n".join(tail), max_chars)


def _shape_value(value: Any, cfg: dict) -> Any:
    if isinstance(value, str):
        if len(value) < _MIN_SHAPE_CHARS:
            return value
        return shape_raw_text(
            value,
            max_lines=cfg["max_lines"], dedupe=cfg["dedupe"], strip_ansi=cfg["strip_ansi"],
            max_chars=cfg["max_chars"],
        )
    if isinstance(value, dict):
        return {k: _shape_value(v, cfg) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape_value(v, cfg) for v in value]
    return value


def shape_result_for_context(name: str, content: Any) -> Any:
    """Shape one tool result before it becomes a history message.

    Strings are the only shaped shape: JSON objects get every oversized string
    field capped; plain text results are capped whole.  Anything else
    (multimodal envelopes, part lists) returns unchanged — vision payloads
    have their own budget owner.
    """
    if name == "skill_view":
        return content
    if not isinstance(content, str) or len(content) < _MIN_SHAPE_CHARS:
        return content
    cfg = _shaping_config()
    stripped = content.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(content)
        except Exception:
            data = None
        if data is not None:
            # Char-capped whole too: many <2KB fields summed still bounded.
            return _cap_chars(
                json.dumps(_shape_value(data, cfg), ensure_ascii=False), cfg["max_chars"])
    return shape_raw_text(
        content, max_lines=cfg["max_lines"], dedupe=cfg["dedupe"], strip_ansi=cfg["strip_ansi"],
        max_chars=cfg["max_chars"],
    )
