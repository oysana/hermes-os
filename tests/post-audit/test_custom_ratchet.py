import copy
import importlib.util
import json
from pathlib import Path
import shutil

import tempfile
import unittest
from unittest.mock import patch


class RatchetProvenanceTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / 'scripts/custom-ratchet.py'
        spec = importlib.util.spec_from_file_location('ratchet_provenance', path)
        self.ratchet = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ratchet)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)/'home'
        self.plugin = home/'plugins/hermes-lcm-x-local-embeddings'
        self.plugin.mkdir(parents=True)
        source = Path.home()/'.hermes/plugins/hermes-lcm-x-local-embeddings'
        for name in self.ratchet.LCM_COMPANION['files']:
            shutil.copyfile(source/name, self.plugin/name)
        (home/'config.yaml').write_text('plugins:\n  enabled: [hermes-lcm-x-local-embeddings]\n')
        record = {k: self.ratchet.LCM_COMPANION[k] for k in ['source', 'revision']}
        record['pinned'] = True
        self.metadata = {self.plugin.name: record}
        patcher = patch.object(self.ratchet, 'HOME', home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def counted(self, metadata):
        (self.plugin.parent/'.install-metadata.json').write_text(json.dumps(metadata))
        return any(path == self.plugin/'__init__.py' for _, path in self.ratchet.counted_files())

    def test_exact_upstream_excluded_but_local_edit_counted(self):
        self.assertTrue(self.ratchet.maintained_companion(self.plugin, self.metadata))
        self.assertFalse(self.counted(self.metadata))
        (self.plugin/'__init__.py').write_text((self.plugin/'__init__.py').read_text()+'# local WIP\n')
        self.assertFalse(self.ratchet.maintained_companion(self.plugin, self.metadata))
        self.assertTrue(self.counted(self.metadata))

    def test_unverified_sources_counted(self):
        for field,value in [('source','https://example.invalid/local-fork'),
                            ('revision','0'*40), ('pinned',False)]:
            with self.subTest(field=field):
                metadata = copy.deepcopy(self.metadata)
                metadata[self.plugin.name][field] = value
                self.assertFalse(self.ratchet.maintained_companion(self.plugin, metadata))
                self.assertTrue(self.counted(metadata))

    def test_extra_code_and_missing_file_counted(self):
        extra = self.plugin/'extra.py'
        extra.write_text('raise RuntimeError("local code")\n')
        self.assertTrue(self.counted(self.metadata))
        extra.unlink()  # this test's disposable file
        (self.plugin/'README.md').unlink()
        self.assertTrue(self.counted(self.metadata))


if __name__ == '__main__':
    unittest.main()
