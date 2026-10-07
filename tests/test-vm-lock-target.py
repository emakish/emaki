#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise installed lock suite staging and credentials with fake transports."""
import importlib.util
import contextlib
import fnmatch
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import stat
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/vm'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('lock_runner', 'tests/vm/run-lock.py')
guest = load('lock_guest', 'tests/vm/check-lock.py')


class FakeTarget:
    user = 'acceptance'
    password = 'private fixture value'

    def __init__(self, status=0):
        self.calls = []
        self.status = status

    def remote(self, command, **kwargs):
        self.calls.append((command, kwargs))
        out = ''
        code = 0
        if command.startswith('python3 -c'):
            out = json.dumps(dict(USER=self.user, HOME='/home/acceptance',
                                  XDG_RUNTIME_DIR='/run/user/1207', WAYLAND_DISPLAY='wayland-3',
                                  NIRI_SOCKET='/run/user/1207/niri.sock'))
        elif command.startswith('mktemp'):
            out = '/tmp/emaki-lock-suite.fixture\n'
        elif '--credentials-stdin' in command:
            out = 'suite evidence\n'
            code = self.status
        return subprocess.CompletedProcess(command, code, out, '')

    def ssh_argv(self):
        return ['fake-ssh', '--user', self.user]


def discover_fixture(executable='niri-emaki', *, extra=None, wrong_owner=False, socket_pid='4321'):
    uid = 1207
    runtime = '/run/user/1207'
    nodes = {
        '/proc/4321': dict(uid=uid),
        '/proc/4321/cmdline': dict(data=('/usr/bin/' + executable + '\0--session\0').encode()),
        runtime: dict(uid=uid),
        runtime + '/wayland-3': dict(uid=uid, mode=stat.S_IFSOCK),
        runtime + '/niri.wayland-3.' + socket_pid + '.sock': dict(uid=42 if wrong_owner else uid, mode=stat.S_IFSOCK),
        '/proc/99': dict(uid=42),
        '/proc/99/cmdline': dict(data=b'/usr/bin/niri-emaki\0'),
    }
    nodes.update(extra or {})

    class FixturePath:
        def __init__(self, value):
            self.value = str(value)

        def __str__(self):
            return self.value

        @property
        def name(self):
            return PurePosixPath(self.value).name

        def __truediv__(self, other):
            return FixturePath(str(PurePosixPath(self.value) / other))

        def glob(self, pattern):
            return [FixturePath(name) for name in nodes
                    if str(PurePosixPath(name).parent) == self.value
                    and fnmatch.fnmatch(PurePosixPath(name).name, pattern)]

        def stat(self):
            item = nodes[self.value]
            return SimpleNamespace(st_uid=item.get('uid', uid), st_mode=item.get('mode', stat.S_IFDIR))

        lstat = stat

        def read_bytes(self):
            return nodes[self.value]['data']

    output = io.StringIO()
    with patch('pathlib.Path', FixturePath), patch('os.getuid', return_value=uid), \
            patch('pwd.getpwuid', return_value=SimpleNamespace(pw_uid=uid, pw_name='acceptance', pw_dir='/home/acceptance')), \
            patch('subprocess.check_output', return_value='kvm\n'), contextlib.redirect_stdout(output):
        exec(compile(runner.DISCOVER, '<guest-discovery>', 'exec'), {})
    return json.loads(output.getvalue())


