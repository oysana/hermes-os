"""Config deferral policy and native listing contracts for H, S and Y.

See native_list.py for the gate's residual plugin-override gap.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from tools.tool_search import ToolSearchConfig, is_deferrable_tool_name

import check_toolsets
from check_toolsets import check_output

RECALL_TOOLS = ('session_search', 'todo_list', 'cronjob_manage')


def assert_eager(raw, profile, managed=()):
    assert not managed, (
        f'{profile}: managed config sets tools.tool_search at {managed}; '
        'this snapshot gate cannot resolve managed precedence. Review the managed setting.')
    config = ToolSearchConfig.from_raw(raw)
    if config.enabled == 'off':
        return
    defer = raw.get('defer') if isinstance(raw, dict) else None
    assert '${' not in json.dumps(defer), (
        f'{profile}: tools.tool_search.defer contains ${{...}}; use literal tool names '
        'before running this snapshot gate. Variables were not resolved.')
    for tool in RECALL_TOOLS:
        assert not is_deferrable_tool_name(tool, config.effective_defer_tools), (
            f'{profile}: {tool} is deferrable. Remove it from tools.tool_search.defer; '
            'a missing defer uses the native default. Set an explicit list without recall tools.')


@pytest.mark.parametrize('profile', ['H', 'S', 'Y'])
def test_installed_recall_tools_are_eager(profile):
    snapshot = json.loads(Path(os.environ['HERMES_VISIBILITY_SNAPSHOT']).read_text())
    assert_eager(snapshot['profiles'][profile], profile, snapshot['managed'])


@pytest.mark.parametrize('raw,message', [
    ({'enabled': 'on', 'defer': ['session_search']}, 'session_search is deferrable'),
    ({'enabled': 'auto', 'defer': ['cronjob_manage']}, 'cronjob_manage is deferrable'),
    ({'enabled': 'on', 'defer': ['${RECALL_TOOLS}']}, 'contains'),
])
def test_config_gate_rejects_regression(raw, message):
    with pytest.raises(AssertionError, match=message):
        assert_eager(raw, 'fixture')


def test_config_gate_rejects_managed_override():
    with pytest.raises(AssertionError, match='managed config sets tools.tool_search'):
        assert_eager({'enabled': 'off', 'defer': []}, 'fixture', ['/etc/hermes/config.yaml'])


def test_presence_gate_rejects_disabled_toolset():
    captured = Path(__file__).with_name('fixtures') / 'H-cli-disabled-session-search.txt'
    with pytest.raises(ValueError, match='H/cli/session_search:'):
        check_output(captured.read_text(), 'H', 'cli')


def test_presence_gate_rejects_unknown_platform():
    with pytest.raises(ValueError, match='required platform is unsupported'):
        check_output("Unknown platform 'telegram'. Valid: cli", 'H', 'telegram')


@pytest.mark.parametrize('optimized', [False, True])
def test_disabled_fixture_exits_nonzero_even_when_optimized(optimized):
    fixture = Path(__file__).with_name('fixtures') / 'H-cli-disabled-session-search.txt'
    env = os.environ.copy()
    env['PYTHONOPTIMIZE'] = '1' if optimized else '0'
    result = subprocess.run(
        [sys.executable, '-B', '-c',
         "from pathlib import Path; from check_toolsets import check_output; "
         "check_output(Path(__import__('sys').argv[1]).read_text(), 'H', 'cli')",
         str(fixture)], cwd=Path(__file__).parent, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'H/cli/session_search:' in result.stderr


def test_inherited_s_profile_cannot_hide_disabled_h(tmp_path, monkeypatch):
    # The process boundary returns the listing for the selected home. H fails;
    # accidentally following the inherited S home would return an enabled row.
    home = Path.home() / '.hermes'
    monkeypatch.setenv('HERMES_HOME', str(home / 'profiles/sqecea'))
    monkeypatch.setenv('HERMES_PROFILE', 'sqecea')
    disabled = (Path(__file__).with_name('fixtures') /
                'H-cli-disabled-session-search.txt').read_text()

    def native_listing(command, *, env, **kwargs):
        assert 'HERMES_PROFILE' not in env
        output = disabled if env['HERMES_HOME'] == str(home) else disabled.replace(
            '✗ disabled  session_search', '✓ enabled  session_search')
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(check_toolsets.subprocess, 'run', native_listing)
    with pytest.raises(SystemExit, match='H/cli/session_search:'):
        check_toolsets.main(tmp_path)


def test_every_profile_and_platform_has_an_explicit_home(tmp_path, monkeypatch):
    home = Path.home() / '.hermes'
    monkeypatch.setenv('HERMES_HOME', '/wrong/profiles/sqecea')
    monkeypatch.setenv('HERMES_PROFILE', 'sqecea')
    selected = []

    def native_listing(command, *, env, **kwargs):
        assert 'HERMES_PROFILE' not in env
        platform = command[-1]
        selected.append((env['HERMES_HOME'], platform))
        return SimpleNamespace(returncode=0, stdout=f'Built-in toolsets ({platform}):\n'
                               '  ✓ enabled session_search\n  ✓ enabled todo\n  ✓ enabled cronjob\n')

    monkeypatch.setattr(check_toolsets.subprocess, 'run', native_listing)
    check_toolsets.main(tmp_path)
    assert selected == [(str(home / subdir), platform)
                        for subdir in ('', 'profiles/sqecea', 'profiles/yqecea')
                        for platform in ('cli', 'telegram')]


@pytest.mark.parametrize('profile', ['H', 'S', 'Y'])
@pytest.mark.parametrize('extra', ['', 'max_turns: 5\n'], ids=['rejected', 'fallback'])
def test_native_listing_rejects_failed_config_read(tmp_path, profile, extra):
    home = tmp_path / profile
    home.mkdir()
    (home / 'config.yaml').write_text('agent: broken\n' + extra + 'tools:\n  tool_search:\n    defer: []\n')
    env = os.environ.copy()
    env.pop('HERMES_PROFILE', None)
    env['HERMES_HOME'] = str(home)
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('native_sandbox.py')),
         'list', str(Path(__import__('hermes_cli').__file__).parent.parent),
         str(home), profile, 'cli'],
        env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode != 0
    assert f'Native config read FAIL: {profile}:' in result.stderr
    reason = 'used fallback settings' if extra else 'rejected config.yaml'
    assert reason in result.stderr
    assert 'Built-in toolsets (' not in result.stdout


@pytest.mark.parametrize('raw', [
    {'enabled': 'off', 'defer': ['todo_list']},
    {'enabled': 'off'},
    {'enabled': 'off', 'defer': ['${RECALL_TOOLS}']},
    False,
])
def test_config_gate_accepts_disabled_tool_search(raw):
    assert_eager(raw, 'fixture')


def test_listing_projection_ignores_unrelated_home_trees(tmp_path):
    from native_sandbox import listing_command
    home = tmp_path / 'profile'
    (home / 'plugins').mkdir(parents=True)
    (home / 'config.yaml').write_text('tools: {}\n')
    (home / 'plugins/example.py').write_text('PUBLIC_PLUGIN = True\n')
    (home / 'tools').mkdir()
    (home / 'tools/unrelated-content.py').write_text('UNRELATED = True\n')
    (home / 'tools/unrelated.py').symlink_to(tmp_path / 'not-a-listing-input')
    command = listing_command(tmp_path / 'native-source', Path(__file__), home, 'H', 'cli')
    assert str(home / 'plugins/example.py') in command
    assert not any(str(home / 'tools') in arg for arg in command)


def test_normal_gate_private_canaries_are_inaccessible(tmp_path):
    home = tmp_path / 'profile'
    home.mkdir()
    (home / 'config.yaml').write_text('tools:\n  tool_search:\n    enabled: off\n')
    for name in ('.env', 'auth.json', 'sessions.db', 'jobs.json'):
        (home / name).write_text('OWNED_PRIVATE_CANARY\n')
    probe = tmp_path / 'probe.py'
    probe.write_text(
        "from pathlib import Path\nimport os\n"
        "home=Path(os.environ['HERMES_HOME'])\n"
        "assert (home/'config.yaml').read_text().startswith('tools:')\n"
        "for name in ('.env','auth.json','sessions.db','jobs.json'):\n"
        " try: (home/name).read_bytes()\n"
        " except FileNotFoundError: pass\n"
        " else: raise SystemExit('PRIVATE_CANARY_VISIBLE')\n"
        "print('CONFIG_PRESENT_PRIVATE_CANARIES_DENIED')\n")
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('native_sandbox.py')),
         'probe', str(Path(__import__('hermes_cli').__file__).parent.parent),
         str(home), str(probe)], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'CONFIG_PRESENT_PRIVATE_CANARIES_DENIED\n'


def test_listing_refuses_interpreter_prefix_that_exposes_profile(tmp_path):
    from native_sandbox import listing_command
    home = tmp_path / 'profile'
    (home / 'bin').mkdir(parents=True)
    (home / 'config.yaml').write_text('tools: {}\n')
    python = home / 'bin/python'
    python.write_text('synthetic interpreter path only')
    with pytest.raises(ValueError, match='Dependency prefix would expose a private home'):
        listing_command(tmp_path / 'native', Path(__file__), home, 'H', 'cli', python=python)


def test_selected_plugin_directory_symlink_refuses_instead_of_disappearing(tmp_path):
    from native_sandbox import listing_command
    home = tmp_path / 'profile'
    (home / 'plugins').mkdir(parents=True)
    (home / 'config.yaml').write_text('tools: {}\n')
    public = tmp_path / 'public'
    public.mkdir()
    (public / 'plugin.py').write_text('PUBLIC = True\n')
    (home / 'plugins/redirect').symlink_to(public, target_is_directory=True)
    with pytest.raises(ValueError, match='Selected public source directory symlink refused'):
        listing_command(tmp_path / 'native', Path(__file__), home, 'H', 'cli')
