"""Public runner seams. No native helper, model, collector or portal executes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import io
from contextlib import contextmanager
from unittest.mock import Mock
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
import os
RUNNER_SOURCE = Path(os.environ.get('ACCEPTANCE_RUNNER_SOURCE', '/home/yqecea/coding-projects/hermes-pro/.artifacts/hermes-bridge-20261011/live-repair/proactivity-completion-20261012/prd-wave1/acceptance/run_suite.py')).resolve()
SCENARIOS = RUNNER_SOURCE.with_name('scenarios.json')
BASELINE_SCENARIOS = HERE / 'fixtures/scenarios-before-ledger-markers.json'
spec = importlib.util.spec_from_file_location('candidate_runner', RUNNER_SOURCE)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def fake_admission(rc=0,output='{"idle_admitted":true}'):
    return SimpleNamespace(pid=777,returncode=rc,stdout=io.StringIO(),stderr=io.StringIO(),
                           communicate=lambda timeout:(output,''),poll=lambda:rc)

@contextmanager
def admission_boundary(model,rc=0,output='{"idle_admitted":true}'):
    admission=Mock(side_effect=lambda command,**kwargs:fake_admission(rc,output))
    def launch(command,**kwargs):
        return admission(command,**kwargs) if command[0]=='python3' else model(command,**kwargs)
    with patch.object(m.subprocess,'Popen',side_effect=launch):yield admission

class BoundaryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hermes-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'; self.run.mkdir()

class RunnerBoundaryTests(BoundaryFixture):
    def test_admission_calls_only_pinned_native_helper(self):
        def public_admission(command, **kwargs):
            self.assertEqual(command[0:2], ['python3', '-B'])
            self.assertTrue(command[-1].endswith('/admission.py'))
            self.assertNotIn('status', command)
            return fake_admission(0,json.dumps({'idle_admitted': True}))
        with patch.object(m.subprocess, 'Popen', side_effect=public_admission) as run:
            m.check_idle(self.run)
        self.assertEqual(run.call_count, 1)

    def test_public_command_uses_owned_workdir_and_original_budget(self):
        s={'profile':'S','budget_s':150,'ledger_mode':'empty-public'}
        command=m.command(s, self.run/'query.txt')
        self.assertEqual(command[command.index('--run-budget')+1], '150')
        self.assertIn('--in', command)
        self.assertEqual(command[command.index('--in')+1], str(self.run/'generated'))


import copy
import datetime
import hashlib
import os
import signal
import stat
from types import SimpleNamespace

class AdmissionAndScopeTests(BoundaryFixture):
    def setUp(self):
        super().setUp()
        self.helper=self.root/'admission.py'; self.helper.write_bytes(m.ADMISSION.read_bytes())
        self.bootstrap=self.root/'run-selected.py'; self.bootstrap.write_bytes(m.BOOTSTRAP.read_bytes())
        controls=patch.multiple(m,ADMISSION=self.helper,BOOTSTRAP=self.bootstrap)
        controls.start();self.addCleanup(controls.stop)

    def test_busy_unknown_and_noninteger_rc_never_admit(self):
        for rc,projection in ((0,{'idle_admitted':False}),(False,{'idle_admitted':True}),
                              (0.0,{'idle_admitted':True}),(1,{'idle_admitted':True}),
                              (0,{}),(0,{'idle_admitted':1}),(0,[])):
            with self.subTest(rc=rc,projection=projection),patch.object(m.subprocess,'Popen',return_value=fake_admission(rc,json.dumps(projection))):
                with self.assertRaises(RuntimeError):m.check_idle(self.run)

    def test_bad_json_and_helper_drift_refuse(self):
        with patch.object(m.subprocess,'Popen',return_value=fake_admission(0,'not-json')):
            with self.assertRaises(RuntimeError):m.check_idle(self.run)
        self.helper.write_text('drift')
        with patch.object(m.subprocess,'Popen',side_effect=AssertionError('Native subprocess forbidden')):
            with self.assertRaises(RuntimeError):m.check_idle(self.run)

    def test_helper_change_during_admission_refuses(self):
        def admission(*args,**kwargs):
            self.helper.write_text('changed during call')
            return fake_admission()
        with patch.object(m.subprocess,'Popen',side_effect=admission):
            with self.assertRaises(RuntimeError):m.check_idle(self.run)

    def test_output_root_rejects_escape_traversal_and_alias(self):
        controls=patch.object(m,'PROJECT_ARTIFACT_ROOT',self.root);controls.start();self.addCleanup(controls.stop)
        alias=self.root/'alias';alias.symlink_to(self.run,target_is_directory=True)
        for path in (Path('relative'),self.root,self.root.parent/'outside',self.run/'../run',alias):
            with self.subTest(path=str(path)),self.assertRaises(ValueError):m.output_root(path)
        self.assertEqual(m.output_root(self.root/'runs'),self.root/'runs')

    def test_legacy_capture_root_is_distinct_from_project(self):
        with patch.multiple(m,PROJECT_ARTIFACT_ROOT=self.root/'project',LEGACY_ARTIFACT_ROOT=self.root/'legacy'):
            self.assertEqual(m.capture_root(self.root/'legacy'/'runs'),self.root/'legacy')
            self.assertEqual(m.capture_root(self.root/'project'/'runs'),self.root/'project')
            with self.assertRaises(RuntimeError):m.capture_root(self.root/'outside')

    def test_all_three_public_ledgers_are_private_and_empty(self):
        m.seed_public_ledgers({'id':'G06','ledger_mode':'empty-public','read_only':True},self.run)
        for profile in ('hqecea','sqecea','yqecea'):
            ledger=self.run/'proactive'/profile/'state/tasks.jsonl'
            self.assertEqual(ledger.read_bytes(),b'')
            self.assertEqual(stat.S_IMODE(ledger.stat().st_mode),0o600)
            for directory in (ledger.parent,ledger.parent.parent,self.run/'proactive'):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o700)

    def test_public_marker_cannot_empty_broad_scenario(self):
        with self.assertRaises(RuntimeError):m.seed_public_ledgers({'id':'G01','ledger_mode':'empty-public','read_only':True},self.run)
        m.seed_public_ledgers({'id':'G01'},self.run)
        self.assertFalse((self.run/'proactive').exists())

    def test_only_original_alias_and_synthetic_role_cases_receive_marker(self):
        cases=json.loads(SCENARIOS.read_text());before=json.loads(BASELINE_SCENARIOS.read_text())
        new_ids=['HP_WORK_SYNTH','HP_STUDY_SYNTH','HP_PERSONAL_SYNTH','HP_STUDY_LINKED',
                 'HP_WORK_ENGINEERING','HP_STUDY_PROGRESS']
        self.assertEqual([c['id'] for c in cases if c.get('ledger_mode')],['G06','G06_S','G06_Y']+new_ids)
        appended_ids=['K03','K08','K10']+new_ids
        self.assertEqual(len(cases),len(before)+len(appended_ids))
        self.assertEqual([c['id'] for c in cases[len(before):]],appended_ids)
        self.assertEqual([c['id'] for c in m.select(cases,'G06')],['G06','G06_S','G06_Y'])
        for old,new in zip(before,cases):
            clean=dict(new);clean.pop('ledger_mode',None);self.assertEqual(old,clean)

    def test_footer_keeps_explicit_scope_permissions(self):
        for action in ('Send one message to verified own Telegram topic','Dispatch read-only T3 workers','Dispatch approved demo implementation'):
            text=m.footer(self.run,{'id':'TEST','read_only':True,'side_effects_allowed':[action]})
            self.assertIn(action,text);self.assertIn('Все инструменты Hermes остаются доступны',text)

class CaptureTests(BoundaryFixture):
    def setUp(self):
        super().setUp();self.start=datetime.datetime.now(datetime.timezone.utc);self.end=self.start+datetime.timedelta(seconds=2)
        self.events=[{'type':'tool_use','name':'terminal','tool_call_id':'public-1'},
                     {'type':'tool_result','name':'terminal','tool_call_id':'public-1','output':'invented'}]
        self.row={'tool_name':'terminal','tool_call_id':'public-1','result':'invented','timestamp':self.start.isoformat()}

    def save(self,row=None,mode=0o600):
        path=self.run/'tool-results.jsonl';path.write_text(json.dumps(row or self.row)+'\n');path.chmod(mode)

    def test_matching_fresh_actual_ids_qualify_callback_only(self):
        self.save();receipt=m.capture_evidence(self.run,self.events,self.start,self.end)
        self.assertEqual(receipt['status'],'VERIFIED');self.assertEqual(receipt['matched_tool_call_ids'],['public-1'])
        self.assertFalse(receipt['gateway_freshness_verified'])

    def test_missing_wrong_old_or_nonprivate_capture_is_incomplete(self):
        self.assertEqual(m.capture_evidence(self.run,self.events,self.start,self.end)['status'],'INCOMPLETE')
        for change in ({'tool_call_id':'other'},{'tool_name':'wrong'},{'timestamp':(self.start-datetime.timedelta(seconds=1)).isoformat()}):
            self.save(dict(self.row,**change));self.assertEqual(m.capture_evidence(self.run,self.events,self.start,self.end)['status'],'INCOMPLETE')
        self.save(mode=0o644);self.assertEqual(m.capture_evidence(self.run,self.events,self.start,self.end)['status'],'INCOMPLETE')

    def test_no_tools_has_no_registration_claim(self):
        result=m.capture_evidence(self.run,[],self.start,self.end)
        self.assertEqual(result['status'],'NOT_EXPECTED');self.assertFalse(result['native_registration_verified'])

class ProcessTests(BoundaryFixture):
    def test_timeout_kill_and_reap_are_bounded(self):
        proc=SimpleNamespace(pid=123,returncode=-9,poll=lambda:None)
        waits=[]
        def wait(timeout):
            waits.append(timeout)
            if len(waits)==1:raise subprocess.TimeoutExpired('invented',timeout)
            return -9
        proc.wait=wait
        with patch.object(m.os,'killpg') as kill:
            self.assertEqual(m.stop_child(proc),-9)
        self.assertEqual(waits,[10,5]);self.assertEqual([c.args[1] for c in kill.call_args_list],[signal.SIGTERM,signal.SIGKILL])

    def test_unreaped_child_is_a_blocker(self):
        proc=SimpleNamespace(pid=123,poll=lambda:None,wait=lambda timeout:(_ for _ in ()).throw(subprocess.TimeoutExpired('invented',timeout)))
        with patch.object(m.os,'killpg'),self.assertRaises(RuntimeError):m.stop_child(proc)

class ExecuteTests(BoundaryFixture):
    def setUp(self):
        super().setUp()
        self.helper=self.root/'admission.py';self.helper.write_bytes(m.ADMISSION.read_bytes())
        self.bootstrap=self.root/'run-selected.py';self.bootstrap.write_bytes(m.BOOTSTRAP.read_bytes())
        controls=patch.multiple(m,ADMISSION=self.helper,BOOTSTRAP=self.bootstrap,OUT=self.root)
        controls.start();self.addCleanup(controls.stop)

    def public_scenario(self):
        cases=json.loads(SCENARIOS.read_text());s=copy.deepcopy(m.select(cases,'G06')[0])
        # max_chars invokes a native offline phone plugin; that boundary is not admitted in this suite.
        s['pass']['auto']=[rule for rule in s['pass']['auto'] if rule['kind']!='max_chars']
        return s

    def test_one_good_admission_launches_exact_scoped_command(self):
        s=self.public_scenario();run=self.root/'case';children=[]
        def launch(cmd,**kwargs):
            self.assertEqual(cmd,m.command(s,run/'query.txt'))
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE_ROOT'],str(self.root))
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE'],str(run))
            for profile in ('hqecea','sqecea','yqecea'):self.assertEqual((run/'proactive'/profile/'state/tasks.jsonl').read_bytes(),b'')
            now=datetime.datetime.now(datetime.timezone.utc).isoformat()
            events=[{'type':'tool_use','name':'terminal','tool_call_id':'one'},
                    {'type':'tool_result','name':'terminal','tool_call_id':'one','output':'public'},
                    {'type':'result','text':'H работа, S учёба, Y жизнь.','exit_code':0}]
            kwargs['stdout'].write(''.join(json.dumps(e)+'\n' for e in events));kwargs['stdout'].flush()
            p=run/'tool-results.jsonl';p.write_text(json.dumps({'tool_name':'terminal','tool_call_id':'one','result':'public','timestamp':now})+'\n');p.chmod(0o600)
            proc=SimpleNamespace(pid=123,returncode=0,poll=lambda:0,wait=lambda timeout:0);children.append(proc);return proc
        with patch.object(m,'PROJECT_ARTIFACT_ROOT',self.root),admission_boundary(launch) as admission,patch.object(m.time,'sleep',side_effect=AssertionError('No retry sleep')),patch.object(m,'state_snapshot',side_effect=AssertionError('No private profile inventory')):
            result=m.execute(s,run)
        self.assertEqual(admission.call_count,1);self.assertEqual(len(children),1)
        self.assertEqual(result['verdict'],'NEEDS_JUDGE');self.assertEqual(result['post_tool_capture']['status'],'VERIFIED')
        for name in ('query.txt','raw.jsonl','stderr.txt','command.json','grade.json'):
            self.assertEqual(stat.S_IMODE((run/name).stat().st_mode),0o600)

    def test_failed_admission_has_zero_native_launches_and_no_retry(self):
        s=self.public_scenario();run=self.root/'refused'
        with patch.object(m,'PROJECT_ARTIFACT_ROOT',self.root),admission_boundary(lambda *a,**k:(_ for _ in ()).throw(AssertionError('No native launch')),rc=1,output='{"idle_admitted":false}') as admission,patch.object(m.time,'sleep',side_effect=AssertionError('No retry')):
            result=m.execute(s,run)
        self.assertEqual(result['verdict'],'REFUSED');self.assertEqual(admission.call_count,1)

    def test_actual_final_media_and_judge_rules_stay_pure(self):
        page=self.root/'page.html';page.write_text('public')
        s={'pass':{'auto':[{'kind':'final_nonempty'},{'kind':'media_files','extensions':['.html']}],'judge':['Inspect actual final']}}
        events=[{'type':'text','text':'MEDIA:'+str(page)},{'type':'result','exit_code':0,'text':'Готово'}]
        result=m.grade(s,events,0);self.assertEqual(result['verdict'],'NEEDS_JUDGE');self.assertEqual(result['final'],'Готово')
        self.assertTrue(result['automatic'][1]['passed'])

    def test_dry_run_never_launches_admission_model_or_setup(self):
        destination=self.root/'outputs'
        (self.root/'scenarios.json').write_bytes(SCENARIOS.read_bytes())
        with patch.object(m,'OUT',self.root),patch.object(m,'PROJECT_ARTIFACT_ROOT',self.root),patch('sys.argv',['runner','--only','G06','--output-root',str(destination),'--dry-run']),patch.object(m.subprocess,'run',side_effect=AssertionError('No native helper')),patch.object(m.subprocess,'Popen',side_effect=AssertionError('No model')):
            m.main()
        self.assertFalse(destination.exists())

class CleanupAndProgressTests(BoundaryFixture):
    def setUp(self):
        super().setUp()
        helper=self.root/'admission.py';helper.write_bytes(m.ADMISSION.read_bytes())
        bootstrap=self.root/'run-selected.py';bootstrap.write_bytes(m.BOOTSTRAP.read_bytes())
        controls=patch.multiple(m,ADMISSION=helper,BOOTSTRAP=bootstrap,OUT=self.root,PROJECT_ARTIFACT_ROOT=self.root)
        controls.start();self.addCleanup(controls.stop)

    def case(self):
        s=copy.deepcopy(m.select(json.loads(SCENARIOS.read_text()),'G06')[0])
        s['pass']['auto']=[rule for rule in s['pass']['auto'] if rule['kind']!='max_chars'];return s

    def test_exception_after_launch_reaps_only_owned_child(self):
        run=self.root/'failure';state={'done':False};calls=[]
        def wait(timeout):
            calls.append(timeout)
            if len(calls)==1:raise OSError('Invented wait failure')
            state['done']=True;return -15
        proc=SimpleNamespace(pid=321,returncode=-15,poll=lambda:-15 if state['done'] else None,wait=wait)
        with admission_boundary(lambda *a,**k:proc),patch.object(m.os,'killpg') as kill:
            result=m.execute(self.case(),run)
        self.assertEqual(result['verdict'],'REFUSED');self.assertTrue(state['done'])
        self.assertEqual(kill.call_args.args,(321,signal.SIGTERM));self.assertEqual(calls,[225,10])

    def test_unreaped_child_stops_progress(self):
        run=self.root/'unreaped'
        proc=SimpleNamespace(pid=321,poll=lambda:None,wait=lambda timeout:(_ for _ in ()).throw(subprocess.TimeoutExpired('invented',timeout)))
        with admission_boundary(lambda *a,**k:proc),patch.object(m.os,'killpg'):
            result=m.execute(self.case(),run)
        self.assertEqual(result['verdict'],'REFUSED');self.assertIn('reaped',result['reason'])

    def test_missing_capture_refuses_an_otherwise_good_real_grade(self):
        run=self.root/'no-capture'
        def launch(command,**kwargs):
            events=[{'type':'tool_use','name':'terminal','tool_call_id':'public'},
                    {'type':'tool_result','name':'terminal','tool_call_id':'public','output':'invented'},
                    {'type':'result','exit_code':0,'text':'H работа S учёба Y жизнь'}]
            kwargs['stdout'].write(''.join(json.dumps(e)+'\n' for e in events));kwargs['stdout'].flush()
            return SimpleNamespace(pid=321,returncode=0,poll=lambda:0,wait=lambda timeout:0)
        with admission_boundary(launch):
            result=m.execute(self.case(),run)
        self.assertEqual(result['verdict'],'REFUSED');self.assertEqual(result['grade_verdict'],'NEEDS_JUDGE')
        self.assertIn('CAPTURE_INCOMPLETE',result['reason'])

    def test_main_stops_after_unknown_admission_without_next_case(self):
        (self.root/'scenarios.json').write_bytes(SCENARIOS.read_bytes())
        with patch('sys.argv',['runner','--only','G06','--output-root',str(self.root/'runs')]),admission_boundary(lambda *a,**k:(_ for _ in ()).throw(AssertionError('No native child')),rc=1,output='{"idle_admitted":false}') as admission:
            with self.assertRaises(SystemExit):m.main()
        self.assertEqual(admission.call_count,1)
        parents=list((self.root/'runs').iterdir());self.assertEqual(len(parents),1)
        self.assertFalse((parents[0]/'G06_S').exists())

class AdmissionCleanupTests(BoundaryFixture):
    def test_exited_admission_leader_still_terminates_and_drains_owned_group(self):
        proc=fake_admission(rc=0)
        proc.communicate=Mock(side_effect=[subprocess.TimeoutExpired('synthetic descendant',10),('','')])
        with patch.object(m.os,'killpg') as kill:
            m.stop_child(proc,drain=True)
        self.assertEqual([c.args for c in kill.call_args_list],[(777,signal.SIGTERM),(777,signal.SIGKILL)])
        self.assertEqual([c.kwargs['timeout'] for c in proc.communicate.call_args_list],[10,5])

    def test_timeout_group_cleanup_finishes_before_refused_without_model(self):
        helper=self.root/'admission.py';helper.write_bytes(m.ADMISSION.read_bytes())
        bootstrap=self.root/'run-selected.py';bootstrap.write_bytes(m.BOOTSTRAP.read_bytes())
        proc=fake_admission();proc.communicate=Mock(side_effect=[subprocess.TimeoutExpired('synthetic',20),('','')])
        scenario=copy.deepcopy(m.select(json.loads(SCENARIOS.read_text()),'G06')[0])
        with patch.multiple(m,ADMISSION=helper,BOOTSTRAP=bootstrap,OUT=self.root,PROJECT_ARTIFACT_ROOT=self.root),patch.object(m.subprocess,'Popen',return_value=proc) as launch,patch.object(m.os,'killpg') as kill:
            result=m.execute(scenario,self.root/'timeout')
        self.assertEqual(result['verdict'],'REFUSED');self.assertEqual(launch.call_count,1)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual([c.kwargs['timeout'] for c in proc.communicate.call_args_list],[20,10])
        kill.assert_called_once_with(777,signal.SIGTERM)
        self.assertTrue(proc.stdout.closed and proc.stderr.closed)

class LinkedExecuteTests(BoundaryFixture):
    def setUp(self):
        super().setUp()
        helper=self.root/'admission.py';helper.write_bytes(m.ADMISSION.read_bytes())
        bootstrap=self.root/'run-selected.py';bootstrap.write_bytes(m.BOOTSTRAP.read_bytes())
        controls=patch.multiple(m,ADMISSION=helper,BOOTSTRAP=bootstrap,OUT=self.root,PROJECT_ARTIFACT_ROOT=self.root)
        controls.start();self.addCleanup(controls.stop)
        inventory=patch.object(m,'state_snapshot',side_effect=AssertionError('No private profile reads'))
        inventory.start();self.addCleanup(inventory.stop)
        self.case=copy.deepcopy(m.select(json.loads(SCENARIOS.read_text()),'HP_STUDY_LINKED')[0])

    def events(self,identity='synthetic-owned'):
        return [{'type':'system','subtype':'init','session_id':identity},
                {'type':'result','session_id':identity,'exit_code':0,'text':'Учебный ответ.'}]

    def model(self,rows):
        def launch(command,**kwargs):
            kwargs['stdout'].write(''.join(json.dumps(row)+'\n' for row in rows))
            kwargs['stdout'].flush()
            kwargs['stderr'].write('Synthetic replay diagnostic retained for joint judge.\n')
            kwargs['stderr'].flush()
            return SimpleNamespace(pid=321,returncode=0,poll=lambda:0,wait=lambda timeout:0)
        return launch

    def test_two_turns_propagate_requests_hold_one_lock_and_retain_evidence(self):
        run=self.root/'linked';order=[];commands=[]
        real_flock=m.fcntl.flock
        def assert_locked():
            with (self.root/'S.lock').open('a') as contender:
                with self.assertRaises(BlockingIOError):
                    real_flock(contender,m.fcntl.LOCK_EX|m.fcntl.LOCK_NB)
        def launch(command,**kwargs):
            assert_locked()
            if command[0]=='python3':
                order.append('admission');return fake_admission()
            number=len(commands)+1;commands.append(command);order.append('model')
            turn=run/f'turn-{number:02d}'
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE'],str(turn))
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE_ROOT'],str(self.root))
            self.assertEqual(command[command.index('--in')+1],str(turn/'generated'))
            self.assertEqual(command[command.index('--query-file')+1],str(turn/'query.txt'))
            self.assertEqual(command[command.index('--run-budget')+1],'120')
            self.assertEqual(kwargs['cwd'],m.HOMES['S'])
            self.assertTrue(kwargs['start_new_session'])
            query=(turn/'query.txt').read_text()
            self.assertTrue(query.startswith(self.case['turns'][number-1]))
            self.assertIn(str(turn)+'/generated',query)
            for profile in ('hqecea','sqecea','yqecea'):
                self.assertEqual((turn/'proactive'/profile/'state/tasks.jsonl').read_bytes(),b'')
            if number==1:self.assertNotIn('--resume',command)
            else:
                self.assertEqual(command[command.index('--resume')+1],'synthetic-owned')
                self.assertNotIn(self.case['turns'][0],query)
            return self.model(self.events())(command,**kwargs)
        with patch.object(m.subprocess,'Popen',side_effect=launch),patch.object(m.fcntl,'flock',wraps=real_flock) as lock:
            result=m.execute(self.case,run)
        self.assertEqual(lock.call_count,1);self.assertTrue(lock.call_args.args[0].closed)
        self.assertEqual(order,['admission','model','admission','model'])
        self.assertEqual(result['verdict'],'NEEDS_JUDGE')
        self.assertEqual(len(result['turns']),2)
        for number in (1,2):
            turn=run/f'turn-{number:02d}'
            for name in ('scenario.json','query.txt','raw.jsonl','stderr.txt','command.json','grade.json','idle-check.json'):
                self.assertEqual(stat.S_IMODE((turn/name).stat().st_mode),0o600)
            self.assertIn('replay diagnostic',(turn/'stderr.txt').read_text())
            grade=json.loads((turn/'grade.json').read_text())
            self.assertEqual(grade['session_link']['entries'],[{'line':1,'session_id':'synthetic-owned'}])
            self.assertEqual(grade['session_link']['terminals'],[{'line':2,'session_id':'synthetic-owned'}])
            self.assertEqual(grade['session_link']['history_replay'],'NEEDS_JUDGE')
            self.assertEqual(grade['verdict'],'NEEDS_JUDGE')
        predecessor={'raw':str(run/'turn-01/raw.jsonl'),'result_line':2,'session_id':'synthetic-owned'}
        self.assertIsNone(result['turns'][0]['session_link']['predecessor'])
        self.assertEqual(result['turns'][1]['session_link']['predecessor'],predecessor)
        self.assertEqual(json.loads((run/'turn-02/command.json').read_text())['predecessor'],predecessor)

    def test_registry_retains_all_69_staged_objects_and_appends_two_cases(self):
        cases=json.loads(SCENARIOS.read_text())
        import hashlib
        self.assertEqual(len(cases),71);self.assertEqual(cases[68],self.case)
        retained=json.dumps(cases[:69],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.assertEqual(hashlib.sha256(retained.encode()).hexdigest(),
                         '0dadfb854da37e4ef652582ddd00a7dc259c50a002e6c5cfee3874246e288e5c')
        self.assertEqual([case['id'] for case in cases[69:]],
                         ['HP_WORK_ENGINEERING','HP_STUDY_PROGRESS'])
        self.assertNotIn('prompt',self.case);self.assertEqual(len(self.case['turns']),2)
        self.assertEqual(self.case['covered_ids'],['HP_STUDY_LINKED'])
        self.assertEqual(self.case['profile'],'S');self.assertIsNone(self.case['fixture'])
        self.assertIs(self.case['read_only'],True)
        self.assertEqual(self.case['ledger_mode'],'empty-public')

    def test_engineering_executes_one_public_h_turn(self):
        case,=m.select(json.loads(SCENARIOS.read_text()),'HP_WORK_ENGINEERING')
        run=self.root/'engineering';commands=[]
        def launch(command,**kwargs):
            commands.append(command)
            self.assertEqual(command[:3],['hermes','chat','--oneshot'])
            self.assertEqual(command[command.index('--run-budget')+1],'180')
            self.assertNotIn('--resume',command)
            self.assertEqual(kwargs['cwd'],m.HOMES['H'])
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE'],str(run))
            self.assertEqual(command[command.index('--in')+1],str(run/'generated'))
            self.assertTrue((run/'query.txt').read_text().startswith(case['prompt']))
            for profile in ('hqecea','sqecea','yqecea'):
                self.assertEqual((run/'proactive'/profile/'state/tasks.jsonl').read_bytes(),b'')
            return self.model(self.events())(command,**kwargs)
        with admission_boundary(launch) as admission:
            result=m.execute(case,run)
        self.assertEqual(admission.call_count,1);self.assertEqual(len(commands),1)
        self.assertEqual(result['verdict'],'NEEDS_JUDGE')
        self.assertFalse((run/'turn-01').exists())

    def test_progress_uses_three_fresh_admissions_and_immediate_predecessors(self):
        case,=m.select(json.loads(SCENARIOS.read_text()),'HP_STUDY_PROGRESS')
        run=self.root/'progress';commands=[]
        def launch(command,**kwargs):
            commands.append(command);number=len(commands);turn=run/f'turn-{number:02d}'
            self.assertEqual(command[:4],['hermes','-p','sqecea','chat'])
            self.assertEqual(command[command.index('--run-budget')+1],'120')
            self.assertEqual(kwargs['env']['HERMES_ACCEPTANCE_CAPTURE'],str(turn))
            self.assertEqual(command[command.index('--query-file')+1],str(turn/'query.txt'))
            self.assertEqual(command[command.index('--in')+1],str(turn/'generated'))
            self.assertTrue((turn/'query.txt').read_text().startswith(case['turns'][number-1]))
            for profile in ('hqecea','sqecea','yqecea'):
                self.assertEqual((turn/'proactive'/profile/'state/tasks.jsonl').read_bytes(),b'')
            if number==1:self.assertNotIn('--resume',command)
            else:self.assertEqual(command[command.index('--resume')+1],'synthetic-owned')
            return self.model(self.events())(command,**kwargs)
        with admission_boundary(launch) as admission:
            result=m.execute(case,run)
        self.assertEqual(admission.call_count,3);self.assertEqual(len(commands),3)
        self.assertEqual(result['verdict'],'NEEDS_JUDGE');self.assertEqual(len(result['turns']),3)
        for number in (1,2,3):
            turn=run/f'turn-{number:02d}'
            for name in ('scenario.json','query.txt','raw.jsonl','stderr.txt','command.json',
                         'grade.json','idle-check.json','modified-state.json'):
                self.assertEqual(stat.S_IMODE((turn/name).stat().st_mode),0o600)
            predecessor=None if number==1 else {
                'raw':str(run/f'turn-{number-1:02d}'/'raw.jsonl'),
                'result_line':2,'session_id':'synthetic-owned'}
            self.assertEqual(result['turns'][number-1]['session_link']['predecessor'],predecessor)
            self.assertEqual(json.loads((turn/'command.json').read_text())['predecessor'],predecessor)
        stale=m._OwnedSession(run/'turn-01/raw.jsonl',2,'synthetic-owned')
        with self.assertRaises(ValueError):
            m.command(case,run/'turn-03/query.txt',predecessor=stale)

    def test_new_cases_refuse_other_profiles_shapes_and_arbitrary_linked_id(self):
        cases=json.loads(SCENARIOS.read_text())
        h,=m.select(cases,'HP_WORK_ENGINEERING');s,=m.select(cases,'HP_STUDY_PROGRESS')
        invalid=[dict(h,profile='S'),dict(h,fixture={}),dict(h,read_only=1),
                 dict(h,ledger_mode=None),dict(s,profile='H'),dict(s,fixture={}),
                 dict(s,read_only=1),dict(s,ledger_mode=None),dict(s,id='ARBITRARY_LINKED'),
                 dict(s,turns=s['turns'][:2]),dict(s,turns=s['turns']+['extra'])]
        single=dict(s,prompt='One prompt');single.pop('turns');invalid.append(single)
        linked=dict(h,turns=['one','two']);linked.pop('prompt');invalid.append(linked)
        for number,case in enumerate(invalid):
            with self.subTest(case=case),patch.object(m.subprocess,'Popen') as launch:
                result=m.execute(case,self.root/f'refused-new-{number}')
            self.assertEqual(result['verdict'],'REFUSED');launch.assert_not_called()

    def test_progress_stops_before_third_turn_after_failed_second_admission(self):
        case,=m.select(json.loads(SCENARIOS.read_text()),'HP_STUDY_PROGRESS')
        run=self.root/'progress-busy';admissions=[];models=[]
        def launch(command,**kwargs):
            if command[0]=='python3':
                admissions.append(command)
                return fake_admission(1 if len(admissions)==2 else 0)
            models.append(command);return self.model(self.events())(command,**kwargs)
        with patch.object(m.subprocess,'Popen',side_effect=launch):
            result=m.execute(case,run)
        self.assertEqual(result['verdict'],'REFUSED')
        self.assertEqual(len(admissions),2);self.assertEqual(len(models),1)
        self.assertFalse((run/'turn-03').exists())

    def test_malformed_shapes_and_unadmitted_linked_cases_never_launch(self):
        changes=[{'prompt':'Also supplied'}, {'turns':[]}, {'turns':'not a list'},
                 {'turns':['valid','']}, {'turns':['valid',None]}, {'turns':['valid','   ']},
                 {'turns':['valid','inline ``` fence']},
                 {'id':'HP_WORK_SYNTH'}, {'profile':'H'}, {'fixture':{}},
                 {'ledger_mode':None}, {'read_only':1}]
        for number,change in enumerate(changes):
            with self.subTest(change=change),patch.object(m.subprocess,'Popen',side_effect=AssertionError('No native launch')):
                result=m.execute(dict(self.case,**change),self.root/f'invalid-{number}')
                self.assertEqual(result['verdict'],'REFUSED')

    def test_failed_first_process_and_second_admission_stop_progress(self):
        for failed_first in (True,False):
            run=self.root/('failed' if failed_first else 'busy');models=[];admissions=[]
            def launch(command,**kwargs):
                if command[0]=='python3':
                    admissions.append(command)
                    return fake_admission(1 if len(admissions)==2 else 0)
                models.append(command)
                proc=self.model(self.events())(command,**kwargs)
                if failed_first:
                    proc.returncode=1;proc.poll=lambda:1;proc.wait=lambda timeout:1
                return proc
            with self.subTest(failed_first=failed_first),patch.object(m.subprocess,'Popen',side_effect=launch):
                result=m.execute(self.case,run)
            self.assertEqual(result['verdict'],'REFUSED');self.assertEqual(len(models),1)
            self.assertEqual(len(admissions),1 if failed_first else 2)
            if not failed_first:
                self.assertTrue((run/'turn-01/grade.json').is_file())
                second=json.loads((run/'turn-02/grade.json').read_text())
                self.assertEqual(second['session_link']['predecessor']['session_id'],'synthetic-owned')
                self.assertTrue((run/'turn-02/command.json').is_file())
            self.assertFalse((run/'turn-03').exists())

    def test_unknown_terminal_identity_and_incomplete_capture_stop_successor(self):
        good=self.events()
        variants=[good[:1],good[1:],good+[good[-1]],good+[{'type':'text','text':'late'}],
                  [good[0],dict(good[1],session_id=None)],
                  [good[0],dict(good[1],exit_code=False)],
                  [good[0],dict(good[1],session_id='rotated')],
                  [good[0],dict(good[1],error='Synthetic failure')],
                  [good[0],[],good[1]],
                  [good[0],{'type':'tool_use','name':'terminal','tool_call_id':'missing'},
                   {'type':'tool_result','name':'terminal','tool_call_id':'missing'},good[1]]]
        for number,rows in enumerate(variants):
            run=self.root/f'unknown-{number}'
            with self.subTest(number=number),admission_boundary(self.model(rows)) as admission:
                result=m.execute(self.case,run)
            self.assertEqual(result['verdict'],'REFUSED')
            self.assertEqual(admission.call_count,1)
            self.assertFalse((run/'turn-02').exists())
            self.assertTrue((run/'turn-01/raw.jsonl').is_file())
            self.assertTrue((run/'turn-01/grade.json').is_file())

    def test_successor_entry_must_match_owned_predecessor(self):
        run=self.root/'wrong-entry';count=[]
        def launch(command,**kwargs):
            count.append(command)
            identity='synthetic-owned' if len(count)==1 else 'unrelated'
            return self.model(self.events(identity))(command,**kwargs)
        with admission_boundary(launch) as admission:
            result=m.execute(self.case,run)
        self.assertEqual(admission.call_count,2);self.assertEqual(len(count),2)
        self.assertEqual(result['verdict'],'REFUSED')
        self.assertIn('LINEAGE_UNQUALIFIED',result['turns'][-1]['reason'])
        self.assertEqual(result['turns'][-1]['session_link']['entries'][0]['session_id'],'unrelated')

    def test_command_refuses_malformed_predecessors_and_changed_raw(self):
        run=self.root/'tokens'
        with admission_boundary(self.model(self.events())):
            self.assertEqual(m.execute(self.case,run)['verdict'],'NEEDS_JUDGE')
        q=run/'turn-02/query.txt';raw=run/'turn-01/raw.jsonl'
        token=m._OwnedSession(raw,2,'synthetic-owned')
        self.assertIn('--resume',m.command(self.case,q,predecessor=token))
        for invalid in ('synthetic-owned',{},m._OwnedSession(raw,False,'synthetic-owned'),
                        m._OwnedSession(raw,2,'--latest'),m._OwnedSession(raw,99,'synthetic-owned'),
                        m._OwnedSession(run/'turn-02/raw.jsonl',2,'synthetic-owned')):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                m.command(self.case,q,predecessor=invalid)
        raw.write_text(json.dumps(self.events()[0])+'\n')
        with self.assertRaises(ValueError):m.command(self.case,q,predecessor=token)

    def test_timeout_reaps_owned_group_and_cannot_resume_even_with_rc_zero(self):
        run=self.root/'timeout';done=[];waits=[]
        def wait(timeout):
            waits.append(timeout)
            if len(waits)==1:raise subprocess.TimeoutExpired('synthetic',timeout)
            done.append(True);return 0
        def launch(command,**kwargs):
            proc=self.model(self.events())(command,**kwargs)
            proc.wait=wait;proc.poll=lambda:0 if done else None
            return proc
        with admission_boundary(launch) as admission,patch.object(m.os,'killpg') as kill:
            result=m.execute(self.case,run)
        self.assertEqual(result['verdict'],'REFUSED');self.assertEqual(admission.call_count,1)
        self.assertEqual(waits,[165,10]);kill.assert_called_once_with(321,signal.SIGTERM)
        self.assertTrue(json.loads((run/'turn-01/command.json').read_text())['timed_out'])
        self.assertFalse((run/'turn-02').exists())

    def test_busy_profile_does_not_release_parent_lock(self):
        real_flock=m.fcntl.flock
        with (self.root/'S.lock').open('a') as parent:
            real_flock(parent,m.fcntl.LOCK_EX|m.fcntl.LOCK_NB)
            with patch.object(m.subprocess,'Popen',side_effect=AssertionError('No launch')):
                result=m.execute(self.case,self.root/'locked')
            self.assertEqual(result['verdict'],'REFUSED');self.assertFalse(parent.closed)
            with (self.root/'S.lock').open('a') as contender:
                with self.assertRaises(BlockingIOError):
                    real_flock(contender,m.fcntl.LOCK_EX|m.fcntl.LOCK_NB)

    def test_repeat_run_never_uses_an_older_run_as_resume_source(self):
        commands=[]
        def launch(command,**kwargs):
            commands.append(command);return self.model(self.events())(command,**kwargs)
        with admission_boundary(launch):
            for name in ('first','second'):
                self.assertEqual(m.execute(self.case,self.root/name)['verdict'],'NEEDS_JUDGE')
        self.assertEqual(len(commands),4)
        self.assertNotIn('--resume',commands[0]);self.assertNotIn('--resume',commands[2])

if __name__ == "__main__":
    unittest.main()
