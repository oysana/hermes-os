#!/usr/bin/env python3
"""Run an offline script using the interpreter/bootstrap selected by the Hermes launcher."""
import json
from pathlib import Path
import subprocess
import sys

script = Path(sys.argv[1]).resolve()
command = json.loads(subprocess.check_output(['hermes', '--print-runtime-command'], text=True))
code_index = command.index('-c') + 1
code = command[code_index]
end = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
if not code.endswith(end): raise SystemExit('Unknown selected runtime bootstrap; refusing fallback')
command[code_index] = code[:-len(end)] + "sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent)); runpy.run_path(sys.argv.pop(1), run_name='__main__')"
raise SystemExit(subprocess.call(command + [str(script)] + sys.argv[2:]))
