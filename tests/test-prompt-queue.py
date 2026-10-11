#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the observable source-head FIFO and actual card in an isolated Qt window."""
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
    with tempfile.TemporaryDirectory(prefix='prompt-queue-') as temporary, short_runtime() as runtime:
        profile = Path(temporary)
        for name in ('cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        shutil.copytree(ROOT / 'shell', profile / 'qml')
        shutil.copyfile(ROOT / 'tests/fixtures/PromptQueueTest.qml', profile / 'qml/shell.qml')
        env = dict(os.environ, HOME=str(profile), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   XDG_RUNTIME_DIR=runtime, XDG_CACHE_HOME=str(profile / 'cache'),
                   XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
                   TMPDIR=str(profile / 'tmp'), QT_LOGGING_RULES='*.debug=false',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
            env.pop(name, None)
        result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=30, preexec_fn=no_core)
        output = '\n'.join(line for line in (result.stdout + result.stderr).splitlines()
                           if 'quickshell.ipc: Failed to start IPC server on path' not in line)
        assert result.returncode == 0 and 'PROMPT_QUEUE_OK' in output, output
        assert not any(word in output for word in ('ERROR', 'WARN', 'ReferenceError', 'TypeError')), output
        print('Prompt queue: both arrival orders, cancellations, replacement, real key guard and retries passed')
        print('Scope: observable source heads only; native providers may retain arrivals internally or reject a second sudo request')


if __name__ == '__main__':
    main()
