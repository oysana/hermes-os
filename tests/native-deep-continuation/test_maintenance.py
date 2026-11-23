"""Rehearse native idle restart and file rollback only in a disposable home."""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_native_deep_continuation import env


def test_native_maintenance_waits_for_turn_and_delegation(env, monkeypatch):
    from gateway import restart_when_idle as native
    active = {'turn': 1, 'delegation': 1}
    stop = AsyncMock()
    runner = SimpleNamespace(
        _restart_task_started=False, _draining=False, _restart_after_turn_timeout=30,
        _awaitable_work_count=lambda: active['turn'],
        _describe_active_work=lambda: [], _scale_to_zero_status=lambda *a: None,
        stop=stop)
    monkeypatch.setattr(native, 'background_units', lambda r:
                        [{'kind': 'delegation'}] if active['delegation'] else [])

    async def run():
        assert native.request_service_restart(runner)
        first_task = runner._restart_task
        assert native.request_service_restart(runner)
        assert runner._restart_task is first_task
        await asyncio.sleep(0.12)
        stop.assert_not_awaited()
        assert not runner._draining
        active['turn'] = 0
        await asyncio.sleep(0.12)
        stop.assert_not_awaited()  # background result must still be able to return
        assert not runner._draining
        active['delegation'] = 0
        async with asyncio.timeout(3):
            await first_task
        stop.assert_awaited_once_with(restart=True, detached_restart=False, service_restart=True)
        assert runner._draining
        assert not runner._restart_force_event.is_set()
    asyncio.run(run())


def test_private_backup_publish_and_partial_failure_rollback(tmp_path):
    packet = Path(__file__).resolve().parents[2]/'runtime/repairs/20261119-native-deep-continuation'
    rows = [line.split('\t') for line in (packet/'manifest.tsv').read_text().splitlines()]
    shadow, backups = tmp_path/'shadow', tmp_path/'backups'
    backups.mkdir(mode=0o700)
    originals = {}
    for installed, before, after, candidate in rows:
        relative = Path(candidate).relative_to('candidate')
        target, backup = shadow/relative, backups/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(installed, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == before
        originals[relative] = target.read_bytes(), stat.S_IMODE(target.stat().st_mode)
        shutil.copy2(target, backup)
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700

    def restore():
        for relative, (data, mode) in originals.items():
            target = shadow/relative
            shutil.copy2(backups/relative, target)
            assert target.read_bytes() == data
            assert stat.S_IMODE(target.stat().st_mode) == mode

    for fail_after in (1, len(rows)):
        try:
            for index, (_installed, before, after, candidate) in enumerate(rows, 1):
                relative = Path(candidate).relative_to('candidate')
                target = shadow/relative
                staged = target.with_name(target.name+'.staged')
                shutil.copy2(packet/candidate, staged)
                staged.chmod(originals[relative][1])
                os.replace(staged, target)
                assert hashlib.sha256(target.read_bytes()).hexdigest() == after
                if index == fail_after:
                    raise OSError('rehearsal: stop before activating the service')
        except OSError:
            restore()
    print('private_backup=0700 partial_publish_rollback=byte_and_mode_exact live_service=untouched')
