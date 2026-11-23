"""Owner role-policy checks on the live pstack sheet. What T3 actually ran is audited by
scripts/audit-off-sheet-delegates.py: run history is not a property of this checkout, so it
is reported by scripts/coord-status.sh instead of gating every goal's check.sh."""
from pathlib import Path
import json
import os
import unittest

SOURCE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "t3-pstack/models.md"
HARNESS = Path(__file__).resolve().parents[2] / "runtime/harness-config"


def roles_from(text):
    return dict(line.split(": ", 1) for line in text.splitlines()
                if line and not line.startswith("#"))


def seats(roles):
    return {tuple(entry.split()[0].split("/", 1))
            for target in roles.values() for entry in target.split(", ") if "/" in entry}


class OwnerModelStrategy(unittest.TestCase):
    def setUp(self):
        self.roles = roles_from(SOURCE.read_text())

    def test_all_roles_are_explicit_so_excluded_tier_defaults_cannot_return(self):
        expected = {
            "feature, refactoring", "bug-fix", "perf-issue", "hillclimb",
            "judgment and prose", "hardest tasks", "how explorer", "how explainer",
            "why investigators", "why synthesizer", "reflect tooling",
            "reflect judgment, divergent, synthesizer", "arena runners",
            "arena cross-judge pool", "swarm workers", "architect runners",
            "interrogate reviewers", "session hook",
        }
        self.assertEqual(expected, set(self.roles))
        self.assertEqual("on", self.roles["session hook"])

    def test_native_t3_defaults_use_only_owner_selected_subscription_models(self):
        settings = json.loads((HARNESS / "t3-defaults.json").read_text())
        self.assertEqual({"defaultModelSelection", "textGenerationModelSelection"}, set(settings))
        worker = settings["defaultModelSelection"]
        self.assertEqual(("acpRegistry_devin", "swe-2-high"), (worker["instanceId"], worker["model"]))
        self.assertEqual({"thought_level": "high", "speed": "standard"},
                         {x["id"]: x["value"] for x in worker["options"]})
        text = settings["textGenerationModelSelection"]
        self.assertEqual(("codex", "gpt-6.1-terra"), (text["instanceId"], text["model"]))
        self.assertEqual("high", {x["id"]: x["value"] for x in text["options"]}["reasoningEffort"])

    def test_main_workers_use_explicit_swe2(self):
        for role in ("feature, refactoring", "hillclimb", "swarm workers"):
            self.assertRegex(self.roles[role],
                             r"^acpRegistry_devin/swe-2-high thought_level=(medium|high|max) speed=standard$")

    def test_deep_work_and_review_use_sol(self):
        # owner 16.11: «sol is reviewer and deep worker, swe is just worker\\executer»
        self.assertEqual("codex/gpt-6.2-sol reasoningEffort=high", self.roles["why investigators"])
        # Pareto t1 17.11: Sol alone 93.3 vs SWE-2-coordinated loop 76.7, cheaper and faster
        self.assertEqual("codex/gpt-6.2-sol reasoningEffort=high", self.roles["hardest tasks"])
        self.assertTrue(self.roles["interrogate reviewers"].startswith("codex/gpt-6.2-sol"))

    def test_gemini_is_bounded_to_the_explorer_seat(self):
        for role, target in self.roles.items():
            if role != "how explorer":
                self.assertNotIn("antigravity/", target)
                self.assertNotIn("gemini", target)

    def test_native_routine_implementer_matches_medium(self):
        path = HARNESS / "devin/implementer.md"
        header = path.read_text().split("\n---\n", 1)[0]
        self.assertIn("\nmodel: swe-2-medium\n", header + "\n")

    def test_nontrivial_work_keeps_high(self):
        for role in ("bug-fix", "perf-issue"):
            self.assertEqual("acpRegistry_devin/swe-2-high thought_level=high speed=standard",
                             self.roles[role])

    def test_sol_judgment_is_not_downgraded(self):
        self.assertEqual("codex/gpt-6.2-sol reasoningEffort=high", self.roles["judgment and prose"])
        for role, target in self.roles.items():
            self.assertNotIn("reasoningEffort=low", target)
            if "codex/gpt-6.2-sol reasoningEffort=medium" in target:
                self.assertIn(role, ("how explainer", "why synthesizer"))

    def test_explanatory_medium_is_explicitly_scoped(self):
        for role in ("how explainer", "why synthesizer"):
            self.assertEqual("codex/gpt-6.2-sol reasoningEffort=medium", self.roles[role])

    def test_no_go_or_excluded_default_models(self):
        for role, target in self.roles.items():
            for excluded in ("opencode-go/", "gpt-6-luna", "claude", "default", "inherit-parent", "auto"):
                self.assertNotIn(excluded, target)
            for entry in target.split(", "):
                if target == "on":
                    continue
                allowed = ("acpRegistry_devin/swe-2-high ",
                           "codex/gpt-6.2-sol ",
                           "codex/gpt-6.1-terra ")
                if role == "how explorer":
                    allowed += ("antigravity/gemini-3.8-flash ",)
                self.assertTrue(entry.startswith(allowed), entry)

    def test_cross_family_judge_exists_for_each_allowed_parent(self):
        pool = self.roles["arena cross-judge pool"].split(", ")
        families = {entry.split()[0].split("/")[-1].split("-")[0] for entry in pool}
        for parent in ("gpt", "swe"):
            self.assertTrue(families - {parent}, f"no independent judge for {parent}")

    def test_mechanical_work_uses_codex_subscription_terra(self):
        self.assertRegex(self.roles["reflect tooling"], r"^codex/gpt-6.1-terra reasoningEffort=(medium|high)$")


if __name__ == "__main__":
    unittest.main()
