"""A deny-by-default filesystem for the native tool-list child."""
import os
from pathlib import Path
import sys

SOURCE_DIRS = ('hermes_agent', 'hermes_cli', 'agent', 'tools', 'gateway', 'providers', 'plugins', 'pm', 'acp_adapter', 'hermes_platform', 'tui_gateway', 'utils', 'tests/_fixtures')


def public_files(roots):
    files = []
    for root in roots:
        if root.is_symlink():
            raise ValueError('Selected public source directory/file symlink refused: ' + str(root))
        if root.is_file():
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    raise ValueError('Selected public source directory symlink refused: ' + str(Path(current) / name))
            for name in sorted(names):
                if name.endswith('.py') or name in ('plugin.yaml', 'platform.yaml'):
                    files.append(Path(current) / name)
    if len(files) > 12000:
        raise ValueError('Native public source projection exceeds its audited bound')
    for path in sorted(files):
        if path.resolve() != path.absolute() or not path.is_file():
            raise ValueError(f'Native public source is not a regular non-symlink path: {path}')
        yield path


def source_files(native):
    return public_files([*native.glob('*.py'), *(native / name for name in SOURCE_DIRS)])


def base_command(native, python=None, private_home=None):
    python = Path(python or sys.executable).absolute()
    command = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
               '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
               '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
               '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    # A non-system interpreter belongs to a selected immutable dependency prefix.
    if not str(python.resolve()).startswith('/usr/'):
        prefix = python.parent.parent.resolve()
        runtime = python.resolve().parent.parent
        for selected in dict.fromkeys((prefix, runtime)):
            if any(root.is_relative_to(selected) for root in (Path.home(), native.parent, private_home) if root is not None):
                raise ValueError('Dependency prefix would expose a private home: ' + str(selected))
            command += ['--ro-bind', str(selected), str(selected)]
    if not str(python).startswith('/usr/') and str(python.resolve()).startswith('/usr/'):
        command += ['--ro-bind', str(python.resolve(strict=True)), str(python)]
    for path in native.glob('*.py'):
        if path.is_symlink():
            raise ValueError('Selected public source directory/file symlink refused: ' + str(path))
        command += ['--ro-bind', str(path), str(path)]
    # Whole-directory binds keep bwrap under its ~9000-argument cap where per-file
    # binds overflowed on the grown source tree. A symlink inside resolves to an
    # unmounted path in the sandbox, so this exposes nothing the file walk hid.
    native_resolved = native.resolve()
    for name in SOURCE_DIRS:
        root = (native / name).resolve()
        if root.is_symlink() or not root.is_relative_to(native_resolved):
            raise ValueError('Selected public source directory symlink refused: ' + str(root))
        if root.is_dir():
            command += ['--ro-bind', str(root), str(root)]
    return command


def listing_command(native, helper, home, profile, platform, *, managed=None, python=None):
    python = Path(python or sys.executable).absolute()
    command = base_command(native, python, private_home=home)
    plugin_root = home / 'plugins'
    if plugin_root.is_symlink():
        raise ValueError('Selected public source directory symlink refused: ' + str(plugin_root))
    aliases = {}
    selected = []
    if plugin_root.is_dir():
        for path in sorted(plugin_root.iterdir()):
            if path.name.startswith('.') or path.name == '__pycache__':
                continue
            if path.is_symlink() and not path.name.endswith('.py'):
                try:
                    canonical = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    canonical = None
                public_roots = (native.parent / 'shared/plugins', native.parent / 'plugins')
                if (canonical is None or canonical.parent not in public_roots
                        or canonical.name != path.name or not canonical.is_dir()):
                    raise ValueError('Selected public source directory symlink refused: ' + str(path))
                aliases[path] = canonical
                selected.append(canonical)
            elif path.is_dir() or path.name.endswith('.py') or path.name in ('plugin.yaml', 'platform.yaml'):
                selected.append(path)
    for path in public_files(selected):
        command += ['--ro-bind', str(path), str(path)]
    for logical, canonical in aliases.items():
        command += ['--symlink', str(canonical), str(logical)]
    for path in (helper, home / 'config.yaml'):
        if path.resolve() != path.absolute():
            raise ValueError('Native listing input must be a regular non-symlink file: ' + str(path))
        command += ['--ro-bind', str(path), str(path)]
    default_managed = Path('/etc/hermes/config.yaml')
    if default_managed.is_file():
        if default_managed.resolve() != default_managed:
            raise ValueError('Managed config symlink refused')
        command += ['--ro-bind', str(default_managed), str(default_managed)]
    if managed:
        path = Path(managed) / 'config.yaml'
        if path.is_file():
            if path.resolve() != path.absolute():
                raise ValueError('Managed config symlink refused')
            command += ['--ro-bind', str(path), str(path)]
        command += ['--setenv', 'HERMES_MANAGED_DIR', str(managed)]
    for key, value in {'HOME': str(Path.home()), 'HERMES_HOME': str(home),
                       'PYTHONPATH': str(native), 'PYTHONDONTWRITEBYTECODE': '1',
                       'LANG': 'C.UTF-8', 'TZ': 'UTC', 'PATH': '/usr/bin:/bin'}.items():
        command += ['--setenv', key, value]
    command += ['--chdir', '/tmp', str(python), '-B', str(helper), profile, platform]
    return command


