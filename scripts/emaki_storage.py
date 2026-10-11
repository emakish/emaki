# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded storage inventories and descriptor-relative cleanup of fixed categories.

Preview tokens contain no authority: every cleanup rebuilds its allowlisted inventory.
Directories are opened without following links, never traversing a nested mount. Root
operations reject writable ancestors; user trash operations never run with elevation.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import subprocess
import sys
import time
import uuid

import emaki_paths

_source_backend = Path(__file__).resolve().parent.parent / 'update-manager'
if (_source_backend / 'storage_arch.py').is_file():
    sys.path.insert(0, str(_source_backend))
import storage_arch
from emaki_update_space import mount_details

MAX_ENTRIES = 20000
MAX_DEPTH = 64
LOG_ROOTS = (Path('/var/log/journal'), Path('/run/log/journal'))
LOG_AGE = 7 * 86400
ARCHIVED_JOURNAL = re.compile(r'(?:system|user-[0-9]+)@[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{16}\.journal\Z')
MACHINE_DIRECTORY = re.compile(r'[0-9a-f]{32}(?:\.[A-Za-z0-9_.-]+)?\Z')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
UNAVAILABLE = '[PLACEHOLDER] Storage details are unavailable. Try again.'
STALE = '[PLACEHOLDER] Storage changed after the preview. Preview again before cleaning.'
ALLOCATED = '[PLACEHOLDER] Allocated file bytes; shared extents may be counted more than once.'
RECLAIM = ('[PLACEHOLDER] Unshared file data is a conservative estimate, excluding metadata '
           'and shared extents. Compressed or unsupported data is unknown. Open files can '
           'delay space returning. Free-space change is measured after cleanup.')


def failure(message=UNAVAILABLE):
    raise RuntimeError(message)


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_blocks]


def mount_id(fd):
    for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines():
        if line.startswith('mnt_id:'):
            return int(line.split(':', 1)[1])
    failure()


def safe_directory(info, owner, privileged):
    if not stat.S_ISDIR(info.st_mode):
        failure()
    if privileged and (info.st_uid != 0 or info.st_mode & 0o022):
        # A sticky, root-owned traversal directory cannot rename another owner's child.
        if not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            failure('[PLACEHOLDER] A storage directory is not safe to clean.')
    if not privileged and info.st_uid != owner and info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
        failure('[PLACEHOLDER] A trash ancestor can be changed by another account.')


