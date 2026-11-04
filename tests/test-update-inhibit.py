#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise updater inhibition with real child processes and a fake logind lease."""
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
sys.path.insert(0, str(ROOT / 'update-manager'))
import emaki_update_inhibit as lease

SCRIPT = ROOT / 'scripts/emaki-update'
MODULE = runpy.run_path(str(SCRIPT))
GLOBALS = MODULE['main'].__globals__
LIVE = '--live' in sys.argv
if LIVE:
    sys.argv.remove('--live')


@contextlib.contextmanager
def fake_bus(descriptors, index=0, error=None):
    """Give acquire() owned descriptors exactly as Gio's steal_fds does."""
    bus = Mock()
    bus.call_with_unix_fd_list_sync.return_value = (
        Mock(unpack=Mock(return_value=(index,))),
        Mock(steal_fds=Mock(return_value=list(descriptors))))
    if error:
        bus.call_with_unix_fd_list_sync.side_effect = error
    gio = types.SimpleNamespace(
        bus_get_sync=Mock(return_value=bus), BusType=types.SimpleNamespace(SYSTEM=1),
        DBusCallFlags=types.SimpleNamespace(NONE=0))
    glib = types.SimpleNamespace(Variant=lambda signature, value: (signature, value),
                                 VariantType=lambda signature: signature, Error=RuntimeError)
    gi = types.ModuleType('gi')
    gi.require_version = Mock()
    repository = types.ModuleType('gi.repository')
    repository.Gio, repository.GLib = gio, glib
    with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
            patch.object(lease.state, 'foreign_root', return_value=False):
        yield bus