def pytest_command(native, tests, evidence, args=()):
    command = base_command(native)
    public = [tests / name for name in ('native_list.py', 'native_sandbox.py',
              'check_toolsets.py', 'test_recall.py', 'test_recall_tools_visible.py',
              'test_public_projection.py', 'run.sh',
              'fixtures/H-cli-disabled-session-search.txt')]
    public += [native / 'tests/conftest.py', native / 'tests/__init__.py',
               native / 'tests/home_io_guard.py', native / 'tests/git_safety.py',
               native / 'tests/pm/__init__.py', native / 'tests/pm/activation_support.py',
               native / 'scripts/_activation.sh', native / 'activate', native / 'activate.fish',
               native / 'activate.ps1', native / 'pyproject.toml', native / 'uv.lock', native / 'pm/lock.json',
               evidence / 'installed-visibility.json']
    public += list(public_files([native.parent / parent / name / filename
               for name, parent in [('bitwarden-session-bridge', 'plugins')]
               for filename in ('__init__.py', 'plugin.yaml')]))
    for path in public:
        command += ['--ro-bind', str(path), str(path)]
    scratch = evidence / 'pytest-temp'
    command += ['--tmpfs', str(scratch), '--tmpfs', str(evidence / 'pytest-home')]
    for key, value in {'HOME': str(evidence / 'pytest-home'), 'TMPDIR': str(scratch),
                       'PYTHONPATH': str(native) + ':' + str(tests),
                       'PYTHONDONTWRITEBYTECODE': '1', 'TZ': 'UTC',
                       'LANG': 'C.UTF-8', 'PYTHONHASHSEED': '0',
                       'PATH': '/usr/bin:/bin',
                       'HERMES_VISIBILITY_SNAPSHOT': str(evidence / 'installed-visibility.json')}.items():
        command += ['--setenv', key, value]
    return command + ['--chdir', str(scratch), sys.executable, '-B', '-m', 'pytest',
                      str(tests / 'test_recall.py'), str(tests / 'test_recall_tools_visible.py'), str(tests / 'test_public_projection.py'),
                      '-p', 'tests.conftest', '-p', 'no:cacheprovider', '--rootdir=' + str(tests), *args]


if __name__ == '__main__':
    import subprocess
    if sys.argv[1] == 'probe':
        native, home, helper = map(Path, sys.argv[2:5])
        if helper.name != 'probe.py' or helper.parent != home.parent:
            raise SystemExit('Fixture probe must be probe.py beside its generated profile directory')
        command = listing_command(native, helper, home, 'fixture', sys.argv[5] if len(sys.argv) > 5 else 'cli', managed=os.environ.get('HERMES_MANAGED_DIR'))
    elif sys.argv[1] == 'list':
        native, home = map(Path, sys.argv[2:4])
        command = listing_command(native, Path(__file__).with_name('native_list.py'),
                                  home, *sys.argv[4:6], managed=os.environ.get('HERMES_MANAGED_DIR'))
    else:
        native, tests, evidence = map(Path, sys.argv[1:4])
        command = pytest_command(native, tests, evidence, sys.argv[4:])
    raise SystemExit(subprocess.run(command).returncode)
