#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test private wallet startup without binding sockets or reading personal files."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class WalletStage(unittest.TestCase):
    def setUp(self):
        self.helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-stage'))
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='wallet-stage-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        salt = self.home / '.local/share/kwalletd/kdewallet.salt'
        salt.parent.mkdir(parents=True)
        salt.write_bytes(bytes(range(56)))
        self.target = self.root / 'target'
        self.target.mkdir(mode=0o700)

    def wallet_double(self, transferred=False):
        collection = '/org/freedesktop/secrets/collection/kdewallet'
        locked = False
        reads = 0
        def call(path, interface, method, *args):
            nonlocal locked, reads
            if method == 'ReadAlias':
                return 'o ' + json.dumps(collection)
            if method == 'Get':
                reads += 1
                if transferred and reads > 1:
                    locked = True
                return 'v b ' + str(locked).lower()
            if method == 'Lock':
                if locked or transferred:
                    raise subprocess.CalledProcessError(1, 'Lock')
                locked = True
                return 'aoo 1 ' + json.dumps(collection) + ' "/"'
            raise AssertionError(method)
        return mock.Mock(side_effect=call)

    def test_hash_matches_pam_parameters(self):
        salt = bytes(range(56))
        self.assertEqual(self.helper['derive_key'](b'fixture password', salt),
            hashlib.pbkdf2_hmac('sha512', b'fixture password', salt, 50000, 56))
        with self.assertRaises(RuntimeError):
            self.helper['derive_key'](b'fixture password', b'too short')

    def test_missing_salt_created_only_for_a_new_wallet(self):
        salt = self.home / '.local/share/kwalletd/kdewallet.salt'
        salt.unlink()
        custom = self.root / 'data'
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, XDG_DATA_HOME=str(custom)):
            first = self.helper['wallet_salt']()
            self.assertEqual(len(first), 56)
            self.assertEqual(salt.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.helper['wallet_salt'](), first)
            salt.unlink()
            wallet = custom / 'kwalletd/kdewallet.kwl'
            wallet.parent.mkdir(parents=True)
            wallet.write_bytes(b'encrypted wallet fixture')
            with self.assertRaisesRegex(RuntimeError, 'salt is missing'):
                self.helper['wallet_salt']()
            self.assertFalse(salt.exists())
            self.assertEqual(wallet.read_bytes(), b'encrypted wallet fixture')

    def test_private_target_uses_unnamed_password_pipe_and_flushes(self):
        self.check_stage_flush(False)

    def test_transfer_already_locked_the_private_target(self):
        self.check_stage_flush(True)

    def check_stage_flush(self, transferred):
        (self.target / 'done').touch()
        process = mock.Mock()
        process.poll.return_value = None
        listener = mock.MagicMock()
        listener.fileno.return_value = 77
        # kwalletfreedesktopservice.cpp 6.30: ReadAlias -> collection path;
        # Properties.Get(Collection, Locked) -> boolean; Lock(ao) closes it.
        collection = '/org/freedesktop/secrets/collection/kdewallet'
        wallet = self.wallet_double(transferred)
        password = b'fixture password'
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture/private'), \
                mock.patch('sys.stdin', mock.Mock(buffer=io.BytesIO(password))), \
                mock.patch('socket.socket', return_value=listener), \
                mock.patch('subprocess.Popen', return_value=process) as launch, \
                mock.patch('os.write', return_value=56) as write, \
                mock.patch.dict(self.helper['stage'].__globals__, secret_call=wallet):
            self.helper['stage'](self.target)
        self.assertEqual(write.call_args.args[1], self.helper['derive_key'](password, bytes(range(56))))
        self.assertNotIn(password.decode(), str(launch.call_args))
        self.assertEqual(launch.call_args.args[0][:2], ['ksecretd', '--pam-login'])
        self.assertEqual(launch.call_args.kwargs['env']['QT_QPA_PLATFORM'], 'offscreen')
        self.assertEqual(len(launch.call_args.kwargs['pass_fds']), 3)
        self.assertEqual(wallet.call_args_list[0], mock.call('/org/freedesktop/secrets',
            'org.freedesktop.Secret.Service', 'ReadAlias', 's', 'default'))
        self.assertEqual(wallet.call_args_list[1], mock.call(collection,
            'org.freedesktop.DBus.Properties', 'Get', 'ss', 'org.freedesktop.Secret.Collection', 'Locked'))
        ready = json.loads((self.target / 'ready').read_text())
        self.assertEqual(ready, {'address': 'unix:path=/fixture/private', 'collection': collection})
        self.assertEqual((self.target / 'ready').stat().st_mode & 0o777, 0o600)
        locks = [call for call in wallet.call_args_list if call.args[2] == 'Lock']
        self.assertEqual(len(locks), 0 if transferred else 1)
        self.assertEqual(wallet.call_count, 3 if transferred else 4)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)

    def test_unresponsive_private_daemon_is_killed_with_bounded_waits(self):
        (self.target / 'done').touch()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('ksecretd', 3), 0]
        listener = mock.MagicMock()
        listener.fileno.return_value = 77
        collection = '/org/freedesktop/secrets/collection/kdewallet'
        wallet = self.wallet_double()
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, DBUS_SESSION_BUS_ADDRESS='unix:path=/fixture/private'), \
                mock.patch('sys.stdin', mock.Mock(buffer=io.BytesIO(b'fixture password'))), \
                mock.patch('socket.socket', return_value=listener), \
                mock.patch('subprocess.Popen', return_value=process), \
                mock.patch('os.write', return_value=56), \
                mock.patch.dict(self.helper['stage'].__globals__, secret_call=wallet):
            self.helper['stage'](self.target)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=3), mock.call(timeout=3)])

    def test_cancelled_stage_never_launches_daemon(self):
        (self.target.parent / 'done').touch()
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.helper['stage'](self.target)
        launch.assert_not_called()

    def test_active_writer_excludes_private_stage(self):
        directory = self.home / '.cache/emaki-wallet-login'
        directory.mkdir(mode=0o700, parents=True)
        with (directory / 'stage.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(Path, 'home', return_value=self.home), \
                    mock.patch('subprocess.Popen') as launch:
                with self.assertRaises(BlockingIOError):
                    self.helper['stage'](self.target)
        launch.assert_not_called()

    def test_existing_public_wallet_excludes_private_stage(self):
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, EMAKI_DESKTOP_BUS='unix:path=/fixture/desktop'), \
                mock.patch('subprocess.check_output', return_value='b true') as call, \
                mock.patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.helper['stage'](self.target)
        launch.assert_not_called()
        self.assertIn('--address=unix:path=/fixture/desktop', call.call_args.args[0])
        self.assertEqual(call.call_args.args[0][-3:], ['NameHasOwner', 's', 'org.kde.ksecretd'])

    def test_target_with_public_permissions_is_rejected(self):
        self.target.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'Invalid migration directory'):
            self.helper['stage'](self.target)

    def test_panel_flush_precedes_each_session_exit(self):
        with mock.patch.object(sys, 'path', [str(ROOT / 'shell/helpers'), *sys.path]):
            module = runpy.run_path(str(ROOT / 'shell/helpers/system-tools.py'))
        for action in ('logout', 'reboot', 'poweroff'):
            run = mock.Mock()
            with mock.patch.dict(module['operation'].__globals__, run=run):
                self.assertEqual(module['operation']({'op': 'session', 'value': action,
                                 'confirmed': True}), {'state': 'requested'})
            self.assertEqual(run.call_args_list[0], mock.call('timeout', ['--kill-after=1s', '3s', 'emaki-wallet-start', '--flush']))
            if action == 'logout':
                self.assertEqual(run.call_args_list[1], mock.call('niri', ['msg', 'action', 'quit', '--skip-confirmation']))
            else:
                self.assertEqual(run.call_args_list[1], mock.call('systemctl', [action]))

    def test_panel_flush_failure_still_performs_exit(self):
        with mock.patch.object(sys, 'path', [str(ROOT / 'shell/helpers'), *sys.path]):
            module = runpy.run_path(str(ROOT / 'shell/helpers/system-tools.py'))
        operations = []
        def run(name, arguments):
            operations.append((name, arguments))
            if name == 'timeout':
                raise module['shared'].Refused('operation_failed')
        for action in ('logout', 'reboot', 'poweroff'):
            operations.clear()
            with mock.patch.dict(module['operation'].__globals__, run=run):
                self.assertEqual(module['operation']({'op': 'session', 'value': action, 'confirmed': True}),
                                 {'state': 'requested'})
            self.assertEqual(operations[0], ('timeout', ['--kill-after=1s', '3s', 'emaki-wallet-start', '--flush']))
            self.assertEqual(operations[1][0], 'niri' if action == 'logout' else 'systemctl')


if __name__ == '__main__':
    unittest.main()
