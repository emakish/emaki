# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure, deterministic planning. No subprocess, archinstall import or disk access."""
import copy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .constants import BTRFS_OPTIONS, GIB, MIB, MIN_DISK, SUBVOLUMES, TEST_REPO_OVERRIDE
from . import inventory as alongside
from . import secureboot
from .errors import Code, require
from .graphics import planned_packages
from .hardware import SECURE_BOOT_CAS, SECURE_BOOT_NO_CA, SECURE_BOOT_UNKNOWN
from .latin_layouts import CONSOLE_CHARS, is_latin
from .render import keyboard_summary, unlock_layout

# The FAT label of an ESP Emaki formats. MokManager's "Enroll key from disk" lists a volume
# by its label, else by a long device path. An existing ESP keeps whatever label it has.
ESP_LABEL = 'EMAKI'
# The Secure Boot machine key's own btrfs subvolume, next to @ rather than inside it: root
# snapshots never hold a copy of the private key, and a rollback of @ keeps the key.
SECURE_BOOT_SUBVOLUME = '@secureboot'
# A kept ESP with Secure Boot: refused at plan review and checked again before the first disk write.
ESP_SECURE_BOOT_ROOM = ("The disk's startup partition is too full for Secure Boot updates: Emaki needs "
                        'about {need} MB free there (it has {free} MB). Making it bigger needs a backup '
                        'and a disk tool. Do not delete Windows startup files.')
# Plan review counts a kept ESP by make_plan's esp_need: the worker's rule applied to the USB's
# archives (worker.review_esp_need), so both gates need the same bytes (R9S9 #9). Only when those
# archives cannot be read does review fall back to these sizes, which no real Secure Boot install
# undercuts; the worker's check from the verified archives stays the final gate.
# Measured: Fedora shim 16.1-7 shimx64.efi 1,036,008 B and mmx64.efi 874,360 B. The signed GRUB
# carries every x86_64-efi module and list (grub 2:2.16-1: 3,857,977 B) and unicode.pf2
# (2,412,512 B) uncompressed in its memdisk; built images measured about 5.9 MB.
PLAN_SHIM_BYTES = 1_000_000
PLAN_MOK_MANAGER_BYTES = 800_000
PLAN_SIGNED_GRUB_BYTES = 4 * MIB
FALLBACK = 'EFI/BOOT/BOOTX64.EFI'
# The ESP Emaki formats for itself: the erase layout's first partition and, next to Windows, the
# start of the freed extent. Room for both A/B boot slots, the EFI/BOOT copies, the boot
# refresh's grub-install stage and fsck leftovers; one size, so one space rule applies to both.
OWN_ESP_BYTES = GIB


def shared_esp(esp):
    """Keep fallback files on an ESP the inventory cannot establish is Emaki's to change."""
    return bool(alongside.other_system(esp))


def removable_fallback(secure_boot, fallback_exists, own_fallback, sidecars_exist, shared=False):
    """Whether the install writes the removable path EFI/BOOT (the bootloader phase's rule).

    Only where no fallback loader is, or over Emaki's own one. With Secure Boot shim's GRUB and
    MokManager sit next to BOOTX64.EFI: never over files Emaki did not write. An ESP this install
    formats (erase, Emaki's own next to Windows) holds none, so EFI/BOOT is always written there;
    a kept ESP holding another system's loader never receives fallback files.
    """
    removable = not shared and (not fallback_exists or own_fallback)
    if secure_boot:
        removable = removable and (own_fallback or not sidecars_exist)
    return bool(removable)


def probed_removable(esp):
    """removable_fallback for a kept ESP from its inventory, before it is mounted. The inventory
    does not fingerprint EFI/Emaki, so a fallback there may be Emaki's own: then it counts as written."""
    names = {name.casefold() for name in (esp.get('_efi_loaders') or {})}
    sidecars = {f'efi/boot/{name}' for name in (secureboot.GRUB, secureboot.MOK_MANAGER)}
    fallback = FALLBACK.casefold() in names
    return removable_fallback(True, fallback, fallback, bool(names & sidecars), shared=shared_esp(esp))


