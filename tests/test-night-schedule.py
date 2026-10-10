#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise custom night light hours with the real service and a guarded fake child."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from runtime_fixture import runtime_path
import reaper

reaper.guard()

ROOT = Path(__file__).resolve().parents[1]


def run_case(case, mutation=None, expected=None):
    with tempfile.TemporaryDirectory(prefix='emaki-night-schedule-') as temporary:
        base = Path(temporary)
        qml = base / 'shell'
        shutil.copytree(ROOT / 'shell', qml)
        subprocess.run(['python3', str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
        shutil.copyfile(ROOT / 'tests/fixtures/night-schedule.qml', qml / 'shell.qml')
        if mutation:
            path = qml / 'NightLight.qml'
            before, after = mutation
            source = path.read_text()
            assert before in source
            path.write_text(source.replace(before, after, 1))
        for name in ('home', 'config', 'state/emaki', 'cache', 'runtime', 'data'):
            (base / name).mkdir(parents=True, mode=0o700)
        statefile = base / 'state/emaki/night-light.json'
        events = base / 'events'
        fake = base / 'sun'
        fake.write_text('''#!/usr/bin/env python3
import json, os, time, sys
from pathlib import Path
root = Path(os.environ['NIGHT_FIXTURE'])
with (root / 'events').open('a') as stream:
    stream.write(json.dumps([os.getpid(), sys.argv[1:]]) + '\\n')
if (root / 'fail').exists() and (root / 'fail').read_text():
    sys.exit(8)
while True:
    time.sleep(.05)
''')
        fake.chmod(0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_RUNTIME_DIR=str(runtime_path(base)), XDG_DATA_HOME=str(base / 'data'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_WLSUNSET=str(fake), NIGHT_FIXTURE=str(base),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(name, None)
        env['NIGHT_CASE'] = case
        if case == 'savefail':
            statefile.mkdir()
        elif case == 'foreign':
            statefile.write_text('{"version":99,"on":true,"warmth":80,"schedule":"manual"}')
        elif case in ('restore', 'same-day'):
            statefile.write_text(json.dumps(dict(version=1, on=True, warmth=0, schedule='manual',
                                                startTime='01:00' if case == 'same-day' else '21:30',
                                                endTime='06:00' if case == 'same-day' else '07:00')))
        result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=30)
        output = result.stdout + result.stderr
        for bad in ('TypeError:', 'ReferenceError:', 'Failed to load configuration'):
            assert bad not in output, output
        if mutation and expected == 'future state preserved':
            # FileView reload is asynchronous: inspect durable bytes after exit,
            # using the same preservation oracle as the unmodified foreign case.
            assert json.loads(statefile.read_text())['version'] == 1, output
        elif mutation:
            assert 'NIGHT_FAIL ' + expected in output and 'NIGHT_RESULT 0' not in output, output
        else:
            assert result.returncode == 0 and 'NIGHT_RESULT 0' in output, output + (events.read_text() if events.exists() else '')
        if case == 'foreign' and not mutation:
            assert statefile.read_text() == '{"version":99,"on":true,"warmth":80,"schedule":"manual"}', 'future bytes unchanged'
        # Every child must disappear after the fixture stops its guarded process.
        until = time.monotonic() + 5
        def children_alive():
            rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
            for pid, _ in rows:
                try:
                    if reaper._stat(pid)[1] != "Z":
                        return True
                except (OSError, ValueError, IndexError):
                    pass
            return False
        while children_alive() and time.monotonic() < until:
            time.sleep(.05)
        assert not children_alive(), 'guard stopped every child'


for case in ('normal', 'restore', 'same-day', 'foreign', 'savefail'):
    run_case(case)
    print('PASS: night schedule ' + case)
for name, case, mutation, expected in [
    ('temperature boundary', 'normal', ('Math.min(6499, temperature)', 'temperature'), 'zero warmth valid temperatures'),
    ('failure visibility', 'normal', ('night.failed = true;', 'night.failed = false;'), 'unexpected child exit visible'),
    ('future save guard', 'foreign', ('if (night.foreign)', 'if (false)'), 'future state preserved'),
]:
    run_case(case, mutation, expected)
    print('PASS: rejected ' + name)
