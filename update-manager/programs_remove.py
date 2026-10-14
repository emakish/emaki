#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Remove one installed program or repository package and its reviewed dependencies.

Run through pkexec with two arguments: the package and the preview token the
person saw. The preview is computed again here; any difference, an Emaki
component, a protected system package or an ineligible target refuses the
removal before pacman changes anything. Never a cascade (-c) or a dependency
override (-d): pacman keeps everything another installed package requires.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programs_catalog as catalog
import programs_arch as arch
import emaki_update_root

ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
# The update window's lock: a removal and a full update never overlap.
LOCK = '/run/emaki-update-manager.lock'
ENTRY = re.compile(r'/usr/share/applications/[^/]+\.desktop')


def emit(code, **fields):
    try:
        print(json.dumps(dict(type='finished', **catalog.result(code, **fields))), flush=True)
    except OSError:
        # The caller may be gone; the result is still in the package log.
        pass


def pacman(*args, timeout=60):
    return subprocess.run(['/usr/bin/pacman', *args], capture_output=True, text=True, env=ENV,
                          cwd='/', timeout=timeout, stdin=subprocess.DEVNULL)


def run_status(command):
    return subprocess.run(command, capture_output=True, text=True, env=ENV, cwd='/', timeout=20)


def is_program(name):
    result = pacman('-Qlq', name)
    return result.returncode == 0 and any(ENTRY.fullmatch(line) for line in result.stdout.splitlines())


def is_repository_package(name):
    try:
        available, installed = arch.inventory()
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError):
        return False
    return name in installed and any(row['package'] == name for row in available.values())


def remove(name, expected):
    if catalog.is_protected(name):
        return emit('emaki' if catalog.is_emaki(name) else 'system', blocked=[name])
    if not is_program(name) and not is_repository_package(name):
        return emit('not-installed' if pacman('-Q', name).returncode else 'not-program')
    check = pacman('-Rs', '--print', '--print-format', '%n', name)
    answer = catalog.assess(name, check.returncode, check.stdout, check.stderr)
    if not answer['ok']:
        answer.pop('ok')
        answer.pop('message')
        return emit(answer.pop('code'), **answer)
    if answer['token'] != expected:
        return emit('changed', packages=answer['packages'], token=answer['token'])
    # Another frontend can change the database after this preview. Commit the
    # checked names without recursion so that a newly unused dependency cannot
    # join the transaction. Pacman's own lock and dependency checks still apply;
    # a missing target or a new dependent makes the entire transaction refuse.
    # A started transaction must finish even if the settings window closes:
    # ignore the signals a closing caller sends, and keep pacman out of its group.
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
        signal.signal(signum, signal.SIG_IGN)
    with subprocess.Popen(['/usr/bin/pacman', '-Rn', '--noconfirm', *answer['packages']], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV, cwd='/',
                          start_new_session=True) as process:
        output = process.communicate()[0]
    remaining = [package for package in answer['packages'] if pacman('-Q', package).returncode == 0]
    if process.returncode == 0 and not remaining:
        return emit('removed', packages=answer['packages'])
    if 'unable to lock database' in output:
        return emit('busy')
    if remaining == answer['packages']:
        return emit('not-removed', packages=answer['packages'])
    return emit('unknown', packages=answer['packages'], blocked=remaining)


def main(argv):
    try:
        if len(argv) != 2:
            raise ValueError('Expected a package and a preview token.')
        catalog.valid_name(argv[0])
        if not catalog.TOKEN.fullmatch(argv[1]):
            raise ValueError('Invalid preview token.')
        if os.geteuid() != 0:
            raise ValueError('Permission is required to remove a program.')
    except ValueError:
        emit('failed')
        return 1
    os.umask(0o077)
    try:
        descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                emit('busy')
                return 1
            try:
                # A pending restore discards this root at restart, with the removal in it.
                emaki_update_root.require_update_root(run_status, 'restart', 'failed')
            except RuntimeError as refusal:
                emit(str(refusal))
                return 1
            remove(argv[0], argv[1])
    except (OSError, subprocess.SubprocessError):
        emit('unknown')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
