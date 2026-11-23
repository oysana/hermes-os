"""Exercises research-find against a temporary research folder."""
import importlib.util
import os
from pathlib import Path
import tempfile
import importlib.machinery
import unittest
from unittest import mock

SOURCE = Path.home() / '.agents/bin/research-find'  # the installed tool; no repo copy
spec = importlib.util.spec_from_file_location('research_find', SOURCE, loader=importlib.machinery.SourceFileLoader('research_find', str(SOURCE)))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ResearchFind(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = mock.patch.dict(os.environ, RESEARCH_ROOTS=str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, text, age_days=0):
        path = self.root / name
        path.write_text(text)
        moment = 1_800_000_000 - age_days * 86400
        os.utime(path, (moment, moment))

    def search(self, *words):
        rows, _truncated = tool.find(list(words), now=1_800_000_000)
        return [(row[1].name, row[2]) for row in rows]

    def test_requires_every_word(self):
        self.write('a.md', 'deepseek router')
        self.write('b.md', 'deepseek only')
        self.assertEqual(self.search('deepseek', 'router'), [('a.md', 0)])

    def test_reports_age(self):
        self.write('old.md', 'kanban notes', age_days=45)
        self.assertEqual(self.search('kanban'), [('old.md', 45)])

    def test_filename_counts_and_outranks_a_passing_mention(self):
        self.write('kanban.md', 'notes')
        self.write('other.md', 'kanban once')
        self.assertEqual([n for n, _ in self.search('kanban')], ['kanban.md', 'other.md'])

    def test_ignores_other_file_types(self):
        self.write('a.json', 'kanban')
        self.assertEqual(self.search('kanban'), [])

    def test_missing_root_is_empty(self):
        with mock.patch.dict(os.environ, RESEARCH_ROOTS=str(self.root / 'absent')):
            self.assertEqual(self.search('kanban'), [])


if __name__ == '__main__':
    unittest.main()
