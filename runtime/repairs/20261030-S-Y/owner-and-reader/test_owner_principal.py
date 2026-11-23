"""Actual status CLI with an external account fixture, no network or real account."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from owner_account_fixture import AccountFixture

class OwnerPrincipalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.account=AccountFixture(self.temp.name)

    def status(self):
        return subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('tg-personal.py')),'status'],
                              env=self.account.env,capture_output=True,text=True,timeout=4)

    def test_personal_principal_is_projected_from_get_me(self):
        child=self.status()
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(child.stdout),{'authorized':True,'personal_account':True,
                          'principal':{'platform':'telegram','sender_id':'424242'}})

    def test_bot_and_malformed_principals_are_not_owner_identities(self):
        for fields in ({'id':424242,'bot':True},{'id':True,'bot':False},
                       {'id':0,'bot':False},{'id':'424242','bot':False},{'id':424242}):
            with self.subTest(fields=fields):
                self.account.set_account(**fields)
                child=self.status()
                if child.returncode==0:
                    self.assertIsNone(json.loads(child.stdout)['principal'])
                else:
                    self.assertEqual(child.stdout,'')
        self.account.set_account(id=424242,bot=False)
        self.assertEqual(json.loads(self.status().stdout)['principal']['sender_id'],'424242')

    def test_status_has_bounded_timeout_and_no_network_retries(self):
        self.account.set_account(id=424242,bot=False,hang=True)
        started=time.monotonic();child=self.status();elapsed=time.monotonic()-started
        self.assertEqual(child.returncode,1)
        self.assertEqual(json.loads(child.stderr),{'error':'TimeoutError'})
        self.assertLess(elapsed,3)
        options=json.loads(self.account.calls.read_text().splitlines()[-1])
        self.assertEqual(options['connection_retries'],0)
        self.assertEqual(options['request_retries'],0)
        self.assertIs(options['auto_reconnect'],False)
        self.assertIs(options['receive_updates'],False)
