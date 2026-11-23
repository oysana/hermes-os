"""Negative controls for our selected role policy, not a harness implementation."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "native_profiles", Path(__file__).with_name("test_native_profiles.py"))
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)
SOURCE = Path(profiles.ROOT) / "runtime/harness-config"


class ProfileGuards(unittest.TestCase):
    def setUp(self):
        preferred = Path(tempfile.gettempdir()) / "opencode"
        self.temp = tempfile.TemporaryDirectory(
            dir=preferred if preferred.is_dir() else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / "devin", self.root / "devin")
        self.patcher = patch.multiple(profiles, DEVIN_DIR=str(self.root / "devin"))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def check_profiles(self):
        result = profiles.Failures()
        profiles.check_devin(result)
        return result.items

    def change(self, host, name, old, new):
        path = self.root / host / name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_real_candidates_pass(self):
        self.assertEqual([], self.check_profiles())

    def test_model_outside_selected_policy_fails(self):
        self.change("devin", "explorer.md", "swe-2-medium", "unverified-model")
        self.assertTrue(any("catalog excerpt" in x for x in self.check_profiles()))

    def test_tool_typo_fails(self):
        self.change("devin", "explorer.md", "  - read", "  - raed")
        self.assertTrue(any("unknown tool" in x for x in self.check_profiles()))

    def test_clerk_cannot_gain_edit(self):
        self.change("devin", "explorer.md", "  - read", "  - edit")
        self.assertTrue(any("exactly read/grep/glob" in x for x in self.check_profiles()))

    def test_missing_frontmatter_fails(self):
        self.change("devin", "explorer.md", "---\nname:", "name:")
        self.assertTrue(any("no frontmatter" in x for x in self.check_profiles()))


class RoleEffortPolicy(unittest.TestCase):
    def test_native_fallbacks_do_not_pin_sol_low(self):
        codex = tomllib.loads((SOURCE / "codex/explorer.toml").read_text())
        self.assertNotIn("model", codex)
        self.assertNotIn("model_reasoning_effort", codex)
        opencode, _ = profiles.parse_frontmatter(SOURCE / "opencode/explore.md")
        self.assertNotIn("model", opencode)

    def test_native_review_keeps_high_effort(self):
        reviewer = tomllib.loads((SOURCE / "codex/reviewer.toml").read_text())
        self.assertEqual("gpt-6.2-sol", reviewer["model"])
        self.assertEqual("high", reviewer["model_reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
