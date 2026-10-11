#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Return only recovery status through the active-session read-only policy."""
import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
_directory = Path(__file__).resolve().parent
_restore_path = _directory / 'restore.py'
if not _restore_path.is_file():
    _restore_path = _directory / 'emaki-update-restore'
_loader = SourceFileLoader('privacy_restore', str(_restore_path))
restore = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(restore)

SNAPSHOTS = Path('/.snapshots')
GRUB = Path('/boot/grub')
MENU_LIMIT = 8 * 1024 * 1024


def snapshot_status(root=None):
    """Use restore's receipt and full-update rules; incomplete discovery is unknown."""
    root = SNAPSHOTS if root is None else root
    try:
        if root.is_symlink() or not stat.S_ISDIR(root.stat().st_mode):
            return {'state': 'unavailable'}
        rows = []
        for directory in root.iterdir():
            if not restore.NUMBER.fullmatch(directory.name):
                continue
            # Unlike the privileged restore picker, unreadable records cannot be
            # skipped: they might describe the newest pre-update snapshot.
            row = restore.receipt(directory)
            if row['kind'] == 'pre' and restore.full_update(row['description']):
                rows.append(row)
        if not rows:
            return {'state': 'none'}
        newest = max(rows, key=lambda row: (row['timestamp'], int(row['number'])))
        return {'state': 'ready', 'number': newest['number'], 'date': newest['date']}
    except (OSError, ValueError, ET.ParseError):
        return {'state': 'unavailable'}


def rollback_status():
    try:
        value = restore.boot_state()
        result = {'mode': value['mode']}
        if result['mode'] == 'snapshot':
            number = value.get('snapshot')
            if not isinstance(number, str) or not restore.NUMBER.fullmatch(number):
                return {'mode': 'unavailable'}
            result['snapshot'] = number
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {'mode': 'unavailable'}


def read_menu(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('invalid menu')
        content = stream.read(MENU_LIMIT + 1)
    if len(content) > MENU_LIMIT:
        raise ValueError('oversized menu')
    return content.decode('utf-8')


def commands(text):
    """Inspect generated command lines only; never evaluate GRUB expressions."""
    for line in text.splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if words:
            yield words


def menu_status(snapshot, root=None):
    """Check syntax and a generated snapshot link, never actual bootability."""
    result = {'state': 'unavailable', 'latest_snapshot_present': None}
    root = GRUB if root is None else root
    try:
        main = read_menu(root / 'grub.cfg')
        menu = read_menu(root / 'grub-btrfs.cfg')
    except FileNotFoundError:
        return dict(result, state='missing')
    except (OSError, ValueError):
        return result
    try:
        for text in (main, menu):
            checked = subprocess.run(['grub-script-check'], input=text, capture_output=True,
                                     text=True, env=restore.ENV, cwd='/', timeout=10)
            if checked.returncode:
                return dict(result, state='invalid')
    except (OSError, subprocess.SubprocessError):
        return result
    result['state'] = 'checked'
    if snapshot['state'] != 'ready':
        return result
    # These are the generated source and kernel command forms. Matching text in
    # a title, a comment, or another snapshot's path proves nothing.
    sources = {'${prefix}/grub-btrfs.cfg', '$prefix/grub-btrfs.cfg',
               '/boot/grub/grub-btrfs.cfg', '($root)/@/boot/grub/grub-btrfs.cfg'}
    linked = any(words[0] in ('source', 'configfile') and len(words) == 2
                 and words[1] in sources for words in commands(main))
    expected = '@snapshots/' + snapshot['number'] + '/snapshot'
    found = False
    for words in commands(menu):
        if words[0] not in ('linux', 'linuxefi', 'linux16'):
            continue
        for word in words[2:]:
            if not word.startswith('rootflags='):
                continue
            found |= any(flag.startswith('subvol=') and flag[7:].lstrip('/') == expected
                         for flag in word[10:].split(','))
    result['latest_snapshot_present'] = linked and found
    return result


def status():
    snapshot = snapshot_status()
    return {'schema_version': 1, 'status': 'ready', 'snapshot': snapshot,
            'rollback': rollback_status(), 'menu': menu_status(snapshot)}


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments != ['status', '--json']:
        return 2
    # No caller-selected paths, commands, environment overrides or repair mode.
    # pkexec's policy admits only the active local session, without a prompt.
    caller = os.environ.get('PKEXEC_UID', '')
    if os.geteuid() != 0 or not caller.isascii() or not caller.isdecimal():
        return 1
    print(json.dumps(status(), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
