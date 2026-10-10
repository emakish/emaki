"""Pure, deterministic planning. No subprocess, archinstall import or disk access."""
import copy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .constants import BTRFS_OPTIONS, GIB, MIB, MIN_DISK, SUBVOLUMES
from . import inventory as alongside
from .errors import Code, require
from .graphics import planned_packages
from .hardware import SECURE_BOOT_MESSAGE, SECURE_BOOT_UNKNOWN
from .latin_layouts import CONSOLE_CHARS, is_latin
from .render import keyboard_summary, unlock_layout


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
    mapper: str | None = None
    luks_uuid: str | None = None

    @property
    def device(self):
        return self.mapper or self.path

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
    swap_bytes: int = 0
    graphics_packages: tuple = ()

    @property
    def encrypted(self):
        return self.config['encryption'] != 'none'

    @property
    def disk_password(self):
        return (self.config['user']['password'] if self.config['encryption'] == 'account'
                else self.config.get('disk_password', ''))

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


# Every user and group name the installed system already has: `useradd` runs
# after the disk is erased and must not meet an existing name. Collected from
# usr/lib/sysusers.d/*.conf (u, u!, g) of every package in the 0.1.2 ISO's
# offline repository plus filesystem's passwd and group; `live` is the live
# session's user. Keep sorted; extend when the package set grows.
# Whether the first (default) layout must type Latin letters: the login screen and the
# launcher start in it, and a non-Latin first layout types no login name or URL until the
# person switches. Off: whether to enforce the rule is not decided yet.
REQUIRE_LATIN_FIRST = False

RESERVED_LOGINS = (
    'adm', 'alpm', 'audio', 'avahi', 'bin', 'brlapi', 'brltty', 'clock', 'cups', 'daemon',
    'dbus', 'dhcpcd', 'disk', 'dnsmasq', 'emaki-install', 'empower', 'flatpak', 'floppy', 'ftp',
    'fwupd', 'games', 'greeter', 'http', 'input', 'kmem', 'kvm', 'live', 'lock', 'log', 'lp',
    'mail', 'mem', 'named', 'nbd', 'network', 'nobody', 'openvpn', 'optical', 'partimag',
    'passim', 'pcscd', 'polkitd', 'power', 'proc', 'render', 'rfkill', 'root', 'rpc', 'rpcuser',
    'saned', 'scanner', 'seat', 'sgx', 'smmsp', 'storage', 'sys', 'systemd-coredump',
    'systemd-imds', 'systemd-journal', 'systemd-journal-remote', 'systemd-network', 'systemd-oom',
    'systemd-resolve', 'systemd-timesync', 'tss', 'tty', 'usbmux', 'users', 'utmp', 'uucp',
    'uuidd', 'vboxsf', 'video', 'wheel',
)


def console_unsafe_chars(layouts, password):
    """The characters of the password the text console types differently: each once, in code
    point order; "" when there are none.

    The login and lock screens start in the first layout, and a character it lacks is typed
    after switching to the first chosen layout that has it. The text console (Ctrl+Alt+F2, the
    login without graphics) has only the first layout's console map (latin_layouts.CONSOLE_CHARS
    says what it types with the same keys). A first layout without a record (not offered by
    this xkeyboard-config) has the us map, and only its printable ASCII counts as safe. The
    window repeats this on the You page (installer/ui/Protocol.js consoleUnsafeChars) with the
    table the worker's hello carries; the password itself is never sent for it.
    """
    first = CONSOLE_CHARS.get(layouts[0])

    def safe(char):
        printable = ' ' <= char <= '~'
        if first is None:
            return printable
        us, differs, more, _, absent = first
        if not printable:
            return char in more
        if char not in differs:
            return True
        if not us or char not in absent:
            return False
        # Typed in a later layout; its keys must be the English (US) ones the console types.
        for name in layouts[1:]:
            later = CONSOLE_CHARS.get(name)
            if later is None:
                return True
            if char not in later[4]:
                return char not in later[3]
        return False
    return ''.join(sorted({char for char in password if not safe(char)}))


