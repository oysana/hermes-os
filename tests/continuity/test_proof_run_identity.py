import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

SOURCE = Path(os.environ.get('PROOF_CHECK_SOURCE', Path.home() / '.hermes/scripts/proof_check.py'))
spec = importlib.util.spec_from_file_location('proof_identity', SOURCE)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class ProofIdentityTests(unittest.TestCase):
    def test_full_run_identifier_never_matches_later_ordinal_or_like_wildcards(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'state.sqlite'
            with sqlite3.connect(db) as conn:
                conn.executescript('CREATE TABLE orchestration_v2_projection_runs (thread_id TEXT,run_id TEXT,status TEXT,ordinal INT); CREATE TABLE orchestration_v2_projection_messages (run_id TEXT,role TEXT,payload_json TEXT,created_at TEXT);')
                for thread, encoded in [('mcp:target', 'mcp%3Atarget'), ('mcp:other', 'mcpXYZ3Atarget')]:
                    for ordinal in (1, 18):
                        run = f'run:thread:{encoded}:ordinal:{ordinal}'
                        conn.execute('INSERT INTO orchestration_v2_projection_runs VALUES (?,?,?,?)', (thread,run,'completed',ordinal))
                        conn.execute('INSERT INTO orchestration_v2_projection_messages VALUES (?,?,?,?)', (run,'assistant',json.dumps({'text':str(ordinal)}),str(ordinal)))
            previous = proof.DB
            proof.DB = db
            try:
                requested = 'run:thread:mcp%3Atarget:ordinal:1'
                thread, run, status, text, _ = proof.thread_text('mcp:target', requested)
                self.assertEqual((thread, run, status, text), ('mcp:target', requested, 'completed', '1'))
                with self.assertRaises(SystemExit):
                    proof.thread_text('mcp:tar', requested)
            finally:
                proof.DB = previous


def _report(command, rc='0'):
    return ('PROOF\n'
            'Итог: проверено\n'
            'Как проверил: ' + command + '\n'
            'Код возврата: ' + rc + '\n'
            'Что показала проверка: ok\n'
            'PR: нет | коммит: ab12cd3 | CI: нет CI\n'
            'Доказательство: /tmp/x.log\n'
            'Что изменено: src/a.py\n'
            'Почему сломалось: не исправление\n'
            'Не проверено: ничего\n')


class RecordedPayloadShapes(unittest.TestCase):
    """executions_after() feeds real command_execution payload shapes into evidence."""

    def _execs(self, *payloads):
        return proof.executions_after(json.dumps(p) for p in payloads)

    def test_compound_payload_input_verified(self):
        executions = self._execs(
            {'input': 'cd /x && t=$(mktemp -d) && bash check.sh > /tmp/l 2>&1; echo GATE_RC=$?', 'exitCode': 0, 'output': 'GATE_RC=0'})
        _mode, _rows, miss = proof.judge(_report('bash check.sh > /tmp/l 2>&1'), executions=executions)
        self.assertEqual([], miss)

    def test_multiline_payload_input_verified(self):
        executions = self._execs(
            {'input': 'cd /x\nbash check.sh > /tmp/l 2>&1\necho RC=$?', 'exitCode': 0, 'output': 'RC=0'})
        _mode, _rows, miss = proof.judge(_report('bash check.sh > /tmp/l 2>&1'), executions=executions)
        self.assertEqual([], miss)

    def test_bash_lc_payload_verified(self):
        executions = self._execs(
            {'input': '/bin/bash -lc "cd /x && python3 -B -m unittest t 2>&1"', 'exitCode': 0, 'output': 'ok'})
        _mode, _rows, miss = proof.judge(_report('python3 -B -m unittest t'), executions=executions)
        self.assertEqual([], miss)

    def test_bash_lc_payload_masked_not_verified(self):
        executions = self._execs(
            {'input': '/bin/bash -lc "bash check.sh | tail -5"', 'exitCode': 0, 'output': ''})
        _mode, _rows, miss = proof.judge(_report('bash check.sh'), executions=executions)
        self.assertTrue(any('конвейером' in m for m in miss), miss)

    def test_payload_heredoc_body_stays_missing(self):
        executions = self._execs(
            {'input': "cat > /tmp/f <<'EOF'\nrm -rf /tmp/x\nEOF", 'exitCode': 0, 'output': ''})
        _mode, _rows, miss = proof.judge(_report('rm -rf /tmp/x'), executions=executions)
        self.assertTrue(any('не выполнялась' in m for m in miss), miss)
