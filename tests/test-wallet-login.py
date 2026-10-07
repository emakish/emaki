#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise automatic migration without touching a real wallet or session bus."""
import os
import configparser
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class LoginMigration(unittest.TestCase):
    def test_login_hook_and_installed_help(self):
        unit = (ROOT / 'systemd/emaki-wallet-migrate.service').read_text()
        self.assertIn('Before=xdg-desktop-autostart.target emaki-shell.service', unit)
        self.assertIn('ExecStart=/usr/bin/emaki-wallet-migrate --automatic', unit)
        session = configparser.ConfigParser(interpolation=None, strict=False)
        session.read(ROOT / 'systemd/niri-emaki.service')
        # One Wants= line: configparser (and the desktop-devices test) keep only the last of repeated keys.
        self.assertIn('emaki-wallet-migrate.service', session['Unit']['Wants'].split())
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('systemd/emaki-wallet-migrate.service', makefile)
        self.assertIn('install -Dm644 docs/ZONES.md', makefile)
        self.assertIn('/usr/lib/systemd/user/emaki-wallet-migrate.service',
                      (ROOT / 'packaging/emaki-config/expected-files.list').read_text())
        help_text = subprocess.check_output([str(ROOT / 'scripts/emaki-wallet-migrate'), '--help'], text=True)
        self.assertIn('/usr/share/doc/emaki/ZONES.md', help_text)
        self.assertIn('Cancel skips an attempt.', help_text)
        self.assertIn('emaki-wallet-migrate', (ROOT / 'docs/ZONES.md').read_text())

    def test_notice_ends_busctl_options_before_its_arguments(self):
        # busctl reads the trailing expire timeout -1 as an option unless `--` ends the options
        # (systemd 262: "unrecognized option '-1'"), and then the notice never appears.
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        calls = []
        with mock.patch.object(subprocess, 'run', side_effect=lambda argv, **_: calls.append(argv)):
            helper['notify']('Old passwords were not moved.')
        argv, = calls
        self.assertEqual(argv[:4], ['busctl', '--user', 'call', '--'])
        self.assertEqual(argv[-1], '-1')
        self.assertGreater(argv.index('-1'), argv.index('--'))

    def test_three_distinct_logins_then_one_notice_and_explicit_retry(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_STATE_HOME=tmp + '/state',
                       XDG_CONFIG_HOME=tmp + '/config', XDG_DATA_HOME=tmp + '/data',
                       XDG_CONFIG_DIRS=str(ROOT / 'packaging/emaki-config'))
            old = Path(env['XDG_DATA_HOME']) / 'kwalletd/kdewallet.kwl'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'old encrypted wallet must remain byte-for-byte intact')
            attempt = mock.Mock(return_value='')
            notify = mock.Mock()
            for login in range(1, 6):
                env['EMAKI_WALLET_SESSION'] = str(login)
                for _ in range(2):
                    helper['run_login'](env, attempt, notify)
                self.assertEqual(attempt.call_count, min(login, 3))
            notify.assert_called_once()
            self.assertIn('emaki-wallet-migrate', notify.call_args.args[0])
            self.assertEqual(old.read_bytes(), b'old encrypted wallet must remain byte-for-byte intact')
            self.assertFalse((Path(env['XDG_CONFIG_HOME']) / 'kwalletrc').exists())
            helper['run_login'](env, attempt, notify, explicit=True)
            self.assertEqual(attempt.call_count, 4)

    def test_passwordless_success_is_automatic_and_silent(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_STATE_HOME=tmp + '/state',
                       XDG_CONFIG_HOME=tmp + '/config', XDG_DATA_HOME=tmp + '/data',
                       XDG_CONFIG_DIRS=str(ROOT / 'packaging/emaki-config'),
                       EMAKI_WALLET_SESSION='first-login')
            old = Path(env['XDG_DATA_HOME']) / 'kwalletd/kdewallet.kwl'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'untouched legacy fixture')
            attempt = mock.Mock(return_value='kdewallet')
            notify = mock.Mock()
            helper['run_login'](env, attempt, notify)
            env['EMAKI_WALLET_SESSION'] = 'second-login'
            helper['run_login'](env, attempt, notify)
            attempt.assert_called_once()
            notify.assert_not_called()
            self.assertEqual(old.read_bytes(), b'untouched legacy fixture')

    def test_crashed_attempt_is_reserved_and_unknown_state_is_untouched(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_STATE_HOME=tmp + '/state',
                       XDG_CONFIG_HOME=tmp + '/config', XDG_DATA_HOME=tmp + '/data',
                       XDG_CONFIG_DIRS=str(ROOT / 'packaging/emaki-config'),
                       EMAKI_WALLET_SESSION='first-login')
            old = Path(env['XDG_DATA_HOME']) / 'kwalletd/kdewallet.kwl'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'untouched')
            attempt = mock.Mock(side_effect=RuntimeError('daemon failed'))
            with self.assertRaisesRegex(RuntimeError, 'daemon failed'):
                helper['run_login'](env, attempt, mock.Mock())
            helper['run_login'](env, attempt, mock.Mock())
            attempt.assert_called_once()
            state = Path(env['XDG_STATE_HOME']) / 'emaki/wallet-migration.json'
            self.assertEqual(json.loads(state.read_text())['attempts'], 1)
            state.write_text('{"version": 99}')
            with self.assertRaisesRegex(SystemExit, 'newer Emaki version'):
                helper['run_login'](env, attempt, mock.Mock())
            self.assertEqual(state.read_text(), '{"version": 99}')

    def test_native_wallet_filename_and_completion_escaping(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_DATA_HOME=tmp)
            directory = Path(tmp) / 'kwalletd'
            directory.mkdir()
            for name in ('work;2Cwallet', 'path;5Cwallet', 'literal%20wallet', '.hidden'):
                (directory / (name + '.kwl')).write_bytes(b'unchanged')
            self.assertEqual(helper['remaining_wallets'](
                env, r'work\,wallet,path\\wallet,literal%20wallet,.hidden'), [])

    def test_failed_notification_retries_without_another_migration(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_STATE_HOME=tmp + '/state',
                       XDG_DATA_HOME=tmp + '/data', EMAKI_WALLET_SESSION='fourth-login')
            old = Path(env['XDG_DATA_HOME']) / 'kwalletd/kdewallet.kwl'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'unchanged')
            state = Path(env['XDG_STATE_HOME']) / 'emaki/wallet-migration.json'
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps(dict(version=1, attempts=3, session='third-login',
                                            notified=False, migrated='')))
            attempt, notice = mock.Mock(), mock.Mock(side_effect=RuntimeError('not ready'))
            with self.assertRaisesRegex(RuntimeError, 'not ready'):
                helper['run_login'](env, attempt, notice)
            notice.side_effect = None
            helper['run_login'](env, attempt, notice)
            helper['run_login'](env, attempt, notice)
            attempt.assert_not_called()
            self.assertEqual(notice.call_count, 2)


if __name__ == '__main__':
    unittest.main()
