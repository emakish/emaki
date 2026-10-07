#!/usr/bin/env python3
"""Offscreen lock geometry, CPU-image handoff, source revocation and fallback checks."""
from runtime_fixture import runtime_path
import os
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
    with tempfile.TemporaryDirectory(prefix='lg-', dir=CACHE) as work:
        profile = Path(work)
        for name in ('r', 'cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        shutil.copytree(ROOT / 'shell', profile / 'qml')
        # Also works before qmldir registers the new type.
        module = profile / 'qml/qmldir'
        if 'LockGlass 1.0' not in module.read_text():
            with module.open('a') as output:
                output.write('LockGlass 1.0 LockGlass.qml\n')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   TMPDIR=str(profile / 'tmp'), PYTHONDONTWRITEBYTECODE='1', NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'),
                   EMAKI_SHELL_SHADER_DIR=str(profile / 'missing-shaders'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        for fixture, marker in [('LockGlassTest.qml', 'LOCK_GLASS_COMPLETE'),
                                ('LockCaptureProbe.qml', 'CAPTURE_PROBE_COMPLETE')]:
            target = profile / 'qml' / fixture
            shutil.copyfile(ROOT / 'tests/fixtures' / fixture, target)
            try:
                result = subprocess.run(['qs', '-p', str(target), '--no-color'], env=env,
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=15, check=True)
            except subprocess.TimeoutExpired as error:
                raise AssertionError((error.stdout or b'').decode(errors='replace')) from error
            assert marker in result.stdout, result.stdout
            assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
            if fixture == 'LockCaptureProbe.qml':
                assert 'must both be children of the same window' in result.stdout, result.stdout
                print('PASS: cross-window frozen ShaderEffectSource is explicitly rejected by Qt')
            else:
                print('\n'.join(line[line.index('LOCK_GLASS_PASS '):] for line in result.stdout.splitlines()
                                if 'LOCK_GLASS_PASS ' in line))
    print('PASS: lock glass offscreen; synthetic CPU frame only, no live compositor or external bus')


if __name__ == '__main__':
    main()