def fingerprint(disk):
    identity = {k: disk.get(k) for k in ('id', 'path', 'size_bytes', '_sector_size',
                                        '_identity', '_pttype', '_fs', 'closed_encrypted')}
    identity['partitions'] = [{k: p.get(k) for k in
                              ('id', 'path', 'number', 'uuid', 'fs', 'size_bytes',
                               'start_bytes', 'esp', '_subvolumes')}
                             for p in disk['partitions']]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def alongside_fingerprint(disk):
    evidence = {'disk': fingerprint(disk), 'parts': [{k: p.get(k) for k in
                ('id', 'shrink', '_shrink_probe', '_gpt', '_esp_free_bytes', '_efi_loaders', '_partuuid', '_parttype', '_partlabel')}
                for p in disk['partitions']]}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def validate_config(value):
    require(isinstance(value, dict), Code.BAD_CONFIG, 'Config must be an object.')
    c = copy.deepcopy(value)
    allowed = {'mode', 'disk_id', 'partition_id', 'fs', 'mounts', 'shrink_bytes', 'hostname', 'timezone',
               'layouts', 'user', 'repo_server', 'online_update', 'scale_guess', 'output_scales', 'software',
               'encryption', 'disk_password', 'hibernation', 'confirmed_encrypted', 'wifi_uuid'}
    require(c.get('wifi_uuid') is None or (isinstance(c['wifi_uuid'], str) and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', c['wifi_uuid'])),
        Code.BAD_CONFIG, 'Invalid Wi-Fi connection ID.')
    require(not (set(c) - allowed), Code.BAD_CONFIG, 'Unknown config fields.')
    require(c.get('mode') in ('erase', 'manual', 'alongside'), Code.BAD_CONFIG, 'Invalid mode.')
    if c['mode'] == 'alongside':
        alongside.require_verified()  # Off by default; refused before any disk is looked at.
    require(isinstance(c.get('disk_id'), str), Code.BAD_CONFIG, 'A disk ID is required.')
    if c['mode'] != 'manual':
        require(c.get('fs') in ('btrfs', 'ext4'), Code.BAD_CONFIG, 'Choose btrfs or ext4.')
    if c['mode'] == 'alongside':
        require(isinstance(c.get('partition_id'), str) and type(c.get('shrink_bytes')) is int,
                Code.BAD_CONFIG, 'Alongside requires partition_id and integer shrink_bytes (bytes freed for Emaki).')
    confirmations = c.setdefault('confirmed_encrypted', [])
    require(isinstance(confirmations, list) and all(
        isinstance(row, dict) and set(row) == {'path', 'type', 'uuid'}
        and all(isinstance(row[key], str) and row[key] for key in row)
        and row['type'] in alongside.ENCRYPTED_TYPES for row in confirmations),
        Code.BAD_CONFIG, 'Encrypted-volume confirmations require a device path, encryption type and UUID.')
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
    if REQUIRE_LATIN_FIRST:
        require(is_latin(c['layouts'][0]), Code.BAD_CONFIG,
                'The default (first) layout must type Latin letters; a non-Latin layout can be second.')
    u = c.get('user')
    require(isinstance(u, dict) and not (set(u) - {'name', 'login', 'password'}),
            Code.BAD_CONFIG, 'Invalid account.')
    login = u.get('login')
    require(isinstance(login, str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', login)
            and login not in RESERVED_LOGINS,
            Code.BAD_CONFIG, 'Invalid user login.')
    u.setdefault('name', login)
    require(isinstance(u['name'], str) and len(u['name']) <= 128
            and not any(ord(x) < 32 or x in ':\x7f' for x in u['name']),
            Code.BAD_CONFIG, 'Invalid full name.')
    # Login and startup passwords must be typeable on an English (US) keyboard.
    # installer/ui/Protocol.js accountErrors checks the same printable ASCII range.
    password = u.get('password')
    require(isinstance(password, str) and 1 <= len(password.encode()) <= 1024,
            Code.BAD_CONFIG, 'Password must be nonempty and contain no control characters (line breaks, tabs).')
    require(password.isascii() and password.isprintable(), Code.BAD_CONFIG,
            'Use only letters, digits and symbols of the English (US) keyboard.')
    require(c.get('encryption') in ('none', 'account', 'separate'), Code.BAD_CONFIG,
            'Choose whether to encrypt the disk and which password to use.')
    if c['encryption'] == 'separate':
        password = c.get('disk_password')
        require(isinstance(password, str) and 1 <= len(password.encode()) <= 1024
                and not any(ord(x) < 32 or x == '\x7f' for x in password), Code.BAD_CONFIG,
                'Disk password must be nonempty and contain no control characters (line breaks, tabs).')
    else:
        require(not c.get('disk_password'), Code.BAD_CONFIG,
                'A separate disk password is only used with that password choice.')
    if c['encryption'] != 'none':
        # The passphrase is typed at GRUB's prompt, which reads US key positions whatever the
        # chosen layouts are (keyboard contract K5: unlock_layout(layouts, 'grub') is 'us'), so
        # it must be printable ASCII. The window says "Startup uses an English (US) keyboard".
        unlock = unlock_layout(c['layouts'], 'grub')
        disk_password = c['user']['password'] if c['encryption'] == 'account' else c['disk_password']
        require(unlock == 'us' and disk_password.isascii()
                and disk_password.isprintable(), Code.BAD_CONFIG,
                'The startup password must use characters available on an English (US) keyboard.')
        require(len(disk_password) >= 8, Code.BAD_CONFIG,
                'Use at least 8 characters for the startup password.')
        # The account password is also typed at the login screen, which starts in the first
        # layout, while GRUB reads unlock_layout(layouts, 'grub'): the window offers it for the
        # disk only when the two agree (InstallerController.accountUnlocks).
        require(c['encryption'] != 'account' or c['layouts'][0] == unlock, Code.BAD_CONFIG,
                f"One password for everything is not available: the login screen starts in {c['layouts'][0]}, "
                f'but the disk is unlocked at startup in {unlock}.')
    c.setdefault('hibernation', False)
    require(type(c['hibernation']) is bool, Code.BAD_CONFIG, 'hibernation must be boolean.')
    c.setdefault('online_update', True)
    c.setdefault('software', 'rich')
    require(c['software'] in ('rich', 'minimal'), Code.BAD_CONFIG, 'Choose Rich or Minimal software.')
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
    scales = c.get('output_scales', {})
    require(isinstance(scales, dict) and len(scales) <= 64, Code.BAD_CONFIG,
            'Display scales must be an object with at most 64 outputs.')
    for output, scale in scales.items():
        require(isinstance(output, str) and 1 <= len(output) <= 256
                and not any(ord(ch) < 32 for ch in output), Code.BAD_CONFIG,
                'Invalid display output name.')
        require(type(scale) in (int, float) and 0.5 <= scale <= 4, Code.BAD_CONFIG,
                'Display scale must be a number from 0.5 to 4.')
    return c


def validate_disk(disk, inventory):
    require(inventory.get('uefi') is True, Code.UEFI_REQUIRED,
            '64-bit UEFI is required.' if inventory.get('uefi_bits') not in (None, 64) else 'UEFI boot is required.')
    require(inventory.get('_boot_medium_known') is True, Code.UNSAFE_DISK,
            'Cannot identify the live boot medium; installation is refused.')
    require(not disk['is_boot_medium'], Code.BOOT_MEDIUM, 'The live boot medium cannot be a target.')
    require(not disk.get('_busy', True) and not any(p.get('mountpoint') for p in disk['partitions']),
            Code.DISK_BUSY, disk.get('reason') or disk.get('_busy_reason') or 'The disk is busy. Close its files and unmount its volumes [umount], then try again.')
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
    require(type(new_size) is int and min_bytes + alongside.WINDOWS_RESERVE <= new_size < old_size,
            Code.SHRINK_BOUNDS, 'Windows must retain its minimum size plus 2 GiB.')
    # Unrelated pre-existing free space cannot make the resized extent safe.
    require(old_size - new_size >= alongside.MIN_ROOT, Code.SHRINK_BOUNDS,
            'At least 32 GiB must be freed from Windows for Emaki.')


def alongside_partitions(c, disk):
    require(disk.get('_pttype') == 'gpt', Code.SHRINK_BOUNDS, 'Alongside requires GPT.')
    candidates = [p for p in disk['partitions'] if p['fs'] in ('ntfs', 'ntfs3')]
    p = next((p for p in candidates if p['id'] == c['partition_id']), None)
    require(p is not None, Code.SHRINK_BOUNDS, 'Selected Windows NTFS partition is not on this disk.')
    require(all(p['size_bytes'] > other['size_bytes'] for other in candidates if other is not p),
            Code.SHRINK_BOUNDS, 'Only an unambiguous largest Windows/NTFS partition can be offered.')
    offer = p.get('shrink') or {}
    require(offer and not offer.get('reason'), Code.SHRINK_BOUNDS,
            offer.get('reason') or 'No verified NTFS shrink bounds are available.')
    require(p.get('_geometry_known') is True and not p.get('_read_only') and not p.get('_busy'),
            Code.SHRINK_BOUNDS, 'Windows geometry is unknown or the partition is busy/read-only.')
    verified = alongside.shrink_offer(offer['min_bytes'], p['size_bytes'], p['start_bytes'])
    freed = c['shrink_bytes']
    require(type(freed) is int and freed % MIB == 0 and alongside.MIN_ROOT <= freed <= verified['max_free_bytes']
            and freed <= offer.get('max_free_bytes', 0), Code.SHRINK_BOUNDS,
            'Choose a whole-MiB Emaki size within the verified shrink bounds (at least 32 GiB).')
    new_size = p['size_bytes'] - freed
    shrink_bounds(new_size, offer['min_bytes'], p['size_bytes'])
    metadata = p.get('_gpt')
    sector = disk['_sector_size']
    require(metadata and metadata['start'] * sector == p['start_bytes']
            and (metadata['end'] + 1) * sector == p['start_bytes'] + p['size_bytes']
            and metadata['guid'] == str(p.get('_partuuid')).lower()
            and metadata['type'] == p.get('_parttype') and metadata['name'] == p.get('_partlabel'), Code.SHRINK_BOUNDS,
            'Windows GPT identity or geometry could not be verified.')
    esps = [part for part in disk['partitions'] if part['esp']]
    require(len(esps) == 1 and esps[0]['fs'] == 'vfat', Code.ESP_SPACE,
            'Alongside requires exactly one existing FAT32 ESP on the selected disk.')
    esp = esps[0]
    require(esp.get('_esp_free_bytes') is not None and esp['_esp_free_bytes'] >= 32 * MIB,
            Code.ESP_SPACE, 'ESP needs at least 32 MiB of verified free space; it will never be formatted.')
    require('EFI/Microsoft/Boot/bootmgfw.efi' in esp.get('_efi_loaders', {}), Code.SHRINK_BOUNDS,
            'No verified Microsoft EFI loader exists on this disk; automatic Windows boot setup is unavailable.')
    occupied = {part['number'] for part in disk['partitions']}
    number = next((n for n in range(1, 129) if n not in occupied), None)
    require(number is not None, Code.SHRINK_BOUNDS, 'No free GPT partition entry is available.')
    start, end = p['start_bytes'] + new_size, p['start_bytes'] + p['size_bytes']
    require(all(other is p or end <= other['start_bytes'] or start >= other['start_bytes'] + other['size_bytes']
                for other in disk['partitions']), Code.SHRINK_BOUNDS, 'Freed extent overlaps another partition.')
    return [Partition(esp['path'], esp['number'], esp['start_bytes'], esp['size_bytes'], 'vfat', False,
                      '/efi', True, uuid=esp['uuid'], partuuid=esp.get('_partuuid')),
            Partition(None, number, start, freed, c['fs'], True,
                      None if c['fs'] == 'btrfs' else '/',
                      subvolumes=SUBVOLUMES.copy() if c['fs'] == 'btrfs' else {})]


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
        # FAT has no owners or modes; creating the account in /home would fail
        # after root has already been formatted.
        require(fs != 'vfat' or not (mp == '/home' or mp.startswith('/home/')), Code.MANUAL_LAYOUT,
                '/home cannot be FAT; choose ext4 or btrfs.')
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


def xkb_rules():
    try:
        return Path('/usr/share/X11/xkb/rules/evdev.lst').read_text()
    except OSError:
        return ''


def validate_encrypted_confirmation(config, disk):
    """Require current identity for each locked volume this plan destroys."""
    mode = config['mode']
    if mode == 'alongside':
        return
    rows = config.get('mounts', [])
    formatted = ({row.get('partition_id') for row in rows
                  if isinstance(row, dict) and row.get('format') is True}
                 if isinstance(rows, list) else set())
    paths = {part['path'] for part in disk['partitions'] if part['id'] in formatted}
    for volume in disk.get('closed_encrypted', []):
        if mode != 'erase' and volume['path'] not in paths:
            continue
        warning = volume['warning']
        require(bool(volume.get('uuid')), Code.ENCRYPTED_CONFIRMATION,
                alongside.unconfirmed_identity_warning(volume['path']))
        identity = {key: volume[key] for key in ('path', 'type', 'uuid')}
        require(identity in config.get('confirmed_encrypted', []), Code.ENCRYPTED_CONFIRMATION, warning)


def make_plan(config, inventory):
    c = validate_config(config)
    graphics_packages = planned_packages(inventory)
    hardware = inventory.get("hardware", {})
    if "secure_boot" in hardware and hardware["secure_boot"] is not False:
        require(False, Code.SECURE_BOOT, SECURE_BOOT_MESSAGE if hardware["secure_boot"] else SECURE_BOOT_UNKNOWN)
    disk = next((d for d in inventory['disks'] if d['id'] == c['disk_id']), None)
    require(disk is not None, Code.DISK_NOT_FOUND, inventory.get('hardware', {}).get('disk_notice') or 'Selected disk no longer exists.')
    validate_disk(disk, inventory)
    validate_encrypted_confirmation(c, disk)
    if c['mode'] == 'alongside':
        parts = alongside_partitions(c, disk)
    else:
        parts = (erase_partitions(disk['size_bytes'], c['fs']) if c['mode'] == 'erase'
                 else manual_partitions(c.get('mounts'), disk))
    swap_bytes = storage_layout(c, parts, inventory)
    summary = [f"{c['mode'].capitalize()} installation on {disk['path']} ({disk['model']})."]
    if c['mode'] == 'erase':
        summary.append('Erase all partitions and create a new GPT partition table.')
    if c['mode'] == 'alongside':
        windows = next(p for p in disk['partitions'] if p['id'] == c['partition_id'])
        keep = windows['size_bytes'] - c['shrink_bytes']
        summary += [f"Windows keeps {keep / 10**9:.2f} GB, Emaki gets {c['shrink_bytes'] / 10**9:.2f} GB; back up your files first.",
                    'Shrink the Windows filesystem and its partition end only; preserve its start, GUID, type, name and files.',
                    'Create and format Emaki root immediately after Windows; preserve all other partitions and reuse the ESP without formatting.',
                    'Preserve Microsoft and foreign fallback EFI loaders; add Windows Boot Manager to GRUB; Emaki stays the default.',
                    'Windows will run a consistency check (chkdsk) on its first boot after resizing. Let it finish.']
    for p in parts:
        dest = ', '.join(f'{name} → {mp}' for name, mp in p.subvolumes.items()) or p.mountpoint
        summary.append(f"Partition {p.number}: {'format' if p.format else 'preserve'} {p.fs}, {p.size / GIB:.2f} GiB; {dest}.")
    summary.extend(['GRUB, linux + linux-lts; /boot inside root; zram with higher swap priority.',
                    {'none': 'Encryption: off.', 'account': 'Encryption: LUKS2 root, account password.',
                     'separate': 'Encryption: LUKS2 root, separate disk password.'}[c['encryption']],
                    (f'Hibernation: reserve {swap_bytes:,} bytes ({swap_bytes / GIB:.2f} GiB), equal to RAM.'
                     if swap_bytes else 'Hibernation: off.'),
                    f"Account: {c['user']['login']}; hostname: {c['hostname']}; timezone: {c['timezone']}.",
                    keyboard_summary(c['layouts'], xkb_rules()),
                    'Update Emaki at the end: ' + ('when connected.' if c['online_update'] else 'off.'),
                    'Software: ' + ('Rich — desktop apps, office, email, media and utilities.'
                                   if c['software'] == 'rich' else 'Minimal — Dolphin, Firefox and kitty.'),
                    ('Packages are downloaded and verified before disk changes.' if hardware.get('media_notice')
                     else 'Packages are installed from the signed offline USB repository.')])
    if graphics_packages:
        summary.append("Graphics driver packages (Nvidia): " + ", ".join(graphics_packages) + ".")
    warnings = []
    if hardware.get("media_notice"):
        warnings.append({"code": "live_media", "msg": hardware["media_notice"]})
    if disk.get("_vmd"):
        warnings.append({"code": "vmd", "msg":
                         "The installed system will include the Intel storage driver (vmd) so this disk can start."})
    if swap_bytes and c['encryption'] == 'none':
        warnings.append({'code': 'unencrypted_memory', 'msg':
                         'Hibernation writes memory, including passwords, to the disk unencrypted.'})
    if c['encryption'] != 'none':
        warnings.append({'code': 'encryption_scope', 'msg':
                         'The root partition is encrypted. The EFI partition and any separate data partitions stay unencrypted.'})
    if c['mode'] == 'manual':
        warnings.append({'code': 'preserved_files', 'msg': 'Unformatted filesystems keep data; installation still writes system and account files.'})
    if disk.get('_id_fallback'):
        warnings.append({'code': 'unstable_id', 'msg': 'No by-id name is available; device identity and geometry will be rechecked.'})
    return Plan(c, copy.deepcopy(disk), parts, summary, warnings,
                alongside_fingerprint(disk) if c['mode'] == 'alongside' else fingerprint(disk), swap_bytes, graphics_packages)


def storage_layout(config, parts, inventory):
    """Apply root storage options after any layout builder, including alongside."""
    root = next(p for p in parts if p.mountpoint == '/' or '/' in p.subvolumes.values())
    encrypted = config['encryption'] != 'none'
    require(not encrypted or root.format, Code.MANUAL_LAYOUT,
            'Encrypting root requires formatting it; existing files cannot be encrypted in place.')
    size = inventory.get('memory_bytes', 0) if config['hibernation'] else 0
    require(not config['hibernation'] or (type(size) is int and size > 0 and size % 4096 == 0),
            Code.BAD_CONFIG, 'Cannot determine RAM size for hibernation.')
    require(root.size >= 20 * GIB + size + (16 * MIB if encrypted else 0), Code.ROOT_TOO_SMALL,
            'Root needs 20 GiB plus the RAM-sized hibernation file and encryption header.')
    if size:
        require(not any((p.mountpoint or '').startswith('/swap/') or p.mountpoint == '/swap' or '/swap' in p.subvolumes.values()
                        or any(mp.startswith('/swap/') for mp in p.subvolumes.values()) for p in parts),
                Code.MANUAL_LAYOUT, '/swap is reserved for hibernation.')
        if root.fs == 'btrfs':
            require('@swap' not in root.subvolumes, Code.MANUAL_LAYOUT, '@swap is reserved for hibernation.')
            root.subvolumes['@swap'] = '/swap'
    return size
