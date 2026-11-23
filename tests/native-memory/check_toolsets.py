"""Check the native tools-list handler in a read-only subprocess for each home.

See native_list.py for the gate's residual plugin-override gap.
"""
import os
import re
import subprocess
from pathlib import Path
import sys

from native_sandbox import listing_command

REQUIRED = ('session_search', 'todo', 'cronjob')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def check_output(output, profile, platform):
    text = ANSI.sub('', output)
    if re.search(rf"Unknown platform '{re.escape(platform)}'\. Valid:", text):
        raise ValueError(f'{profile}/{platform}: required platform is unsupported; restore native platform support')
    if f'Built-in toolsets ({platform}):' not in text:
        raise ValueError(f'{profile}/{platform}: native toolset listing is missing; inspect the native handler')
    rows = re.findall(r'^\s*([✓✗])\s+(enabled|disabled)\s+(\S+)(?:\s|$)', text, re.MULTILINE)
    for toolset in REQUIRED:
        matches = [(mark, status) for mark, status, name in rows if name == toolset]
        if matches != [('✓', 'enabled')]:
            raise ValueError(
                f'{profile}/{platform}/{toolset}: expected one enabled toolset row, got {matches}. '
                'Enable this toolset and remove conflicting disabled_toolsets or plugin overrides.')
    print(f'PASS {profile}/{platform}: {", ".join(REQUIRED)} enabled')


def main(evidence):
    home = Path.home() / '.hermes'
    native = home / 'hermes-agent'
    helper = Path(__file__).with_name('native_list.py').resolve()
    for profile, subdir in [('H', ''), ('S', 'profiles/sqecea'), ('Y', 'profiles/yqecea')]:
        env = os.environ.copy()
        env.pop('HERMES_HOME', None)
        env.pop('HERMES_PROFILE', None)
        env.update(HERMES_HOME=str(home / subdir), PYTHONPATH=str(native),
                   PYTHONDONTWRITEBYTECODE='1')
        for platform in ('cli', 'telegram'):
            try:
                result = subprocess.run(
                    [sys.executable, '-B', str(helper.with_name('native_sandbox.py')),
                     'list', str(native), str(home / subdir), profile, platform],
                    env=env, cwd=native, capture_output=True, text=True, timeout=45)
                # Only retain the listing. Native startup diagnostics may include private endpoints.
                output = ANSI.sub('', result.stdout)
                lines = [line for line in output.splitlines()
                         if re.match(r'^\s*[✓✗]\s+(enabled|disabled)\s+', line)
                         or line.startswith('Built-in toolsets (')
                         or re.search(r"Unknown platform '.*'\. Valid:", line)]
                (evidence / f'{profile}-{platform}.txt').write_text('\n'.join(lines) + '\n')
                if result.returncode != 0:
                    failure = next((line for line in result.stderr.splitlines()
                                    if line.startswith('Native config read FAIL:')), None)
                    if failure:
                        raise ValueError(failure)
                    raise ValueError(
                        f'{profile}/{platform}: native listing exited {result.returncode}; '
                        'toolsets unverified. Check bwrap and native handler prerequisites.')
                check_output('\n'.join(lines), profile, platform)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                raise SystemExit(f'Native presence gate FAIL: {error}')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
