#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure real Programs controller/view startup with an isolated, offline JSON fixture.

The source revision changes only the production UI, not the fixture or measurement.
Times include process startup and software rendering; they are not hardware/network timings.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import interactions
import layout

ROOT = interactions.ROOT
UI = interactions.UI
sys.path.insert(0, str(ROOT / 'tests'))
import reaper
reaper.guard()

BACKEND = r'''#!PYTHON
import json, os, sys, time
from pathlib import Path

def trace(event, **values):
    with open(os.environ['TEST_TRACE'], 'a') as output:
        output.write(json.dumps(dict(event=event, epoch_ms=time.time_ns() // 1000000, **values)) + '\n')

def wait_for_frame():
    if os.environ['TEST_HOLD_AUXILIARY'] != '1':
        return
    deadline = time.monotonic() + float(os.environ['TEST_TIMEOUT']) / 1000
    while not Path(os.environ['TEST_FRAME']).is_file():
        if time.monotonic() >= deadline:
            raise AssertionError('Discovery waited for an auxiliary response')
        time.sleep(.01)

if sys.argv[1] == 'list':
    trace('inventory-start')
    time.sleep(float(os.environ['TEST_INVENTORY_DELAY']))
    wait_for_frame()
    print(json.dumps(dict(ok=True, programs=[])), flush=True)
    trace('inventory-reply')
    sys.exit(0)
rows = json.loads(Path(os.environ['TEST_CATALOG']).read_text())
for line in sys.stdin:
    request = json.loads(line)
    method, params = request['method'], request['params']
    trace('request', method=method, offset=params.get('offset', 0))
    value = dict(version=1, id=request['id'], type='result', ok=True)
    if method == 'catalog':
        offset, limit = params.get('offset', 0), params.get('limit', 500)
        if offset == 0:
            time.sleep(float(os.environ['TEST_CATALOG_DELAY']))
        value.update(programs=rows[offset:offset + limit], total=len(rows), warnings=[], shelf=[],
                     essentials=dict(minimal=False, programs=[]),
                     storefront=dict(version=1, featured=dict(title='Picked by Emaki',
                         note='A good place to start', programs=['org.kde.krita',
                         'md.obsidian.obsidian', 'net.supertuxkart.supertuxkart']),
                         rows=[], names={'org.gimp.gimp': 'GIMP'}))
    elif method == 'aur':
        time.sleep(float(os.environ['TEST_AUR_DELAY']))
        wait_for_frame()
        value.update(enabled=False, showWarning=True, accepted='', snapshots=True)
    else:
        raise AssertionError('Unexpected startup method: ' + method)
    print(json.dumps(value), flush=True)
    trace('reply', method=method, offset=params.get('offset', 0))
'''


def catalog(count):
    """Interaction-fixture identities plus the large-controller fixture's unique filler pattern."""
    seeds = [('org.kde.krita', 'krita', 'Krita', 'Graphics'),
             ('md.obsidian.Obsidian', 'obsidian', 'Obsidian', 'Office'),
             ('net.supertuxkart.SuperTuxKart', 'supertuxkart', 'SuperTuxKart', 'Game'),
             ('org.inkscape.Inkscape', 'inkscape', 'Inkscape', 'Graphics'),
             ('org.gimp.GIMP', 'gimp', 'GNU Image Manipulation Program', 'Graphics'),
             ('org.kde.dolphin', 'dolphin', 'Dolphin', 'System'),
             ('org.mozilla.firefox', 'firefox', 'Firefox', 'Network'),
             ('org.videolan.VLC', 'vlc', 'VLC', 'AudioVideo'),
             ('org.libreoffice.LibreOffice', 'libreoffice', 'LibreOffice', 'Office'),
             ('org.blender.Blender', 'blender', 'Blender', 'Graphics')]
    rows = []
    for index in range(count):
        identity, package, name, category = (seeds[index] if index < len(seeds) else
            (f'org.example.App{index}', f'app-{index}', f'App {index}', ['Utility', 'Office'][index % 2]))
        kind = 'flatpak' if index % 3 == 2 else 'arch'
        rows.append(dict(key=kind + ':' + package, id=identity, package=package, kind=kind,
                         name=name, summary='Offline catalog program', categories=[category],
                         installed=False, actions=['install'], icons=[], screenshots=[], version='1',
                         releaseDate='2026-10-01'))
    return rows


