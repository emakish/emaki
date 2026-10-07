#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check KConfig defaults; --runtime exercises the real bridge on a private bus.

The runtime check supplies a nonempty throwaway keyring password so it can
create and unlock its login keyring without a display. Encrypted legacy-wallet migration still needs the VM check.
"""
import os
import io
import runpy
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import reaper

reaper.guard()

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'packaging/emaki-config/kwalletrc'


def run(*command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True,
                          timeout=20, **kwargs).stdout.strip()


def private_environment(base):
    env = os.environ.copy()
    for key in ('DBUS_SESSION_BUS_ADDRESS', 'GNOME_KEYRING_CONTROL',
                'SECRET_SERVICE_BUS_NAME', 'DISPLAY', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    env.update(HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'home/config'),
               XDG_DATA_HOME=str(base / 'home/data'), XDG_CACHE_HOME=str(base / 'home/cache'),
               XDG_RUNTIME_DIR=str(base / 'runtime'),
               XDG_CONFIG_DIRS=str(CONFIG.parent), QT_QPA_PLATFORM='offscreen',
               GSETTINGS_BACKEND='memory')
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR'):
        Path(env[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
    return env


class Defaults(unittest.TestCase):
    def test_real_kwallet_settings_keep_consumed_gate_after_snapshot_save(self):
        # Compile the installed schema with the same KConfig generator as KWallet.
        # A stale migration snapshot must not restore the consumed one-shot gate.
        with tempfile.TemporaryDirectory(prefix='emaki-wallet-skeleton-') as tmp:
            base = Path(tmp)
            env = private_environment(base)
            schema = Path('/usr/share/config.kcfg/kwalletsettings.kcfg')
            compiler = '/usr/lib/kf6/kconfig_compiler_kf6'
            self.assertTrue(schema.is_file(), 'The installed KWallet schema is required')
            self.assertTrue(Path(compiler).is_file(), 'The KConfig compiler is required')
            shutil.copyfile(schema, base / schema.name)
            (base / 'kwalletsettings.kcfgc').write_text(
                'File=kwalletsettings.kcfg\nClassName=KWalletSettings\nMutators=true\n')
            run(compiler, str(base / schema.name), str(base / 'kwalletsettings.kcfgc'),
                '-d', str(base), env=env)
            source = base / 'probe.cpp'
            source.write_text(r'''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QCoreApplication>
#include <QProcess>
#include "kwalletsettings.h"

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QStringList original = {QStringLiteral("work,wallet"),
                                  QStringLiteral("path\\wallet")};
    for (const bool completed : {false, true}) {
        {
            KWalletSettings seed;
            seed.setMigrateTo3rdParty(true);
            seed.setWalletsMigratedToSecretService(original);
            if (!seed.save()) return 1;
        }
        {
            KWalletSettings migration;
            if (!migration.migrateTo3rdParty()) return 2;
            if (QProcess::execute(QStringLiteral("kwriteconfig6"), {
                    QStringLiteral("--file"), QStringLiteral("kwalletrc"),
                    QStringLiteral("--group"), QStringLiteral("Migration"),
                    QStringLiteral("--key"), QStringLiteral("MigrateTo3rdParty"),
                    QStringLiteral("false")}) != 0) return 3;
            QStringList progress = migration.walletsMigratedToSecretService();
            if (completed) progress.append(QStringLiteral("new-wallet"));
            migration.setWalletsMigratedToSecretService(progress);
            if (!migration.save()) return 4;
        }
        {
            KWalletSettings next;
            if (next.migrateTo3rdParty()) return 5;
            QStringList expected = original;
            if (completed) expected.append(QStringLiteral("new-wallet"));
            if (next.walletsMigratedToSecretService() != expected) return 6;
        }
    }
    return 0;
}
''')
            flags = shlex.split(run('pkg-config', '--cflags', '--libs', 'Qt6Core', 'Qt6Gui'))
            run('c++', '-fPIC', str(source), str(base / 'kwalletsettings.cpp'),
                '-I/usr/include/KF6/KConfigGui', '-I/usr/include/KF6/KConfigCore',
                '-I/usr/include/KF6/KConfig', *flags, '-lKF6ConfigCore', '-lKF6ConfigGui',
                '-o', str(base / 'probe'), env=env)
            run(str(base / 'probe'), env=env)
            self.assertEqual(run('kreadconfig6', '--file', 'kwalletrc', '--group',
                                 'Migration', '--key', 'MigrateTo3rdParty', env=env), 'false')

    def test_real_kconfig_selects_one_provider_without_writing_user_config(self):
        with tempfile.TemporaryDirectory(prefix='emaki-secret-config-') as tmp:
            env = private_environment(Path(tmp))
            personal = Path(env['XDG_CONFIG_HOME']) / 'kwalletrc'
            personal.write_text('[Wallet]\nDefault Wallet=existing-wallet\n')
            before = personal.read_bytes()
            for group, key, expected in (('KSecretD', 'Enabled', 'false'),
                                          ('Migration', 'MigrateTo3rdParty', 'false'),
                                          ('Wallet', 'Default Wallet', 'existing-wallet')):
                self.assertEqual(run('kreadconfig6', '--file', 'kwalletrc', '--group', group,
                                     '--key', key, '--default', 'missing', env=env), expected)
            self.assertEqual(personal.read_bytes(), before)

    def test_explicit_attempt_preserves_settings_and_bounds_cancel(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as tmp:
                env = private_environment(Path(tmp))
                helper['config_write'](helper['KEY'], 'already,work\\,wallet,path\\\\wallet', env)
                run('kwriteconfig6', '--file', 'kwalletrc', '--group', 'Wallet',
                    '--key', 'Default Wallet', 'personal-wallet', env=env)
                personal = Path(env['XDG_CONFIG_HOME']) / 'kwalletrc'
                original = personal.read_bytes()
                overlays = []
                daemon = mock.Mock()
                daemon.stderr = io.StringIO('')

                def launch(command, **kwargs):
                    self.assertEqual(command, ['timeout', '--foreground', '--signal=TERM',
                                              '--kill-after=5', '300', 'kwalletd6'])
                    overlay = kwargs['env']
                    overlays.append(overlay)
                    self.assertEqual(kwargs.get('stderr'), subprocess.PIPE)
                    self.assertTrue(kwargs.get('text'))
                    self.assertEqual(helper['config_read']('MigrateTo3rdParty', overlay), 'true')
                    self.assertEqual(run('kreadconfig6', '--file', 'kwalletrc', '--group',
                                         'Wallet', '--key', 'Default Wallet', env=overlay),
                                     'personal-wallet')
                    self.assertEqual(helper['config_read']('MigrateTo3rdParty', env), 'false')
                    return daemon

                def wait():
                    if cancelled:
                        raise KeyboardInterrupt()
                    helper['config_write'](helper['KEY'], 'already,work\\,wallet,path\\\\wallet,new', overlays[0])

                result = helper['attempt'](env, wait, launch)
                daemon.terminate.assert_called_once()
                daemon.wait.assert_called_once_with(timeout=7)
                self.assertFalse(Path(overlays[0]['XDG_CONFIG_HOME']).exists())
                self.assertEqual(helper['config_read']('MigrateTo3rdParty', env), 'false')
                self.assertEqual(result, 'already,work\\,wallet,path\\\\wallet' +
                                 ('' if cancelled else ',new'))
                self.assertEqual(personal.read_bytes(), original)

    def test_native_migration_warning_consumes_gate_before_progress_save(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory(prefix='emaki-wallet-warning-') as tmp:
            base = Path(tmp)
            env = private_environment(base)
            personal = Path(env['XDG_CONFIG_HOME']) / 'kwalletrc'
            personal.write_text('[Wallet]\nDefault Wallet=personal-wallet\n')
            migrated = 'already,work\\,wallet,path\\\\wallet'
            helper['config_write'](helper['KEY'], migrated, env)
            original = personal.read_bytes()
            child = base / 'migration.py'
            child.write_text('''# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import sys
import time

def read(key):
    return subprocess.check_output(['kreadconfig6', '--file', 'kwalletrc',
        '--group', 'Migration', '--key', key], text=True).removesuffix('\\n')

assert read('MigrateTo3rdParty') == 'true'
progress = read('WalletsMigratedToSecretService')
print('kf.wallet.kwalletd: Migrating "legacy-wallet"', file=sys.stderr, flush=True)
deadline = time.monotonic() + 5
while read('MigrateTo3rdParty') != 'false':
    assert time.monotonic() < deadline, 'The native migration gate stayed open'
    time.sleep(.01)
subprocess.run(['kwriteconfig6', '--file', 'kwalletrc', '--group', 'Migration',
    '--key', 'WalletsMigratedToSecretService', progress + ',new-wallet'], check=True)
''')
            processes = []
            overlays = []

            def launch(command, **kwargs):
                overlays.append(kwargs['env'])
                process = subprocess.Popen(command[:-1] + [sys.executable, str(child)], **kwargs)
                processes.append(process)
                return process

            result = helper['attempt'](env, lambda: processes[0].wait(timeout=10),
                                       launch)
            self.assertEqual(processes[0].returncode, 0)
            self.assertEqual(result, migrated + ',new-wallet')
            self.assertEqual(personal.read_bytes(), original)
            for key, expected in (('QT_FORCE_STDERR_LOGGING', '1'),
                                  ('QT_LOGGING_TO_CONSOLE', '1'),
                                  ('QT_MESSAGE_PATTERN', '%{category}: %{message}'),
                                  ('QT_LOGGING_RULES', 'kf.wallet.kwalletd.warning=true')):
                self.assertEqual(overlays[0][key], expected)

    def test_attempt_timeout_stops_child_without_terminal_input(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        with tempfile.TemporaryDirectory() as tmp:
            env = private_environment(Path(tmp))
            stopped = Path(tmp) / 'stopped'
            processes = []

            def launch(command, **kwargs):
                command = command[:-2] + ['0.2', sys.executable, '-c',
                    'import signal,time,pathlib,sys; '
                    'signal.signal(signal.SIGTERM, lambda *_: '
                    '(pathlib.Path(sys.argv[1]).write_text("terminated"), sys.exit(0))); '
                    'time.sleep(20)', str(stopped)]
                daemon = subprocess.Popen(command, **kwargs)
                processes.append(daemon)
                return daemon

            helper['attempt'](env, lambda: processes[0].wait(timeout=5), launch)
            self.assertEqual(stopped.read_text(), 'terminated')
            self.assertEqual(processes[0].returncode, 124)
            self.assertEqual(helper['config_read']('MigrateTo3rdParty', env), 'false')

    def test_migration_refuses_running_wallet_using_installed_busctl(self):
        helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        attempt = mock.Mock()
        with mock.patch('sys.stdin.isatty', return_value=True), \
                mock.patch.dict(helper['main'].__globals__, {'attempt': attempt}), \
                mock.patch('subprocess.check_output', return_value='b true\n') as query, \
                mock.patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(SystemExit, 'KWallet is already running'):
                helper['main']([])
        self.assertEqual(query.call_args.args[0], [
            'busctl', '--user', 'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner', 's', 'org.kde.kwalletd6',
        ])
        launch.assert_not_called()
        attempt.assert_not_called()

    def test_runtime_worker_supplies_a_password_before_closing_stdin(self):
        daemon = mock.Mock()
        daemon.poll.return_value = None
        with mock.patch.dict(os.environ, {'EMAKI_SECRET_TEST': os.environ['HOME']}), \
                mock.patch('subprocess.Popen', return_value=daemon), \
                mock.patch(__name__ + '.run', side_effect=RuntimeError('stop after unlock')):
            with self.assertRaisesRegex(RuntimeError, 'stop after unlock'):
                runtime_worker()
        daemon.stdin.write.assert_called_once()
        self.assertTrue(daemon.stdin.write.call_args.args[0])
        self.assertEqual(daemon.stdin.method_calls[-1], mock.call.close())

    def test_existing_gnome_login_unlock_and_secret_portal_are_kept(self):
        self.assertIn('pam_gnome_keyring.so auto_start', (ROOT / 'greetd/pam').read_text())
        self.assertIn('org.freedesktop.impl.portal.Secret=gnome-keyring;',
                      (ROOT / 'packaging/emaki-config/niri-portals.conf').read_text())


def runtime_worker():
    # Only the caller's throwaway environment and private bus may reach this path.
    assert os.environ.get('EMAKI_SECRET_TEST') == os.environ['HOME']
    daemon = subprocess.Popen(['gnome-keyring-daemon', '--foreground', '--unlock',
                               '--components=secrets'], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # EOF without any bytes means no password, so no login keyring is created.
    daemon.stdin.write(b'emaki-disposable-test-password')
    daemon.stdin.close()
    try:
        for _ in range(100):
            if run('qdbus6', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                   'org.freedesktop.DBus.NameHasOwner', 'org.freedesktop.secrets') == 'true':
                break
            assert daemon.poll() is None, 'Keyring exited before owning Secret Service'
            time.sleep(.05)
        else:
            raise AssertionError('Keyring did not start')
        run('secret-tool', 'store', '--label=Existing browser secret',
            'application', 'emaki-old-browser', input='old-secret')
        svc = ('qdbus6', 'org.kde.kwalletd6', '/modules/kwalletd6')
        wallet = run(*svc, 'org.kde.KWallet.networkWallet')
        handle = run(*svc, 'org.kde.KWallet.open', wallet, '0', 'emaki-test')
        assert int(handle) >= 0, handle
        assert run(*svc, 'org.kde.KWallet.writePassword', handle, 'emaki-test',
                   'new-key', 'new-secret', 'emaki-test') == '0'
        assert run('secret-tool', 'lookup', 'server', 'emaki-test', 'user', 'new-key') == 'new-secret'
        assert run(*svc, 'org.kde.KWallet.readPassword', handle, 'emaki-test',
                   'new-key', 'emaki-test') == 'new-secret'
        assert run('secret-tool', 'lookup', 'application', 'emaki-old-browser') == 'old-secret'
        assert run('qdbus6', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                   'org.freedesktop.DBus.NameHasOwner', 'org.kde.secretservicecompat') == 'false'
        print('PASS: KWallet and libsecret use one provider; existing keyring secret is readable')
    finally:
        daemon.terminate()
        daemon.wait(timeout=5)


if __name__ == '__main__':
    if '--worker' in sys.argv:
        runtime_worker()
    elif '--runtime' in sys.argv:
        for program in ('dbus-run-session', 'gnome-keyring-daemon', 'secret-tool', 'qdbus6'):
            if not shutil.which(program):
                raise SystemExit(f'Missing runtime dependency: {program}')
        with tempfile.TemporaryDirectory(prefix='emaki-secret-runtime-') as tmp:
            env = private_environment(Path(tmp))
            env['EMAKI_SECRET_TEST'] = env['HOME']
            result = subprocess.run(['dbus-run-session', '--', sys.executable,
                                     str(Path(__file__).resolve()), '--worker'], env=env, timeout=60)
            raise SystemExit(result.returncode)
    else:
        unittest.main()
