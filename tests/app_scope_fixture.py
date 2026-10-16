# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A fake user manager: no test launch can reach the host systemd or niri."""
import json
import os
from pathlib import Path
import re
import shutil


def install(root, env):
    real_niri = shutil.which('niri', path=env['PATH'])
    folder = root / 'scope-bin'
    folder.mkdir()
    runner = folder / 'systemd-run'
    runner.write_text('''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['EMAKI_SCOPE_FIXTURE'])
args = sys.argv[1:]
with (root / 'scopes.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
mode = (root / 'scope-mode').read_text() if (root / 'scope-mode').exists() else 'ok'
if mode == 'fail':
    sys.exit(1)
command = args[args.index('--') + 1:]
if mode == 'late':
    # A setup timeout must revoke the ticket, even if a child outlives systemd-run.
    if os.fork():
        time.sleep(5)
        sys.exit(1)
    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
    time.sleep(1.3)
os.environ['EMAKI_TEST_SCOPE'] = next(a[7:] for a in args if a.startswith('--unit='))
os.execvp(command[0], command)
''')
    runner.chmod(0o700)
    niri = folder / 'niri'
    niri.write_text('#!/usr/bin/env python3\nimport os, sys\n'
                    'args = sys.argv[1:]\n'
                    'if args[:1] == ["--version"] or args[:1] == ["validate"]:\n'
                    f'    os.execv({real_niri!r}, [{real_niri!r}, *args])\n'
                    'sys.exit("unexpected niri IPC in scope fixture")\n')
    niri.chmod(0o700)
    # URL tests stay inside their private desktop registry, never the host browser.
    opener = folder / 'xdg-open'
    opener.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio
with (Path(os.environ['EMAKI_SCOPE_FIXTURE']) / 'urls.jsonl').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
try:
    accepted = Gio.AppInfo.launch_default_for_uri(sys.argv[1], None)
except Exception:
    sys.exit(1)
sys.exit(0 if accepted else 1)
''')
    opener.chmod(0o700)
    env.update(PATH=str(folder) + os.pathsep + env['PATH'], EMAKI_SCOPE_FIXTURE=str(root))


def launches(root, expected):
    rows = [json.loads(line) for line in (root / 'scopes.jsonl').read_text().splitlines()]
    ids = []
    units = []
    for args in rows:
        assert args[:3] == ['--user', '--scope', '--slice=app.slice'], args
        assert '--collect' in args and '--expand-environment=no' in args, args
        for property in ('BindsTo', 'PartOf', 'After'):
            assert f'--property={property}=graphical-session.target' in args, args
        unit = next(a[7:] for a in args if a.startswith('--unit='))
        match = re.fullmatch(r'app-emaki-(.+)-[0-9a-f]{32}\.scope', unit)
        assert match and len(unit) <= 255, unit
        ident = re.sub(r'\\x([0-9a-f]{2})', lambda m: chr(int(m[1], 16)), match[1])
        ids.append(ident)
        units.append(unit)
        command = args[args.index('--') + 1:]
        assert len(command) == 4 and command[1] == '-B' and Path(command[2]).name == 'app_scope.py', command
    assert len(units) == len(set(units)), units
    assert set(ids) == set(expected), ids
    return ids