@contextlib.contextmanager
def open_directory(path, owner, privileged=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        failure()
    fd = os.open('/', DIR_FLAGS)
    try:
        safe_directory(os.fstat(fd), owner, privileged)
        for name in path.parts[1:]:
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            safe_directory(os.fstat(fd), owner, privileged)
        info = os.fstat(fd)
        if info.st_uid != owner or info.st_mode & 0o022:
            failure('[PLACEHOLDER] A storage directory is not safe to clean.')
        yield fd
    finally:
        os.close(fd)


def home_path():
    # Elevated inspection never trusts a caller-supplied HOME or XDG directory.
    uid = os.getuid()
    if os.geteuid() == 0:
        value = os.environ.get('PKEXEC_UID', '0')
        if not re.fullmatch(r'[0-9]+', value):
            failure()
        uid = int(value)
    return Path(pwd.getpwuid(uid).pw_dir)


def trash_path():
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        failure('[PLACEHOLDER] Trash cleanup must run as your own account, without authentication.')
    data = Path(os.environ.get('XDG_DATA_HOME') or home_path() / '.local/share')
    if not data.is_absolute() or '..' in data.parts:
        failure('[PLACEHOLDER] The trash location is unavailable.')
    return data / 'Trash'


def filesystem(path):
    path = Path(path)
    data = os.statvfs(path)
    source, kind = mount_details(path)
    pool = source or f'device:{path.stat().st_dev}'
    if kind == 'btrfs':
        # Linux BTRFS_IOC_FS_INFO returns the real filesystem UUID, shared by all
        # subvolumes and devices. statvfs.f_fsid is per subvolume on this filesystem.
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            info = bytearray(1024)
            fcntl.ioctl(fd, 0x8400941f, info)
            pool = 'btrfs:' + str(uuid.UUID(bytes=bytes(info[16:32])))
        finally:
            os.close(fd)
    return dict(path=str(path), filesystem=kind or 'unknown', pool=pool,
                total_bytes=data.f_blocks * data.f_frsize,
                used_bytes=(data.f_blocks - data.f_bfree) * data.f_frsize,
                available_bytes=data.f_bavail * data.f_frsize, shared_with=None)


def filesystems():
    result = []
    pools = {}
    for path in (Path('/'), home_path()):
        try:
            item = filesystem(path)
            item['shared_with'] = pools.get(item['pool'])
            pools.setdefault(item['pool'], str(path))
            result.append(item)
        except OSError:
            result.append(dict(path=str(path), filesystem='unknown', pool=None,
                               total_bytes=None, used_bytes=None, available_bytes=None,
                               shared_with=None))
    return result


class Inventory:
    def __init__(self, kind, owner):
        self.kind = kind
        self.owner = owner
        self.entries = []
        self.roots = {}
        self.state = {}

    def root(self, path, fd):
        info = os.fstat(fd)
        # Directory timestamps change during normal journal/cache activity. Identity
        # and permission checks matter; each selected child has its own full stamp.
        self.roots[str(path)] = [info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                 info.st_gid, mount_id(fd)]

    def add(self, root, relative, info):
        if len(self.entries) >= MAX_ENTRIES:
            failure('[PLACEHOLDER] There are too many files for one safe cleanup preview.')
        if info.st_uid != self.owner and self.kind not in ('trash', 'usage'):
            failure('[PLACEHOLDER] A storage file has unexpected ownership.')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                or (self.kind == 'trash' and stat.S_ISLNK(info.st_mode))):
            failure('[PLACEHOLDER] A storage entry is not a regular file, folder or trash link.')
        self.entries.append(dict(root=str(root), relative=str(relative),
                                 path=str(root / relative), bytes=info.st_blocks * 512,
                                 identity=identity(info), directory=stat.S_ISDIR(info.st_mode)))

    def token(self):
        data = dict(kind=self.kind, owner=self.owner, roots=self.roots, state=self.state,
                    entries=sorted(self.entries, key=lambda item: item['path']))
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def preview(self):
        known, unknown = exclusive_data(self)
        return dict(schema_version=1, status='preview', kind=self.kind, token=self.token(),
                    entries=[dict(path=item['path'], bytes=item['bytes']) for item in
                             sorted(self.entries, key=lambda item: item['path'])],
                    allocated_bytes=sum(item['bytes'] for item in self.entries),
                    reclaimable_bytes=known if not unknown else None,
                    reclaimable_lower_bound_bytes=known, reclaimable_unknown_files=unknown,
                    reason=RECLAIM)


def child_info(fd, name, expected_mount):
    child = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    try:
        if mount_id(child) != expected_mount:
            failure('[PLACEHOLDER] A nested mount cannot be included in cleanup.')
        return os.fstat(child)
    finally:
        os.close(child)


def walk(inventory, root, fd, prefix=Path('.'), depth=0):
    if depth > MAX_DEPTH:
        failure('[PLACEHOLDER] A trash folder is too deep for one safe cleanup preview.')
    expected_mount = inventory.roots[str(root)][-1]
    for name in sorted(os.listdir(fd)):
        relative = prefix / name
        info = child_info(fd, name, expected_mount)
        inventory.add(root, relative, info)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            try:
                if identity(os.fstat(child)) != identity(info):
                    failure(STALE)
                walk(inventory, root, child, relative, depth + 1)
            finally:
                os.close(child)