def plan_esp_need(cluster, removable):
    """The lower bound of the ESP bytes a kept ESP needs for a Secure Boot install, when plan
    review cannot read the archives: the boot refresh's rule (secureboot.slots_need) with the
    PLAN_* sizes, and the shared reserve."""
    grub = PLAN_SIGNED_GRUB_BYTES
    return secureboot.slots_need({secureboot.SHIM: PLAN_SHIM_BYTES, secureboot.MOK_MANAGER: PLAN_MOK_MANAGER_BYTES,
                                  secureboot.GRUB: grub, secureboot.ESP_CERT: 1},
                                 cluster, removable=removable, stage=grub) + secureboot.SHARED_ESP_RESERVE


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
    # The partition editor's new partition: its kind ('esp', 'linux', 'swap'); None for one that exists.
    create: str | None = None
    # LUKS2 on a partition other than root (/home, data, swap); root follows the plan's encryption.
    encrypt: bool = False

    @property
    def device(self):
        return self.mapper or self.path

    @property
    def swap(self):
        return self.fs == 'swap'

    @property
    def mapper_name(self):
        """The device-mapper name of an encrypted non-root partition, also its crypttab name."""
        return f'emaki-part{self.number}' if self.encrypt else None

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
    # The Microsoft third-party CAs of a firmware with Secure Boot on ('both', '2011',
    # '2023'); '' when Secure Boot is off: then the boot path is the unsigned one.
    secure_boot: str = ''
    # The partition editor's changes (Edits), applied by the worker before any format; None without them.
    edits: object = None

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
    def esp_label(self):
        """ESP_LABEL when this plan formats the ESP for Secure Boot, or makes Emaki's own ESP
        next to Windows (with Secure Boot on or off: it tells Emaki's ESP from Windows' one);
        None when it keeps an existing one, and for other installs without Secure Boot, which
        stay exactly as before."""
        esp = next((p for p in self.partitions if p.esp and p.mountpoint == '/efi'), None)
        own = self.config.get('mode') == 'alongside'
        return ESP_LABEL if (self.secure_boot or own) and esp is not None and esp.format else None

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


