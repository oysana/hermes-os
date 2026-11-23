"""Run the native list handler for an explicit existing profile home.

Premarking home initialization skips mkdir/chmod/SOUL scaffolding. The native
loader still owns config merging, expansion and plugin discovery. The caller
uses a deny-by-default bwrap namespace with exact public inputs and no network; unavailable isolation
fails the gate. Full CLI startup can write logs, bootstrap or resume gateways;
its source audit is in .artifacts/native-memory/side-effects-audit.md.

Residual gap: a plugin can override one tool inside an enabled toolset. This gate
checks deferral policy and toolset enablement, not the final individual schemas.
"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from hermes_cli import config
from hermes_cli.env_loader import load_hermes_dotenv

# Listing existing homes needs no scaffold initialization (mkdir/chmod/SOUL).
# Preserve the native loader, managed precedence, expansion and plugin discovery.
# Private credential, session and job files are not mounted by the caller.
home = Path(os.environ['HERMES_HOME'])
if not (home / 'config.yaml').is_file():
    raise SystemExit('Native listing requires an existing profile config.yaml')
config._HERMES_HOME_ENSURED.update((str(home), str(home.resolve())))
# Match CLI environment expansion through its native loader, never our own reads.
load_hermes_dotenv(project_env=Path(config.__file__).resolve().parents[1] / '.env')
profile, platform = sys.argv[1:]
try:
    loaded = config.load_config()
except Exception:
    raise SystemExit(f'Native config read FAIL: {profile}: native loader rejected config.yaml; '
                     'repair the profile config before rerunning the gate.') from None
if isinstance(loaded, config.FailedConfigRead):
    raise SystemExit(f'Native config read FAIL: {profile}: native loader used fallback settings; '
                     'repair the profile config before rerunning the gate.')
from tools.tool_search import ToolSearchConfig, is_deferrable_tool_name
import json
raw = (loaded.get('tools') or {}).get('tool_search')
search = ToolSearchConfig.from_raw(raw)
if search.enabled != 'off':
    defer = raw.get('defer') if isinstance(raw, dict) else None
    if '${' in json.dumps(defer):
        raise SystemExit('Native deferral FAIL: unresolved environment reference')
    for tool in ('session_search', 'todo_list', 'cronjob_manage'):
        if is_deferrable_tool_name(tool, search.effective_defer_tools):
            raise SystemExit(f'Native deferral FAIL: {profile}: {tool} is deferrable')
from hermes_cli.tools_config_mcp import tools_disable_enable_command

tools_disable_enable_command(SimpleNamespace(tools_action='list', platform=platform))