def trash_inventory():
    root = trash_path()
    result = Inventory('trash', os.getuid())
    try:
        with open_directory(root, os.getuid()) as parent:
            result.state['trash'] = [os.fstat(parent).st_dev, os.fstat(parent).st_ino]
            for directory in ('files', 'info'):
                path = root / directory
                with open_directory(path, os.getuid()) as fd:
                    if mount_id(fd) != mount_id(parent):
                        failure('[PLACEHOLDER] A nested mount cannot be included in cleanup.')
                    result.root(path, fd)
                    if directory == 'files':
                        walk(result, path, fd)
                    else:
                        for name in sorted(os.listdir(fd)):
                            if not name.endswith('.trashinfo'):
                                continue
                            info = child_info(fd, name, mount_id(fd))
                            if not stat.S_ISREG(info.st_mode):
                                failure('[PLACEHOLDER] Trash metadata is not a regular file.')
                            result.add(path, Path(name), info)
    except FileNotFoundError:
        # A half-created trash must not hide files already inventoried.
        if root.exists():
            failure('[PLACEHOLDER] The trash layout is incomplete. Open it in Files first.')
    return result


def log_inventory():
    result = Inventory('logs', 0)
    cutoff = time.time() - LOG_AGE
    for root in LOG_ROOTS:
        try:
            with open_directory(root, 0, True) as fd:
                result.root(root, fd)
                for directory in sorted(os.listdir(fd)):
                    if not MACHINE_DIRECTORY.fullmatch(directory):
                        continue
                    info = child_info(fd, directory, mount_id(fd))
                    if not stat.S_ISDIR(info.st_mode):
                        failure('[PLACEHOLDER] A journal directory is not safe to clean.')
                    with open_directory(root / directory, 0, True) as machine:
                        for name in sorted(os.listdir(machine)):
                            if not ARCHIVED_JOURNAL.fullmatch(name):
                                continue
                            info = child_info(machine, name, mount_id(fd))
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                failure('[PLACEHOLDER] An archived journal is not safe to clean.')
                            if info.st_mtime <= cutoff:
                                result.add(root, Path(directory) / name, info)
        except FileNotFoundError:
            if root.exists():
                failure(STALE)
    return result


def cache_inventory():
    source = storage_arch.cache_inventory()
    result = Inventory('cache', 0)
    result.state = dict(retention=source['state'], protected_versions=source['protected_versions'])
    roots = {Path(root) for root in source['roots']}
    for root in sorted(roots):
        with open_directory(root, 0, True) as fd:
            result.root(root, fd)
            for path in sorted(Path(path) for path in source['candidates'] if Path(path).parent == root):
                info = child_info(fd, path.name, mount_id(fd))
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    failure('[PLACEHOLDER] A package archive is not safe to clean.')
                result.add(root, Path(path.name), info)
    if any(Path(path).parent not in roots for path in source['candidates']):
        failure()
    return result


def inventory(kind):
    return {'cache': cache_inventory, 'logs': log_inventory, 'trash': trash_inventory}[kind]()


def allocated_tree(root, owner, privileged):
    result = Inventory('usage', owner)
    with open_directory(root, owner, privileged) as fd:
        result.root(root, fd)
        # Usage shares the same traversal bounds and mount/link protections as cleanup.
        walk(result, root, fd)
    return sum(item['bytes'] for item in result.entries)


