#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""List pre-update and pre-install restore points and delegate confirmed restoration to rollback."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import emaki_paths
except ModuleNotFoundError:
    ENV = {'PATH': os.defpath, 'LC_ALL': 'C'}
    ROLLBACK = 'emaki-rollback'
else:
    ENV = {'PATH': emaki_paths.TRUSTED_PATH, 'LC_ALL': 'C'}
    ROLLBACK = str(Path(emaki_paths.BINDIR) / 'emaki-rollback')

SNAPSHOTS = Path('/.snapshots')
LOCK = Path('/run/emaki-update-manager.lock')
TOKEN = re.compile(r'([1-9][0-9]{0,18}):([0-9a-f]{64})')
NUMBER = re.compile(r'[1-9][0-9]{0,18}')
LIMIT = 65536
INDEX = Path('/run/emaki-restore-points.json')
INDEX_LIMIT = 8 * 1024 * 1024
# Allow the five-minute refresh and timer coalescing; restoration rechecks live receipts.
INDEX_MAX_AGE = 600
INDEX_UNAVAILABLE = 'The restore point list is unavailable. Try again shortly.'


class Refused(RuntimeError):
    pass


def full_update(description):
    """Recognize snap-pac's parent command, never execute receipt contents."""
    try:
        words = shlex.split(description)
    except ValueError:
        return False
    if not words or Path(words[0]).name != 'pacman':
        return False
    options = words[1:words.index('--')] if '--' in words else words[1:]
    sync = '--sync' in options
    upgrade = '--sysupgrade' in options
    for word in options:
        if re.fullmatch(r'-[A-Za-z]+', word):
            sync |= 'S' in word
            upgrade |= 'u' in word
    return sync and upgrade


def clean_text(value):
    return ''.join(char for char in value if char.isprintable())[:4096]


