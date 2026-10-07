#!/usr/bin/env python3
"""Guest diagnostic only (grim): never evidence of the visible host display."""
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import pwd
import subprocess
import sys


def main():
    if os.geteuid() != 0 or subprocess.check_output(
            ['systemd-detect-virt', '--vm'], text=True).strip() not in ('qemu', 'kvm'):
        raise RuntimeError('requires root in the disposable QEMU/KVM guest')
    # Pick the foreground seat session; grim alone can also see inactive greeters.
    session = subprocess.check_output(
        ['loginctl', 'show-seat', 'seat0', '-p', 'ActiveSession', '--value'], text=True).strip()
    if not session:
        raise RuntimeError('no active seat session')
    uid = int(subprocess.check_output(
        ['loginctl', 'show-session', session, '-p', 'User', '--value'], text=True).strip())
    user = pwd.getpwuid(uid).pw_name
    runtime = Path('/run/user') / str(uid)
    sockets = sorted(path for path in runtime.glob('wayland-*') if path.is_socket())
    if len(sockets) != 1:
        raise RuntimeError('expected one Wayland socket for the active session')
    subprocess.run(['runuser', '-u', user, '--', 'env', f'XDG_RUNTIME_DIR={runtime}',
                    f'WAYLAND_DISPLAY={sockets[0].name}', 'grim', '-'], check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'guest screenshot failed: {error}')
