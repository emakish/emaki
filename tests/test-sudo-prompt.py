#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the production sudo adapter and card using isolated native state."""
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile

from runtime_fixture import short_runtime
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parent.parent


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    qs = shutil.which('qs')
    assert qs, 'qs is required'
    for source in (ROOT / 'shell').glob('Sudo*.qml'):
        if source.name != 'SudoPromptService.qml':
            assert 'IpcHandler' not in source.read_text(), source.name
    service = (ROOT / 'shell/SudoPromptService.qml').read_text()
    assert 'function revokeAll(): void' in service
    assert 'function submit' not in service and 'function password' not in service
    with tempfile.TemporaryDirectory(prefix='sudo-ui-test-') as temporary, short_runtime() as runtime:
        profile = Path(temporary)
        for name in ('cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        shutil.copytree(ROOT / 'shell', profile / 'qml')
        shutil.copyfile(ROOT / 'tests/fixtures/SudoPromptTest.qml', profile / 'qml/shell.qml')
        env = dict(os.environ, HOME=str(profile), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   XDG_RUNTIME_DIR=runtime, XDG_CACHE_HOME=str(profile / 'cache'),
                   XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
                   TMPDIR=str(profile / 'tmp'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=60, preexec_fn=no_core)
        output = result.stdout + result.stderr
        output = '\n'.join(line for line in output.splitlines()
                           if 'quickshell.ipc: Failed to start IPC server on path' not in line)
        assert result.returncode == 0 and 'SUDO_PROMPT_OK' in output, output
        assert not any(word in output for word in ('ERROR', 'WARN', 'ReferenceError', 'TypeError')), output
        assert 'PRIVATE_SUDO_FIXTURE_71' not in output, 'response leaked to output'
        for base in (Path(runtime), profile / 'cache', profile / 'state'):
            for path in base.rglob('*'):
                if path.is_file():
                    assert b'PRIVATE_SUDO_FIXTURE_71' not in path.read_bytes(), path.name
    print('Sudo UI: package and warning gates, typed minute and turn bounds, session and indicator, revoke, lock and channel loss passed')


if __name__ == '__main__':
    main()