class LockTargetTests(unittest.TestCase):
    def test_actual_discovery_accepts_both_installed_compositors(self):
        for executable in ('niri', 'niri-emaki'):
            with self.subTest(executable=executable):
                result = discover_fixture(executable)
                self.assertEqual(result['USER'], 'acceptance')
                self.assertEqual(result['XDG_RUNTIME_DIR'], '/run/user/1207')
                self.assertEqual(result['NIRI_SOCKET'], '/run/user/1207/niri.wayland-3.4321.sock')
                self.assertEqual(result['WAYLAND_DISPLAY'], 'wayland-3')

    def test_actual_discovery_rejects_ambiguous_and_foreign_sessions(self):
        ambiguous = [
            {'/proc/8765': dict(uid=1207), '/proc/8765/cmdline': dict(data=b'/usr/bin/niri\0')},
            {'/run/user/1207/wayland-4': dict(uid=1207, mode=stat.S_IFSOCK)},
            {'/run/user/1207/niri.wayland-3.8765.sock': dict(uid=1207, mode=stat.S_IFSOCK)},
        ]
        for extra in ambiguous:
            with self.subTest(extra=extra), self.assertRaises(AssertionError):
                discover_fixture(extra=extra)
        with self.assertRaisesRegex(AssertionError, 'do not match'):
            discover_fixture(socket_pid='8765')
        with self.assertRaises(AssertionError):
            discover_fixture(extra={'/run/user/1207/wayland-3': dict(uid=1207, mode=stat.S_IFLNK)})
        with self.assertRaises(AssertionError):
            discover_fixture(wrong_owner=True)
        with self.assertRaises(AssertionError):
            discover_fixture('other-compositor')

    def test_nonempty_output_is_preserved_before_remote_access(self):
        target = FakeTarget()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = output / 'suite.log'
            evidence.write_text('previous evidence')
            with self.assertRaisesRegex(ValueError, 'empty directory'):
                runner.run_suite(target, output)
            self.assertEqual(evidence.read_text(), 'previous evidence')
        self.assertEqual(target.calls, [])

    def test_installed_suite_only_stages_test_files_and_streams_evidence(self):
        target = FakeTarget()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as transport:
            self.assertEqual(runner.run_suite(target, Path(directory), two_outputs=True), 0)
            self.assertTrue((Path(directory) / 'guest-evidence.tar').exists())
            self.assertIn('tar -C', transport.call_args.args[0][-1])
        staged = [command for command, _ in target.calls if 'base64 -d' in command]
        self.assertEqual(len(staged), len(runner.FILES))
        self.assertTrue(all('/tests/' in command for command in staged))
        commands = '\n'.join(command for command, _ in target.calls)
        self.assertNotIn(target.password, commands)
        invocation, payload = next(call for call in target.calls if '--credentials-stdin' in call[0])
        self.assertIn('--user acceptance', invocation)
        self.assertIn('--two-outputs', invocation)
        self.assertIn('XDG_RUNTIME_DIR=/run/user/1207', invocation)
        self.assertEqual(json.loads(payload['data']), {'password': target.password})
        self.assertEqual(guest.SHELL, Path('/usr/share/emaki/shell'))

    def test_failed_scope_keeps_guest_evidence(self):
        target = FakeTarget(status=1)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            self.assertEqual(runner.run_suite(target, Path(directory), scope_only=True), 1)
        commands = '\n'.join(command for command, _ in target.calls)
        self.assertIn('check-lock-scope.py --user', commands)
        self.assertNotIn('rm -rf', commands)

    def test_discovery_failure_never_stages_or_runs_suite(self):
        target = FakeTarget()
        with patch.object(target, 'remote', return_value=subprocess.CompletedProcess([], 1, '', 'QEMU required')) as remote:
            with self.assertRaisesRegex(RuntimeError, 'QEMU required'):
                runner.run_suite(target, Path('unused'))
        self.assertEqual(remote.call_count, 1)

    def test_sudo_failure_never_injects_password(self):
        with patch.object(guest, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'denied')) as run:
            with self.assertRaisesRegex(AssertionError, 'guest sudo authentication failed'):
                guest.keys('text', text='fixture secret')
        self.assertEqual(run.call_count, 1)

    def test_password_authentication_is_separate_from_injector_input(self):
        with patch.object(guest, 'PASSWORD', 'fixture secret'), \
                patch.object(guest, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            guest.keys('text', 'key:Return', text='text to type')
        authentication, injection = run.call_args_list
        self.assertEqual(authentication.kwargs['input'], 'fixture secret\n')
        self.assertEqual(injection.kwargs['input'], 'text to type')
        self.assertNotIn('fixture secret', repr(authentication.args) + repr(injection.args))
        self.assertIn('-n', injection.args[0])


if __name__ == '__main__':
    unittest.main()