def receipt(directory):
    """Read only bounded regular receipts and existing, non-symlink snapshots."""
    if not NUMBER.fullmatch(directory.name) or directory.is_symlink():
        raise ValueError('invalid snapshot directory')
    snapshot = directory / 'snapshot'
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError('missing snapshot')
    fd = os.open(directory / 'info.xml', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('invalid snapshot receipt')
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('invalid snapshot receipt')
    node = ET.fromstring(raw)
    if node.tag != 'snapshot' or node.findtext('num') != directory.name:
        raise ValueError('snapshot identity mismatch')
    stamp = datetime.strptime(node.findtext('date', ''), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    return dict(number=directory.name, kind=node.findtext('type'),
                timestamp=stamp.isoformat(), date=stamp.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'),
                description=clean_text(node.findtext('description', '')),
                pre=node.findtext('pre_num'), digest=hashlib.sha256(raw).hexdigest())


def list_points(root=None):
    root = SNAPSHOTS if root is None else root
    if root.is_symlink() or not root.is_dir():
        raise Refused('Restore points are unavailable on this system.')
    rows = []
    for directory in root.iterdir():
        try:
            rows.append(receipt(directory))
        except (OSError, ValueError, ET.ParseError):
            # Cleanup can remove a point during discovery. Never offer a broken one.
            continue
    result = []
    for pre in rows:
        install = pre['description'] in ('AUR install', 'Program install')
        if pre['kind'] != 'pre' or not (install or full_update(pre['description'])):
            continue
        posts = [post for post in rows if post['kind'] == 'post' and post['pre'] == pre['number']
                 and post['timestamp'] >= pre['timestamp']]
        post = posts[0] if len(posts) == 1 else None
        result.append(dict(id=pre['number'] + ':' + pre['digest'], date=pre['date'],
                           timestamp=pre['timestamp'], description=pre['description'],
                           operation='install' if install else 'update',
                           completed=post is not None, postDate=post['date'] if post else '',
                           packages=post['description'] if post else '',
                           notice='' if post or install else 'No matching after-update snapshot is available.'))
    return sorted(result, key=lambda row: (row['timestamp'], int(row['id'].split(':')[0])), reverse=True)


def refresh_index():
    """Publish a bounded public inventory, without changing private snapshot modes."""
    if os.geteuid() != 0:
        raise Refused('Administrator permission is required to refresh the restore point list.')
    try:
        value = dict(points=list_points(), error='')
    except (OSError, ValueError, RuntimeError):
        # A failed scan must not keep advertising the previous successful inventory.
        value = dict(points=[], error=INDEX_UNAVAILABLE)
    payload = json.dumps(value).encode('utf-8')
    if len(payload) > INDEX_LIMIT:
        value = dict(points=[], error=INDEX_UNAVAILABLE)
        payload = json.dumps(value).encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(prefix='.emaki-restore-points-', dir=INDEX.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(payload)
            output.flush()
            # Only the new public inventory changes mode; never the receipts or
            # existing private state. /run is owned by root and not user-writable.
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.replace(temporary, INDEX)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def read_index():
    """Read an atomic root-owned inventory; restoration still checks live receipts."""
    try:
        descriptor = os.open(INDEX, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as source:
            metadata = os.fstat(source.fileno())
            age = time.time() - metadata.st_mtime
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                    or stat.S_IMODE(metadata.st_mode) != 0o644
                    or metadata.st_size > INDEX_LIMIT or age < 0 or age > INDEX_MAX_AGE):
                raise ValueError('untrusted or stale restore index')
            raw = source.read(INDEX_LIMIT + 1)
        if len(raw) > INDEX_LIMIT:
            raise ValueError('oversize restore index')
        value = json.loads(raw)
        if (not isinstance(value, dict) or not isinstance(value.get('points'), list)
                or not isinstance(value.get('error'), str)):
            raise ValueError('invalid restore index')
        if value['error']:
            raise Refused(INDEX_UNAVAILABLE)
        for point in value['points']:
            if not isinstance(point, dict) or not isinstance(point.get('id'), str) or not TOKEN.fullmatch(point['id']):
                raise ValueError('invalid restore token')
        return value['points']
    except (OSError, ValueError) as error:
        raise Refused(INDEX_UNAVAILABLE) from error


def public_points():
    state = boot_state()
    try:
        require_normal(state)
    except Refused as error:
        return dict(type='restorePoints', points=[], error=str(error), restorePending=state['mode'] == 'pending')
    return dict(type='restorePoints', points=read_index(), error='', restorePending=False)


def run(arguments, timeout=30):
    return subprocess.run(arguments, capture_output=True, text=True, env=ENV,
                          cwd='/', stdin=subprocess.DEVNULL, timeout=timeout)


def boot_state():
    result = run([ROLLBACK, 'status', '--json'])
    if result.returncode:
        raise Refused('The system restore status could not be read.')
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('mode') not in ('normal', 'snapshot', 'pending', 'unsupported'):
        raise Refused('The system restore status is invalid.')
    return value


def require_normal(state):
    if state['mode'] == 'pending':
        raise Refused('A system restore is ready. Restart before making another change.')
    if state['mode'] != 'normal':
        raise Refused('Restore is available only from the normal installed system.')


def restore_point(token, root=None):
    match = TOKEN.fullmatch(token)
    if match is None:
        raise Refused('Choose a restore point from the current list.')
    require_normal(boot_state())
    points = list_points(root)
    if not any(point['id'] == token for point in points):
        raise Refused('This restore point changed or is no longer available. Load the list again.')
    return _apply_snapshot(match[1])


def restore_install_point(token, root=None):
    """Restore an exact pre-install point authenticated by the install receipt."""
    match = TOKEN.fullmatch(token)
    if match is None:
        raise Refused('The restore point from before this install is unavailable.')
    require_normal(boot_state())
    root = SNAPSHOTS if root is None else root
    try:
        if root.is_symlink() or not root.is_dir():
            raise ValueError('invalid snapshot root')
        point = receipt(root / match[1])
        if point['kind'] != 'pre' or point['digest'] != match[2]:
            raise ValueError('changed pre-install snapshot')
    except (OSError, ValueError, ET.ParseError) as error:
        raise Refused('The restore point from before this install changed or is no longer available. Nothing was restored.') from error
    return _apply_snapshot(match[1])


def _apply_snapshot(number):
    # The existing command owns layout checks, the package/boot locks, preservation
    # of shared subvolumes and the atomic root exchange. Do not reproduce them here.
    # No timeout: killing a restore in progress cannot establish whether it committed.
    result = run([ROLLBACK, 'snapshot', number], timeout=None)
    if result.returncode:
        # A command can fail after exchanging the roots (for example, a failed
        # durability sync). Do not invite another update on the retained root.
        try:
            pending = boot_state()['mode'] == 'pending'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pending = False
        return dict(type='restoreFinished', ok=False, restart=pending,
                    message='The system restore did not finish. Review the details before trying again.',
                    details=clean_text(result.stderr or result.stdout))
    return dict(type='restoreFinished', ok=True, restart=True,
                message='The selected system restore point is ready. Restart to use it. Your home folder was left untouched.',
                details=clean_text(result.stdout))


def validate(arguments):
    if arguments in (['list'], ['refresh-index']):
        return
    if len(arguments) == 2 and arguments[0] == 'restore' and TOKEN.fullmatch(arguments[1]):
        return
    raise Refused('Choose list or restore with an exact restore point.')


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    listing = arguments == ['list']
    try:
        validate(arguments)
        if listing:
            # No privilege, lock file or snapshot traversal on the read path.
            answer = public_points()
        elif arguments == ['refresh-index']:
            value = refresh_index()
            return 1 if value['error'] else 0
        else:
            if os.geteuid() != 0:
                raise Refused('Administrator permission is required to restore the system.')
            os.umask(0o077)
            descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, 'w') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Refused('An update or restore is running. Wait for it to finish.') from None
                answer = restore_point(arguments[1])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, Refused) else 'System restore points could not be read or restored.'
        answer = (dict(type='restorePoints', points=[], error=message) if listing else
                  dict(type='restoreFinished', ok=False, restart=False, message=message))
    print(json.dumps(answer), flush=True)
    return 0 if not answer.get('error') and answer.get('ok', True) else 1


if __name__ == '__main__':
    sys.exit(main())
