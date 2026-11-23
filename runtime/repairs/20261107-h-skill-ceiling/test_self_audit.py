import datetime
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("self_audit", Path(__file__).with_name("self-audit.py"))
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)

NOW = datetime.datetime(2026, 11, 7, 23, 0, tzinfo=datetime.timezone.utc)


def ledger_row(actor, ts, action="patch"):
    return json.dumps({"actor": actor, "action": action, "skill": "s", "ts": ts})


class SkillReviewAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def count(self, ledger_lines, log_lines):
        lp = self.tmp / "ledger.jsonl"
        lp.write_text("\n".join(ledger_lines))
        logs = self.tmp / "logs"
        logs.mkdir(exist_ok=True)
        (logs / "agent.log").write_text("\n".join(log_lines))
        return sa.skill_review_audit(now=NOW, ledger_path=lp, log_dir=logs)

    def test_counts_curator_within_window(self):
        res = self.count(
            [
                ledger_row("curator", "2026-11-06T10:00:00+00:00"),
                ledger_row("curator", "2026-11-05T10:00:00+00:00", "write_file"),
                ledger_row("agent", "2026-11-06T10:00:00+00:00"),
                ledger_row("curator", "2026-10-20T10:00:00+00:00"),  # older than 7d
                "not json",
            ],
            [
                "2026-11-07 19:42:42,594 INFO [x] agent.background_review: "
                "Background review complete: thread=bg-review calls=3 result=none",
                "2026-11-07 19:53:17,060 INFO [x] agent.background_review: "
                "Background review complete: thread=bg-review calls=4 result=ok",
                "2026-10-01 19:53:17,060 INFO [x] agent.background_review: "
                "Background review complete: thread=bg-review calls=4 result=none",
                "unrelated line result=none",
            ],
        )
        self.assertEqual(res["edits_7d"], 2)
        self.assertEqual(res["result_none_runs"], 1)

    def test_missing_files_zero(self):
        res = sa.skill_review_audit(now=NOW, ledger_path=self.tmp / "none.jsonl",
                                    log_dir=self.tmp / "nodir")
        self.assertEqual(res, {"edits_7d": 0, "result_none_runs": 0})

    def test_line_shape_in_report(self):
        res = self.count([ledger_row("curator", "2026-11-06T10:00:00+00:00")], [])
        expected = (f"правок навыков фоновым ревью за 7 дней: {res['edits_7d']} (по журналу), "
                    f"запусков с result=none: {res['result_none_runs']}")
        self.assertIn("result=none:", expected)


if __name__ == "__main__":
    unittest.main()
