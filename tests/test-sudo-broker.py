#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and exercise native authorization without invoking sudo or a desktop."""
from pathlib import Path
import os
import subprocess
import shlex
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def run(argv, **kwargs):
    result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result


def production_pam(build):
    # Exercise the actual module and rendered service via real libpam, without
    # installing any PAM configuration or invoking sudo on the host.
    shipped = (build / 'emaki-sudo-pam').read_text()
    installed = next(line.split('] ', 1)[1] for line in shipped.splitlines()
                     if line.startswith('auth ['))
    assert shipped.count(installed) == 2
    with tempfile.TemporaryDirectory(prefix='sudo-production-pam-') as directory:
        base = Path(directory)
        broken = base / 'broken.so'
        broken.write_text('invalid shared object')
        modules = {'real': build / 'pam_emaki_sudo.so',
                   'missing': base / 'missing.so', 'broken': broken}
        count = 0
        for kind, module in modules.items():
            for stock in ('permit', 'deny'):
                config = base / (kind + '-' + stock)
                config.mkdir()
                (config / 'emaki-sudo').write_text(shipped.replace(installed, str(module)))
                (config / 'sudo').write_text(
                    f'auth required pam_{stock}.so\naccount required pam_permit.so\n')
                for user, terminal in (('test-user', False), ('root', False),
                                       ('test-user', True)):
                    command = [str(build / 'sudo-pam-production-tests'), str(config),
                               user, stock, 'tty' if terminal else 'none']
                    if terminal:
                        command = ['script', '-qec', shlex.join(command), os.devnull]
                    result = run(command, stdin=subprocess.DEVNULL,
                                 start_new_session=True, timeout=10)
                    assert 'PRODUCTION_PAM_PASSED' in result.stdout
                    count += 1
    print(f'Production module and rendered PAM service: {count} cases passed')


def main(build):
    run(['make', 'build-sudo', 'SUDO_BUILD=' + str(build)], timeout=180)
    result = run(['ctest', '--test-dir', str(build), '--verbose'], timeout=30)
    print(result.stdout, end='')
    production_pam(build)
    # Load the real native client disabled. It must make no host connection,
    # expose no request/key. Revocations use the isolated native transport test.
    with tempfile.TemporaryDirectory(prefix='sudo-native-') as directory:
        base = Path(directory)
        qml = base / 'shell.qml'
        qml.write_text('''import QtQuick
import Quickshell
import Emaki.Sudo 1.0
Scope {
    SudoClient { id: client; enabled: false }
    Timer {
        interval: 100; running: true
        onTriggered: {
            if (client.connected || Object.keys(client.request).length || client.keys.length || client.installed)
                Qt.exit(1);
            client.setLocked(true);
            client.cancel("1");
            client.acknowledgeWarning(); client.submit("1", "", "time", 420);
            console.info("SUDO_NATIVE_CLOSED"); Qt.exit(0);
        }
    }
}
''')
        env = dict(os.environ, HOME=directory, XDG_RUNTIME_DIR=directory,
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_IMPORT_PATH=str(build / 'qml'), QML_DISABLE_DISK_CACHE='1',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-session'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system'))
        for variable in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
            env.pop(variable, None)
        result = run(['qs', '-p', str(qml), '--no-color'], env=env, timeout=15)
        assert 'SUDO_NATIVE_CLOSED' in result.stdout + result.stderr
    print('Native client disabled state: passed')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='sudo-broker-build-') as directory:
        main(Path(directory))
