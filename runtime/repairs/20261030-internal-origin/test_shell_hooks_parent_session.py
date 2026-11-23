"""Hook wire contract: parent_session_id is a real top-level payload field.

``_TOP_LEVEL_PAYLOAD_KEYS`` promotes parent_session_id out of ``extra``; consumers that
check the top-level key (or were promised it) must actually receive it. A missing field
is indistinguishable from 'no parent', so the serializer owns this contract.
"""
import json

from agent import shell_hooks


def _payload(kwargs):
    return json.loads(shell_hooks._serialize_payload("pre_llm_call", kwargs))


def test_parent_session_id_emitted_top_level():
    payload = _payload({"session_id": "child-1", "parent_session_id": "parent-9"})
    assert payload["session_id"] == "child-1"
    assert payload["parent_session_id"] == "parent-9"
    assert "parent_session_id" not in payload["extra"]


def test_parent_session_id_absent_when_no_parent():
    payload = _payload({"session_id": "owner-1"})
    assert payload["parent_session_id"] == ""
    assert "parent_session_id" not in payload["extra"]


def test_session_id_falls_back_to_parent_when_absent():
    payload = _payload({"parent_session_id": "parent-9"})
    assert payload["session_id"] == "parent-9"
    assert payload["parent_session_id"] == "parent-9"
