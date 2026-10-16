#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Two passive QML catalogs follow real durable writes and process restart."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
assert BINARY is not None and BINARY.is_file(), 'Build the emaki binary before this test'

with tempfile.TemporaryDirectory(prefix='appearance-live-') as temporary:
    base = Path(temporary)
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
    processes = []
    logs = []

    def start(name):
        directory = base / name
        directory.mkdir()
        names = ('SettingsCatalog', 'SettingsBridge', 'PrivateJob', 'AppLaunch',
                 'Appearance', 'ShellPalette')
        singletons = ('SettingsBridge', 'AppLaunch', 'Appearance', 'ShellPalette')
        for component in names:
            shutil.copyfile(ROOT / 'shell' / (component + '.qml'),
                            directory / (component + '.qml'))
        shutil.copyfile(ROOT / 'shell/AppearanceLogic.js', directory / 'AppearanceLogic.js')
        shutil.copytree(ROOT / 'shell/helpers', directory / 'helpers')
        (directory / 'qmldir').write_text(''.join(
            ('singleton ' if component in singletons else '')
            + component + ' 1.0 ' + component + '.qml\n' for component in names))
        shutil.copyfile(ROOT / 'tests/fixtures/AppearanceLive.qml', directory / 'shell.qml')
        runtime = directory / 'runtime'
        runtime.mkdir(mode=0o700)
        process_env = dict(env, XDG_RUNTIME_DIR=str(runtime))
        path = directory / 'output.log'
        log = path.open('w')
        logs.append(log)
        process = subprocess.Popen(['qs', '-p', str(directory / 'shell.qml'), '--no-color'],
                                   env=process_env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process, path

    def observe(instance, theme, accent, color):
        process, path = instance
        expected = dict(theme=theme, accent=accent, dark=theme == 'dark', color=color,
                        managerBackground='#211815' if theme == 'dark' else '#fffaf6')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            output = path.read_text()
            assert process.poll() is None, output
            snapshots = [json.loads(line.split('APPEARANCE_LIVE ', 1)[1])
                         for line in output.splitlines() if 'APPEARANCE_LIVE {' in line]
            if snapshots and snapshots[-1] == expected:
                return
            time.sleep(.05)
        raise AssertionError(f'{path.parent.name} did not observe {expected}\n{output}')

    def observe_busy(instance):
        process, path = instance
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            output = path.read_text()
            assert process.poll() is None, output
            if 'APPEARANCE_CATALOG settings_busy' in output:
                assert 'APPEARANCE_LIVE {' not in output, output
                return
            time.sleep(.05)
        raise AssertionError(f'{path.parent.name} did not encounter the held lock\n{output}')

    def set_value(key, value):
        result = subprocess.run([str(BINARY), 'settings', 'set', key, value,
                                 '--profile-root', str(base), '--json'], env=env,
                                capture_output=True, text=True, check=True, timeout=20)
        reply = json.loads(result.stdout)
        assert reply['status'] == 'committed', reply

    def stop(instance):
        process, _ = instance
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    try:
        # Isolated profiles lock this directory. Force both initial real reads
        # to encounter contention, then require recovery without a file change.
        lock = os.open(base / 'runtime', os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            shell = start('shell-session')
            manager = start('manager-session')
            assert shell[0].pid != manager[0].pid
            for instance in (shell, manager):
                observe_busy(instance)
        finally:
            os.close(lock)
        # Nothing saved: both readers show the light default with the rose accent (2026-10-15).
        for instance in (shell, manager):
            observe(instance, 'light', 'rose', '#e780aa')
        # First creation and subsequent atomic replacement must reach both readers.
        set_value('appearance.theme', 'dark')
        for instance in (shell, manager):
            observe(instance, 'dark', 'rose', '#e780aa')
        set_value('appearance.theme', 'light')
        for instance in (shell, manager):
            observe(instance, 'light', 'rose', '#e780aa')
        set_value('appearance.accent', 'blue')
        for instance in (shell, manager):
            observe(instance, 'light', 'blue', '#78aaf0')
        stored = tomllib.loads((base / 'config/emaki/settings.toml').read_text())
        assert stored['appearance']['theme'] == 'light', stored
        assert stored['appearance']['accent'] == 'blue', stored
        stop(manager)
        manager = start('manager-restarted')
        observe(manager, 'light', 'blue', '#78aaf0')
        # Verify watching still works after replacement and a consumer restart.
        set_value('appearance.accent', 'violet')
        for instance in (shell, manager):
            observe(instance, 'light', 'violet', '#b294e8')
    finally:
        for process in processes:
            stop((process, None))
        for log in logs:
            log.close()
    print('PASS: two live QML processes retry busy reads, share durable appearance and restart state')