def validate_config(value, test_mode=False):
    require(isinstance(value, dict), Code.BAD_CONFIG, 'Config must be an object.')
    c = copy.deepcopy(value)
    allowed = {'mode', 'disk_id', 'partition_id', 'fs', 'mounts', 'shrink_bytes', 'hostname', 'timezone',
               'layouts', 'user', 'repo_server', 'online_update', 'scale_guess', 'output_scales', 'software',
               'encryption', 'disk_password', 'hibernation', 'confirmed_encrypted', 'wifi_uuid', 'partitioning'}
    require(c.get('wifi_uuid') is None or (isinstance(c['wifi_uuid'], str) and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', c['wifi_uuid'])),
        Code.BAD_CONFIG, 'Invalid Wi-Fi connection ID.')
    require(not (set(c) - allowed), Code.BAD_CONFIG, 'Unknown config fields.')
    require(c.get('mode') in ('erase', 'manual', 'alongside'), Code.BAD_CONFIG, 'Invalid mode.')
    if c['mode'] == 'alongside':
        alongside.require_verified()  # Refuse a disabled build before inspecting its disks.
    require(isinstance(c.get('disk_id'), str), Code.BAD_CONFIG, 'A disk ID is required.')
    if c['mode'] != 'manual':
        require(c.get('fs') in ('btrfs', 'ext4'), Code.BAD_CONFIG, 'Choose btrfs or ext4.')
        require('partitioning' not in c, Code.BAD_CONFIG, 'Partition changes belong to a manual installation.')
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
    if 'repo_server' in c:
        require(TEST_REPO_OVERRIDE and test_mode, Code.BAD_CONFIG,
                'A repository override requires a test build in test mode.')
        require(isinstance(server, str) and len(server) <= 2048
                and not any(ch.isspace() or ord(ch) < 32 for ch in server),
                Code.BAD_CONFIG, 'Invalid repository URL.')
        parsed = urlsplit(server)
        require(parsed.scheme == 'https'
                and parsed.hostname
                and not parsed.username and not parsed.password and not parsed.fragment,
                Code.BAD_CONFIG, 'Repository URL must be HTTPS, without credentials or fragments.')
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
    return [Partition(None, 1, MIB, OWN_ESP_BYTES, 'vfat', True, '/efi', True),
            Partition(None, 2, OWN_ESP_BYTES + MIB, end - OWN_ESP_BYTES - MIB, fs, True,
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
    type_refusal = alongside.alongside_type_refusal(disk, p)
    require(not type_refusal, Code.SHRINK_BOUNDS, type_refusal)
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
    new_size = alongside.usable_size(p['size_bytes']) - freed
    shrink_bounds(new_size, offer['min_bytes'], p['size_bytes'])
    metadata = p.get('_gpt')
    sector = disk['_sector_size']
    require(metadata and metadata['start'] * sector == p['start_bytes']
            and (metadata['end'] + 1) * sector == p['start_bytes'] + p['size_bytes']
            and metadata['guid'] == str(p.get('_partuuid')).lower()
            and metadata['type'] == p.get('_parttype') and metadata['name'] == p.get('_partlabel'), Code.SHRINK_BOUNDS,
            'Windows GPT identity or geometry could not be verified.')
    # Windows' ESP stays as it is, never mounted or written: GRUB's Windows entry needs its loader.
    # Emaki's own ESP comes first in the freed extent, so its free space never limits Secure Boot.
    esps = [part for part in disk['partitions'] if part['esp']]
    require(len(esps) == 1 and esps[0]['fs'] == 'vfat', Code.ESP_SPACE,
            'Installing next to Windows requires exactly one existing FAT32 startup partition on the selected disk.')
    esp = esps[0]
    require('EFI/Microsoft/Boot/bootmgfw.efi' in esp.get('_efi_loaders', {}), Code.SHRINK_BOUNDS,
            'No verified Microsoft EFI loader exists on this disk; automatic Windows boot setup is unavailable.')
    occupied = {part['number'] for part in disk['partitions']}
    numbers = [n for n in range(1, 129) if n not in occupied][:2]
    require(len(numbers) == 2, Code.SHRINK_BOUNDS,
            "Emaki needs two free GPT partition entries (its startup partition and its root); this disk has "
            f"{len(numbers)}.")
    start = p['start_bytes'] + new_size
    end = start + freed
    require(all(other is p or end <= other['start_bytes'] or start >= other['start_bytes'] + other['size_bytes']
                for other in disk['partitions']), Code.SHRINK_BOUNDS, 'Freed extent overlaps another partition.')
    # The ESP comes out of Emaki's share, never out of Windows' size; storage_layout checks the root.
    return [Partition(None, numbers[0], start, OWN_ESP_BYTES, 'vfat', True, '/efi', True),
            Partition(None, numbers[1], start + OWN_ESP_BYTES, freed - OWN_ESP_BYTES, c['fs'], True,
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


# The partition editor (manual mode). Nothing is written at review: the plan lists the changes,
# and the worker applies them before formatting, after checking that the disk still matches.
# New partitions of every kind but the ESP get the generic Linux type, as erase makes root:
# systemd's GPT auto-discovery never mounts or unlocks one on its own.
NEW_TYPES = {'esp': 'U', 'linux': 'L', 'swap': 'L'}
NEW_KINDS = {'esp': 'EFI system partition', 'linux': 'Linux', 'swap': 'swap'}
# An ESP Emaki formats must really be FAT32 (mkfs.fat makes FAT16 below about 260 MiB with 4 KiB
# clusters) and hold both Secure Boot slots; the default is the erase layout's 1 GiB.
ESP_FORMAT_MIN = 512 * MIB
SWAP_MIN = 256 * MIB
DATA_MIN = GIB
MAX_CREATED = 16


@dataclass
class Edits:
    new_table: bool
    deleted: list  # The reviewed inventory rows of the partitions the plan deletes.
    created: list  # Partition rows of the virtual layout the plan creates (ids 'new-N').


def usable_end(disk):
    """The end of the space partitions can use: 1 MiB stays for the backup GPT (as erase)."""
    return (disk['size_bytes'] - MIB) // MIB * MIB


def deletion_identity(part):
    """What a typed confirmation to delete another system's partition must name."""
    return {'path': part['path'], 'start_bytes': part['start_bytes'], 'size_bytes': part['size_bytes'],
            'system': alongside.other_system(part)}


def partition_edits(value, disk):
    """The disk's partitions after the editor's changes, and those changes; nothing is written.

    value: the config's `partitioning` ({new_table, delete, create}) or None. Each deleted row
    names a partition of the probed disk; a partition of another system (inventory.other_system)
    needs `confirm`, its current deletion_identity. Each created row is {id: 'new-N', type: 'esp' |
    'linux' | 'swap', start_bytes, size_bytes}: whole MiB, inside the usable space, overlapping no
    kept or other new partition. New partitions take the lowest free GPT entries in their order,
    deleted ones included: sfdisk --append, after the deletions, fills the first free entry.
    """
    if value is None:
        require(disk.get('_pttype') == 'gpt', Code.MANUAL_LAYOUT, 'Manual installation requires GPT.')
        return disk['partitions'], None
    require(isinstance(value, dict) and not (set(value) - {'new_table', 'delete', 'create'}),
            Code.MANUAL_LAYOUT, 'Invalid partition changes.')
    new_table, deletes, creates = value.get('new_table', False), value.get('delete', []), value.get('create', [])
    require(type(new_table) is bool and isinstance(deletes, list) and isinstance(creates, list)
            and len(deletes) <= 128 and len(creates) <= MAX_CREATED, Code.MANUAL_LAYOUT, 'Invalid partition changes.')
    if new_table:
        require(not disk['partitions'] and not disk.get('_fs') and not disk.get('closed_encrypted'),
                Code.MANUAL_LAYOUT, 'A new partition table is made only on an empty disk; this disk holds data.')
    else:
        require(disk.get('_pttype') == 'gpt', Code.MANUAL_LAYOUT, 'Manual installation requires GPT.')
    # Free space is measured between the partitions: every one's place must be known.
    unplaced = next((p for p in disk['partitions'] if p.get('_geometry_known') is not True), None)
    require(unplaced is None, Code.MANUAL_LAYOUT,
            f"The place of partition {unplaced and unplaced.get('number')} on the disk is unknown, "
            'so the partition editor cannot change this disk.')
    known = {p['id']: p for p in disk['partitions']}
    deleted = []
    for row in deletes:
        require(isinstance(row, dict) and not (set(row) - {'partition_id', 'confirm'})
                and isinstance(row.get('partition_id'), str) and row['partition_id'] in known,
                Code.MANUAL_LAYOUT, 'A partition to delete is not on the selected disk.')
        p = known[row['partition_id']]
        require(p not in deleted, Code.MANUAL_LAYOUT, 'A partition is deleted twice.')
        require(p.get('_geometry_known') is True and p['number'] > 0 and not p.get('_read_only', False),
                Code.MANUAL_LAYOUT, f"Partition {p['number']} cannot be deleted: its place on the disk is unknown "
                'or it is read-only.')
        system = alongside.other_system(p)
        if system:
            require(row.get('confirm') == deletion_identity(p), Code.MANUAL_LAYOUT,
                    f"Partition {p['number']} holds {system}. Confirm deleting it by typing DELETE.")
        else:
            require('confirm' not in row, Code.MANUAL_LAYOUT, 'Invalid partition changes.')
        deleted.append(p)
    kept = [p for p in disk['partitions'] if p not in deleted]
    end = usable_end(disk)
    free = [n for n in range(1, 129) if n not in {p['number'] for p in kept}]
    require(len(creates) <= len(free), Code.MANUAL_LAYOUT, 'This disk has no free GPT partition entry left.')
    created, extents = [], [(p['start_bytes'], p['start_bytes'] + p['size_bytes']) for p in kept]
    for number, row in zip(free, creates):
        require(isinstance(row, dict) and set(row) == {'id', 'type', 'start_bytes', 'size_bytes'}
                and isinstance(row['id'], str) and re.fullmatch(r'new-[1-9][0-9]?', row['id'])
                and row['id'] not in {p['id'] for p in created} and row['type'] in NEW_TYPES
                and type(row['start_bytes']) is int and type(row['size_bytes']) is int,
                Code.MANUAL_LAYOUT, 'Invalid new partition.')
        start, size = row['start_bytes'], row['size_bytes']
        require(start % MIB == 0 and size % MIB == 0 and size > 0 and start >= MIB and start + size <= end,
                Code.MANUAL_LAYOUT, 'A new partition must start and end on whole MiB inside the free space.')
        require(all(start + size <= a or start >= b for a, b in extents), Code.MANUAL_LAYOUT,
                'A new partition overlaps another partition.')
        least = {'esp': ESP_FORMAT_MIN, 'swap': SWAP_MIN, 'linux': DATA_MIN}[row['type']]
        require(size >= least, Code.MANUAL_LAYOUT, {
            'esp': 'An EFI system partition needs at least 512 MiB.',
            'swap': 'A swap partition needs at least 256 MiB.',
            'linux': 'A new partition needs at least 1 GiB.'}[row['type']])
        extents.append((start, start + size))
        created.append({'id': row['id'], 'path': None, 'number': number, 'uuid': None, 'fs': None,
                        'size_bytes': size, 'start_bytes': start, 'esp': row['type'] == 'esp',
                        'os_hint': None, 'other_system': None, '_new': row['type'], '_partuuid': None,
                        '_geometry_known': True, '_read_only': False, '_btrfs_devices': None, '_subvolumes': None})
    if new_table:
        require(created, Code.MANUAL_LAYOUT, 'Create the partitions for the new partition table.')
    if not (new_table or deleted or created):
        return disk['partitions'], None
    return kept + created, Edits(new_table, deleted, created)


def _swap_row(row, p):
    require(row.get('fs') == 'swap' and row.get('subvolume') is None and type(row.get('format')) is bool,
            Code.MANUAL_LAYOUT, 'A swap partition takes the swap format.')
    require(row['format'] or p['fs'] == 'swap', Code.MANUAL_LAYOUT,
            'Using a partition as swap requires formatting it.')
    require(not p['esp'] and p.get('_new') in (None, 'swap'), Code.MANUAL_LAYOUT,
            'Only a swap partition can be used as swap.')
    require(p['size_bytes'] >= SWAP_MIN, Code.MANUAL_LAYOUT, 'A swap partition needs at least 256 MiB.')


def manual_partitions(rows, disk, encryption='none', edits=None):
    """The manual layout: mount rows on the disk's partitions after the editor's changes.

    rows: {partition_id, mountpoint ('swap' for swap), fs, format, subvolume, encrypt}; a new
    partition's id is its editor id. encrypt (LUKS2) is for formatted partitions other than root
    and needs an encrypted root, which keeps its key; root follows the plan's encryption.
    Partitions of another system (inventory.other_system) are never formatted or mounted; only
    an ESP may be kept, unformatted, at /efi.
    """
    partitions, edits = partition_edits(edits, disk)
    require(isinstance(rows, list) and 2 <= len(rows) <= 64, Code.MANUAL_LAYOUT,
            'Assign exactly one root and one startup partition, plus optional data mounts.')
    known = {p['id']: p for p in partitions}
    removed = {p['id'] for p in (edits.deleted if edits else [])}
    grouped, mountpoints, swaps = {}, set(), []
    for row in rows:
        require(isinstance(row, dict) and not (set(row) -
                {'partition_id', 'mountpoint', 'fs', 'format', 'subvolume', 'encrypt'}),
                Code.MANUAL_LAYOUT, 'Invalid mount row.')
        require(row.get('partition_id') not in removed, Code.MANUAL_LAYOUT,
                'A deleted partition cannot be used.')
        require(isinstance(row.get('partition_id'), str) and row['partition_id'] in known,
                Code.MANUAL_LAYOUT, 'Partition is not on the selected disk.')
        p = known[row['partition_id']]
        require(p.get('_geometry_known') is True and p['size_bytes'] > 0
                and p['start_bytes'] > 0 and not p.get('_read_only', False),
                Code.MANUAL_LAYOUT, 'Partition geometry is unknown or the partition is read-only.')
        encrypt = row.get('encrypt', False)
        require(type(encrypt) is bool, Code.MANUAL_LAYOUT, 'Invalid mount row.')
        system = alongside.other_system(p)
        require(not system or (p['esp'] and row.get('mountpoint') == '/efi' and row.get('format') is False),
                Code.MANUAL_LAYOUT, f"Partition {p['number']} holds {system}. Emaki does not change it; "
                'delete the partition to use its space.')
        if row.get('mountpoint') == 'swap':
            _swap_row(row, p)
            require(row['partition_id'] not in grouped and not any(q is p for q, _ in swaps), Code.MANUAL_LAYOUT,
                    'A swap partition cannot have other uses.')
            require(not encrypt or (row['format'] and encryption != 'none'), Code.MANUAL_LAYOUT,
                    'An encrypted swap partition needs formatting and an encrypted root, which keeps its key.')
            swaps.append((p, row))
            continue
        require(p.get('_new') != 'swap', Code.MANUAL_LAYOUT, 'A new swap partition can only be used as swap.')
        require(not any(q is p for q, _ in swaps), Code.MANUAL_LAYOUT, 'A swap partition cannot have other uses.')
        mp, fs, fmt, sv = (row.get(k) for k in ('mountpoint', 'fs', 'format', 'subvolume'))
        _mountpoint(mp)
        require(mp not in mountpoints, Code.MANUAL_LAYOUT, 'Duplicate mountpoint.')
        mountpoints.add(mp)
        require(fs in ('btrfs', 'ext4', 'vfat') and type(fmt) is bool,
                Code.MANUAL_LAYOUT, 'Invalid filesystem or format flag.')
        require(fmt or fs == p['fs'], Code.MANUAL_LAYOUT,
                'Changing filesystem type requires format: true.')
        require(not encrypt or (mp not in ('/', '/efi') and fmt and fs != 'vfat' and encryption != 'none'),
                Code.MANUAL_LAYOUT, 'Only a formatted partition other than root and /efi can be encrypted on its own, '
                'and only with an encrypted root, which keeps its key.' if mp != '/' else
                'Root encryption is chosen on the Encryption step (the plan encryption).')
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
                    '/efi must be an existing GPT EFI system partition with FAT32.')
            if fmt:
                require(p['size_bytes'] >= ESP_FORMAT_MIN, Code.ESP_SPACE,
                        'An EFI system partition needs at least 512 MiB.')
            free = p.get('_esp_free_bytes')
            require(fmt or (free is not None and free >= secureboot.SHARED_ESP_RESERVE), Code.ESP_SPACE,
                    'The startup partition needs at least 32 MiB of verified free space.')
        else:
            require(not p['esp'], Code.MANUAL_LAYOUT,
                    'A startup partition may only be mounted at /efi.')
        prior = grouped.setdefault(p['id'], [])
        require(not prior or (fs == 'btrfs' and sv is not None and
                all(r.get('subvolume') is not None and r['fs'] == fs and r['format'] == fmt
                    and r.get('encrypt', False) == encrypt for r in prior)),
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
                                subvols, p['uuid'], p.get('_partuuid'), p.get('_subvolumes'),
                                create=p.get('_new'), encrypt=row.get('encrypt', False)))
    for p, row in swaps:
        result.append(Partition(p['path'], p['number'], p['start_bytes'], p['size_bytes'], 'swap', row['format'],
                                uuid=p['uuid'], partuuid=p.get('_partuuid'), create=p.get('_new'),
                                encrypt=row.get('encrypt', False)))
    used = {p.number for p in result}
    require(all(p['number'] in used for p in (edits.created if edits else [])), Code.MANUAL_LAYOUT,
            'Every new partition needs a use: choose its mount point or swap, or remove it.')
    expanded = [mp for p in result for mp in ([p.mountpoint] if p.mountpoint else []) + list(p.subvolumes.values())]
    require(len(expanded) == len(set(expanded)), Code.MANUAL_LAYOUT, 'Implicit and explicit mounts overlap.')
    root = next(p for p in result if p.mountpoint == '/' or '/' in p.subvolumes.values())
    if root.fs == 'btrfs':
        require(set(SUBVOLUMES.values()) <= set(expanded), Code.MANUAL_LAYOUT,
                'Btrfs needs separate /home, /var/log, package cache and /.snapshots mounts.')
    # archinstall sorts btrfs partitions without a mountpoint as '/'. Keep the
    # root partition first even if the UI submitted the ESP/home rows first.
    return sorted(result, key=lambda p: not (p.mountpoint == '/' or '/' in p.subvolumes.values())), edits


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
    # The partition editor's deletions destroy a locked volume as surely as a format.
    edits = config.get('partitioning')
    deletes = edits.get('delete') if isinstance(edits, dict) else None
    formatted |= ({row.get('partition_id') for row in deletes if isinstance(row, dict)}
                  if isinstance(deletes, list) else set())
    paths = {part['path'] for part in disk['partitions'] if part['id'] in formatted}
    for volume in disk.get('closed_encrypted', []):
        if mode != 'erase' and volume['path'] not in paths:
            continue
        warning = volume['warning']
        require(bool(volume.get('uuid')), Code.ENCRYPTED_CONFIRMATION,
                alongside.unconfirmed_identity_warning(volume['path']))
        identity = {key: volume[key] for key in ('path', 'type', 'uuid')}
        require(identity in config.get('confirmed_encrypted', []), Code.ENCRYPTED_CONFIRMATION, warning)


def make_plan(config, inventory, test_mode=False, esp_need=None):
    """esp_need(cluster, removable, encrypted): the ESP bytes a Secure Boot install on a kept
    ESP needs, from the installation's archives (worker.review_esp_need reads them; the plan
    itself reads nothing), or None when they cannot be read: then plan_esp_need's bounds."""
    c = validate_config(config, test_mode)
    graphics_packages = planned_packages(inventory)
    hardware = inventory.get("hardware", {})
    secure_boot = ''
    if "secure_boot" in hardware and hardware["secure_boot"] is not False:
        # Emaki starts through shim, which the Microsoft third-party CAs sign; without
        # either one in db nothing can start, and an unreadable state is not guessed.
        cas = hardware.get("secure_boot_cas") if hardware["secure_boot"] is True else None
        require(cas in SECURE_BOOT_CAS, Code.SECURE_BOOT, SECURE_BOOT_NO_CA if cas == 'none' else SECURE_BOOT_UNKNOWN)
        secure_boot = cas
    disk = next((d for d in inventory['disks'] if d['id'] == c['disk_id']), None)
    require(disk is not None, Code.DISK_NOT_FOUND, inventory.get('hardware', {}).get('disk_notice') or 'Selected disk no longer exists.')
    validate_disk(disk, inventory)
    validate_encrypted_confirmation(c, disk)
    edits = None
    if c['mode'] == 'alongside':
        parts = alongside_partitions(c, disk)
    elif c['mode'] == 'erase':
        parts = erase_partitions(disk['size_bytes'], c['fs'])
    else:
        parts, edits = manual_partitions(c.get('mounts'), disk, c['encryption'], c.get('partitioning'))
    if secure_boot:
        root = next((p for p in parts if '/' in p.subvolumes.values()), None)
        state = str(secureboot.STATE)
        if root is not None:
            require(state not in [mp for part in parts for mp in [part.mountpoint, *part.subvolumes.values()]]
                    and SECURE_BOOT_SUBVOLUME not in root.subvolumes, Code.MANUAL_LAYOUT,
                    f'{state} and the subvolume {SECURE_BOOT_SUBVOLUME} are reserved for the Secure Boot key.')
            root.subvolumes = {**root.subvolumes, SECURE_BOOT_SUBVOLUME: state}
        kept = next((p for p in parts if p.esp and not p.format), None)
        if kept is not None:
            probed = next((p for p in disk['partitions'] if p.get('path') == kept.path), {})
            free = probed.get('_esp_free_bytes')
            # Inventories from before the probe recorded the cluster size: FAT32's usual 4 KiB.
            cluster, removable = probed.get('_esp_cluster_bytes') or 4096, probed_removable(probed)
            need = esp_need(cluster, removable, c['encryption'] != 'none') if esp_need else None
            if need is None:
                need = plan_esp_need(cluster, removable)
            require(free is not None and free >= need, Code.ESP_SPACE, ESP_SECURE_BOOT_ROOM.format(
                need=-(-need // 1000000), free=(free or 0) // 1000000))
    swap_bytes = storage_layout(c, parts, inventory)
    summary = [f"{c['mode'].capitalize()} installation on {disk['path']} ({disk['model']})."]
    if c['mode'] == 'erase':
        summary.append('Erase all partitions and create a new GPT partition table.')
    if c['mode'] == 'alongside':
        windows = next(p for p in disk['partitions'] if p['id'] == c['partition_id'])
        keep = alongside.usable_size(windows['size_bytes']) - c['shrink_bytes']
        summary += [f"Windows keeps {keep / 10**9:.2f} GB, Emaki gets {c['shrink_bytes'] / 10**9:.2f} GB; back up your files first.",
                    'Shrink the Windows filesystem and its partition end only; preserve its start, GUID, type, name and files.',
                    "Create Emaki's own startup partition (1 GiB) and root in the freed space; "
                    "Windows' startup partition is not changed.",
                    'Preserve all other partitions; add Windows Boot Manager to GRUB; Emaki stays the default.',
                    'Windows will run a consistency check (chkdsk) on its first boot after resizing. Let it finish.']
    if c['mode'] == 'manual':
        summary += manual_summary(disk, parts, edits, c['encryption'] != 'none')
    for p in parts if c['mode'] != 'manual' else []:
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
    if secure_boot:
        summary += [SECURE_BOOT_CAS[secure_boot],
                    "After the restart a blue screen asks you to confirm Emaki's key with a password "
                    "shown at the end of the installation; you do this only once."]
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
                         'The root partition and the partitions marked encrypted are encrypted. The startup partition '
                         'and the other partitions stay unencrypted.' if any(p.encrypt for p in parts) else
                         'The root partition is encrypted. The startup partition and any separate data partitions stay unencrypted.'})
    if c['mode'] == 'manual':
        warnings.append({'code': 'preserved_files', 'msg': 'Unformatted filesystems keep data; installation still writes system and account files.'})
    if edits and edits.deleted:
        warnings.append({'code': 'deleted_partitions', 'msg': 'Deleted partitions lose all their files. '
                         'Nothing on the disk changes until you press Install.'})
    if disk.get('_id_fallback'):
        warnings.append({'code': 'unstable_id', 'msg': 'No by-id name is available; device identity and geometry will be rechecked.'})
    return Plan(c, copy.deepcopy(disk), parts, summary, warnings,
                alongside_fingerprint(disk) if c['mode'] == 'alongside' else fingerprint(disk), swap_bytes, graphics_packages,
                secure_boot, edits)


def manual_summary(disk, parts, edits, encrypted):
    """Review lines for a manual layout, one per change in the order the worker makes them."""
    def size(n):
        return f'{n / GIB:.2f} GiB'

    lines = []
    if edits and edits.new_table:
        lines.append(f"Create a new GPT partition table on {disk['path']}; the disk is empty.")
    for p in edits.deleted if edits else []:
        system = alongside.other_system(p)
        what = ', '.join([p['path'], size(p['size_bytes'])] + ([p['fs']] if p.get('fs') else [])
                         + ([system] if system else []))
        lines.append(f"Delete partition {p['number']} ({what}): all its files are lost.")
    for p in sorted(parts, key=lambda p: p.number):
        root = p.mountpoint == '/' or '/' in p.subvolumes.values()
        lock = ', encrypted (LUKS2)' if p.encrypt or (root and encrypted) else ''
        use = ('swap' if p.swap else ', '.join(f'{name} → {mp}' for name, mp in p.subvolumes.items()) or p.mountpoint)
        if p.create:
            kind = 'startup partition, FAT32' if p.esp else p.fs if not p.swap else 'swap'
            lines.append(f'Create partition {p.number}: {size(p.size)}, {kind}{lock}; use: {use}.')
        elif p.format:
            fs = 'swap' if p.swap else 'FAT32' if p.fs == 'vfat' else p.fs
            lines.append(f'Format partition {p.number} ({p.path}, {size(p.size)}) as {fs}{lock}: '
                         f'its files are lost; use: {use}.')
        else:
            probed = next((part for part in disk['partitions'] if part.get('path') == p.path), {})
            startup = ''
            system = alongside.other_system(probed) if p.esp else None
            if system == alongside.UNCHECKED:
                startup = ' Emaki could not check its files; its fallback loader will not be changed.'
            elif system:
                startup = " It holds another system's startup files; its fallback loader will not be changed."
            lines.append(f'Keep partition {p.number} ({p.path}, {size(p.size)}, {p.fs}) and its files; use: {use}.'
                         + startup)
    lines.append('Partitions not listed here are not changed.')
    return lines


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