def run(source, arguments, iteration):
    with tempfile.TemporaryDirectory(prefix='emaki-startup-') as temporary:
        root = Path(temporary)
        target = root / 'ui'
        target.mkdir()
        for path in list(source.glob('*.qml')) + list(source.glob('*.js')):
            (target / path.name).write_text(path.read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        backend = root / 'engine'
        backend.write_text(BACKEND.replace('PYTHON', sys.executable, 1))
        backend.chmod(0o700)
        controller = target / 'ProgramsController.qml'
        original = controller.read_text()
        assert original.count('"emaki-programs"') >= 2
        controller.write_text(original.replace('"emaki-programs"', json.dumps(str(backend))))
        (target / 'UpdateController.qml').write_text(interactions.HEADER + interactions.UPDATE)
        entry = (UI / 'tests/StartupTimingTest.qml').read_text()
        (target / 'test.qml').write_text(entry.replace('".." as UI', '"." as UI').replace('@EMAKI_DATADIR@', str(ROOT)))
        (root / 'catalog.json').write_text(json.dumps(catalog(arguments.rows)))
        env = dict(os.environ, QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), EMAKI_LANGUAGE='en',
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QT_SCALE_FACTOR='1', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   TEST_CATALOG=str(root / 'catalog.json'), TEST_TRACE=str(root / 'trace.jsonl'),
                   TEST_FRAME=str(root / 'first-content.png'),
                   TEST_CATALOG_DELAY=str(arguments.catalog_delay), TEST_AUR_DELAY=str(arguments.aur_delay),
                   TEST_HOLD_AUXILIARY='1' if getattr(arguments, 'hold_auxiliary', False) else '0',
                   TEST_INVENTORY_DELAY=str(arguments.inventory_delay), TEST_TIMEOUT=str(arguments.timeout * 1000))
        for name in ('home', 'runtime', 'cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
            variable = 'HOME' if name == 'home' else 'XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')
            env[variable] = str(root / name)
        env['XDG_DATA_DIRS'] = str(root / 'data')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
            env.pop(name, None)
        env['TEST_START_MS'] = str(time.time_ns() // 1000000)
        completed = subprocess.run(['qs', '-p', str(target / 'test.qml'), '--no-color'], env=env,
                                   capture_output=True, text=True, timeout=arguments.timeout + 5)
        log = completed.stdout + completed.stderr
        assert completed.returncode == 0 and 'STARTUP_OK' in log, log
        # The existing Updates restore-date control warns in both revisions.
        checked = re.sub(r'[^\n]*QML ComboBox at @ManagerUpdates\.qml\[\d+:\d+\]: Binding loop detected for property "displayText":\n[^\n]*\n', '', log)
        assert not any(word in checked for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        measurements = [json.loads(line.split('STARTUP_EVENT ', 1)[1])
                        for line in log.splitlines() if 'STARTUP_EVENT ' in line]
        if getattr(arguments, 'hold_auxiliary', False):
            milestones = {event['event']: event['ms'] for event in measurements}
            assert milestones['first-content-frame'] <= min(milestones['inventory-received'], milestones['aur-received']), measurements
        trace = [json.loads(line) for line in (root / 'trace.jsonl').read_text().splitlines()]
        start = int(env['TEST_START_MS'])
        for item in trace:
            item['ms'] = item.pop('epoch_ms') - start
        result = dict(source=arguments.source or 'worktree', iteration=iteration, rows=arguments.rows,
                      hold_auxiliary=getattr(arguments, 'hold_auxiliary', False),
                      delays=dict(catalog=arguments.catalog_delay, aur=arguments.aur_delay,
                                  inventory=arguments.inventory_delay), measurements=measurements, backend=trace)
        if arguments.evidence:
            destination = Path(arguments.evidence)
            destination.mkdir(parents=True, exist_ok=True)
            label = (arguments.source or 'worktree').replace('/', '-') + f'-{arguments.rows}-{iteration}'
            (destination / (label + '.png')).write_bytes((root / 'first-content.png').read_bytes())
            (destination / (label + '.json')).write_text(json.dumps(result, indent=2) + '\n')
            (destination / (label + '.log')).write_text(log)
        print(json.dumps(result), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', help='production UI git revision or UI directory; fixture remains identical')
    parser.add_argument('--rows', type=int, default=5600)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--catalog-delay', type=float, default=0)
    parser.add_argument('--aur-delay', type=float, default=0)
    parser.add_argument('--inventory-delay', type=float, default=0)
    parser.add_argument('--hold-auxiliary', action='store_true',
                        help='withhold inventory and AUR replies until the first content frame is saved')
    parser.add_argument('--evidence', help='retain first frame, trace and runtime log in this directory')
    parser.add_argument('--timeout', type=int, default=120)
    arguments = parser.parse_args()
    assert arguments.rows >= 8 and arguments.runs > 0
    with tempfile.TemporaryDirectory(prefix='emaki-startup-source-') as temporary:
        if arguments.source and Path(arguments.source).is_dir():
            source = Path(arguments.source).resolve()
        else:
            source = layout.checkout(arguments.source, Path(temporary) / 'ui') if arguments.source else UI
        assert source is not None, 'source revision is unavailable'
        for iteration in range(arguments.runs):
            run(source, arguments, iteration + 1)


if __name__ == '__main__':
    main()
