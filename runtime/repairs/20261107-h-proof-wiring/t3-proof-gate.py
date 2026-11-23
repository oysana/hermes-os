#!/usr/bin/env python3
"""pre_tool_call hook for H: a T3 dispatch without a PROOF contract is refused in code.

Owner rule 07.11.2026: every launch (delegate_task) and every new run
(t3_thread_send) must carry the «Что вернуть» section with the PROOF block
template, so the return can be accepted by proof_check.py instead of prose.
Checks the message text only; reads stay free. Escape hatch for the owner:
create ~/.hermes/state/allow-t3-no-proof. A hook failure must never block
the owner: errors are logged to ~/.hermes/state/t3-proof-gate.log and the
call passes.
"""
import json
import os
import re
import sys
import time

GATED_TOOLS = ("mcp__t3__delegate_task", "mcp__t3__t3_thread_send")
FLAG = os.path.expanduser("~/.hermes/state/allow-t3-no-proof")
LOG = os.path.expanduser("~/.hermes/state/t3-proof-gate.log")

RETURN_SECTION = re.compile(r"что\s+вернуть", re.I)
PROOF_BLOCK = re.compile(r"(?im)^\s*[#>*\s]*PROOF\s*$")
PROOF_FIELD = re.compile(r"код\s+возврата|exit\s*code", re.I)
T3_CLI = re.compile(r"t3-mcp-call\.py\s+(delegate_task|t3_thread_send)\b")

MESSAGE = (
    "Блок: запуск и прогон T3 без договора о приёмке запрещены. "
    "В сообщении нужен раздел «Что вернуть» с блоком PROOF (шаблон: "
    "python3 ~/.hermes/scripts/proof_check.py --template). "
    "Возврат без принятого блока не закрывает карточку. "
    "Обход для владельца: touch ~/.hermes/state/allow-t3-no-proof"
)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _missing(text):
    if not RETURN_SECTION.search(text):
        return "нет раздела «Что вернуть»"
    if not PROOF_BLOCK.search(text) or not PROOF_FIELD.search(text):
        return "в «Что вернуть» нет блока PROOF с полем «код возврата»"
    return None


def decide(payload):
    if os.path.exists(FLAG):
        return None
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input") or payload.get("args") or {}
    if tool in GATED_TOOLS:
        missing = _missing("\n".join(_strings(args)))
        return (MESSAGE + " Не хватает: " + missing) if missing else None
    if tool == "terminal":
        cmd = str(args.get("command", ""))
        if T3_CLI.search(cmd):
            missing = _missing(cmd.replace("\\n", "\n"))
            return (MESSAGE + " Не хватает: " + missing) if missing else None
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        reason = decide(payload)
    except Exception as error:
        try:
            with open(LOG, "a") as fh:
                fh.write("%s hook error: %r\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), error))
        except Exception:
            pass
        return 0
    if reason:
        print(json.dumps({"action": "block", "message": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
