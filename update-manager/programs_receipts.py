# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install receipts and the exact undo of one install.

Receipts retain added, upgraded and removed packages plus the pre-install
snapshot identity. Add-only installs undo through dependency-checked package
removal. Changes to existing packages require the recorded system snapshot
and a restart, preserving the complete prior package state.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

import programs_catalog as removal
import restore

try:
    from emaki_paths import LOCALSTATEDIR
except ModuleNotFoundError:
    LOCALSTATEDIR = '/var/lib/emaki'
DIRECTORY = Path(LOCALSTATEDIR) / 'programs' / 'installs'
IDENTITY = re.compile(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\Z')
TOKEN = re.compile(r'[0-9a-f]{64}\Z')
KINDS = ('arch', 'aur', 'flatpak')
KEEP = 32
LIMIT = 1024 * 1024


def now():
    return datetime.now(timezone.utc)


def changes(before, after):
    added = {name: version for name, version in after.items() if name not in before}
    upgraded = {name: [before[name], version] for name, version in after.items()
                if name in before and before[name] != version}
    return added, upgraded


def record(kind, key, name, target, before, after, snapshot='', refs=(), directory=None, snapshot_token=''):
    """Record only the install delta from its post-update baseline, atomically."""
    if kind not in KINDS:
        raise ValueError('Invalid receipt kind')
    directory = DIRECTORY if directory is None else Path(directory)
    added, upgraded = changes(before, after)
    stamp = now()
    identity = stamp.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex
    value = dict(version=1, id=identity, kind=kind, key=key, name=name, target=target,
                 time=stamp.isoformat(timespec='seconds'), added=added, upgraded=upgraded,
                 removed={name: version for name, version in before.items() if name not in after},
                 snapshotToken=snapshot_token,
                 snapshot=str(snapshot or ''), refs=list(refs), undone='')
    write(directory / (identity + '.json'), value)
    prune(directory)
    return value


def write(path, value):
    # Helpers run with umask 077; the window reads receipts as the person.
    previous = os.umask(0o022)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    finally:
        os.umask(previous)
    os.chmod(path.parent, 0o755)
    descriptor, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prune(directory):
    paths = sorted(path for path in directory.glob('*.json') if IDENTITY.fullmatch(path.stem))
    for path in paths[:-KEEP]:
        path.unlink(missing_ok=True)


def valid(value):
    if (not isinstance(value, dict) or value.get('version') != 1 or value.get('kind') not in KINDS
            or not isinstance(value.get('id'), str) or not IDENTITY.fullmatch(value['id'])):
        return False
    for field in ('key', 'name', 'target', 'time', 'snapshot', 'undone'):
        if not isinstance(value.get(field), str) or len(value[field]) > 4096:
            return False
    if not isinstance(value.get('added'), dict) or not isinstance(value.get('upgraded'), dict):
        return False
    if (not isinstance(value.get('removed', {}), dict)
            or not all(isinstance(version, str) for version in value.get('removed', {}).values())):
        return False
    snapshot_token = value.get('snapshotToken', '')
    if (not isinstance(snapshot_token, str) or (snapshot_token and
            (not restore.TOKEN.fullmatch(snapshot_token) or snapshot_token.split(':')[0] != value['snapshot']))):
        return False
    if not isinstance(value.get('refs'), list) or not all(isinstance(ref, str) for ref in value['refs']):
        return False
    if value['kind'] != 'flatpak':
        try:
            removal.valid_name(value['target'])
            for name in list(value['added']) + list(value['upgraded']) + list(value.get('removed', {})):
                removal.valid_name(name)
        except ValueError:
            return False
    return all(isinstance(version, str) for version in value['added'].values())


def read(path, trusted=True):
    """Read one receipt; the privileged helper accepts only root-owned files."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > LIMIT:
            raise ValueError('Invalid receipt file')
        if trusted and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
            raise ValueError('Untrusted receipt file')
        value = json.loads(source.read(LIMIT + 1))
    if not valid(value) or value['id'] != Path(path).stem:
        raise ValueError('Invalid receipt')
    return value


def receipts(directory=None, trusted=False):
    directory = DIRECTORY if directory is None else Path(directory)
    found = []
    try:
        paths = list(directory.glob('*.json'))
    except OSError:
        return []
    for path in paths:
        if not IDENTITY.fullmatch(path.stem):
            continue
        try:
            found.append(read(path, trusted))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda value: value['id'], reverse=True)


def latest(rows, key='', package=''):
    """The newest install of this program that has not been taken back."""
    for value in rows:
        if value['undone']:
            continue
        if (key and value['key'] == key) or (package and value['kind'] != 'flatpak' and value['target'] == package):
            return value
    return None


def summary(value):
    """What the window shows before asking for anything."""
    return dict(id=value['id'], kind=value['kind'], name=value['name'], time=value['time'],
                target=value['target'], added=sorted(value['added']), snapshot=value['snapshot'],
                removed=sorted(value.get('removed', {})), upgraded=sorted(value['upgraded']),
                mode='snapshot' if needs_restore(value) else 'remove')


def parse_reasons(text):
    reasons = {}
    for name, fields in removal.info(text).items():
        reasons[name] = fields.get('Install Reason', '')
    return reasons


def needs_restore(value):
    return value['kind'] != 'flatpak' and bool(value.get('removed') or value.get('upgraded'))


def snapshot_plan(value):
    token = value.get('snapshotToken', '')
    if not token:
        # Older receipts recorded only the number. The public, root-owned index
        # supplies its digest; the privileged helper checks the live point again.
        try:
            points = restore.list_points() if os.geteuid() == 0 else restore.read_index()
            token = next((point['id'] for point in points
                          if point['id'].split(':')[0] == value['snapshot']), '')
        except (OSError, ValueError, RuntimeError):
            token = ''
    if not restore.TOKEN.fullmatch(token) or token.split(':')[0] != value['snapshot']:
        return dict(ok=False, code='snapshot-unavailable')
    state = dict(receipt=value['id'], restoreToken=token, mode='snapshot')
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(ok=True, code='ready', mode='snapshot', restart=True, receipt=value['id'],
                target=value['target'], restoreToken=token, token=digest, remove=[], keep={}, gone=[],
                removed=sorted(value.get('removed', {})), upgraded=sorted(value['upgraded']),
                snapshot=value['snapshot'],
                message='This install changed existing packages. Undo restores the whole system to the snapshot before the install and discards system changes made since then. Restart to use it. Your /home files stay untouched.')


def undo_plan(value, pacman):
    """Exactly which packages an undo removes, which stay and why. Never runs a transaction.

    pacman(*arguments) returns a CompletedProcess of a read-only query.
    """
    if value['kind'] == 'flatpak':
        raise ValueError('Flatpak installs are undone by their own backend')
    if value['undone']:
        return dict(ok=False, code='undone')
    if value['target'] == 'emaki-nvidia':
        return dict(ok=False, code='restore-only')
    if needs_restore(value):
        return snapshot_plan(value)
    if not value['added']:
        return dict(ok=False, code='nothing')
    query = pacman('-Q')
    if query.returncode:
        raise RuntimeError('Installed packages could not be read')
    installed = dict(line.split(maxsplit=1) for line in query.stdout.splitlines())
    target = value['target']
    if target not in installed:
        return dict(ok=False, code='not-installed')
    present = sorted(name for name in value['added'] if name in installed)
    reasons = parse_reasons(pacman('-Qi', *present).stdout) if present else {}
    keep = {}
    for name in present:
        if removal.is_system(name):
            keep[name] = 'system'
        elif removal.is_emaki(name) and name not in removal.OPTIONAL:
            keep[name] = 'emaki'
        elif name != target and reasons.get(name) == 'Explicitly installed':
            keep[name] = 'explicit'
    if target in keep:
        return dict(ok=False, code=keep[target], keep=keep)
    remove = [name for name in present if name not in keep]
    check = pacman('-Rp', '--print-format', '%n', *remove)
    if check.returncode:
        blockers = removal.required_by(check.stdout + '\n' + check.stderr)
        return dict(ok=False, code='required' if blockers else 'failed', requiredBy=blockers)
    gone = sorted(name for name in value['added'] if name not in installed)
    state = dict(receipt=value['id'], remove={name: installed[name] for name in remove})
    token = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(ok=True, code='ready', receipt=value['id'], target=target, remove=remove, keep=keep,
                gone=gone, upgraded=sorted(value['upgraded']), snapshot=value['snapshot'], token=token)


def mark_undone(path, value):
    write(Path(path), dict(value, undone=now().isoformat(timespec='seconds')))


def snapshot_numbers(root):
    """Remember existing snapshot identities before a new package transaction."""
    try:
        return {path.name for path in Path(root).iterdir() if path.name.isdigit()}
    except FileNotFoundError:
        return set()


def first_snapshot(since, root, exclude=()):
    """The first pre-transaction snapshot taken at or after `since` (UTC), or ''."""
    best = None
    try:
        directories = list(Path(root).iterdir())
    except OSError:
        return ''
    for directory in directories:
        if not directory.name.isdigit() or directory.name in exclude:
            continue
        try:
            info = (directory / 'info.xml').read_text(errors='replace')[:65536]
        except OSError:
            continue
        kind = re.search(r'<type>([a-z]+)</type>', info)
        date = re.search(r'<date>([0-9-]+ [0-9:]+)</date>', info)
        if not kind or kind[1] != 'pre' or not date:
            continue
        stamp = datetime.strptime(date[1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        if stamp >= since.replace(microsecond=0) and (best is None or int(directory.name) < best):
            best = int(directory.name)
    return str(best) if best is not None else ''
