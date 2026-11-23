"""Exercise the audit CLI against disposable coordinator/provider fixtures."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path.home() / ".hermes"
sys.path.insert(0, str(ROOT / "hermes-agent"))
import hermes_yaml as yaml

TARGET = Path(os.environ.get("TARGET", ROOT / "scripts/route-policy-check.py"))


class NativeRoutePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.hermes = self.home / ".hermes"
        self.hermes.mkdir()
        policy = yaml.safe_load((ROOT / "route-policy.yaml").read_text())
        (self.hermes / "route-policy.yaml").write_text(yaml.safe_dump(policy))
        sheet = self.home / ".config/t3-pstack/models.md"
        sheet.parent.mkdir(parents=True)
        sheet.write_text("feature, refactoring: acpRegistry_devin/swe-2-high thought_level=high\n"
                         "judgment and prose: codex/gpt-6.1-sol reasoningEffort=high\n"
                         "session hook: on\n")
        self.paths = {}
        for profile in ("default", "hqecea", "sqecea", "yqecea"):
            path = self.hermes / "config.yaml" if profile == "default" else self.hermes / "profiles" / profile / "config.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.paths[profile] = path
            self.write(profile, {"model": {"provider": "opencode-go", "default": "deepseek-v4.1-flash"},
                                 "delegation": {"provider": "opencode-go", "model": "deepseek-v4.1-flash",
                                                "fallback_providers": [{"provider": "opencode-go", "model": "muse-spark-1.3-contributor"}]}})

    def write(self, profile, cfg):
        self.paths[profile].write_text(yaml.safe_dump(cfg))

    def change(self, section, value, profile="default"):
        cfg = yaml.safe_load(self.paths[profile].read_text())
        cfg[section] = value
        self.write(profile, cfg)

    def rejected_for_y_and_s(self, section, value, needle):
        """Owner 16.11.2026: Y and S stay on opencode-go; only H may use Codex."""
        for profile in ("yqecea", "sqecea"):
            before = self.paths[profile].read_text()
            self.change(section, value, profile)
            result = self.run_cli()
            self.assertEqual(1, result.returncode, profile)
            self.assertIn(f"{profile}.{needle}", result.stdout)
            self.paths[profile].write_text(before)

    def run_cli(self):
        return subprocess.run(["python3", "-B", str(TARGET)], env={**os.environ, "HOME": str(self.home)},
                              text=True, capture_output=True, timeout=10)

    def test_api_coordinators_are_outer_and_native_t3_roles_are_inner(self):
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_y_and_s_direct_subscription_delegation_is_rejected(self):
        self.rejected_for_y_and_s("delegation", {"provider": "openai-codex", "model": "gpt-6.1-sol"},
                                  "deleg")

    def test_h_may_delegate_to_the_codex_subscription(self):
        self.change("delegation", {"provider": "openai-codex", "model": "gpt-6.1-sol"})
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_ordinary_api_card_pin_is_allowed(self):
        with sqlite3.connect(self.hermes / "kanban.db") as db:
            db.execute("CREATE TABLE tasks(id,provider_override,model_override,status)")
            db.execute("INSERT INTO tasks VALUES('fixture','openai-api','gpt-6.1-terra','blocked')")
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_codex_card_pin_on_the_h_board_is_allowed(self):
        with sqlite3.connect(self.hermes / "kanban.db") as db:
            db.execute("CREATE TABLE tasks(id,provider_override,model_override,status)")
            db.execute("INSERT INTO tasks VALUES('fixture','openai-codex','gpt-6.1-sol','blocked')")
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_sol_on_the_api_card_pin_is_rejected(self):
        with sqlite3.connect(self.hermes / "kanban.db") as db:
            db.execute("CREATE TABLE tasks(id,provider_override,model_override,status)")
            db.execute("INSERT INTO tasks VALUES('fixture','openai-api','gpt-6.1-sol','blocked')")
        result = self.run_cli()
        self.assertEqual(1, result.returncode)
        self.assertIn("kanban.fixture", result.stdout)

    def test_standalone_sheets_do_not_override_t3(self):
        for path in (self.home / ".agents/pstack-models.md", self.home / ".claude/rules/pstack-models.md"):
            path.parent.mkdir(parents=True)
            path.write_text("feature, refactoring: stale/value\n")
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_gemini_is_allowed_only_for_the_explorer_role(self):
        sheet = self.home / ".config/t3-pstack/models.md"
        sheet.write_text("feature, refactoring: acpRegistry_devin/swe-2-high thought_level=high\n"
                         "how explorer: antigravity/gemini-3.8-flash-high\n"
                         "session hook: on\n")
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        sheet.write_text("feature, refactoring: antigravity/gemini-3.8-flash-high\n")
        result = self.run_cli()
        self.assertEqual(1, result.returncode)
        self.assertIn("not in inner pool", result.stdout)

    def test_disabled_hermes_image_tool_keeps_its_inert_pin_without_using_subscription(self):
        self.change("image_gen", {"provider": "openai-codex", "model": "gpt-image-2-medium"})
        self.change("agent", {"disabled_toolsets": ["image_gen"]})
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("image_gen disabled", result.stdout)

    def test_native_t3_provider_name_is_not_a_hermes_image_provider(self):
        self.change("image_gen", {"provider": "codex", "model": "gpt-image-2-medium"})
        result = self.run_cli()
        self.assertEqual(1, result.returncode)

    def test_y_and_s_may_generate_images_on_codex(self):
        """Owner 19.11.2026: «норм, картинки in go никто нормальное не может читать»."""
        for profile in ("yqecea", "sqecea"):
            self.change("image_gen", {"provider": "openai-codex", "model": "gpt-image-2-medium"}, profile)
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
