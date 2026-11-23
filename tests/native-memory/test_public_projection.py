"""Native public paths and fresh test selection through ordinary gate entries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from native_sandbox import listing_command


def test_selected_system_python_alias_starts(tmp_path):
    alias = tmp_path / 'bin/python'
    alias.parent.mkdir()
    alias.symlink_to('/usr/bin/python3')
    from native_sandbox import base_command
    command = base_command(tmp_path / 'native', python=alias)
    result = subprocess.run(command + [str(alias), '-I', '-B', '-c', 'print(42)'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '42\n'


@pytest.mark.parametrize('kind', ['missing', 'cycle', 'private', 'outside'])
def test_plugin_alias_refuses_undeclared_sources(tmp_path, kind):
    native = tmp_path / 'root/hermes-agent'
    home = tmp_path / 'profile'
    (home / 'plugins').mkdir(parents=True)
    (home / 'config.yaml').write_text('tools: {}\n')
    alias = home / 'plugins/example'
    targets = {'missing': tmp_path / 'root/shared/plugins/example',
               'cycle': alias, 'private': home / 'sessions',
               'outside': tmp_path / 'other/example'}
    if kind in ('private', 'outside'):
        targets[kind].mkdir(parents=True)
        (targets[kind] / '__init__.py').write_text('raise RuntimeError("unsafe")\n')
    alias.symlink_to(targets[kind], target_is_directory=True)
    with pytest.raises(ValueError, match='Selected public source directory symlink refused'):
        listing_command(native, Path(__file__), home, 'H', 'cli')


@pytest.mark.parametrize('platform,enabled', [('cli', False), ('cli', True), ('telegram', True)])
def test_shared_plugins_register_with_private_inputs_absent(tmp_path, platform, enabled):
    import hermes_cli
    native = Path(hermes_cli.__file__).parent.parent
    home = tmp_path / 'profile'
    (home / 'plugins').mkdir(parents=True)
    for name, parent in [('bitwarden-session-bridge', 'plugins')]:
        (home / 'plugins' / name).symlink_to(native.parent / parent / name,
                                           target_is_directory=True)
    private = ('.env', 'auth.json', 'sessions.db', 'jobs.json', 'bridge-session-canary')
    for name in private:
        (home / name).write_text('OWNED_PRIVATE_CANARY\n')
    (home / 'config.yaml').write_text(
        'tools:\n  tool_search:\n    enabled: off\nplugins:\n  enabled:\n'
        '    - bitwarden-session-bridge\n'
        'vault:\n  bitwarden:\n    enabled: ' + str(enabled).lower() + '\n'
        '    session_file: ' + str(home / 'bridge-session-canary') + '\n')
    probe = tmp_path / 'probe.py'
    audit = '''import os,sys
from pathlib import Path
home=Path(os.environ['HERMES_HOME'])
private=('.env','auth.json','sessions.db','jobs.json','bridge-session-canary')
opens={str(home/n):0 for n in private}
launches=[]
def audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=os.fsdecode(args[0])
        if path in opens: opens[path]+=1
    if event=='subprocess.Popen': launches.append(True)
for name in private:
    try: (home/name).read_bytes()
    except FileNotFoundError: pass
    else: raise SystemExit('PRIVATE_CANARY_VISIBLE')
sys.addaudithook(audit)
'''
    checks = '''
from hermes_cli.plugins import get_plugin_manager
from agent.vault_backends import unlock
wanted={'bitwarden-session-bridge':2}
rows={r['name']:r for r in get_plugin_manager().list_plugins() if r['name'] in wanted}
assert set(rows)==set(wanted), rows
for name,hooks in wanted.items():
    assert rows[name]['enabled'] is True and rows[name]['error'] is None, rows
    assert rows[name]['hooks']==hooks, rows
assert not unlock.is_unlocked('bitwarden')
assert not launches, launches
assert all(not (home/n).exists() for n in private)
assert opens[str(home/'bridge-session-canary')]==EXPECTED_OPENS, opens
print('PUBLIC_PLUGINS_REGISTERED_PRIVATE_INPUTS_ABSENT')
'''.replace('EXPECTED_OPENS', str(int(enabled)))
    probe.write_text(audit + Path(__file__).with_name('native_list.py').read_text() + checks)
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('native_sandbox.py')),
         'probe', str(native), str(home), str(probe), platform],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    from check_toolsets import check_output
    check_output(result.stdout, 'fixture', platform)
    assert 'PUBLIC_PLUGINS_REGISTERED_PRIVATE_INPUTS_ABSENT' in result.stdout


@pytest.mark.parametrize('inherited', [False, True])
def test_gate_refuses_missing_native_test_selection(tmp_path, monkeypatch, inherited):
    from tests.pm.activation_support import fake_store, isolated_checkout
    from pm.environments import (runtime_facts_path, activation_input_mtimes,
                                 activation_inputs_dir, record_activation_inputs)
    from pm.testenv import testenv_python
    home = tmp_path / 'home'
    (home / '.hermes').mkdir(parents=True)
    root = isolated_checkout(tmp_path)
    native = home / '.hermes/hermes-agent'
    root.rename(native)
    import hermes_cli
    source = Path(hermes_cli.__file__).parent.parent
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copyfile(source / name, native / name)
    store, _ = fake_store(tmp_path)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home / '.hermes'))
    monkeypatch.setenv('HERMES_RUNTIME_DIR', str(store))
    facts = runtime_facts_path(native)
    facts.parent.mkdir(parents=True, exist_ok=True)
    facts.write_text('{"packages": {}}\n')
    record_activation_inputs(activation_inputs_dir(native), activation_input_mtimes(native),
                             native, test_environment=True)
    assert testenv_python(native) is None
    entry = tmp_path / 'candidate/tests/native-memory/run.sh'
    entry.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).with_name('run.sh'), entry)
    env = os.environ.copy()
    env.pop('__HERMES_TEST_PYTHON', None)
    env.pop('__HERMES_ACTIVATED', None)
    if inherited:
        env['__HERMES_TEST_PYTHON'] = '/usr/bin/python3'
    result = subprocess.run(['/usr/bin/bash', str(entry)], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stderr
    assert 'native test environment selection is missing' in result.stderr, result.stderr
    assert 'unbound variable' not in result.stderr
    assert result.stdout == ''


def test_tui_gateway_imports_without_nonpublic_sibling(tmp_path):
    from native_sandbox import base_command
    native = tmp_path / 'native'
    package = native / 'tui_gateway'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'launch_profile_policy.py').write_text('PUBLIC_VALUE = 42\n')
    canary = native / 'nonpublic_canary'
    canary.mkdir()
    (canary / '__init__.py').write_text('raise RuntimeError("NONPUBLIC_CANARY")\n')
    probe = (
        'import importlib.util, sys; '
        f'sys.path.insert(0, {str(native)!r}); '
        'assert importlib.util.find_spec("nonpublic_canary") is None; '
        'from tui_gateway.launch_profile_policy import PUBLIC_VALUE; '
        'assert PUBLIC_VALUE == 42; '
        'print("PUBLIC_IMPORT_OK_NONPUBLIC_ABSENT")'
    )
    result = subprocess.run(
        base_command(native, python='/usr/bin/python3')
        + ['--chdir', '/tmp', '/usr/bin/python3', '-I', '-B', '-c', probe],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'PUBLIC_IMPORT_OK_NONPUBLIC_ABSENT\n'
