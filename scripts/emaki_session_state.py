# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read transaction and root identity without treating a leftover lock as a process."""
import json
import os
from pathlib import Path
import re
import subprocess

PROC = Path('/proc')
ROOT = Path('/')
LOCK = Path('/var/lib/pacman/db.lck')
LIVE = Path('/etc/emaki-live/greetd.toml')


def root_id(path=ROOT):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def boot_id():
    return (PROC / 'sys/kernel/random/boot_id').read_text().strip()


def foreign_root():
    # The installer shares /run with the target. Neither marker writes nor user
    # bus access are appropriate there. Fail closed if root identity is unknown.
    try:
        if root_id(PROC / '1/root') != root_id(ROOT):
            return True
        result = subprocess.run(['/usr/bin/systemd-detect-virt', '--chroot'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=2, check=False)
        return result.returncode != 1
    except (OSError, subprocess.SubprocessError):
        return True


def process_start(pid):
    try:
        fields = (PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] not in ('Z', 'X') else None
    except (OSError, IndexError):
        return None


def readonly_root():
    return bool(os.statvfs(ROOT).f_flag & os.ST_RDONLY)


def pacman_changes_system(command):
    """Recognize installed-package operations, excluding queries and other roots."""
    operations, options = set(), set()
    arguments = iter(os.fsdecode(arg) for arg in command[1:] if arg)
    paths = {'--root': '/', '--sysroot': '/', '--dbpath': '/var/lib/pacman',
             '-r': '/', '-b': '/var/lib/pacman'}
    values = {'--arch', '--assume-installed', '--cachedir', '--color', '--config',
              '--gpgdir', '--hookdir', '--ignore', '--ignoregroup', '--logfile',
              '--overwrite', '--print-format'}
    long_operations = {'--sync': 'S', '--upgrade': 'U', '--remove': 'R',
                       '--query': 'Q', '--files': 'F', '--database': 'D', '--deptest': 'T'}
    for argument in arguments:
        if argument == '--':
            break
        if argument.startswith('--'):
            option, separator, value = argument.partition('=')
            if option in paths:
                value = value if separator else next(arguments, '')
                if not value or os.path.normpath(value) != paths[option]:
                    return False
            elif option in long_operations:
                operations.add(long_operations[option])
            else:
                options.add(option)
                if option in values and not separator:
                    next(arguments, '')
        elif argument.startswith('-'):
            cluster = argument[1:]
            for index, option in enumerate(cluster):
                if '-' + option in paths:
                    value = cluster[index + 1:] or next(arguments, '')
                    if not value or os.path.normpath(value) != paths['-' + option]:
                        return False
                    break
                if option in 'SURQFDT':
                    operations.add(option)
                else:
                    options.add(option)
    if len(operations) != 1 or not operations.intersection('SUR'):
        return False
    if options.intersection({'p', 'h', 'V', '--print', '--print-format', '--help', '--version'}):
        return False
    if operations == {'S'} and options.intersection(
            {'s', 'i', 'l', 'g', 'c', '--search', '--info', '--list', '--groups', '--clean'}):
        return False
    return not options.intersection({'w', '--downloadonly'})


def transaction_running(lock=None):
    lock = LOCK if lock is None else lock
    if readonly_root():
        return False
    try:
        lock_stat = lock.stat()
    except FileNotFoundError:
        lock_stat = None
    try:
        token = lock.read_text()[:256]
    except (OSError, UnicodeError):
        token = ''
    refresh = re.fullmatch(r'emaki-boot-refresh:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})', token)
    if refresh and refresh[1] != boot_id():
        return False
    for process in PROC.iterdir():
        if not process.name.isdecimal() or process_start(process.name) is None:
            continue
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')
            name = (process / 'comm').read_text().strip()
            if name == 'pacman' and not pacman_changes_system(command):
                continue
            if name == 'pacman' and root_id(process / 'root') != root_id(ROOT):
                continue
        except OSError:
            # Root-owned process roots and descriptors may be unreadable.
            pass
        # Where permissions allow, inspect actual holders (also covers libalpm
        # clients and the boot refresh lock). Pacman keeps its lock descriptor.
        if lock_stat is not None:
            try:
                for fd in (process / 'fd').iterdir():
                    try:
                        held = fd.stat()
                        if (held.st_dev, held.st_ino) == (lock_stat.st_dev, lock_stat.st_ino):
                            return True
                    except OSError:
                        continue
            except OSError:
                pass
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')
            name = (process / 'comm').read_text().strip()
            # Ordinary users cannot inspect root-owned descriptors. Process
            # state/arguments remain readable on the shipped proc mount.
            if name == 'pacman' and pacman_changes_system(command):
                return True
            if refresh and any(
                    Path(os.fsdecode(arg)).name == 'emaki-boot-refresh' for arg in command if arg):
                return True
        except OSError:
            continue
    return False


def marker_value(path):
    try:
        value = json.loads(path.read_text())
        if value['root'] == root_id(ROOT) and value['boot'] == boot_id():
            return value['transaction']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
