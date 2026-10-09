#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authorize exactly one fixed full-upgrade service, never a supplied command."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import update_catalog as catalog
except ModuleNotFoundError:
    import catalog

ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'}
SERVICE = 'emaki-update.service'


def validate(arguments):
    if arguments != ['apply']:
        raise ValueError('Only a full repository update is accepted.')


def emit(kind, **fields):
    print(json.dumps(dict(type=kind, **fields)), flush=True)


def run(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, env=ENV, cwd='/', timeout=20)


def versions():
    result = run(['/usr/bin/pacman', '-Q'])
    if result.returncode:
        raise RuntimeError('The installed package versions could not be read.')
    return dict(line.split(maxsplit=1) for line in result.stdout.splitlines())


def changed(before, after):
    names = {name for name, version in after.items() if before.get(name) != version}
    restart = any(re.fullmatch(r'linux(?:-lts|-zen|-hardened|-rt|-rt-lts)?', name) for name in names)
    sign_out = catalog.session_changes(names)
    return restart, sign_out


def apply():
    # /run is root-owned; no caller-controlled paths, environment or descriptors
    # reach the service. Its lifetime belongs to the system service manager.
    descriptor = os.open('/run/emaki-update-manager.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit('finished', ok=False, message='An update is already running. Wait for it to finish.', restart=False, signOut=False)
            return 1
        before = versions()
        since = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        cursor = ''
        space_refusal = ''
        emit('progress', message='Updating all repository packages. You can hide this window; the update will keep running.')
        with subprocess.Popen(['/usr/bin/systemctl', 'start', SERVICE], cwd='/', env=ENV,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as starter:
            stopped = False
            while True:
                log = run(['/usr/bin/journalctl', '--quiet', '--no-pager', '-u', SERVICE, '-o', 'json', '-n', '100',
                           *(['--after-cursor', cursor] if cursor else ['--since', since])])
                for line in log.stdout.splitlines():
                    try:
                        record = json.loads(line)
                        cursor = record['__CURSOR']
                        message = record['MESSAGE']
                        if not isinstance(message, str):
                            continue
                        message = ''.join(char for char in message if ord(char) >= 32 or char == '\t')[:4096]
                        if re.fullmatch(r'Not enough free space for this update: it needs about '
                                        r'\d+\.\d [GM]B, \d+\.\d [GM]B is free\. '
                                        r'Free some space, then try again\.', message):
                            space_refusal = message
                        emit('progress', message=message)
                    except (ValueError, KeyError):
                        continue
                if stopped:
                    break
                stopped = starter.poll() is not None
                if not stopped:
                    time.sleep(1)
            status = starter.wait()
        # The service refuses no snapshots or rollback hooks: scripts/emaki-update
        # still owns the full pacman transaction. Recovery integration belongs to
        # its existing pre/post-transaction hooks, shared by terminal upgrades.
        after = versions()
        restart, sign_out = changed(before, after)
        if status == 0:
            message = 'Repository updates finished. AUR programs must be updated with your AUR tool.'
        else:
            message = 'The update did not finish. Review the details before trying again.'
            if before != after:
                message = 'The update did not finish. Some packages changed. Review the details before trying again.'
            elif space_refusal:
                message = space_refusal
        emit('finished', ok=status == 0, message=message, restart=restart, signOut=sign_out)
        return 0 if status == 0 else 1


def main():
    try:
        validate(sys.argv[1:])
        if os.geteuid() != 0:
            raise ValueError('Permission is required to update repository packages.')
    except ValueError as error:
        emit('finished', ok=False, notStarted=True, message=str(error), restart=False, signOut=False)
        return 1
    try:
        os.umask(0o077)
        return apply()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        emit('finished', ok=False, message='The update service could not be started or observed. A started update keeps running; check its result before trying again.', restart=False, signOut=False)
        return 1


if __name__ == '__main__':
    sys.exit(main())
