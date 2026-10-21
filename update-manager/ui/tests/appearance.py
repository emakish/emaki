#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installed manager's own qs instance reads Appearance from the durable store.

The manager runs as `qs -p <manager ui>/shell.qml`, so Quickshell.shellPath() points into the
manager's UI directory, which carries no shell helpers. The test lays out the install the way the
packages do (the shell under the data directory, the manager UI in its own directory without
helpers), writes theme and accent with the real core, starts the real shell.qml and requires the
palette to follow the store at start and after a live write.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tests'))
import reaper  # noqa: E402

reaper.guard()
UI = ROOT / 'update-manager/ui'
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
assert BINARY is not None and BINARY.is_file(), 'Build the emaki binary before this test'
assert (ROOT / 'shell/Platform.qml').is_file(), 'Run make render before this test'
QS = shutil.which('qs')
assert QS, 'qs is not on PATH'

PROBE = '''
    Timer {
        property string previous: ""
        interval: 40
        running: true
        repeat: true
        onTriggered: {
            if (!Shell.SettingsBridge.values.length)
                return;
            const snapshot = JSON.stringify({
                theme: Shell.Appearance.theme,
                accent: Shell.Appearance.accent,
                dark: Shell.ShellPalette.dark,
                color: Shell.ShellPalette.accent.toString()
            });
            if (snapshot !== previous) {
                previous = snapshot;
                console.log("MANAGER_APPEARANCE " + snapshot);
            }
        }
    }
}
'''


def accent_base(name):
    palette = (ROOT / 'shell/ShellPalette.qml').read_text()
    # The generator lays the list out over several lines, as qmlformat prints it.
    listing = re.search(r'readonly property var accents: (\[\n.*?\n    \])\n', palette, re.S)[1]
    # Accent names are translatable (0.11): read each qsTranslate call as its English source.
    listing = re.sub(r'qsTranslate\("ShellPalette", ("(?:[^"\\]|\\.)*")\)', r'\1', listing)
    accents = json.loads(listing)
    return next(entry['base'] for entry in accents if entry['id'] == name)


with tempfile.TemporaryDirectory(prefix='manager-appearance-') as temporary:
    base = Path(temporary)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'bin'):
        (base / name).mkdir(mode=0o700)
    data = base / 'share/emaki'
    shutil.copytree(ROOT / 'shell', data / 'shell', ignore=shutil.ignore_patterns('__pycache__'))
    platform = data / 'shell/Platform.qml'
    text = platform.read_text()
    text = re.sub(r'(dataDir: )"[^"]*"', lambda m: m[1] + json.dumps(str(data)), text)
    # No backend here: the controllers' processes fail to start instead of reaching the host.
    text = re.sub(r'(libexecDir: )"[^"]*"', lambda m: m[1] + json.dumps(str(base / 'libexec')), text)
    platform.write_text(text)
    manager = base / 'share/emaki-update-manager/ui'
    manager.mkdir(parents=True)
    for path in list(UI.glob('*.qml')) + list(UI.glob('*.js')):
        (manager / path.name).write_text(path.read_text().replace('@EMAKI_DATADIR@', str(data)))
    assert not (manager / 'helpers').exists()
    shell = manager / 'shell.qml'
    source = shell.read_text().rstrip()
    assert source.endswith('}'), source[-200:]
    shell.write_text(source[:-1] + PROBE)

    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               EMAKI_SETTINGS_PROFILE=str(base), EMAKI_BIN=str(BINARY), EMAKI_PYTHON=sys.executable,
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               QS_DISABLE_CRASH_HANDLER='1', PYTHONDONTWRITEBYTECODE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'EMAKI_MANAGER_PAGE'):
        env.pop(key, None)

    def set_value(key, value):
        result = subprocess.run([str(BINARY), 'settings', 'set', key, value,
                                 '--profile-root', str(base), '--json'], env=env,
                                capture_output=True, text=True, check=True, timeout=20)
        reply = json.loads(result.stdout)
        assert reply['status'] == 'committed', reply

    def observe(process, path, theme, accent):
        expected = dict(theme=theme, accent=accent, dark=theme == 'dark', color=accent_base(accent))
        deadline = time.monotonic() + 20
        output = ''
        while time.monotonic() < deadline:
            output = path.read_text()
            assert process.poll() is None, output
            snapshots = [json.loads(line.split('MANAGER_APPEARANCE ', 1)[1])
                         for line in output.splitlines() if 'MANAGER_APPEARANCE {' in line]
            if snapshots and snapshots[-1] == expected:
                return output
            time.sleep(.05)
        raise AssertionError(f'the manager did not observe {expected}\n{output}')

    set_value('appearance.theme', 'dark')
    set_value('appearance.accent', 'teal')
    # Only the manager's own programs: no host emaki-programs or emaki-shell.
    process_env = dict(env, PATH=str(base / 'bin'))
    log_path = base / 'manager.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([QS, '-p', str(shell), '--no-color'], env=process_env,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            observe(process, log_path, 'dark', 'teal')
            set_value('appearance.accent', 'violet')
            set_value('appearance.theme', 'light')
            output = observe(process, log_path, 'light', 'violet')
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    for word in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign'):
        assert word not in output, output
print('PASS manager: its own qs instance reads dark + teal from the store and follows live writes')
