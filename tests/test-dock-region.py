#!/usr/bin/env python3
"""Real dock drag-release/return bounds in isolated software Qt; no session/IPC."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import shlex
import re
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
(ROOT / '.cache/evidence').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dock-region-', dir=ROOT / '.cache/evidence') as temporary:
    profile = Path(temporary)
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    fixture = (ROOT / 'tests/fixtures/DockRegionTest.qml').read_text()
    # Execute the production layer binding with the real policy and Niri state below.
    source = (ROOT / 'shell/Surfaces.qml').read_text()
    dock_source = source.split('id: dockWindow', 1)[1].split('// What the dock glass', 1)[0]
    layer = re.search(r'WlrLayershell.layer: (.+)', dock_source).group(1)
    fixture = fixture.replace('property int testedDockLayer: 0',
                              'property int testedDockLayer: ' + layer.replace('surfaces.controller', 'scene'))
    (profile / 'qml/shell.qml').write_text(fixture)
    plugin = profile / 'qml/InputMaskProbe'
    plugin.mkdir()
    (plugin / 'qmldir').write_text('module InputMaskProbe\nplugin inputmask\n')
    source = ROOT / 'tests/input-mask-probe.cpp'
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Quick', 'Qt6Qml'], text=True))
    subprocess.run(['/usr/lib/qt6/moc', *[flag for flag in flags if flag.startswith('-I')],
                    str(source), '-o', str(plugin / 'input-mask-probe.moc')], check=True)
    subprocess.run(['c++', '-shared', '-fPIC', '-std=c++17', '-I' + str(plugin), str(source),
                    '-o', str(plugin / 'libinputmask.so'), *flags], check=True)
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
    for scale in ('1', '1.25', '1.5', '2'):
        env['QT_SCALE_FACTOR'] = scale
        # Each run starts with the default hidden dock, not the preceding fixture's saved state.
        shutil.rmtree(profile / 'state')
        (profile / 'state').mkdir(mode=0o700)
        result = subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=12)
        log = result.stdout + result.stderr
        assert result.returncode == 0 and 'DOCK_REGION_OK' in log and 'DOCK_LAYER_OK' in log, log
        assert 'Failed to start IPC server' not in log, log
        # The offscreen backend retains QWindow.mask for the probe but cannot
        # install an operating-system input region. Compositor routing is a live check.
        unexpected = [line for line in log.splitlines()
                      if any(word in line for word in ('WARN', 'ERROR', 'Error:'))
                      and line.strip() != 'WARN: This plugin does not support setting window masks']
        assert not unexpected, '\n'.join(unexpected)
        print('scale=' + scale, next(line for line in log.splitlines() if 'DOCK_REGION_OK' in line))
print('PASS: native dock input mask at four scales; lifted app return bounds; menu bounds; Regular reach')
