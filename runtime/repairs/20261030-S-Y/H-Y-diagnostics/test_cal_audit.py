"""Public CLI and pure calendar summaries on supplied synthetic pages, no accounts/network."""
import importlib.util,json,os,subprocess,sys,tempfile,unittest
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('calendar_audit_fixture',HERE/'cal-audit.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
NOW=datetime.fromisoformat('2026-10-30T12:00:00+05:00')


class CalendarAuditTests(unittest.TestCase):
    def test_api_error_is_not_empty_weekdays(self):
        with self.assertRaises(ValueError):audit.render([{'error':{'code':403}}],NOW)

    def test_partial_last_page_is_rejected(self):
        with self.assertRaises(ValueError):audit.render([{'items':[],'nextPageToken':'remaining'}],NOW)

    def test_no_page_is_not_empty_success(self):
        with self.assertRaises(ValueError):audit.render([],NOW)

    def test_duplicates_are_found_across_pages(self):
        event={'start':{'dateTime':'2026-10-30T10:00:00+05:00'},'summary':'Fixture'}
        result=audit.render([{'items':[event],'nextPageToken':'two'},{'items':[event]}],NOW)
        self.assertTrue(any('ДУБЛЬ?' in line for line in result))

    def test_empty_weekdays_are_reported_only_after_successful_full_fetch(self):
        result=audit.render([{'items':[]}],NOW)
        self.assertEqual(sum('ПУСТОЙ БУДЕНЬ:' in line for line in result),6)

    def test_times_are_normalized_to_profile_timezone(self):
        event={'start':{'dateTime':'2026-10-29T23:00:00Z'},'summary':'Fixture'}
        result=audit.render([{'items':[event,event]}],NOW)
        self.assertTrue(any('2026-10-30' in line and '04:00' in line for line in result))

    def test_all_day_date_is_preserved(self):
        event={'start':{'date':'2026-10-30'},'summary':'Fixture'}
        self.assertFalse(any('ПУСТОЙ БУДЕНЬ: 2026-10-30' in line for line in audit.render([{'items':[event]}],NOW)))

    def test_error_in_later_page_cannot_emit_partial_advice(self):
        child=subprocess.run([sys.executable,str(HERE/'cal-audit.py')],input='{"items":[]}\n{"error":{"code":403}}\n',capture_output=True,text=True)
        self.assertEqual(child.returncode,1);self.assertEqual(child.stdout,'');self.assertNotIn('403',child.stderr)

    def test_native_cli_failure_propagates_past_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            for name in ['cal-audit.sh','cal-audit.py']:(home/name).write_bytes((HERE/name).read_bytes())
            (home/'gws-helpers.sh').write_text("gws_token() { printf 'synthetic-token'; }\n")
            (home/'gws').write_text('#!/bin/sh\nprintf \'{"items":[]}\\n\'\nexit 2\n');(home/'gws').chmod(0o700)
            child=subprocess.run(['bash',str(home/'cal-audit.sh')],env={**os.environ,'PATH':temp+os.pathsep+os.environ['PATH']},capture_output=True,text=True)
            self.assertNotEqual(child.returncode,0);self.assertEqual(child.stdout,'')

    def test_native_cli_uses_complete_pagination_and_correct_account_token(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            for name in ['cal-audit.sh','cal-audit.py']:(home/name).write_bytes((HERE/name).read_bytes())
            (home/'gws-helpers.sh').write_text("gws_token() { printf 'synthetic-token'; }\n")
            (home/'gws').write_text('#!/bin/sh\n[ "$GOOGLE_WORKSPACE_CLI_TOKEN" = synthetic-token ] || exit 9\ncase " $* " in *" --page-all "*) ;; *) exit 8;; esac\nprintf \'{"items":[]}\\n\'\n');(home/'gws').chmod(0o700)
            child=subprocess.run(['bash',str(home/'cal-audit.sh')],env={**os.environ,'PATH':temp+os.pathsep+os.environ['PATH']},capture_output=True,text=True)
            self.assertEqual(child.returncode,0,child.stderr);self.assertNotIn('synthetic-token',child.stdout)


if __name__=='__main__':unittest.main()
