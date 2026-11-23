"""Exercises agents-update against fake harness binaries."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import importlib.machinery
import unittest
from unittest import mock

SOURCE = Path.home() / '.agents/bin/agents-update'  # the installed tool; no repo copy


def load():
    spec = importlib.util.spec_from_file_location('agents_update', SOURCE, loader=importlib.machinery.SourceFileLoader('agents_update', str(SOURCE)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def script(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class AgentsUpdate(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        patcher = mock.patch.dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = load()
        self.tool.STATE = self.root / 'state'

    def test_new_version_is_reported(self):
        new = self.root / 'new'
        script(new, 'echo 2.0')
        old = self.root / 'old'
        script(old, f'[ "$1" = update ] && ln -sf {new} {self.bin}/fake; echo 1.0')
        (self.bin / 'fake').symlink_to(old)
        row = self.tool.update_cli('fake', ['fake', 'update'], 10)
        self.assertEqual((row['result'], row['before'], row['after']), ('updated', '1.0', '2.0'))

    def test_broken_symlink_update_is_restored(self):
        broken = self.root / 'broken'
        script(broken, 'exit 3')
        old = self.root / 'old'
        script(old, f'[ "$1" = update ] && ln -sf {broken} {self.bin}/fake; echo 1.0')
        (self.bin / 'fake').symlink_to(old)
        row = self.tool.update_cli('fake', ['fake', 'update'], 10)
        self.assertEqual(row['result'], 'restored')
        self.assertEqual(self.tool.version('fake'), '1.0')

    def test_broken_plain_binary_update_is_restored(self):
        path = self.bin / 'fake'
        script(path, f'[ "$1" = upgrade ] && {{ rm {path}; printf "#!/bin/sh\\nexit 3\\n" > {path}; chmod +x {path}; }}; echo 1.0')
        row = self.tool.update_cli('fake', ['fake', 'upgrade'], 10)
        self.assertEqual(row['result'], 'restored')
        self.assertEqual(self.tool.version('fake'), '1.0')

    def test_failed_updater_keeps_working_binary_and_is_reported(self):
        script(self.bin / 'fake', '[ "$1" = update ] && { echo denied; exit 1; }; echo 1.0')
        row = self.tool.update_cli('fake', ['fake', 'update'], 10)
        self.assertEqual((row['result'], row['detail']), ('failed', 'denied'))

    def test_absent_harness_is_not_a_failure(self):
        self.assertEqual(self.tool.update_cli('absent', ['absent', 'update'], 10)['result'], 'missing')

    def test_hung_updater_is_bounded(self):
        script(self.bin / 'fake', '[ "$1" = update ] && exec sleep 30; echo 1.0')
        self.assertEqual(self.tool.update_cli('fake', ['fake', 'update'], 1)['result'], 'failed')

    def test_one_failure_does_not_stop_the_rest_and_sets_exit_code(self):
        script(self.bin / 'good', 'echo 1.0')
        self.tool.CLIS = [('bad', ['false']), ('good', ['good', 'update'])]
        self.tool.SYNCS = [('sync-bad', 'exit 4'), ('sync-good', 'true')]
        script(self.bin / 'bad', 'echo 1.0')
        with mock.patch.object(sys, 'argv', ['agents-update']):
            code = self.tool.main()
        report = json.loads((self.tool.STATE / 'last.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(report['failed'], ['bad', 'sync-bad'])
        self.assertEqual({r['name']: r['result'] for r in report['results']}['sync-good'], 'current')

    def test_hermes_is_never_updated_here(self):
        commands = [' '.join(c) for _, c in self.tool.CLIS] + [c for _, c in self.tool.SYNCS]
        self.assertFalse([c for c in commands if 'hermes' in c])


if __name__ == '__main__':
    unittest.main()
