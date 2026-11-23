"""Exercise the actual read-only query and transaction admission against private SQLite."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from test_sn_kanban_intake import adapter, gate_show

class AdmissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE tasks(id TEXT,title TEXT,body TEXT,created_by TEXT,assignee TEXT,status TEXT,block_kind TEXT,current_run_id INTEGER);
        CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload TEXT);
        CREATE TABLE task_runs(id INTEGER,task_id TEXT);
        CREATE TABLE task_comments(task_id TEXT,body TEXT);
        ''')
        self.conn.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',('t1','SmartNation 3559999 topic 9496: prepare X','',adapter.CREATED_BY,'sqecea','blocked',None,None))
        self.conn.execute('INSERT INTO task_events(task_id,kind,payload) VALUES(?,?,?)',('t1','blocked',json.dumps({'reason':'initial_status'})))
        self.conn.commit()
        class Native:
            @staticmethod
            def unblock_task(conn, task_id):
                assert conn.in_transaction
                return conn.execute("UPDATE tasks SET status='ready' WHERE id=?",(task_id,)).rowcount==1
        self.kb=Native
    def tearDown(self):self.conn.close()
    def activate(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:return adapter.activate_creation_gate(self.conn,'t1',self.kb)
        finally:self.conn.commit()
    def test_initial_gate_activates(self):self.assertTrue(self.activate())
    def test_late_typed_owner_hold_wins(self):
        self.assertTrue(adapter.at_creation_gate(gate_show('t1')))
        self.conn.execute("UPDATE tasks SET block_kind='needs_input'");self.conn.commit()
        self.assertFalse(self.activate());self.assertEqual(self.conn.execute('SELECT status FROM tasks').fetchone()[0],'blocked')
    def test_late_owner_comment_wins(self):
        self.conn.execute("INSERT INTO task_comments VALUES('t1','do not run')");self.conn.commit()
        self.assertFalse(self.activate())
    def test_late_reassignment_wins(self):
        self.conn.execute("UPDATE tasks SET assignee='default'");self.conn.commit();self.assertFalse(self.activate())
    def test_active_run_wins(self):
        self.conn.execute('UPDATE tasks SET current_run_id=123');self.conn.commit();self.assertFalse(self.activate())
    def test_native_listing_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'kanban.db';self.conn.backup(sqlite3.connect(db))
            before=db.read_bytes()
            cli=adapter.HermesCli(root_home=d,runner=lambda *a,**k:self.fail('list must not execute mutating CLI'))
            self.assertEqual(cli.list_tasks()[0]['id'],'t1');self.assertEqual(before,db.read_bytes())
    def test_running_creation_option_rejected(self):
        with self.assertRaises(SystemExit):adapter.build_arg_parser().parse_args(['--scan','x','--initial-status','running'])

class ReviewedFailureTests(unittest.TestCase):
    def test_sqlite_activation_error_is_reported(self):
        from test_sn_kanban_intake import FakeCli, make_scan,subject,make_row,run
        class Locked(FakeCli):
            def unblock(self,*a,**k):raise sqlite3.OperationalError('database is locked')
        report=run(make_scan([subject('1','Python',[make_row('3559999')])]),Locked())
        self.assertFalse(report['ok']);self.assertEqual(report['counts'],{'error':1})
    def test_body_temp_removed_after_create(self):
        from test_sn_kanban_intake import FakeCli, make_scan,subject,make_row,run
        class Capture(FakeCli):
            def create_task(self,title,path,**kw):
                self.body=Path(path);assert self.body.is_file()
                return super().create_task(title,path,**kw)
        cli=Capture();report=run(make_scan([subject('1','Python',[make_row('3559999')])]),cli)
        self.assertTrue(report['ok']);self.assertFalse(cli.body.exists());self.assertFalse(cli.body.parent.exists())
