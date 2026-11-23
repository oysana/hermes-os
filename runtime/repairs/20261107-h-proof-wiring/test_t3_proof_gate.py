import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("tpg", pathlib.Path(__file__).with_name("t3-proof-gate.py"))
tpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpg)
tpg.FLAG = "/nonexistent/allow-t3-no-proof"

BRIEF_OK = """Задача в T3.

## Что вернуть: первым сообщением блок PROOF, строго в этом виде
PROOF
команда: <команда>
код возврата: <число>
"""


def delegate(text):
    return {"tool_name": "mcp__t3__delegate_task", "tool_input": {"task": text}}


def send(text):
    return {"tool_name": "mcp__t3__t3_thread_send",
            "tool_input": {"threadId": "thr_1", "message": text}}


class T3ProofGate(unittest.TestCase):
    def test_delegate_without_return_section_blocked(self):
        self.assertIsNotNone(tpg.decide(delegate("сделай задачу и верни результат")))

    def test_send_without_proof_blocked(self):
        self.assertIsNotNone(tpg.decide(send("продолжай по плану")))

    def test_section_without_proof_block_blocked(self):
        self.assertIsNotNone(tpg.decide(delegate("## Что вернуть: отчёт текстом")))

    def test_proof_word_without_fields_blocked(self):
        self.assertIsNotNone(tpg.decide(delegate("## Что вернуть\nPROOF")))

    def test_full_template_passes(self):
        self.assertIsNone(tpg.decide(delegate(BRIEF_OK)))
        self.assertIsNone(tpg.decide(send(BRIEF_OK)))

    def test_case_insensitive_section(self):
        self.assertIsNone(tpg.decide(delegate("## что вернуть\nPROOF\nкоманда: x\nкод возврата: 0")))

    def test_other_tools_untouched(self):
        for payload in ({"tool_name": "mcp__t3__t3_thread_read", "tool_input": {}},
                        {"tool_name": "terminal", "tool_input": {"command": "ls"}},
                        {"tool_name": "delegate_task", "tool_input": {"task": "x"}},
                        {"tool_name": "mcp__t3__t3_thread_wait", "tool_input": {"runId": "r"}}):
            self.assertIsNone(tpg.decide(payload), payload)

    def test_terminal_t3_mcp_call_gated(self):
        bad = {"tool_name": "terminal",
               "tool_input": {"command": "python3 ~/.hermes/scripts/t3-mcp-call.py t3_thread_send '{\"threadId\":\"t\",\"message\":\"работай\"}'"}}
        good = {"tool_name": "terminal",
                "tool_input": {"command": "python3 ~/.hermes/scripts/t3-mcp-call.py t3_thread_send '{\"threadId\":\"t\",\"message\":\"x\"}'".replace('"x"', '"%s"' % BRIEF_OK.replace('"', '\\"').replace("\n", "\\n"))}}
        self.assertIsNotNone(tpg.decide(bad))
        self.assertIsNone(tpg.decide(good))
        plain = {"tool_name": "terminal", "tool_input": {"command": "python3 ~/.hermes/scripts/t3-mcp-call.py t3_thread_read '{\"threadId\":\"t\"}'"}}
        self.assertIsNone(tpg.decide(plain))

    def test_escape_hatch(self):
        path = "/tmp/allow-t3-no-proof-test"
        open(path, "w").close()
        old = tpg.FLAG
        tpg.FLAG = path
        try:
            self.assertIsNone(tpg.decide(delegate("без секции")))
        finally:
            tpg.FLAG = old
            pathlib.Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
