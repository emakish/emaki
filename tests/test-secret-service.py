#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check native password-provider defaults and startup using isolated fixtures."""
import configparser
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def private_environment(base):
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               XDG_CONFIG_DIRS=str(ROOT / 'packaging/emaki-config'),
               QT_QPA_PLATFORM='offscreen', GSETTINGS_BACKEND='memory')
    for key in ('DBUS_SESSION_BUS_ADDRESS', 'GNOME_KEYRING_CONTROL',
                'SECRET_SERVICE_BUS_NAME', 'DISPLAY', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR'):
        Path(env[key]).mkdir(parents=True, mode=0o700)
    return env


class NativeProvider(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='emaki-secret-')
        self.addCleanup(tmp.cleanup)
        self.env = private_environment(Path(tmp.name))
        self.runtime = Path(self.env['XDG_RUNTIME_DIR'])
        self.helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-start'))

    def read_config(self, group, key):
        return subprocess.check_output(['kreadconfig6', '--file', 'kwalletrc', '--group',
            group, '--key', key, '--default', 'missing'], env=self.env, text=True).strip()

    @unittest.skipUnless(shutil.which('kreadconfig6'), 'needs KDE KConfig reader')
    def test_real_kconfig_enables_native_provider_without_migration(self):
        for group, key, expected in (('Wallet', 'Enabled', 'true'),
                                     ('KSecretD', 'Enabled', 'true'),
                                     ('org.freedesktop.secrets', 'apiEnabled', 'true'),
                                     ('Migration', 'MigrateTo3rdParty', 'false')):
            self.assertEqual(self.read_config(group, key), expected)
        self.assertFalse((Path(self.env['XDG_CONFIG_HOME']) / 'kwalletrc').exists())

    @unittest.skipUnless(shutil.which('kreadconfig6'), 'needs KDE KConfig reader')
    def test_real_kconfig_preserves_personal_choices(self):
        personal = Path(self.env['XDG_CONFIG_HOME']) / 'kwalletrc'
        personal.write_text('[Wallet]\nDefault Wallet=work\n[KSecretD]\nEnabled=false\n'
                            '[org.freedesktop.secrets]\napiEnabled=false\n')
        before = personal.read_bytes()
        self.assertEqual(self.read_config('Wallet', 'Default Wallet'), 'work')
        self.assertEqual(self.read_config('KSecretD', 'Enabled'), 'false')
        self.assertEqual(self.read_config('org.freedesktop.secrets', 'apiEnabled'), 'false')
        self.assertEqual(self.read_config('Migration', 'MigrateTo3rdParty'), 'false')
        with self.assertRaisesRegex(RuntimeError, 'disabled by your effective'):
            self.helper['check_settings'](self.env)
        self.assertEqual(personal.read_bytes(), before)

    def test_pam_unlock_and_secret_portal_select_native_wallet(self):
        pam = (ROOT / 'greetd/pam').read_text() + (ROOT / 'greetd/pam-auth').read_text()
        self.assertIn('auth       optional    pam_kwallet5.so', pam)
        self.assertIn('pam_kwallet5.so kwalletd=/usr/bin/ksecretd', pam)
        self.assertNotIn('auto_start', pam)
        self.assertNotIn('force_run', pam)
        self.assertIn('[success=ignore ignore=ignore default=2]', pam)
        self.assertIn('--password-account', pam)
        self.assertIn('expose_authtok', pam)
        self.assertNotIn('pam_gnome_keyring', pam)
        portal = configparser.ConfigParser()
        portal.read(ROOT / 'packaging/emaki-config/niri-portals.conf')
        self.assertEqual(portal['preferred']['org.freedesktop.impl.portal.Secret'], 'kwallet;')
        self.assertEqual(portal['preferred']['org.freedesktop.impl.portal.ScreenCast'], 'gnome;')

    def test_pam_init_precedes_activation_reload_and_owner_verification(self):
        events = []
        def call(method, *args):
            events.append((method, *args))
            if method == 'ReloadConfig':
                path = self.helper['activation_path'](self.runtime)
                self.assertEqual(path.read_text(), self.helper['ACTIVATION'])
                return ''
            return 's ":1.42"'
        def run(argv, **kwargs):
            events.append(('run', *argv))
            self.assertTrue(kwargs['check'])
            self.assertEqual(kwargs['env']['PAM_KWALLET5_LOGIN'], '/private/login.sock')
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        self.helper['start'](self.env, self.runtime, run, call, mock.Mock())
        self.assertEqual(events[:2], [('ReloadConfig',), ('run', '/usr/lib/pam_kwallet_init')])
        self.assertEqual([event[-1] for event in events[2:]], [
            'org.kde.ksecretd', 'org.freedesktop.secrets',
            'org.freedesktop.impl.portal.desktop.kwallet'])

    def test_terminal_login_activates_portal_without_pam_password(self):
        self.env.pop('PAM_KWALLET5_LOGIN', None)
        call = mock.Mock(return_value='s ":1.42"')
        run = mock.Mock()
        self.helper['start'](self.env, self.runtime, run, call, mock.Mock())
        self.assertEqual(run.call_args.args[0][-1], '/usr/bin/ksecretd')
        self.assertEqual(call.call_args_list[0], mock.call('ReloadConfig'))

    def test_gnome_owner_collision_is_not_success(self):
        def call(method, *args):
            return 's ":1.17"' if args[-1] == 'org.freedesktop.secrets' else 's ":1.42"'
        self.assertFalse(self.helper['same_provider'](call))
        failure = mock.Mock(side_effect=subprocess.CalledProcessError(1, 'busctl'))
        self.assertFalse(self.helper['same_provider'](failure))

    def test_startup_timeout_keeps_provider_selection(self):
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        def call(method, *args):
            if method == 'ReloadConfig':
                return ''
            raise subprocess.CalledProcessError(1, 'busctl')
        sleep = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'old password files are untouched'):
            self.helper['start'](self.env, self.runtime, mock.Mock(), call, sleep)
        self.assertEqual(sleep.call_count, 100)
        self.assertTrue(self.helper['activation_path'](self.runtime).exists())
        unit = (ROOT / 'systemd/emaki-wallet-start.service').read_text()
        self.assertIn('ExecStop=/usr/bin/emaki-wallet-start --stop', unit)
        self.assertNotIn('ExecStopPost=', unit)

    def test_disabled_provider_discards_pending_pam(self):
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        run = mock.Mock()
        discard = mock.Mock()
        with mock.patch.dict(self.helper['start'].__globals__,
                check_settings=mock.Mock(side_effect=RuntimeError('disabled')),
                discard_pending_pam=discard):
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                self.helper['start'](self.env, self.runtime, run, mock.Mock(), mock.Mock())
        run.assert_not_called()
        discard.assert_called_once_with(self.env)
        self.assertTrue(self.helper['activation_path'](self.runtime).exists())

    def test_prepare_migrates_before_compositor_and_stops_old_owner_first(self):
        events = []
        def run(argv, **kw):
            events.append(argv)
            return subprocess.CompletedProcess(argv, 0, 'loaded')
        self.helper['prepare'](self.env, self.runtime, run, mock.Mock())
        self.assertEqual(events[2][:4], ['systemctl', '--user', 'mask', '--runtime'])
        self.assertEqual(events[2][4:], ['gnome-keyring-daemon.socket', 'gnome-keyring-daemon.service'])
        self.assertEqual(events[3], ['systemctl', '--user', 'stop', 'gnome-keyring-daemon.socket'])
        self.assertEqual(events[4], ['systemctl', '--user', 'stop', 'gnome-keyring-daemon.service'])
        self.assertEqual(events[5], ['/usr/bin/emaki-wallet-migrate', '--snapshot'])
        for unit in ('systemd/niri-emaki.service', 'systemd/niri-wallet.conf'):
            self.assertIn('ExecStartPre=-/usr/bin/emaki-wallet-start --prepare', (ROOT / unit).read_text())
        self.assertIn('Exec=/usr/bin/emaki-wallet-start --wait', self.helper['ACTIVATION'])
        self.assertNotIn('Exec=/usr/bin/ksecretd', self.helper['ACTIVATION'])

    def test_fresh_install_does_not_stop_absent_old_units(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'not-found'))
        self.helper['prepare'](self.env, self.runtime, run, mock.Mock())
        self.assertFalse(any('stop' in call.args[0] for call in run.call_args_list))
        self.assertEqual(run.call_args.args[0], ['/usr/bin/emaki-wallet-migrate', '--snapshot'])

    def test_failure_preserves_matching_file_not_created_by_this_session(self):
        path = self.helper['activation_path'](self.runtime)
        path.write_text(self.helper['ACTIVATION'])
        run = mock.Mock(side_effect=subprocess.CalledProcessError(1, 'systemctl'))
        with self.assertRaises(subprocess.CalledProcessError):
            self.helper['prepare'](self.env, self.runtime, run, mock.Mock())
        self.assertEqual(path.read_text(), self.helper['ACTIVATION'])

    def test_prepare_failure_keeps_activation_override(self):
        run = mock.Mock(side_effect=subprocess.CalledProcessError(1, 'systemctl'))
        call = mock.Mock()
        with self.assertRaises(subprocess.CalledProcessError):
            self.helper['prepare'](self.env, self.runtime, run, call)
        self.assertTrue(self.helper['activation_path'](self.runtime).exists())
        self.assertEqual(call.call_args_list, [mock.call('ReloadConfig')])

    def test_disabled_provider_keeps_activation_blocked(self):
        check = self.helper['prepare'].__globals__
        with mock.patch.dict(check, check_settings=mock.Mock(side_effect=RuntimeError('disabled'))):
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                self.helper['prepare'](self.env, self.runtime, mock.Mock(), mock.Mock())
        self.assertTrue(self.helper['activation_path'](self.runtime).exists())

    def test_stop_releases_pam_before_status_checks(self):
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        (self.runtime / 'emaki').mkdir()
        (self.runtime / 'emaki/wallet-start-error').symlink_to(self.runtime / 'private')
        release = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch('sys.argv', ['emaki-wallet-start', '--stop']), \
                mock.patch.dict(self.helper['main'].__globals__, release_pending_pam=release):
            self.assertEqual(self.helper['main'](), 0)
        release.assert_called_once()

    def test_status_setup_failure_still_releases_pam(self):
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        (self.runtime / 'emaki').write_text('not a directory')
        release = mock.Mock()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch('sys.argv', ['emaki-wallet-start']), \
                mock.patch.dict(self.helper['main'].__globals__, release_pending_pam=release), \
                mock.patch('sys.stderr'):
            self.assertEqual(self.helper['main'](), 1)
        release.assert_called_once()

    def test_stale_pam_socket_cleanup_never_unlocks_wallet(self):
        run = mock.Mock()
        self.helper['release_pending_pam']({'PAM_KWALLET5_LOGIN': '/gone'}, run)
        run.assert_not_called()
        for unit in ('systemd/niri-emaki.service', 'systemd/niri-wallet.conf'):
            contents = (ROOT / unit).read_text()
            self.assertIn('ExecStopPost=-/usr/bin/emaki-wallet-start --stop', contents)
            self.assertIn('ExecStopPost=-/usr/bin/systemctl --user unset-environment PAM_KWALLET5_LOGIN', contents)

    def test_discard_identifies_only_the_pending_socket_holder(self):
        proc = self.runtime / 'proc'
        (proc / 'net').mkdir(parents=True)
        address = self.runtime / 'pam.sock'
        address.touch()
        (proc / 'net/unix').write_text('header\n0: 0 0 0 0 0 654 ' + str(address) + '\n')
        for pid, inode in (('101', '654'), ('102', '655')):
            directory = proc / pid
            (directory / 'fd').mkdir(parents=True)
            (directory / 'cmdline').write_bytes(b'\0'.join((b'/usr/bin/ksecretd', b'--pam-login', b'4', b'5', b'')))
            (directory / 'exe').symlink_to('/usr/bin/ksecretd')
            (directory / 'fd/5').symlink_to('socket:[' + inode + ']')
        with mock.patch('os.pidfd_open', return_value=999) as pidfd, \
                mock.patch('signal.pidfd_send_signal') as kill, mock.patch('os.close'):
            self.helper['discard_pending_pam']({'PAM_KWALLET5_LOGIN': str(address)}, proc)
        pidfd.assert_called_once_with(101)
        kill.assert_called_once_with(999, 9)

    def test_empty_password_and_locked_accounts_skip_optional_auth(self):
        shadow = self.runtime / 'shadow'
        shadow.write_text('empty::1::::::\nlocked:!hash:1::::::\nregular:$y$hash:1::::::\n')
        for user, expected in [('empty', False), ('locked', False), ('regular', True), ('missing', False)]:
            self.assertEqual(self.helper['password_account']({'PAM_USER': user}, shadow), expected)
        session = (ROOT / 'scripts/niri-emaki-session').read_text()
        self.assertIn('unset-environment PAM_KWALLET5_LOGIN', session)

    def test_flush_locks_default_collection_without_activation(self):
        # KWallet 6.30 Secret Service: ReadAlias(s)->o and Lock(ao)->(ao,o).
        collection = '/org/freedesktop/secrets/collection/kdewallet'
        run = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0, 'b true'),
            subprocess.CompletedProcess([], 0, 'o "' + collection + '"'),
            subprocess.CompletedProcess([], 0, 'v b false'),
            subprocess.CompletedProcess([], 0, '')])
        self.helper['flush'](run)
        self.assertEqual(run.call_args_list[-1].args[0][-7:],
            ['org.freedesktop.secrets', '/org/freedesktop/secrets',
             'org.freedesktop.Secret.Service', 'Lock', 'ao', '1', collection])
        for request in run.call_args_list:
            self.assertIn('--auto-start=no', request.args[0])
            self.assertLessEqual(request.kwargs['timeout'], 1.2)
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'b false'))
        self.helper['flush'](run)
        self.assertEqual(run.call_count, 1)

    def test_flush_already_locked_collection_is_saved_without_second_lock(self):
        collection = '/org/freedesktop/secrets/collection/kdewallet'
        run = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0, 'b true'),
            subprocess.CompletedProcess([], 0, 'o "' + collection + '"'),
            subprocess.CompletedProcess([], 0, 'v b true')])
        self.helper['flush'](run)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args.args[0][-7:],
            ['org.freedesktop.secrets', collection, 'org.freedesktop.DBus.Properties',
             'Get', 'ss', 'org.freedesktop.Secret.Collection', 'Locked'])

    def test_locked_or_unread_legacy_keys_gate_all_activation_names(self):
        old = Path(self.env['XDG_DATA_HOME']) / 'keyrings'
        old.mkdir()
        (old / 'login.keyring').touch()
        self.env['PAM_KWALLET5_LOGIN'] = '/private/login.sock'
        run = mock.Mock()
        discard = mock.Mock()
        with mock.patch.dict(self.helper['start'].__globals__, discard_pending_pam=discard):
            with self.assertRaisesRegex(RuntimeError, 'Saved application keys'):
                self.helper['start'](self.env, self.runtime, run, mock.Mock())
        run.assert_not_called()
        discard.assert_called_once_with(self.env)
        services = self.helper['activation_path'](self.runtime).parent
        for name in ('org.freedesktop.secrets', 'org.freedesktop.impl.portal.desktop.kwallet',
                     'org.kde.secretservicecompat'):
            content = (services / (name + '.service')).read_text()
            self.assertIn('Name=' + name, content)
            self.assertIn('Exec=/usr/bin/emaki-wallet-start --wait', content)
        for name in ('org.gnome.keyring', 'org.freedesktop.impl.portal.Secret'):
            legacy = (services / (name + '.service')).read_text()
            self.assertIn('Name=' + name, legacy)
            self.assertIn('Exec=/usr/bin/false', legacy)
        self.env['XDG_STATE_HOME'] = str(self.runtime / 'state')
        report = Path(self.env['XDG_STATE_HOME']) / 'emaki/wallet-migration-v1.json'
        report.parent.mkdir(parents=True)
        report.write_text('{"portal_keys_safe": true, "complete": false}')
        self.assertTrue(self.helper['migration_allowed'](self.env))

    def test_active_private_writer_prevents_public_start(self):
        run = mock.Mock()
        with self.helper['writer_lock'](self.env):
            with self.assertRaisesRegex(RuntimeError, 'still stopping'):
                self.helper['start'](self.env, self.runtime, run, mock.Mock())
        run.assert_not_called()

    def test_stop_keeps_all_runtime_activation_overrides(self):
        self.helper['select_provider'](self.runtime, mock.Mock())
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch('sys.argv', ['emaki-wallet-start', '--stop']):
            self.assertEqual(self.helper['main'](), 0)
        self.assertEqual(len(list(self.helper['activation_path'](self.runtime).parent.glob('*.service'))), 5)

    def test_personal_activation_file_or_symlink_is_not_clobbered(self):
        path = self.helper['activation_path'](self.runtime)
        personal = self.runtime / 'personal.service'
        personal.write_text('personal provider')
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    path.symlink_to(personal)
                else:
                    path.write_text('personal provider')
                call = mock.Mock()
                with self.assertRaisesRegex(RuntimeError, 'personal Secret Service'):
                    self.helper['select_provider'](self.runtime, call)
                self.assertEqual(path.read_text(), 'personal provider')
                self.assertEqual(path.is_symlink(), symlink)
                call.assert_not_called()
                path.unlink()
        self.assertEqual(personal.read_text(), 'personal provider')

    def test_personal_runtime_directory_symlink_is_not_used(self):
        personal = self.runtime / 'personal'
        personal.mkdir()
        (self.runtime / 'dbus-1').symlink_to(personal, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'not owned by this session'):
            self.helper['select_provider'](self.runtime, mock.Mock())
        self.assertEqual(list(personal.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
