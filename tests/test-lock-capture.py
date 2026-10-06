#!/usr/bin/env python3
"""Memory-only cross-window image handoff and capture preflight failure paths."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


def main():
    # Unlike transfer fixtures below, lint reads the unchanged production panel.
    lint = subprocess.run([str(Path(os.environ.get('QML_TOOLS_DIR', '/usr/lib/qt6/bin')) / 'qmllint'), '--ignore-settings', '-W', '0',
                           '--json', '-', '-I', os.environ.get('QML_IMPORT_DIR', '/usr/lib/qt6/qml'), '-I', str(ROOT / 'shell'),
                           str(ROOT / 'shell/LockCapturePanel.qml')],
                          text=True, capture_output=True, timeout=30)
    report = json.loads(lint.stdout)
    assert report['files'], lint.stdout + lint.stderr
    for entry in report['files']:
        for warning in entry['warnings']:
            assert (warning['id'], warning['message']) == (
                'uncreatable-type', 'Type PanelWindow is not creatable.'), warning
    assert lint.returncode in (0, 255), lint.stderr
    print('PASS: strict production capture-panel lint (only documented QS PanelWindow metadata gap)')
    for mode in ('normal', 'missing', 'broken', 'privacy', 'production'):
        with tempfile.TemporaryDirectory(prefix='lc-', dir=CACHE) as work:
            profile = Path(work)
            for name in ('r', 'cache', 'config', 'state', 'data', 'tmp'):
                (profile / name).mkdir(mode=0o700)
            shutil.copytree(ROOT / 'shell', profile / 'qml')
            fixture = 'LockCapturePrivacyTest.qml' if mode == 'privacy' else 'LockCaptureTest.qml'
            if mode == 'production':
                fixture = 'LockCaptureProductionLoadTest.qml'
            shutil.copyfile(ROOT / 'tests/fixtures/lock' / fixture, profile / 'qml/check.qml')
            helper = profile / 'qml/LockCapturePanel.qml'
            if mode in ('normal', 'privacy'):
                shutil.copyfile(ROOT / 'tests/fixtures/lock/LockCapturePanel.qml', helper)
            elif mode == 'missing':
                helper.unlink(missing_ok=True)
            elif mode == 'broken':
                helper.write_text('This is a deliberately broken optional capture helper\n')
            if mode == 'privacy':
                shutil.copyfile(ROOT / 'tests/fixtures/lock/LockPam.qml', profile / 'qml/LockPam.qml')
                # Keep production visual components, but give their child helpers
                # synthetic, isolated inputs with no user wallpaper or IPC reads.
                (profile / 'qml/helpers/lock-environment.py').write_text(
                    'import sys\nsys.stdin.buffer.read()\n')
                (profile / 'qml/helpers/wallpaper.py').write_text(
                    'import json\nprint(json.dumps(dict(state="unavailable", texture="")))\n')
            module = profile / 'qml/qmldir'
            if 'LockCapture 1.0' not in module.read_text():
                with module.open('a') as output:
                    output.write('LockCapture 1.0 LockCapture.qml\n')
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(profile / 'r'),
                       XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                       XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                       TMPDIR=str(profile / 'tmp'), PYTHONDONTWRITEBYTECODE='1', NIRI_SOCKET='',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'),
                       LOCK_CAPTURE_FIXTURE=mode,
                       EMAKI_SHELL_SHADER_DIR=str(profile / 'missing-shaders'))
            for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'QT_SCALE_FACTOR'):
                env.pop(name, None)
            result = subprocess.run(['qs', '-p', str(profile / 'qml/check.qml'), '--no-color'],
                                    env=env, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=15)
            assert result.returncode == 0 and 'LOCK_CAPTURE_COMPLETE' in result.stdout, result.stdout
            assert 'LOCK_CAPTURE_FAILED' not in result.stdout, result.stdout
            assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
            # Captured pixels stay solely in Qt memory. Even this synthetic fixture
            # never saves screenshots or prints the image-provider capability URL.
            assert 'itemgrabber:' not in result.stdout, 'Capture URL leaked into output'
            assert not list(profile.rglob('*.png')), 'Fixture wrote captured pixels to disk'
            assert not list(profile.rglob('*.jpg')), 'Fixture wrote captured pixels to disk'
            for line in result.stdout.splitlines():
                if 'LOCK_CAPTURE_PASS ' in line:
                    print(line[line.index('LOCK_CAPTURE_PASS '):])
    print('PASS: memory-only CPU handoff, capture deadline, privacy cleanup and optional helper fallback')


if __name__ == '__main__':
    main()
