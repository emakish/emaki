# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Arch retention rules; callers alone own preview and deletion."""

try:
    from emaki_i18n import format_number, translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import format_number, translation

_ = translation('programs').gettext

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from functools import cmp_to_key, wraps
from emaki_package_lock import lock_message
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import tempfile

try:
    from emaki_paths import BINDIR, TRUSTED_PATH
except ModuleNotFoundError:
    BINDIR = '/bin'
    TRUSTED_PATH = '/bin'

ENV = {'PATH': TRUSTED_PATH, 'LC_ALL': 'C'}
DATABASE = Path('/var/lib/pacman')
CACHE = Path('/var/cache/pacman/pkg')
SNAPSHOTS = Path('/.snapshots')
HOLDS = Path('/var/lib/emaki/rollback-holds.json')
LOCKS = tuple(Path('/run') / name for name in (
    'emaki-update-manager.lock', 'emaki-rollback.lock', 'emaki-boot-refresh.lock'))
NAME = re.compile(r'[a-zA-Z0-9@_+][a-zA-Z0-9@_.+-]*')
VERSION = re.compile(r'[^\s/\x00]+')
MAX_ENTRIES = 20000
MAX_ARCHIVES = 4096
MAX_SNAPSHOTS = 256
MAX_OUTPUT = 8 * 1024 * 1024
SCAN_SECONDS = 45
DEADLINE = ContextVar('storage_inventory_deadline', default=None)
ROOT_VIEW = ContextVar('storage_root_view', default=None)
KEPT = re.compile(r'@emaki-kept-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}')
ARCHIVE = re.compile(r'.+\.pkg\.tar(?:\.(?:zst|xz|gz|bz2|lrz|lzo|Z))?')


class Refused(RuntimeError):
    """The retention inventory is uncertain; nothing may be removed."""


def require(condition):
    if not condition:
        raise Refused(_('Package retention could not be verified. Nothing was removed.'))


def remaining():
    deadline = DEADLINE.get()
    seconds = SCAN_SECONDS if deadline is None else deadline - time.monotonic()
    if seconds <= 0:
        raise Refused(_('Storage inventory took too long. Nothing was removed.'))
    return seconds


