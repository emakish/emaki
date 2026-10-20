#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authentication lock gate tests without a desktop, socket or real locker."""
import importlib.machinery
import importlib.util
import os
import json
import select
import signal
import sys
import time
from pathlib import Path
import stat
import subprocess
import tempfile
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


lock = load('lock_gate_supervisor', ROOT / 'scripts/emaki-lock')
watch = load('lock_gate_watch', ROOT / 'shell/helpers/polkit-lock-state.py')


def check_observer():
    with tempfile.TemporaryDirectory() as temporary:
        env = dict(os.environ, XDG_RUNTIME_DIR=temporary, WAYLAND_DISPLAY='wayland-test')
        with patch.dict(os.environ, env, clear=True):
            directory = lock.runtime_directory()
        child = subprocess.Popen([sys.executable, '-I', '-B',
                                  str(ROOT / 'shell/helpers/polkit-lock-state.py')],
                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, bufsize=0)
        buffered = bytearray()

        def sample(expected, generation=None):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if b'\n' not in buffered:
                    ready, _, _ = select.select([child.stdout], [], [],
                                                max(0, deadline - time.monotonic()))
                    assert ready, 'observer did not report a gate change'
                    chunk = os.read(child.stdout.fileno(), 65536)
                    assert chunk, child.stderr.read().decode()
                    buffered.extend(chunk)
                while b'\n' in buffered:
                    line, _, rest = buffered.partition(b'\n')
                    buffered[:] = rest
                    value = json.loads(line)
                    assert type(value['generation']) is int
                    if value['locked'] is expected and (generation is None or value['generation'] == generation):
                        return value
            raise AssertionError('observer did not report expected gate state')

        try:
            sample(False)
            assert not select.select([child.stdout], [], [], 1.2)[0], 'idle observer must not poll'
            marker = directory / 'authentication-blocked'
            marker.write_text('{}')
            marker.chmod(0o600)
            sample(True)
            marker.unlink()
            sample(False)
            # Explicit refresh works even when no gate event follows the IPC.
            child.stdin.write(b'1\n')
            sample(False, generation=1)
            # A stalled helper has no synthetic lock event or heartbeat timeout.
            os.kill(child.pid, signal.SIGSTOP)
            assert not select.select([child.stdout], [], [], 1.2)[0]
            os.kill(child.pid, signal.SIGCONT)
            directory.rmdir()
            sample(False)
            directory.mkdir(mode=0o700)
            marker.write_text('{}')
            marker.chmod(0o600)
            sample(True)
        finally:
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
            child.communicate(timeout=3)


def main():
    capture = (ROOT / 'shell/CaptureService.qml').read_text()
    prompts = (ROOT / 'shell/PolkitPromptService.qml').read_text()
    outputs = (ROOT / 'shell/ShellOutputs.qml').read_text()
    assert 'PolkitLockState {' not in capture, 'capture must share the authentication observer'
    assert prompts.count('PolkitLockState {') == 1
    assert 'property PolkitLockState authenticationLockState: null' in outputs
    assert 'property: "authenticationLockState"' in prompts
    assert 'locked: service.outputs.authenticationLockState?.locked ?? true' in capture
    with tempfile.TemporaryDirectory() as temporary:
        env = {'XDG_RUNTIME_DIR': temporary, 'WAYLAND_DISPLAY': 'wayland-test'}
        with patch.dict(os.environ, env, clear=True):
            directory = lock.runtime_directory()
            assert not watch.blocked(env)
            supervisor = lock.Supervisor(directory)
            def acknowledge(argv, **kwargs):
                assert watch.blocked(env), 'marker must precede input cancellation'
                assert argv == ['emaki-shell', 'call', 'authentication', 'lock']
                assert kwargs['stdout'] == subprocess.DEVNULL
                assert kwargs['stderr'] == subprocess.DEVNULL
                assert kwargs['stdin'] == subprocess.DEVNULL
                return Mock(returncode=0, **{'poll.return_value': 0})
            with patch.object(lock.subprocess, 'Popen', side_effect=acknowledge):
                supervisor.block_authentication()
                supervisor.cancel_authentication()
            assert watch.blocked(env), 'a restarted shell must stay blocked'
            marker = directory / 'authentication-blocked'
            marker.write_text('{"device":1,"inode":2}')
            with patch.object(watch.Path, 'stat', return_value=Mock(st_mode=stat.S_IFSOCK, st_dev=1, st_ino=2)):
                assert watch.blocked(env), 'same compositor remains blocked'
            with patch.object(watch.Path, 'stat', return_value=Mock(st_mode=stat.S_IFSOCK, st_dev=1, st_ino=3)):
                assert not watch.blocked(env), 'a new compositor does not inherit the old gate'
            assert not watch.blocked(dict(env, WAYLAND_DISPLAY='wayland-other'))
            with patch.object(lock.subprocess, 'Popen', side_effect=OSError('fixture')):
                supervisor.block_authentication()
                supervisor.cancel_authentication()
            assert watch.blocked(env), 'lost IPC must retain the gate'
            supervisor.child = Mock(returncode=0)
            supervisor.child.poll.return_value = 0
            supervisor.authenticated_exit = True
            supervisor.retire = Mock()
            supervisor.tick()
            assert supervisor.phase == 'unlocked'
            assert not watch.blocked(env), 'authenticated unlock opens the gate'
            with patch.object(lock.subprocess, 'Popen', side_effect=acknowledge):
                supervisor.block_authentication()
                supervisor.cancel_authentication()
            supervisor.backend = 'hyprlock'
            supervisor.secure = False
            supervisor.tick()
            assert supervisor.phase == 'refused'
            assert not watch.blocked(env), 'unconfirmed refusal must reopen authentication'
            (directory / 'authentication-blocked').unlink(missing_ok=True)
            (directory / 'authentication-blocked').symlink_to(directory / 'absent')
            assert watch.blocked(env), 'even a broken marker symlink is blocked'
            with patch.object(lock.subprocess, 'Popen', side_effect=acknowledge) as notify:
                supervisor.block_authentication()
                supervisor.cancel_authentication()
                assert notify.called, 'gate write failure must still cancel and let locking proceed'
            directory.chmod(0o755)
            assert watch.blocked(env), 'unsafe runtime directory fails closed'
            directory.chmod(0o700)
            supervisor.selector.close()
        assert watch.blocked({}), 'missing runtime must block'
    check_observer()
    print('polkit lock state: passed')


if __name__ == '__main__':
    main()
