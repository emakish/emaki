#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transaction inhibitor lifecycle using real pidfds and a fake inhibitor FD."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import select
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import reaper
reaper.guard()
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/emaki-transaction-inhibit'
MODULE = runpy.run_path(str(SCRIPT))
GLOBALS = MODULE['main'].__globals__


class Inhibitor(unittest.TestCase):
    def test_hook_runs_before_all_package_changes_without_aborting(self):
        hook = (ROOT / 'packaging/emaki-config/00-emaki-transaction-inhibit.hook').read_text()
        for line in ('Operation = Install', 'Operation = Upgrade', 'Operation = Remove',
                     'When = PreTransaction',
                     'Exec = /usr/bin/emaki-transaction-inhibit'):
            self.assertIn(line, hook.splitlines())
        self.assertNotIn('AbortOnFail', hook.splitlines())

    def test_main_uses_direct_parent_and_fails_open(self):
        with patch.object(GLOBALS['state'], 'foreign_root', return_value=False), \
                patch.object(sys, 'argv', [str(SCRIPT)]), \
                patch.dict(GLOBALS, start=Mock()) as namespace:
            self.assertEqual(MODULE['main'](), 0)
            namespace['start'].assert_called_once_with(os.getppid())
            namespace['start'].side_effect = RuntimeError('unavailable')
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(MODULE['main'](), 0)
            self.assertIn('could not prevent sleep', error.getvalue())
            self.assertEqual(len(error.getvalue().splitlines()), 1)

    def test_reparented_hook_never_acquires_or_leaks_a_pidfd(self):
        for pid, executable in ((1, '/usr/bin/pacman'),
                                (os.getpid(), '/usr/bin/subreaper')):
            with self.subTest(pid=pid):
                before = len(os.listdir('/proc/self/fd'))
                with patch.object(os, 'readlink', return_value=executable), \
                        patch.object(subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(RuntimeError, 'transaction has ended'):
                        MODULE['start'](pid)
                    spawn.assert_not_called()
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_owner_validation_happens_after_opening_pidfd(self):
        events = []
        real_open = os.pidfd_open
        def opened(pid):
            events.append('pidfd')
            return real_open(pid)
        def executable(_path):
            events.append('exe')
            raise FileNotFoundError('owner exited')
        with patch.object(os, 'pidfd_open', side_effect=opened), \
                patch.object(os, 'readlink', side_effect=executable):
            with self.assertRaises(FileNotFoundError):
                MODULE['start'](os.getpid())
        self.assertEqual(events, ['pidfd', 'exe'])

    def test_mutant_accepting_a_subreaper_is_detected(self):
        source = SCRIPT.read_text()
        check = "if pid <= 1 or os.readlink(f'/proc/{pid}/exe') != '/usr/bin/pacman':"
        self.assertIn(check, source)
        namespace = {'__name__': 'mutant', '__file__': str(SCRIPT)}
        exec(compile(source.replace(check, 'if False:'), str(SCRIPT), 'exec'), namespace)
        with patch.object(os, 'readlink', return_value='/usr/bin/subreaper'), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('unverified owner')):
            with self.assertRaisesRegex(AssertionError, 'unverified owner'):
                namespace['start'](os.getpid())

    def test_mutant_refusing_transaction_on_missing_logind_is_detected(self):
        source = SCRIPT.read_text()
        old = "'Continuing without sleep protection.', file=sys.stderr)\n        return 0"
        self.assertIn(old, source)
        namespace = {'__name__': 'mutant', '__file__': str(SCRIPT)}
        exec(compile(source.replace(old, old[:-1] + '1'), str(SCRIPT), 'exec'), namespace)
        with patch.object(namespace['state'], 'foreign_root', return_value=False), \
                patch.object(sys, 'argv', [str(SCRIPT)]), \
                patch.dict(namespace, start=Mock(side_effect=RuntimeError('logind unavailable'))), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(AssertionError):
                self.assertEqual(namespace['main'](), 0)

    def test_foreign_root_never_acquires(self):
        with patch.object(GLOBALS['state'], 'foreign_root', return_value=True), \
                patch.object(sys, 'argv', [str(SCRIPT)]), \
                patch.dict(GLOBALS, start=Mock()) as namespace:
            self.assertEqual(MODULE['main'](), 0)
            namespace['start'].assert_not_called()

    def test_bus_flags_and_descriptor_transfer(self):
        descriptors = [os.open('/dev/null', os.O_RDONLY) for _ in range(3)]
        reply = Mock()
        reply.unpack.return_value = (1,)
        fd_list = Mock()
        fd_list.steal_fds.return_value = descriptors.copy()
        bus = Mock()
        bus.call_with_unix_fd_list_sync.return_value = (reply, fd_list)
        gio = types.SimpleNamespace(bus_get_sync=Mock(return_value=bus),
                                    BusType=types.SimpleNamespace(SYSTEM=1),
                                    DBusCallFlags=types.SimpleNamespace(NONE=0))
        glib = types.SimpleNamespace(Variant=lambda kind, value: (kind, value),
                                     VariantType=lambda kind: kind)
        with patch.dict(sys.modules, {'gi': Mock(),
                                    'gi.repository': types.SimpleNamespace(Gio=gio, GLib=glib)}):
            result = MODULE['acquire']()
        try:
            self.assertEqual(result, descriptors[1])
            self.assertFalse(os.get_inheritable(result))
            arguments = bus.call_with_unix_fd_list_sync.call_args.args
            self.assertEqual(arguments[3], 'Inhibit')
            self.assertEqual(arguments[4][1][0], 'sleep:handle-lid-switch')
            self.assertEqual(arguments[4][1][3], 'block')
            for descriptor in (descriptors[0], descriptors[2]):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
        finally:
            os.close(result)

    def test_missing_bus_missing_logind_and_refused_inhibit_are_reported(self):
        for unavailable in ('system bus unavailable', 'login1 absent', 'inhibit refused'):
            with self.subTest(unavailable=unavailable):
                bus = Mock()
                bus.call_with_unix_fd_list_sync.side_effect = RuntimeError(unavailable)
                gio = types.SimpleNamespace(
                    bus_get_sync=Mock(return_value=bus),
                    BusType=types.SimpleNamespace(SYSTEM=1),
                    DBusCallFlags=types.SimpleNamespace(NONE=0))
                if unavailable == 'system bus unavailable':
                    gio.bus_get_sync.side_effect = RuntimeError(unavailable)
                glib = types.SimpleNamespace(Variant=lambda kind, value: (kind, value),
                                             VariantType=lambda kind: kind)
                with patch.dict(sys.modules, {'gi': Mock(), 'gi.repository':
                                             types.SimpleNamespace(Gio=gio, GLib=glib)}):
                    with self.assertRaisesRegex(RuntimeError, unavailable):
                        MODULE['acquire']()

    @contextlib.contextmanager
    def holder(self, mutation=None, acquire_error=False, exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / 'inhibitor'
            os.mkfifo(fifo)
            reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            source = SCRIPT.read_text()
            before, remainder = source.split('def acquire():', 1)
            _, after = remainder.split('\ndef hold(', 1)
            replacement = ("    raise OSError('fake unavailable')\n" if acquire_error else
                           f"    fd = os.open({str(fifo)!r}, os.O_WRONLY)\n"
                           "    os.write(fd, b'A')\n    return fd\n")
            source = (before + f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n" +
                      'def acquire():\n' + replacement + '\ndef hold(' + after)
            # The state import happens before the added path, so give the isolated
            # child the repository path at that import as well.
            source = source.replace('import emaki_session_state as state',
                                    f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
                                    'import emaki_session_state as state')
            if mutation:
                self.assertIn('        select.select([owner], [], [])', source)
                source = source.replace('        select.select([owner], [], [])',
                                        '        while True:\n            select.select([], [], [], 60)')
            script = Path(directory) / 'holder'
            script.write_text(source)
            module = runpy.run_path(str(script))
            pacman = subprocess.Popen([sys.executable, '-c',
                                      'import os, sys; os.read(0, 1); sys.exit(int(sys.argv[1]))',
                                      str(exit_code)],
                                      stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            child = None
            try:
                if acquire_error:
                    with self.assertRaisesRegex(RuntimeError, 'did not start'):
                        with patch.object(os, 'readlink', return_value='/usr/bin/pacman'):
                            module['start'](pacman.pid)
                    yield pacman, None, reader
                else:
                    with patch.object(os, 'readlink', return_value='/usr/bin/pacman'):
                        child = module['start'](pacman.pid)
                    self.assertEqual(os.read(reader, 1), b'A')
                    self.assertEqual(os.getsid(child.pid), child.pid)
                    self.assertIsNone(child.poll())
                    self.assertFalse(select.select([reader], [], [], .03)[0])
                    yield pacman, child, reader
            finally:
                pacman.kill()
                pacman.wait()
                pacman.stdin.close()
                if child is not None:
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                os.close(reader)

    def released(self, child, reader, timeout=2):
        child.wait(timeout=timeout)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(os.read(reader, 1), b'')
        self.assertFalse(Path(f'/proc/{child.pid}').exists())

    def test_normal_exit_releases_inhibitor(self):
        with self.holder() as (pacman, child, reader):
            pacman.stdin.write(b'x')
            pacman.stdin.flush()
            pacman.wait(timeout=2)
            self.released(child, reader)

    def test_failed_transaction_releases_inhibitor(self):
        with self.holder(exit_code=7) as (pacman, child, reader):
            pacman.stdin.write(b'x')
            pacman.stdin.flush()
            self.assertEqual(pacman.wait(timeout=2), 7)
            self.released(child, reader)

    def test_exit_during_acquisition_never_reports_ready(self):
        owner_reader, owner_writer = os.pipe()
        ready_reader, ready_writer = os.pipe()
        inhibitor = os.open('/dev/null', os.O_RDONLY)

        def acquire():
            os.close(owner_writer)
            return inhibitor

        try:
            with patch.dict(MODULE['hold'].__globals__, acquire=acquire):
                self.assertEqual(MODULE['hold'](owner_reader, ready_writer), 1)
            self.assertEqual(os.read(ready_reader, 1), b'')
            for descriptor in (owner_reader, ready_writer, inhibitor):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
        finally:
            os.close(ready_reader)

    def test_failure_interrupt_and_kill_release_even_before_reaping(self):
        for termination in (signal.SIGINT, signal.SIGKILL, signal.SIGTERM):
            with self.subTest(signal=termination), self.holder() as (pacman, child, reader):
                pacman.send_signal(termination)
                # Do not wait/poll the owner: the pidfd must observe an unreaped exit.
                self.released(child, reader)
                self.assertEqual(Path(f'/proc/{pacman.pid}/stat').read_text().split(') ')[1][0], 'Z')

    def test_failed_acquisition_leaves_no_child_or_descriptors(self):
        before = set(reaper.descendants())
        fds = len(os.listdir('/proc/self/fd'))
        with self.holder(acquire_error=True):
            pass
        self.assertEqual(set(reaper.descendants()), before)
        self.assertEqual(len(os.listdir('/proc/self/fd')), fds)

    def test_mutant_never_released_is_detected_and_cleaned_up(self):
        with self.holder(mutation='never released') as (pacman, child, reader):
            pacman.kill()
            pacman.wait(timeout=2)
            with self.assertRaises(subprocess.TimeoutExpired):
                self.released(child, reader, timeout=.2)
            self.assertFalse(select.select([reader], [], [], 0)[0])
        self.assertFalse(Path(f'/proc/{child.pid}').exists())


if __name__ == '__main__':
    unittest.main()