def bounded(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        previous = DEADLINE.get()
        token = DEADLINE.set(min(previous or float('inf'), time.monotonic() + SCAN_SECONDS))
        try:
            return function(*args, **kwargs)
        finally:
            DEADLINE.reset(token)
    return wrapper


def entries(directory, limit=MAX_ENTRIES):
    result = []
    for entry in directory.iterdir():
        remaining()
        require(len(result) < limit)
        result.append(entry)
    return sorted(result)


def command(arguments):
    result = subprocess.run([str(arg) for arg in arguments], env=ENV, cwd='/',
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=remaining())
    require(result.returncode == 0 and len(result.stdout) <= MAX_OUTPUT and len(result.stderr) <= MAX_OUTPUT)
    return result.stdout


def safe(path, directory=False):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    return path


@bounded
def cache_roots():
    """Read the fixed cache layout without taking locks or creating files."""
    caches = command(['pacman-conf', 'CacheDir']).splitlines()
    require(caches in ([str(CACHE) + '/'], [str(CACHE)]))
    return [safe(CACHE, True)]


def configuration():
    require(Path(command(['pacman-conf', 'DBPath']).strip()) == DATABASE)
    safe(DATABASE, True)
    cache_roots()


@contextmanager
def package_guard():
    """Lock in rollback's order, then reserve the package database exclusively.

    Even previews need root: accepting a racy retention snapshot is unsafe. A
    crash leaves db.lck for standard administrator recovery, never automatic
    deletion of a lock whose owner cannot be proven.
    """
    with ExitStack() as stack:
        for path in LOCKS:
            safe(path.parent, True)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            stream = stack.enter_context(os.fdopen(descriptor, 'w'))
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configuration()
        directory = os.open(DATABASE, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        stack.callback(os.close, directory)
        try:
            descriptor = os.open('db.lck', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
        except FileExistsError as error:
            raise Refused(lock_message(DATABASE / 'db.lck')) from error
        stack.callback(os.close, descriptor)
        owned = os.fstat(descriptor)
        try:
            yield
        finally:
            current = os.stat('db.lck', dir_fd=directory, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino))
            os.unlink('db.lck', dir_fd=directory)


def read_versions(database):
    result = {}
    for entry in entries(safe(database, True)):
        remaining()
        if entry.name == 'ALPM_DB_VERSION':
            continue
        safe(entry, True)
        desc = safe(entry / 'desc')
        require(desc.stat().st_size <= 1024 * 1024)
        lines = desc.read_text().splitlines()
        require(lines.count('%NAME%') == 1 and lines.count('%VERSION%') == 1)
        try:
            name, version = lines[lines.index('%NAME%') + 1], lines[lines.index('%VERSION%') + 1]
        except IndexError as error:
            raise Refused(_('Package metadata is incomplete.')) from error
        require(NAME.fullmatch(name) and VERSION.fullmatch(version) and name not in result)
        result[name] = version
    require(bool(result))
    return result


@contextmanager
def top_mount(filesystem):
    """Expose retained roots read-only, only in this process's mount namespace."""
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.unshare(0x00020000) == 0)
    command(['mount', '--make-rprivate', '/'])
    top = Path(tempfile.mkdtemp(prefix='emaki-storage-', dir='/run'))
    try:
        command(['mount', '-t', 'btrfs', '-o', 'ro,subvolid=5,nosuid,nodev,noexec',
                 '/dev/disk/by-uuid/' + filesystem, str(top)])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        # rmdir cannot traverse contents or remove an unexpectedly mounted tree.
        try:
            top.rmdir()
        except OSError:
            pass
        raise
    try:
        yield top
    finally:
        # Never recursively remove a mountpoint, including after failed unmount.
        # Cleanup must still run when the inventory's shared deadline expired.
        token = DEADLINE.set(None)
        try:
            command(['umount', str(top)])
            top.rmdir()
        finally:
            DEADLINE.reset(token)


def root_view(function):
    """Keep a lazily opened view alive until every retained database is read."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        with ExitStack() as stack:
            token = ROOT_VIEW.set({'stack': stack, 'top': None})
            try:
                return function(*args, **kwargs)
            finally:
                ROOT_VIEW.reset(token)
    return wrapper


def subvolume_listing(arguments):
    result = {}
    for line in command(['btrfs', 'subvolume', 'list', *arguments, '/']).splitlines():
        remaining()
        require(len(result) < MAX_ENTRIES)
        match = re.fullmatch(r'ID ([0-9]+) .* path (.+)', line)
        require(match is not None)
        identity, relative = '0/' + match[1], match[2].removeprefix('<FS_TREE>/')
        require(identity not in result and relative not in result.values())
        result[identity] = relative
    return result


@bounded
def snapshot_paths():
    kind = command(['findmnt', '-n', '-o', 'FSTYPE', '/']).strip()
    if kind != 'btrfs':
        require(not SNAPSHOTS.exists())
        return []
    filesystem = command(['findmnt', '-n', '-o', 'UUID', '/']).strip()
    require(re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', filesystem))
    view = ROOT_VIEW.get()
    require(view is not None)
    if view['top'] is None:
        view['top'] = view['stack'].enter_context(top_mount(filesystem))
        view['filesystem'] = filesystem
    require(view['filesystem'] == filesystem)
    top = view['top']
    listing = subvolume_listing([])
    snapshots = subvolume_listing(['-s'])
    require(all(listing.get(identity) == relative for identity, relative in snapshots.items()))
    live = subvolume_id('/')
    require(listing.get(live) == '@' and subvolume_id(top / '@') == live)
    records = {}
    directory = top / '.emaki-rollback'
    if directory.exists() or directory.is_symlink():
        for path in entries(safe(directory, True), MAX_SNAPSHOTS):
            require(path.suffix == '.json' and KEPT.fullmatch(path.stem))
            safe(path)
            require(path.stat().st_size <= 1024 * 1024)
            record = json.loads(path.read_text())
            require(isinstance(record, dict) and record.get('kept') == path.stem
                    and record.get('filesystem') == filesystem
                    and type(record.get('previous_id')) is int and record['previous_id'] > 0
                    and type(record.get('new_id')) is int and record['new_id'] > 0
                    and record.get('state') is None)
            records[path.stem] = record
    # A writable snapshot at @ is expected only after a recorded exchange.
    require(live not in snapshots or any('0/' + str(record['new_id']) == live
                                        for record in records.values()))
    result = []
    # @secureboot holds the Secure Boot machine key outside the root's snapshots.
    known = {'@home', '@log', '@pkg', '@secureboot', '@snapshots', '@swap'}
    kept = set()
    root_names = {'@', *records, *(relative for relative in listing.values()
                                 if re.fullmatch(r'@snapshots/([1-9][0-9]*)/snapshot', relative))}
    for identity, relative in listing.items():
        remaining()
        if identity == live:
            continue
        if relative in known:
            require(identity not in snapshots)
            continue
        nested = next((relative.removesuffix('/' + suffix)
                       for suffix in ('var/lib/machines', 'var/lib/portables')
                       if relative.endswith('/' + suffix)), None)
        if nested in root_names:
            child = safe(top / relative, True)
            require(identity not in snapshots and not entries(child)
                    and subvolume_id(child) == identity)
            continue
        match = re.fullmatch(r'@snapshots/([1-9][0-9]*)/snapshot', relative)
        if match:
            root = top / relative
        else:
            require(KEPT.fullmatch(relative) and relative in records)
            require(identity == '0/' + str(records[relative]['previous_id']))
            kept.add(relative)
            root = top / relative
        require(len(result) < MAX_SNAPSHOTS)
        safe(root, True)
        require(subvolume_id(root) == identity)
        result.append(root)
    # Missing roots, incomplete preparations and swapped/reused names are unsafe.
    require(kept == records.keys())
    view['identities'] = sorted((relative, identity) for identity, relative in listing.items())
    view['records'] = records
    return sorted(result)


def stable_root(path):
    view = ROOT_VIEW.get()
    if view and view['top'] is not None:
        return '/' + str(path.relative_to(view['top']))
    return str(path)


def hold_versions(path):
    if not path.exists() and not path.is_symlink():
        return {}
    safe(path)
    require(path.stat().st_size < 4 * 1024 * 1024)
    value = json.loads(path.read_text())
    require(isinstance(value, dict) and isinstance(value.get('holds'), dict))
    require(all(NAME.fullmatch(name) and isinstance(version, str) and VERSION.fullmatch(version)
                for name, version in value['holds'].items()))
    return value['holds']


@root_view
@bounded
def cache_inventory():
    """Call under package_guard for both the preview and its consumption."""
    configuration()
    status = json.loads(command([str(Path(BINDIR) / 'emaki-rollback'), 'status', '--json']))
    require(status.get('mode') in ('normal', 'unsupported'))
    installed = read_versions(DATABASE / 'local')
    protected = set(installed.items()) | set(hold_versions(HOLDS).items())
    snapshots = snapshot_paths()
    for root in snapshots:
        protected.update(read_versions(root / DATABASE.relative_to('/') / 'local').items())
        protected.update(hold_versions(root / HOLDS.relative_to('/')).items())
    archives = []
    for path in entries(CACHE):
        remaining()
        if not ARCHIVE.fullmatch(path.name):
            continue
        safe(path)
        require(path.stat().st_nlink == 1)
        fields = command(['pacman', '-Qp', '--', str(path)]).split()
        require(len(fields) == 2 and NAME.fullmatch(fields[0]) and VERSION.fullmatch(fields[1]))
        name, version = fields
        # Unknown packages are retained rather than guessed from file names.
        if name in installed:
            require(len(archives) < MAX_ARCHIVES)
            archives.append((path, name, version))
    def compare(left, right):
        value = command(['vercmp', left, right]).strip()
        require(re.fullmatch(r'-?[0-9]+', value))
        return int(value)
    for name in {name for _unused, name, _unused in archives}:
        versions = {version for _unused, package, version in archives if package == name}
        protected.update((name, version) for version in sorted(versions, key=cmp_to_key(compare))[-3:])
    candidates = []
    for path, name, version in archives:
        if (name, version) in protected:
            continue
        candidates.append(path)
        signature = Path(str(path) + '.sig')
        if signature.exists() or signature.is_symlink():
            safe(signature)
            require(signature.stat().st_nlink == 1)
            candidates.append(signature)
    versions = sorted(f'{name} {version}' for name, version in protected)
    return {'roots': [CACHE], 'candidates': sorted(candidates), 'protected_versions': versions,
            'state': {'versions': versions, 'snapshots': [stable_root(path) for path in snapshots],
                      'subvolumes': ROOT_VIEW.get().get('identities', []),
                      'rollback_records': ROOT_VIEW.get().get('records', {})}}


def quota_status():
    """Read the quota-tree status item, never change quota state.

    Linux btrfs.h: TREE_SEARCH has a 104-byte key in a 4096-byte argument;
    btrfs_tree.h: quota tree 8, status key (0, 240, 0). Only version 1 with
    exactly ON is full healthy accounting; reject rescan, inconsistent,
    simple mode, disabled state and future unknown flags.
    """
    remaining()
    buffer = bytearray(4096)
    struct.pack_into('=7Q4I4Q', buffer, 0, 8, 0, 0, 0, 0, 0, 2**64 - 1,
                     240, 240, 1, 0, 0, 0, 0, 0)
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        fcntl.ioctl(descriptor, (3 << 30) | (4096 << 16) | (0x94 << 8) | 17, buffer, True)
    finally:
        os.close(descriptor)
    require(struct.unpack_from('=I', buffer, 64)[0] == 1)
    _unused, objectid, offset, kind, length = struct.unpack_from('=QQQII', buffer, 104)
    require((objectid, offset, kind) == (0, 0, 240) and 32 <= length <= 40)
    version, generation, flags, _unused = struct.unpack_from('<4Q', buffer, 136)
    require(version == 1 and flags == 1)
    return generation


def qgroup_inventory(text):
    """Parse complete parent/child relationships; never infer missing members."""
    groups = {}
    for line in text.splitlines():
        remaining()
        fields = line.split(maxsplit=5)
        if not fields or fields[0].lower() == 'qgroupid' or set(fields[0]) == {'-'}:
            continue
        require(len(groups) < MAX_ENTRIES and len(fields) >= 5)
        identity, referenced, exclusive, parents, children = fields[:5]
        require(re.fullmatch(r'[0-9]+/[0-9]+', identity) and identity not in groups
                and referenced.isdigit() and exclusive.isdigit() and int(exclusive) <= int(referenced))
        links = []
        for value in (parents, children):
            values = [] if value == '-' else value.split(',')
            require(all(re.fullmatch(r'[0-9]+/[0-9]+', item) for item in values)
                    and len(values) == len(set(values)))
            links.append(set(values))
        groups[identity] = {'bytes': int(exclusive), 'parents': links[0], 'children': links[1],
                            'path': fields[5] if len(fields) > 5 else ''}
    require(bool(groups))
    for identity, item in groups.items():
        remaining()
        level = int(identity.split('/')[0])
        require(level > 0 or not item['children'])
        for parent in item['parents']:
            require(parent in groups and identity in groups[parent]['children']
                    and int(parent.split('/')[0]) > level)
        for child in item['children']:
            require(child in groups and identity in groups[child]['parents']
                    and int(child.split('/')[0]) < level)
    return groups


def aggregate_usage(groups, wanted):
    descendants = {}
    for identity in sorted(groups, key=lambda value: int(value.split('/')[0])):
        remaining()
        if identity.startswith('0/'):
            descendants[identity] = {identity}
        else:
            descendants[identity] = set().union(*(descendants[child] for child in groups[identity]['children']))
    matches = [identity for identity, leaves in descendants.items()
               if not identity.startswith('0/') and leaves == wanted]
    # Multiple aggregate candidates can signal an ambiguous administration
    # layout; individual exclusive bytes remain a valid lower bound.
    return groups[matches[0]]['bytes'] if len(matches) == 1 else None


def subvolume_id(path):
    value = command(['btrfs', 'subvolume', 'show', str(path)])
    matches = re.findall(r'^\s*Subvolume ID:\s*([0-9]+)\s*$', value, re.M)
    require(len(matches) == 1)
    return '0/' + matches[0]


@root_view
@bounded
def snapshot_usage():
    """Use an existing exact aggregate, or report individual exclusive data."""
    unavailable = {'bytes': None, 'reason': _('Exclusive snapshot usage is unavailable.')}
    try:
        paths = snapshot_paths()
        if not paths:
            return {'bytes': 0, 'reason': _('No retained snapshots were found.')}
        generation = quota_status()
        wanted = {subvolume_id(path) for path in paths}
        require(len(wanted) == len(paths) and subvolume_id('/') not in wanted)
        # Warnings signal disabled/inconsistent accounting even on exit zero.
        result = subprocess.run(['btrfs', 'qgroup', 'show', '--raw', '-p', '-c', '/'], env=ENV, cwd='/',
                                stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=remaining())
        if result.returncode or result.stderr.strip() or len(result.stdout) > MAX_OUTPUT:
            return unavailable
        groups = qgroup_inventory(result.stdout)
        require(all(identity in groups and not groups[identity]['path'].startswith('<') for identity in wanted))
        require(snapshot_paths() == paths and {subvolume_id(path) for path in paths} == wanted
                and subvolume_id('/') not in wanted and quota_status() == generation)
        total = sum(groups[identity]['bytes'] for identity in wanted)
        aggregate = aggregate_usage(groups, wanted)
        if aggregate is not None:
            require(aggregate >= total)
            return {'bytes': aggregate, 'exclusive_bytes': total,
                    'reason': _('Space exclusively held by retained snapshots, including their shared extents.')}
        return {'bytes': None, 'exclusive_bytes': total,
                'reason': _('Individual snapshots hold at least {size} of exclusive space; space shared between snapshots is unavailable.').format(
                    size=_('{value} B').format(value=format_number(total, grouping=True)))}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return unavailable
