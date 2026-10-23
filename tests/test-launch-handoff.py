#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen launch focus ordering and real launcher/dock frame captures."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from runtime_fixture import short_runtime
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='emaki-handoff-') as temporary, \
        short_runtime() as runtime:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    # A standalone run must not depend on generated files left by make paths.
    # Render the real helper dependencies only inside this private fixture.
    (base / 'scripts').mkdir()
    shutil.copyfile(ROOT / 'scripts/emaki_i18n.py', base / 'scripts/emaki_i18n.py')
    subprocess.run([sys.executable, str(ROOT / 'scripts/render-paths'),
                    '--source', str(base)], check=True)
    revision = os.environ.get('EMAKI_LAUNCH_REF')
    if os.environ.get('EMAKI_LAUNCH_BASELINE') == '1':
        revision = '78b918a'
    if revision:
        for name in ('LauncherBody.qml', 'Dock.qml', 'RecentFiles.qml'):
            result = subprocess.run(['git', 'show', revision + ':shell/' + name], cwd=ROOT,
                                    capture_output=True, check=True)
            (base / 'shell' / name).write_bytes(result.stdout)
    shutil.copyfile(ROOT / 'tests/fixtures/LaunchHandoffTest.qml', base / 'shell/handoff.qml')
    # Keep production AppCatalog and its typed launch method. Only its isolated
    # child helper is gated, so activation cannot reach the host session or apps.
    scope = base / 'shell/helpers/app_scope.py'
    source = scope.read_text().split("if __name__ == '__main__':", 1)[0]
    scope.write_text(source + "if __name__ == '__main__':\n"
        '    import os, time\n    from pathlib import Path\n'
        '    gate = Path(os.environ["EMAKI_LAUNCH_GATE"])\n'
        '    while not gate.exists(): time.sleep(0.01)\n'
        '    code = int(gate.read_text())\n    gate.unlink()\n    raise SystemExit(code)\n')
    # Replace only external opens; list/frequency helpers retain their real protocol.
    gate = """
def fixture_open():
    import time
    gate = Path(os.environ['EMAKI_OPEN_GATE'])
    while not gate.exists(): time.sleep(.01)
    state = gate.read_text()
    gate.unlink()
    return dict(state=state)
"""
    helper = base / 'shell/helpers/launcher-tools.py'
    source = helper.read_text().split("if __name__=='__main__':", 1)[0]
    helper.write_text(source + gate + """
original_handle = handle
def handle(request):
    return fixture_open() if request.get('op') == 'web' else original_handle(request)
main()
""")
    helper = base / 'shell/helpers/recent-files.py'
    source = helper.read_text().split("if __name__ == '__main__':", 1)[0]
    helper.write_text(source + gate + """
def read_recent():
    return dict(state='ready', entries=[dict(path='/fixture.txt', name='fixture.txt', modified=1)], limited=False)
def open_file(path):
    return fixture_open()
main()
""")
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    apps = base / 'data/applications'
    apps.mkdir()
    (apps / 'fixture.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Fixture\nExec=true\nTerminal=false\n')
    env = dict(os.environ, EMAKI_LAUNCH_GATE=str(base / 'launch-gate'), EMAKI_OPEN_GATE=str(base / 'open-gate'), HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=runtime,
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    shots = env.get('EMAKI_LAUNCH_SHOTS')
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['qs', '-p', str(base / 'shell/handoff.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert 'Failed to start IPC server' not in output, output
    assert result.returncode == 0 and 'LAUNCH_HANDOFF_RESULT 4 0' in output, output
    print(output)
    if env.get('EMAKI_LAUNCH_EVIDENCE_ONLY') == '1':
        print('CAPTURED: baseline launcher/dock frames; regression assertions disabled for comparison')
    else:
        print('PASS: app, web and file launch handoff; busy app feedback; late replies preserve reopened launcher; failures remain visible')
