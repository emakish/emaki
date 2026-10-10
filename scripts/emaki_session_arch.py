# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Arch producer of the desktop state: pacman transactions and the installed desktop packages.

Only `emaki-session-update` reads this module; the session asks that command for its JSON state.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import emaki_session_state as session


def load_catalog():
    # The one list of session packages is the update backend's. Installed, the catalog is
    # this module's neighbour; in the source tree it is the update manager's file.
    path = HERE / 'update_catalog.py'
    if not path.exists():
        path = HERE.parent / 'update-manager/catalog.py'
    spec = importlib.util.spec_from_file_location('emaki_update_catalog', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog = load_catalog()

LOCK = Path('/var/lib/pacman/db.lck')
PACKAGES = Path('/var/lib/pacman/local')


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
    if session.readonly_root():
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
    if refresh and refresh[1] != session.boot_id():
        return False
    for process in session.PROC.iterdir():
        if not process.name.isdecimal() or session.process_start(process.name) is None:
            continue
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')
            name = (process / 'comm').read_text().strip()
            if name == 'pacman' and not pacman_changes_system(command):
                continue
            if name == 'pacman' and session.root_id(process / 'root') != session.root_id(session.ROOT):
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


def desktop_packages(packages=None):
    """Installed name-version-release entries of the packages a session runs from."""
    packages = PACKAGES if packages is None else packages
    # Entry names are name-pkgver-pkgrel; neither version field contains a hyphen. The
    # entry's desc also holds the install reason, which pacman -D changes without
    # replacing files, so only the entry name identifies a payload.
    names = (desc.parent.name for desc in packages.glob('*/desc'))
    return sorted(name for name in names
                  if name.count('-') >= 2 and catalog.session_changes([name.rsplit('-', 2)[0]]))


def desktop_id(packages=None):
    """An opaque identity that changes whenever a desktop package is installed, replaced or removed."""
    return hashlib.sha256(json.dumps(desktop_packages(packages)).encode()).hexdigest()[:24]


def session_components(names):
    """The transaction's packages that a running session has loaded."""
    return sorted({name for name in names if catalog.session_changes([name])})
