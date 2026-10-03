"""Pure, deterministic planning. No subprocess, archinstall import or disk access."""
import copy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .constants import BTRFS_OPTIONS, GIB, MIB, MIN_DISK, SUBVOLUMES
from .errors import Code, require


@dataclass
class Partition:
    path: str | None
    number: int
    start: int
    size: int
    fs: str
    format: bool
    mountpoint: str | None = None
    esp: bool = False
    subvolumes: dict = field(default_factory=dict)
    uuid: str | None = None
    partuuid: str | None = None
    existing_subvolumes: list | None = None

    @property
    def options(self):
        if self.fs == 'btrfs':
            return BTRFS_OPTIONS.copy()
        return ['umask=0077'] if self.esp else ['noatime']


@dataclass
class Plan:
    config: dict = field(repr=False)  # Contains the in-memory password, never repr it.
    disk: dict
    partitions: list[Partition]
    summary: list[str]
    warnings: list[dict]
    fingerprint: str

    @property
    def root(self):
        return next(p for p in self.partitions
                    if p.mountpoint == '/' or '/' in p.subvolumes.values())

    @property
    def btrfs(self):
        return self.root.fs == 'btrfs'

    @property
    def mounts(self):
        rows = []
        for p in self.partitions:
            if p.mountpoint:
                rows.append((p, p.mountpoint, None))
            rows.extend((p, mp, name) for name, mp in p.subvolumes.items())
        return sorted(rows, key=lambda row: (len(PurePosixPath(row[1]).parts), row[1]))