def status():
    categories = {}
    try:
        categories['snapshots'] = storage_arch.snapshot_usage()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        categories['snapshots'] = dict(bytes=None, reason=UNAVAILABLE)
    for kind in ('cache', 'logs', 'trash'):
        try:
            if kind == 'trash':
                count = sum(item['bytes'] for item in trash_inventory().entries)
                reason = '[PLACEHOLDER] Home trash only. Other drives are not included. ' + ALLOCATED
            elif kind == 'logs':
                count = sum(allocated_tree(root, 0, True) for root in LOG_ROOTS if root.exists())
                reason = '[PLACEHOLDER] System journals. Cleanup includes only archives older than seven days. ' + ALLOCATED
            else:
                # The backend owns cache locations; status must not acquire package locks.
                roots = storage_arch.cache_roots()
                count = sum(allocated_tree(root, 0, True) for root in roots)
                reason = '[PLACEHOLDER] Installed and rollback package versions are kept. ' + ALLOCATED
            categories[kind] = dict(bytes=count, reason=reason)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            reason = str(error) if isinstance(error, RuntimeError) else UNAVAILABLE
            categories[kind] = dict(bytes=None, reason=reason)
    return dict(schema_version=1, status='ready', filesystems=filesystems(), categories=categories,
                message='')


@contextlib.contextmanager
def parent_fd(root, relative, owner, privileged, root_stamp=None):
    with open_directory(root, owner, privileged) as rootfd:
        info = os.fstat(rootfd)
        if root_stamp is not None and [info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                       info.st_gid, mount_id(rootfd)] != root_stamp:
            failure(STALE)
        fd = os.dup(rootfd)
        try:
            for part in Path(relative).parts[:-1]:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                if mount_id(fd) != mount_id(rootfd):
                    failure(STALE)
                if privileged:
                    safe_directory(os.fstat(fd), owner, True)
            yield fd
        finally:
            os.close(fd)


def file_extents(fd):
    """Read physical unshared data bytes, without flushing or altering a file.

    Linux FIEMAP marks shared, delayed, compressed and inline extents explicitly.
    Never substitute logical extent length for encoded physical allocation. Limit
    fragmented files to 4096 extents; an incomplete map is unknown, not zero.
    """
    count = 4096
    data = bytearray(32 + count * 56)
    struct.pack_into('=QQIIII', data, 0, 0, (1 << 64) - 1, 0, 0, count, 0)
    fcntl.ioctl(fd, 0xc020660b, data)  # FS_IOC_FIEMAP, struct fiemap is 32 bytes.
    mapped = struct.unpack_from('=I', data, 20)[0]
    if mapped > count:
        return None
    total = 0
    last = False
    for index in range(mapped):
        offset = 32 + 56 * index
        length = struct.unpack_from('=Q', data, offset + 16)[0]
        flags = struct.unpack_from('=I', data, offset + 40)[0]
        # Only LAST, UNWRITTEN, MERGED and SHARED are interpretable here.
        if flags & ~(0x1 | 0x800 | 0x1000 | 0x2000):
            return None
        if not flags & 0x2000:
            total += length
        last = bool(flags & 1)
    return total if not mapped or last else None


