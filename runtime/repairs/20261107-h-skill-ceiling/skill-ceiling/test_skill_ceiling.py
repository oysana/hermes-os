import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path.home() / ".hermes"
sys.path.insert(0, str(HOME / "hermes-agent"))
_tmp = tempfile.mkdtemp()
os.environ["HERMES_HOME"] = _tmp
TMP = Path(_tmp)

spec = importlib.util.spec_from_file_location("skill_ceiling", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

FRONT = "---\nname: %s\ndescription: test skill\n---\n"
MARKER = "UNIQUE-TAIL-MARKER"


def make_skill(name, size):
    d = TMP / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    body = FRONT % name
    pad = size - len(body) - len(MARKER)
    assert pad >= 0
    (d / "SKILL.md").write_text(body + "x" * pad + MARKER)
    return d / "SKILL.md"


class Ceiling(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HERMES_SKILL_CEILING", None)

    def call(self, args, tool="skill_manage"):
        return plugin.pre_tool_call(tool_name=tool, args=args)

    def test_growth_over_cap_blocked(self):
        make_skill("big-one", 26000)
        res = self.call({"action": "patch", "name": "big-one",
                         "old_string": MARKER, "new_string": "y" * 2000})
        self.assertIsInstance(res, dict)
        self.assertEqual(res["action"], "block")
        self.assertIn("references/", res["message"])
        self.assertIn("SKILL.md", res["message"])

    def test_shrinking_edit_passes(self):
        make_skill("big-two", 30000)
        res = self.call({"action": "patch", "name": "big-two",
                         "old_string": MARKER, "new_string": "y"})
        self.assertIsNone(res)

    def test_growth_under_cap_passes(self):
        make_skill("small-one", 2000)
        res = self.call({"action": "patch", "name": "small-one",
                         "old_string": MARKER, "new_string": "y" * 500})
        self.assertIsNone(res)

    def test_full_rewrite_blocked_and_allowed(self):
        make_skill("rew-one", 20000)
        big = FRONT % "rew-one" + "z" * 29000
        res = self.call({"action": "edit", "name": "rew-one", "content": big})
        self.assertEqual(res["action"], "block")
        small = FRONT % "rew-one" + "z" * 100
        self.assertIsNone(self.call({"action": "edit", "name": "rew-one", "content": small}))
        res = self.call({"action": "patch", "name": "rew-one", "content": big})
        self.assertEqual(res["action"], "block")

    def test_create_over_cap_blocked(self):
        content = FRONT % "brand-new" + "z" * 30000
        res = self.call({"action": "create", "name": "brand-new", "content": content})
        self.assertEqual(res["action"], "block")
        self.assertIsNone(self.call({"action": "create", "name": "ok-new",
                                     "content": FRONT % "ok-new" + "z" * 100}))

    def test_supporting_file_writes_pass(self):
        make_skill("big-three", 30000)
        res = self.call({"action": "write_file", "name": "big-three",
                         "file_path": "references/deep.md", "file_content": "z" * 90000})
        self.assertIsNone(res)
        res = self.call({"action": "patch", "name": "big-three",
                         "file_path": "references/deep.md",
                         "old_string": "a", "new_string": "b" * 90000})
        self.assertIsNone(res)

    def test_write_file_to_skill_md_gated(self):
        make_skill("big-four", 30000)
        res = self.call({"action": "write_file", "name": "big-four",
                         "file_path": "SKILL.md", "file_content": FRONT % "big-four" + "z" * 40000})
        self.assertEqual(res["action"], "block")

    def test_other_tools_and_actions_pass(self):
        self.assertIsNone(self.call({"action": "patch", "name": "x",
                                     "old_string": "a", "new_string": "b" * 99000},
                                    tool="file_patch"))
        self.assertIsNone(self.call({"action": "delete", "name": "big-one"}))

    def test_batch_ops_blocked(self):
        make_skill("batch-one", 24000)
        ops = [{"action": "patch", "name": "batch-one",
                "old_string": MARKER, "new_string": "A" * 2000}]
        res = self.call({"action": "patch", "name": "batch-one", "operations": ops})
        self.assertEqual(res["action"], "block")
        ok = [{"action": "patch", "name": "batch-one",
               "old_string": MARKER, "new_string": "A" * 20}]
        self.assertIsNone(self.call({"action": "patch", "name": "batch-one", "operations": ok}))

    def test_kill_switch(self):
        make_skill("big-five", 30000)
        os.environ["HERMES_SKILL_CEILING"] = "off"
        res = self.call({"action": "edit", "name": "big-five",
                         "content": FRONT % "big-five" + "z" * 90000})
        self.assertIsNone(res)

    def test_hook_error_fails_open_and_logs(self):
        log = TMP / "logs" / "skill-ceiling.jsonl"
        if log.exists():
            log.unlink()
        real = plugin._check_op
        plugin._check_op = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            res = self.call({"action": "patch", "name": "nope",
                             "old_string": "a", "new_string": "b"})
        finally:
            plugin._check_op = real
        self.assertIsNone(res)
        self.assertTrue(log.is_file())
        rows = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertEqual(rows[-1]["event"], "hook_error")

    def test_missing_skill_patch_passes(self):
        res = self.call({"action": "patch", "name": "ghost-skill",
                         "old_string": "a", "new_string": "b" * 99000})
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