def fingerprint(disk):
    identity = {k: disk.get(k) for k in ('id', 'path', 'size_bytes', '_sector_size',
                                        '_identity', '_pttype')}
    identity['partitions'] = [{k: p.get(k) for k in
                              ('id', 'path', 'number', 'uuid', 'fs', 'size_bytes',
                               'start_bytes', 'esp', '_subvolumes')}
                             for p in disk['partitions']]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def validate_config(value):
    require(isinstance(value, dict), Code.BAD_CONFIG, 'Config must be an object.')
    c = copy.deepcopy(value)
    allowed = {'mode', 'disk_id', 'fs', 'mounts', 'shrink_bytes', 'hostname', 'timezone',
               'layouts', 'user', 'repo_server', 'online_update', 'scale_guess'}
    require(not (set(c) - allowed), Code.BAD_CONFIG, 'Unknown config fields.')
    require(c.get('mode') in ('erase', 'manual', 'alongside'), Code.BAD_CONFIG, 'Invalid mode.')
    require(isinstance(c.get('disk_id'), str), Code.BAD_CONFIG, 'A disk ID is required.')
    if c['mode'] != 'manual':
        require(c.get('fs') in ('btrfs', 'ext4'), Code.BAD_CONFIG, 'Choose btrfs or ext4.')
    c.setdefault('hostname', 'emaki')
    require(isinstance(c['hostname'], str) and re.fullmatch(
        r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', c['hostname']),
        Code.BAD_CONFIG, 'Hostname must be one DNS label (1–63 characters).')
    c.setdefault('timezone', 'UTC')
    require(isinstance(c['timezone'], str) and re.fullmatch(r'[A-Za-z0-9_+/-]+', c['timezone'])
            and '..' not in c['timezone'].split('/') and not c['timezone'].startswith('/'),
            Code.BAD_CONFIG, 'Invalid timezone.')
    try:
        ZoneInfo(c['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        require(False, Code.BAD_CONFIG, 'Unknown timezone.')
    c.setdefault('layouts', ['us'])
    require(isinstance(c['layouts'], list) and 1 <= len(c['layouts']) <= 4
            and all(isinstance(x, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,19}', x)
                    for x in c['layouts']) and len(set(c['layouts'])) == len(c['layouts']),
            Code.BAD_CONFIG, 'Supply one to four distinct XKB layouts.')
    u = c.get('user')
    require(isinstance(u, dict) and not (set(u) - {'name', 'login', 'password'}),
            Code.BAD_CONFIG, 'Invalid account.')
    login = u.get('login')
    require(isinstance(login, str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', login)
            and login not in ('root', 'greeter', 'live', 'nobody'),
            Code.BAD_CONFIG, 'Invalid user login.')
    u.setdefault('name', login)
    require(isinstance(u['name'], str) and len(u['name']) <= 128
            and not any(ord(x) < 32 or x in ':\x7f' for x in u['name']),
            Code.BAD_CONFIG, 'Invalid full name.')
    password = u.get('password')
    require(isinstance(password, str) and 1 <= len(password.encode()) <= 1024
            and not any(x in password for x in ('\0', '\n', '\r')),
            Code.BAD_CONFIG, 'Password must be nonempty and contain no line breaks or NUL.')
    c.setdefault('online_update', True)
    require(type(c['online_update']) is bool, Code.BAD_CONFIG, 'online_update must be boolean.')
    server = c.get('repo_server')
    if server is not None:
        require(isinstance(server, str) and len(server) <= 2048
                and not any(ch.isspace() or ord(ch) < 32 for ch in server),
                Code.BAD_CONFIG, 'Invalid repository URL.')
        parsed = urlsplit(server)
        require(parsed.scheme in ('http', 'https') and parsed.hostname
                and not parsed.username and not parsed.password and not parsed.fragment,
                Code.BAD_CONFIG, 'Repository URL must be HTTP(S), without credentials or fragments.')
    scale = c.get('scale_guess')
    require(scale is None or (type(scale) in (int, float) and 0.5 <= scale <= 4),
            Code.BAD_CONFIG, 'Scale must be null or a number from 0.5 to 4.')
    return c


def validate_disk(disk, inventory):
    require(inventory.get('uefi') is True, Code.UEFI_REQUIRED, 'UEFI boot is required.')
    require(inventory.get('_boot_medium_known') is True, Code.UNSAFE_DISK,
            'Cannot identify the live boot medium; installation is refused.')
    require(not disk['is_boot_medium'], Code.BOOT_MEDIUM, 'The live boot medium cannot be a target.')
    require(not disk.get('_busy', True) and not any(p.get('mountpoint') for p in disk['partitions']),
            Code.DISK_BUSY, 'The target or one of its partitions is mounted, held, or in use.')
    require(not disk.get('_read_only', True), Code.UNSAFE_DISK, 'The target is read-only.')
    require(disk['size_bytes'] >= MIN_DISK, Code.DISK_TOO_SMALL, 'The disk must be at least 24 GiB.')
    require(disk.get('_sector_size') in (512, 4096), Code.UNSAFE_DISK, 'Unsupported sector geometry.')
    require(re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', disk['path']), Code.UNSAFE_DISK,
            'Only directly attached whole block devices are supported.')


def erase_partitions(size, fs):
    require(size >= MIN_DISK, Code.DISK_TOO_SMALL, 'The disk must be at least 24 GiB.')
    require(fs in ('btrfs', 'ext4'), Code.BAD_CONFIG, 'Unsupported filesystem.')
    # Half-open extents, 1 MiB alignment, reserve 1 MiB for the backup GPT.
    end = (size - MIB) // MIB * MIB
    return [Partition(None, 1, MIB, GIB, 'vfat', True, '/efi', True),
            Partition(None, 2, GIB + MIB, end - GIB - MIB, fs, True,
                      None if fs == 'btrfs' else '/',
                      subvolumes=SUBVOLUMES.copy() if fs == 'btrfs' else {})]


def shrink_bounds(new_size, min_bytes, old_size, disk_free=0):
    """Bounds only; never constitutes permission to resize an NTFS volume."""
    require(type(new_size) is int and min_bytes + GIB <= new_size < old_size,
            Code.SHRINK_BOUNDS, 'Windows must retain its minimum size plus 1 GiB.')
    require(old_size - new_size + disk_free >= MIN_DISK,
            Code.SHRINK_BOUNDS, 'At least 24 GiB must remain for Emaki.')


def _mountpoint(mp):
    require(isinstance(mp, str) and re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/?)*', mp)
            and str(PurePosixPath(mp)) == mp and '..' not in PurePosixPath(mp).parts,
            Code.MANUAL_LAYOUT, 'Mountpoints must be canonical absolute paths.')
    # Keep the kernel, userspace and package database in one root snapshot.
    protected = ('/boot', '/usr', '/etc', '/dev', '/proc', '/sys', '/run', '/var/lib/pacman')
    require(mp not in ('/var', '/var/lib', '/var/cache', '/var/cache/pacman')
            and not any(mp == name or mp.startswith(name + '/') for name in protected)
            and not mp.startswith(('/efi/', '/.snapshots/')),
            Code.MANUAL_LAYOUT, 'Unsupported mountpoint; /boot, /usr and the pacman database must stay in root.')


def manual_partitions(rows, disk):
    require(disk.get('_pttype') == 'gpt', Code.MANUAL_LAYOUT, 'Manual installation requires GPT.')
    require(isinstance(rows, list) and 2 <= len(rows) <= 64, Code.MANUAL_LAYOUT,
            'Assign exactly one root and one ESP, plus optional data mounts.')
    known = {p['id']: p for p in disk['partitions']}
    grouped, mountpoints = {}, set()
    for row in rows:
        require(isinstance(row, dict) and not (set(row) -
                {'partition_id', 'mountpoint', 'fs', 'format', 'subvolume'}),
                Code.MANUAL_LAYOUT, 'Invalid mount row.')
        require(isinstance(row.get('partition_id'), str) and row['partition_id'] in known,
                Code.MANUAL_LAYOUT, 'Partition is not on the selected disk.')
        p = known[row['partition_id']]
        require(p.get('_geometry_known') is True and p['size_bytes'] > 0
                and p['start_bytes'] > 0 and not p.get('_read_only', False),
                Code.MANUAL_LAYOUT, 'Partition geometry is unknown or the partition is read-only.')
        mp, fs, fmt, sv = (row.get(k) for k in ('mountpoint', 'fs', 'format', 'subvolume'))
        _mountpoint(mp)
        require(mp not in mountpoints, Code.MANUAL_LAYOUT, 'Duplicate mountpoint.')
        mountpoints.add(mp)
        require(fs in ('btrfs', 'ext4', 'vfat') and type(fmt) is bool,
                Code.MANUAL_LAYOUT, 'Invalid filesystem or format flag.')
        require(fmt or fs == p['fs'], Code.MANUAL_LAYOUT,
                'Changing filesystem type requires format: true.')
        require(sv is None or (fs == 'btrfs' and isinstance(sv, str)
                and re.fullmatch(r'[@A-Za-z0-9_-][@A-Za-z0-9_.-]{0,63}', sv)
                and sv not in ('.', '..')), Code.MANUAL_LAYOUT,
                'Use a simple top-level btrfs subvolume name.')
        require(mp != '/' or fs in ('btrfs', 'ext4'), Code.MANUAL_LAYOUT,
                'Root must be btrfs or ext4.')
        require(mp != '/' or p['size_bytes'] >= 20 * GIB, Code.ROOT_TOO_SMALL,
                'Root partition must be at least 20 GiB.')
        if mp == '/efi':
            require(fs == 'vfat' and p['esp'] and sv is None, Code.MANUAL_LAYOUT,
                    '/efi must be an existing GPT ESP with FAT32.')
            free = p['size_bytes'] - MIB if fmt else p.get('_esp_free_bytes')
            require(free is not None and free >= 32 * MIB, Code.ESP_SPACE,
                    'ESP needs at least 32 MiB of verified free space.')
        else:
            require(not p['esp'], Code.MANUAL_LAYOUT,
                    'An ESP may only be mounted at /efi.')
        prior = grouped.setdefault(p['id'], [])
        require(not prior or (fs == 'btrfs' and sv is not None and
                all(r.get('subvolume') is not None and r['fs'] == fs and r['format'] == fmt for r in prior)),
                Code.MANUAL_LAYOUT, 'Repeated partition rows need distinct subvolumes and identical format flags.')
        prior.append(row)
    require('/' in mountpoints and '/efi' in mountpoints, Code.MANUAL_LAYOUT,
            'Exactly one / and one /efi are required.')
    result = []
    for ident, group in grouped.items():
        p, row = known[ident], group[0]
        fs, fmt = row['fs'], row['format']
        is_root = any(r['mountpoint'] == '/' for r in group)
        subvols = {}
        if fs == 'btrfs':
            require(fmt or p.get('_btrfs_devices') == 1, Code.MANUAL_LAYOUT,
                    'Only verified single-device btrfs filesystems can be preserved.')
            if any(r.get('subvolume') for r in group):
                subvols = {r['subvolume']: r['mountpoint'] for r in group}
                require(len(subvols) == len(group), Code.MANUAL_LAYOUT, 'Duplicate subvolume name.')
            elif is_root:
                subvols = {name: mp for name, mp in SUBVOLUMES.items()
                           if mp == '/' or mp not in mountpoints}
            if is_root:
                require('/.snapshots' in subvols.values(), Code.MANUAL_LAYOUT,
                        'Btrfs root requires a separate snapshot subvolume on the root filesystem.')
            if not fmt and subvols:
                existing = p.get('_subvolumes')
                require(existing is not None, Code.MANUAL_LAYOUT, 'Cannot inspect existing btrfs subvolumes.')
                # Missing names may be created after confirm; never delete existing subvolumes.
        result.append(Partition(p['path'], p['number'], p['start_bytes'], p['size_bytes'],
                                fs, fmt, None if subvols else row['mountpoint'], p['esp'],
                                subvols, p['uuid'], p.get('_partuuid'), p.get('_subvolumes')))
    expanded = [mp for p in result for mp in ([p.mountpoint] if p.mountpoint else []) + list(p.subvolumes.values())]
    require(len(expanded) == len(set(expanded)), Code.MANUAL_LAYOUT, 'Implicit and explicit mounts overlap.')
    root = next(p for p in result if p.mountpoint == '/' or '/' in p.subvolumes.values())
    if root.fs == 'btrfs':
        require(set(SUBVOLUMES.values()) <= set(expanded), Code.MANUAL_LAYOUT,
                'Btrfs needs separate /home, /var/log, package cache and /.snapshots mounts.')
    # archinstall sorts btrfs partitions without a mountpoint as '/'. Keep the
    # root partition first even if the UI submitted the ESP/home rows first.
    return sorted(result, key=lambda p: not (p.mountpoint == '/' or '/' in p.subvolumes.values()))


def make_plan(config, inventory):
    c = validate_config(config)
    disk = next((d for d in inventory['disks'] if d['id'] == c['disk_id']), None)
    require(disk is not None, Code.DISK_NOT_FOUND, 'Selected disk no longer exists.')
    validate_disk(disk, inventory)
    require(c['mode'] != 'alongside', Code.UNSUPPORTED_MODE,
            'Windows alongside installation is deferred; no NTFS resize is performed in 0.1.0.')
    parts = (erase_partitions(disk['size_bytes'], c['fs']) if c['mode'] == 'erase'
             else manual_partitions(c.get('mounts'), disk))
    summary = [f"{c['mode'].capitalize()} installation on {disk['path']} ({disk['model']})."]
    if c['mode'] == 'erase':
        summary.append('Erase all partitions and create a new GPT partition table.')
    for p in parts:
        dest = ', '.join(f'{name} → {mp}' for name, mp in p.subvolumes.items()) or p.mountpoint
        summary.append(f"Partition {p.number}: {'format' if p.format else 'preserve'} {p.fs}, {p.size / GIB:.2f} GiB; {dest}.")
    summary.extend(['GRUB, linux + linux-lts; /boot inside root; zram only; no encryption.',
                    f"Account: {c['user']['login']}; hostname: {c['hostname']}; timezone: {c['timezone']}.",
                    'Packages are installed from the signed offline USB repository.'])
    warnings = []
    if c['mode'] == 'manual':
        warnings.append({'code': 'preserved_files', 'msg': 'Unformatted filesystems keep data; installation still writes system and account files.'})
    if disk.get('_id_fallback'):
        warnings.append({'code': 'unstable_id', 'msg': 'No by-id name is available; device identity and geometry will be rechecked.'})
    return Plan(c, copy.deepcopy(disk), parts, summary, warnings, fingerprint(disk))