class Acquisition(unittest.TestCase):
    def test_foreign_root_skips_bus_and_descriptor_cleanup(self):
        for required in (True, False):
            with self.subTest(required=required), \
                    patch.object(lease.state, 'foreign_root', return_value=True), \
                    patch.object(lease, 'acquire') as acquire:
                with lease.inhibit(required=required) as descriptor:
                    self.assertIsNone(descriptor)
                acquire.assert_not_called()

    def test_unavailable_lease_refuses_by_default_but_terminal_warns(self):
        with fake_bus([], error=RuntimeError('bus unavailable')):
            with self.assertRaisesRegex(lease.AcquisitionError, 'The update was not started'):
                with lease.inhibit():
                    self.fail('protected transaction started without a lease')
            output = io.StringIO()
            with contextlib.redirect_stderr(output), lease.inhibit(required=False) as descriptor:
                self.assertIsNone(descriptor)
            self.assertIn('Warning:', output.getvalue())
            self.assertIn('transaction will continue', output.getvalue())

    def test_exact_policy_and_owned_descriptor_cleanup(self):
        reader, writer = os.pipe()
        extra = os.open('/dev/null', os.O_RDONLY)
        try:
            with fake_bus([extra, writer], index=1) as bus:
                with lease.inhibit() as descriptor:
                    self.assertEqual(descriptor, writer)
                    self.assertFalse(os.get_inheritable(descriptor))
                    os.write(descriptor, b'held')
                    with self.assertRaises(OSError):
                        os.fstat(extra)
                    args = bus.call_with_unix_fd_list_sync.call_args.args
                    self.assertEqual(args[4], ('(ssss)', (
                        'shutdown:sleep:handle-lid-switch', 'Emaki packages', 'Updating Emaki', 'block')))
                self.assertEqual(os.read(reader, 64), b'held')
                self.assertEqual(os.read(reader, 1), b'')
        finally:
            os.close(reader)

    def test_invalid_reply_releases_every_descriptor(self):
        for index in (-1, 2):
            with self.subTest(index=index):
                reader, writer = os.pipe()
                extra = os.open('/dev/null', os.O_RDONLY)
                try:
                    with fake_bus([writer, extra], index=index):
                        with self.assertRaises((RuntimeError, ValueError)):
                            lease.acquire()
                    self.assertEqual(os.read(reader, 1), b'')
                    with self.assertRaises(OSError):
                        os.fstat(extra)
                finally:
                    os.close(reader)

    def test_exception_and_interrupt_release_lease(self):
        for error in (RuntimeError('backend failed'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                reader, writer = os.pipe()
                try:
                    with fake_bus([writer]):
                        with self.assertRaises(type(error)):
                            with lease.inhibit():
                                raise error
                    self.assertEqual(os.read(reader, 1), b'')
                finally:
                    os.close(reader)


BACKEND = '''import os, signal, sys
from pathlib import Path
operation = sys.argv[1]
with open(os.environ['TEST_UPDATE_LOG'], 'a') as output:
    output.write(operation + '\\n')
if operation == '-Qu':
    print('archlinux-keyring 1 -> 2')
    raise SystemExit(0)
os.write(int(os.environ['TEST_UPDATE_LEASE']), operation.encode() + b'\\n')
if operation == os.environ.get('TEST_UPDATE_FAIL'):
    raise SystemExit(7)
if operation == os.environ.get('TEST_UPDATE_CANCEL'):
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGINT)
'''


class Transaction(unittest.TestCase):
    def test_acquisition_failure_stops_service_but_allows_terminal(self):
        for arguments, expected in ((['--noninteractive'], 1), ([], 0)):
            with self.subTest(arguments=arguments), \
                    fake_bus([], error=RuntimeError('bus unavailable')), \
                    patch.object(os, 'geteuid', return_value=0), \
                    patch.object(sys, 'argv', [str(SCRIPT), *arguments]), \
                    patch.dict(GLOBALS, preflight=Mock(), update=Mock(return_value=0)), \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(MODULE['main'](), expected)
                if arguments:
                    GLOBALS['preflight'].assert_not_called()
                    GLOBALS['update'].assert_not_called()
                    self.assertIn('The update was not started', output.getvalue())
                else:
                    GLOBALS['preflight'].assert_called_once_with()
                    GLOBALS['update'].assert_called_once_with(False, targets=[], inhibitor=None)
                    self.assertIn('Warning:', output.getvalue())

    def invoke(self, *, fail='', cancel='', arguments=(), preflight_error=None):
        reader, writer = os.pipe()
        os.set_blocking(reader, False)
        with tempfile.TemporaryDirectory(prefix='emaki-update-inhibit-') as directory:
            backend = Path(directory) / 'backend.py'
            backend.write_text(BACKEND)
            log = Path(directory) / 'calls'
            actual_update = GLOBALS['update']
            command = (sys.executable, str(backend))
            def update(noninteractive, **options):
                return actual_update(noninteractive, pacman=command, **options)
            def preflight(**_options):
                # Acquired before even the temporary-database preflight starts.
                with self.assertRaises(BlockingIOError):
                    os.read(reader, 1)
                if preflight_error:
                    raise preflight_error
            try:
                with fake_bus([writer]), patch.object(os, 'geteuid', return_value=0), \
                        patch.dict(GLOBALS, PACMAN=command, update=update,
                                   held=Mock(return_value={}), preflight=preflight), \
                        patch.dict(os.environ, TEST_UPDATE_LOG=str(log), TEST_UPDATE_LEASE=str(writer),
                                   TEST_UPDATE_FAIL=fail, TEST_UPDATE_CANCEL=cancel), \
                        patch.object(sys, 'argv', [str(SCRIPT), '--noninteractive', *arguments]), \
                        contextlib.redirect_stderr(io.StringIO()):
                    result = MODULE['main']()
                evidence = b''
                while chunk := os.read(reader, 4096):
                    evidence += chunk
                with self.assertRaises(OSError):
                    os.fstat(writer)
                return result, log.read_text().splitlines() if log.exists() else [], evidence.splitlines()
            finally:
                os.close(reader)

    def test_full_update_holds_lease_through_sync_keyrings_and_upgrade(self):
        status, calls, held = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['-Sy', '-Qu', '-S', '-Su'])
        self.assertEqual(held, [b'-Sy', b'-S', b'-Su'])

    def test_failure_releases_lease_and_stops_later_transactions(self):
        for stage, expected in (('-Sy', ['-Sy']), ('-S', ['-Sy', '-Qu', '-S']),
                                ('-Su', ['-Sy', '-Qu', '-S', '-Su'])):
            with self.subTest(stage=stage):
                status, calls, held = self.invoke(fail=stage)
                self.assertEqual(status, 7)
                self.assertEqual(calls, expected)
                self.assertEqual(held[-1], stage.encode())

    def test_backend_cancellation_releases_lease(self):
        status, calls, held = self.invoke(cancel='-Su')
        self.assertEqual(status, 130)
        self.assertEqual(calls[-1], '-Su')
        self.assertEqual(held[-1], b'-Su')

    def test_preflight_failure_and_interrupt_release_before_backend(self):
        for error, expected in ((RuntimeError('preflight failed'), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__):
                status, calls, held = self.invoke(preflight_error=error)
                self.assertEqual((status, calls, held), (expected, [], []))

    def test_install_only_path_also_holds_lease(self):
        status, calls, held = self.invoke(arguments=('--install-only', 'extra/example'))
        self.assertEqual((status, calls, held), (0, ['-S'], [b'-S']))

    def test_backend_keeps_inherited_lease_after_updater_is_killed(self):
        reader, writer = os.pipe()
        with tempfile.TemporaryDirectory(prefix='emaki-update-owner-') as directory:
            backend = Path(directory) / 'backend.py'
            backend.write_text(
                'import os, signal, sys\n'
                'os.write(int(sys.argv[1]), str(os.getpid()).encode() + b"\\n")\n'
                'signal.pause()\n')
            child = os.fork()
            if child == 0:
                os.close(reader)
                try:
                    with fake_bus([writer]), lease.inhibit() as descriptor:
                        MODULE['run']([sys.executable, str(backend), str(descriptor)],
                                      inhibitor=descriptor)
                finally:
                    os._exit(0)
            os.close(writer)
            worker = None
            worker_fd = None
            try:
                self.assertTrue(select.select([reader], [], [], 5)[0], 'backend did not start')
                worker = int(os.read(reader, 64))
                worker_fd = os.pidfd_open(worker)
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
                child = None
                self.assertFalse(select.select([reader], [], [], 0.05)[0],
                                 'lease disappeared while the backend still ran')
                signal.pidfd_send_signal(worker_fd, signal.SIGTERM)
                self.assertTrue(select.select([reader], [], [], 5)[0], 'backend retained lease')
                self.assertEqual(os.read(reader, 1), b'')
            finally:
                if child is not None:
                    os.kill(child, signal.SIGKILL)
                    os.waitpid(child, 0)
                if worker_fd is not None:
                    try:
                        signal.pidfd_send_signal(worker_fd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    os.close(worker_fd)
                os.close(reader)

    def test_acquisition_failure_prevents_preflight_and_backend(self):
        with fake_bus([], error=RuntimeError('logind unavailable')), \
                patch.object(os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', [str(SCRIPT), '--noninteractive']), \
                patch.dict(GLOBALS, preflight=Mock(), run=Mock(), update=Mock()) as namespace, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MODULE['main'](), 1)
            for name in ('preflight', 'run', 'update'):
                namespace[name].assert_not_called()


@unittest.skipUnless(LIVE, 'pass --live to inspect the real system logind inhibitor list')
class LiveInhibitor(unittest.TestCase):
    def test_real_list_during_fake_transaction(self):
        listing = subprocess.run(['systemd-inhibit', '--list', '--no-pager'],
                                 capture_output=True, text=True)
        if listing.returncode:
            self.skipTest('system logind unavailable: ' + listing.stderr.strip())
        # No package manager runs: the transaction is a read-only list command.
        try:
            with lease.inhibit():
                during = subprocess.run(['systemd-inhibit', '--list', '--no-pager'],
                                        capture_output=True, text=True, check=True)
                self.assertIn('Updating Emaki', during.stdout)
                self.assertIn('shutdown', during.stdout)
                self.assertIn('sleep', during.stdout)
                self.assertIn('block', during.stdout)
                print(during.stdout)
        except RuntimeError as error:
            self.skipTest('system logind refused test inhibition: ' + str(error))


if __name__ == '__main__':
    unittest.main()
