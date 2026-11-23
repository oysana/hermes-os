"""Offline oracle for one identical live lookup, not a task runner/router."""
import copy
import json
from pathlib import Path
import unittest

FIXTURE = Path(__file__).parent / "fixtures/route-probe"
EXPECTED = {
    "revision": 17,
    "batch_ids": ["delta", "nova"],
    "fallback_ms": 2800,
    "marker": "Cobalt / Казан",
    "references": {
        "revision": "registry.json:2",
        "batch_ids": ["registry.json:7", "registry.json:9"],
        "fallback_ms": "probe_symbols.py:8",
        "marker": "notes.md:7",
    },
}


def validate_reply(text):
    """Accept only the requested JSON and exact values/references."""
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return False
    return value == EXPECTED


class RouteProbeOracle(unittest.TestCase):
    def test_oracle_matches_real_fixture(self):
        registry = json.loads((FIXTURE / "registry.json").read_text())
        self.assertEqual(EXPECTED["revision"], registry["revision"])
        self.assertEqual(EXPECTED["batch_ids"], sorted(
            row["id"] for row in registry["services"]
            if row["enabled"] and row["batch"]))
        for file, line, needle in [
            ("registry.json", 2, '"revision": 17'),
            ("registry.json", 7, '"id": "delta"'),
            ("registry.json", 9, '"id": "nova"'),
            ("probe_symbols.py", 8, "fallback_ms = 2800"),
            ("notes.md", 7, "Active marker: Cobalt / Казан"),
        ]:
            self.assertIn(needle, (FIXTURE / file).read_text().splitlines()[line - 1])

    def test_exact_reply_passes(self):
        self.assertTrue(validate_reply(json.dumps(EXPECTED)))

    def test_wrong_value_fails(self):
        wrong = copy.deepcopy(EXPECTED)
        wrong["fallback_ms"] = 1900
        self.assertFalse(validate_reply(json.dumps(wrong)))

    def test_wrong_reference_fails(self):
        wrong = copy.deepcopy(EXPECTED)
        wrong["references"]["marker"] = "notes.md:3"
        self.assertFalse(validate_reply(json.dumps(wrong)))

    def test_non_json_or_extra_text_fails(self):
        self.assertFalse(validate_reply("```json\n" + json.dumps(EXPECTED) + "\n```"))


if __name__ == "__main__":
    unittest.main()
