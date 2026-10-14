#!/usr/bin/env python3
"""Synthetic native URI drag into the real Dock, with a capture-only launcher."""
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import shlex
import sys
import json
from app_scope_fixture import install
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
(ROOT / '.cache/evidence').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dock-drop-', dir=ROOT / '.cache/evidence') as temporary:
    profile = Path(temporary)
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    fixture = (ROOT / 'tests/fixtures/DockDropTest.qml').read_text()
    (profile / 'qml/shell.qml').write_text(fixture)
    plugin = profile / 'qml/DockDropProbe'
    plugin.mkdir()
    (plugin / 'qmldir').write_text('module DockDropProbe\nplugin dockdrop\n')
    source = ROOT / 'tests/dock-drop-probe.cpp'
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Quick', 'Qt6Qml'], text=True))
    subprocess.run(['/usr/lib/qt6/moc', *[flag for flag in flags if flag.startswith('-I')],
                    str(source), '-o', str(plugin / 'dock-drop-probe.moc')], check=True)
    subprocess.run(['c++', '-shared', '-fPIC', '-std=c++17', '-I' + str(plugin), str(source),
                    '-o', str(plugin / 'libdockdrop.so'), *flags], check=True)
    apps = profile / 'data/applications'
    apps.mkdir()
    (apps / 'fixture-editor.desktop').write_text('[Desktop Entry]\nType=Application\nName=Editor\nExec=/usr/bin/true\nTerminal=false\n')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_SCALE_FACTOR='1', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_TEST_SYSTEM='0', EMAKI_TEST_MPRIS='0',
               EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', NIRI_SOCKET='',
               XDG_RUNTIME_DIR=str(runtime_path(profile)), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               TMPDIR=str(profile / 'tmp'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    env['QML_IMPORT_PATH'] = str(profile / 'qml')
    fake = profile / 'capture-launch'
    fake.write_text('#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\nPath(' + repr(str(profile / 'argv.json')) + ').write_text(json.dumps(sys.argv[1:]))\n')
    fake.chmod(0o700)
    env['EMAKI_GTK_LAUNCH'] = str(fake)
    install(profile, env)
    for scale in ('1',):
        env['QT_SCALE_FACTOR'] = scale
        # Each run starts with the default hidden dock, not the preceding fixture's saved state.
        shutil.rmtree(profile / 'state')
        (profile / 'state').mkdir(mode=0o700)
        try:
            result = subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                    text=True, capture_output=True, timeout=12)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError((exc.stdout, exc.stderr)) from exc
        log = result.stdout + result.stderr
        assert result.returncode == 0 and 'DOCK_DROP_OK' in log, log
        assert json.loads((profile / 'argv.json').read_text()) == [
            '--', 'fixture-editor.desktop', 'file:///tmp/a%20b.txt',
            'https://example.invalid/$(touch%20BAD)?a=1&b=2'], ((profile / 'argv.json').read_text(), log)
        assert 'Error:' not in log, log
        errors = [line for line in log.splitlines() if 'ERROR' in line and 'Failed to start IPC server' not in line]
        assert not errors, errors
        if 'Failed to start IPC server' in log:
            print('NOTE: sandbox blocks unused IPC socket; native synthetic drag still executed')
        print('PASS: hidden edge URI drag, plate hold, drop release, literal launch argv')

    # Validate the terminal helper's URI handoff without starting a terminal or a bus.
    (apps / 'fixture-term.desktop').write_text('[Desktop Entry]\nType=Application\nName=Terminal fixture\nExec=true %U\nTerminal=true\n')
    code = """
import runpy, sys
helper = runpy.run_path(sys.argv[1])
calls = []
helper['app_scope'].gio_launch = lambda app, **kw: calls.append(kw) or True
uris = ['file:///tmp/a%20b.txt', 'https://example.invalid/$(touch%20BAD)?a=1&b=2']
assert helper['terminal']({'id':'fixture-term', 'terminal':sys.argv[2], 'uris':uris}) == {'state':'requested'}
assert calls[0]['uris'] == uris
assert '%U' in calls[0]['keyfile'].get_string('Desktop Entry', 'Exec')
assert not calls[0]['keyfile'].get_boolean('Desktop Entry', 'Terminal')
for invalid in ['not-a-list', [None], ['bad\\x00uri']]:
    try:
        helper['terminal']({'id':'fixture-term', 'terminal':sys.argv[2], 'uris':invalid})
    except helper['Refused']:
        pass
    else:
        raise AssertionError('invalid URI payload accepted')
"""
    subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'shell/helpers/launcher-tools.py'), str(fake)],
                   env=env, check=True, timeout=8)
    print('PASS: terminal helper URI handoff preserves field codes and rejects malformed payloads')