def exclusive_data(plan):
    """Return known unshared data and the number of files with uncertain maps."""
    total = 0
    unknown = 0
    for item in plan.entries:
        mode, links = item['identity'][2], item['identity'][5]
        # Directory/link metadata is deliberately absent from a data-only estimate.
        if not stat.S_ISREG(mode) or links != 1:
            continue
        try:
            with parent_fd(item['root'], item['relative'], plan.owner, plan.kind != 'trash', plan.roots[item['root']]) as parent:
                fd = os.open(Path(item['relative']).name,
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
                try:
                    if identity(os.fstat(fd)) != item['identity']:
                        unknown += 1
                        continue
                    size = file_extents(fd)
                    if size is None or identity(os.fstat(fd)) != item['identity']:
                        unknown += 1
                        continue
                    total += size
                finally:
                    os.close(fd)
        except OSError:
            unknown += 1
    return total, unknown


def free_pools(roots):
    pools = {}
    for path in roots:
        data = filesystem(path)
        pools[data['pool']] = data['available_bytes']
    return pools


def clean(plan, token):
    if plan.token() != token:
        failure(STALE)
    # Payloads go first so interruption never discards restore metadata for a
    # still-present trash item. Directories are removed only after their children.
    ordered = sorted(plan.entries, key=lambda item: (
        plan.kind == 'trash' and Path(item['root']).name == 'info',
        -len(Path(item['relative']).parts), item['path']))
    # Verify every entry before the first unlink, including descriptor-bound mount identity.
    for item in ordered:
        with parent_fd(item['root'], item['relative'], plan.owner, plan.kind != 'trash', plan.roots[item['root']]) as fd:
            if identity(child_info(fd, Path(item['relative']).name, plan.roots[item['root']][-1])) != item['identity']:
                failure(STALE)
    before = free_pools(plan.roots)
    removed = 0
    unlinked = {}
    try:
        for item in ordered:
            with parent_fd(item['root'], item['relative'], plan.owner, plan.kind != 'trash', plan.roots[item['root']]) as fd:
                name = Path(item['relative']).name
                info = child_info(fd, name, plan.roots[item['root']][-1])
                stamp = identity(info)
                if item['directory']:
                    # Removing previewed children changes directory size and timestamps.
                    if stamp[:6] != item['identity'][:6]:
                        failure(STALE)
                    os.rmdir(name, dir_fd=fd)
                else:
                    prior = unlinked.get(tuple(stamp[:2]))
                    expected = item['identity'].copy()
                    if prior is not None:
                        # Our earlier unlink changes the inode's link count and ctime.
                        expected[5], expected[8] = prior
                    if stamp != expected:
                        failure(STALE)
                    held = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    try:
                        if identity(os.fstat(held)) != stamp:
                            failure(STALE)
                        os.unlink(name, dir_fd=fd)
                        remaining = os.fstat(held)
                        unlinked[tuple(stamp[:2])] = (remaining.st_nlink, remaining.st_ctime_ns)
                    finally:
                        os.close(held)
                removed += 1
    except (OSError, RuntimeError):
        return dict(schema_version=1, status='error', kind=plan.kind, removed_count=removed,
                    message='[PLACEHOLDER] Cleanup stopped after removing ' + str(removed)
                    + ' entries. Remaining files were kept. Preview again before retrying.')
    try:
        after = free_pools(plan.roots)
        reclaimed = max(0, sum(after.values()) - sum(before.values())) if before.keys() == after.keys() else None
    except OSError:
        reclaimed = None
    return dict(schema_version=1, status='cleaned', kind=plan.kind, removed_count=removed,
                reclaimed_bytes=reclaimed,
                message='[PLACEHOLDER] Cleanup finished. The measured free-space change may also include other disk activity.')


def dispatch(action, kind=None, token=None):
    if action == 'status':
        return status()
    if kind in ('cache', 'logs') and os.geteuid() != 0:
        failure('[PLACEHOLDER] Authentication is required to preview or clean system storage.')
    guard = storage_arch.package_guard() if kind == 'cache' else contextlib.nullcontext()
    with guard:
        plan = inventory(kind)
        if action == 'preview':
            return plan.preview()
        return clean(plan, token)


def main(arguments):
    # No paths, arbitrary options or program names can be supplied through this interface.
    valid = (arguments == ['status', '--json'] or
             (len(arguments) == 3 and arguments[0] == 'preview' and arguments[1] in ('cache', 'logs', 'trash') and arguments[2] == '--json') or
             (len(arguments) == 4 and arguments[0] == 'cleanup' and arguments[1] in ('cache', 'logs', 'trash')
              and re.fullmatch(r'[0-9a-f]{64}', arguments[2]) and arguments[3] == '--json'))
    if not valid:
        print(json.dumps(dict(schema_version=1, status='error', message='[PLACEHOLDER] Invalid storage command.')))
        return 2
    try:
        result = dispatch(arguments[0], arguments[1] if len(arguments) > 2 else None,
                          arguments[2] if len(arguments) == 4 else None)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result = dict(schema_version=1, status='error', message=str(error) if isinstance(error, RuntimeError) else UNAVAILABLE)
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result['status'] == 'error' else 0
