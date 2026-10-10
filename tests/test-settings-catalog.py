#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Production QML catalog, JSON helper, and durable core in a private profile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
assert BINARY is not None, 'Build the emaki binary before this test'

with tempfile.TemporaryDirectory(prefix='catalog-') as temporary:
    base = Path(temporary)
    shell = base / 'shell'
    shell.mkdir()
    names = ('SettingsCatalog', 'SettingsBridge', 'PrivateJob', 'AppLaunch')
    for name in names:
        shutil.copyfile(ROOT / 'shell' / (name + '.qml'), shell / (name + '.qml'))
    (shell / 'qmldir').write_text(''.join(
        ('singleton ' if name in ('SettingsBridge', 'AppLaunch') else '')
        + name + ' 1.0 ' + name + '.qml\n' for name in names))
    shutil.copytree(ROOT / 'shell/helpers', shell / 'helpers')
    fixture = (ROOT / 'tests/fixtures/SettingsCatalogTest.qml').read_text()
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    (shell / 'catalog-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               EMAKI_SETTINGS_PROFILE=str(base), EMAKI_BIN=str(BINARY),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(shell / 'catalog-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=150)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'SETTINGS_CATALOG_RESULT 2 0' in output, output

    def read(action):
        result = subprocess.run([str(BINARY), 'settings', action, '--profile-root', str(base), '--json'],
                                env=env, capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout)

    settings = tomllib.loads((base / 'config/emaki/settings.toml').read_text())
    assert 'autohide' not in settings.get('bar', {}), settings
    rows = read('list')['settings']
    row = next(row for row in rows if row['key'] == 'bar.autohide')
    assert row['value'] is False and row['override_value'] is None, row
    history = read('history')['history']
    assert len(history) == 5 and len({entry['id'] for entry in history}) == 5, history
    assert sum(entry['kind'] == 'undo' for entry in history) == 1, history
    changes = [change for entry in history for change in entry['changes']]
    assert len(changes) == 5 and all(change['key'] == 'bar.autohide' for change in changes), changes
    journal = base / 'state/emaki/history'
    entries = [json.loads(path.read_text()) for path in journal.glob('*.json')]
    assert len(entries) == 5, entries
    ordered = sorted(entries, key=lambda entry: entry['sequence'])
    assert [(entry['changes'][0]['before'], entry['changes'][0]['after']) for entry in ordered] == [
        (None, True), (True, None), (None, True), (True, False), (False, None)], ordered
    assert not list(journal.glob('*pending*'))
    print('PASS: real catalog/core list, set, reset, undo, queued writes and duplicate reset')
