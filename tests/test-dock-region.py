#!/usr/bin/env python3
"""Real dock drag-release/return bounds in isolated software Qt; no session/IPC."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
(ROOT / '.cache').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dock-region-', dir=ROOT / '.cache') as temporary:
    profile = Path(temporary)
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/DockRegionTest.qml', profile / 'qml/shell.qml')
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
    result = subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                            text=True, capture_output=True, timeout=10)
    log = result.stdout + result.stderr
    assert result.returncode == 0 and 'DOCK_REGION_OK' in log, log
    assert 'Failed to start IPC server' not in log, log
    unexpected = [line for line in log.splitlines() if any(word in line for word in ('WARN', 'ERROR', 'Error:'))]
    assert not unexpected, '\n'.join(unexpected)
    print(next(line for line in log.splitlines() if 'DOCK_REGION_OK' in line))
print('PASS: lifted running app returns before backdrop reclamation; menu reuses bounds; Regular reach')
