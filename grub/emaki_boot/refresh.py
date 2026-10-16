# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a complete boot generation before replacing any firmware entry point.

Each EFI image carries matching modules in its memdisk and selects a complete
boot generation. Restoring an older root can remove that generation, so the
embedded menu also understands the conventional menu in the restored root.
"""
import argparse
from contextlib import contextmanager, nullcontext
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid

from .package_lock import LIVE as LOCK_LIVE, lock_message
from . import boot
from . import esp_records
from . import secureboot
from . import shared
from . import update_menu

OLD_INCLUDE = '. /usr/share/emaki/grub/defaults.cfg'
INCLUDE = ('if [ -r /usr/share/emaki/grub/defaults.cfg ]; then '
           '. /usr/share/emaki/grub/defaults.cfg; fi')
MODULES = Path('/usr/lib/grub/x86_64-efi')
IMAGE_MODULES = (*boot.GRUB_EARLY_MODULES, 'search_fs_uuid')
MIB = 1024 * 1024
LOG = None
LOG_PATH = '/var/log/emaki-boot-refresh.log'
LOG_LIMIT = MIB
PENDING = '/var/lib/emaki/boot-refresh-pending'
BOOT_RETRIES = 3
SLEEP_LOCK_TIMEOUT = 2.0
SLEEP_STATE = Path('/run/emaki-boot-sleep.json')
SHARED_ESP = Path('/var/lib/emaki/alongside-boot.json')
FOREIGN = 'The existing boot loaders were kept because the fallback loader is not recognized as Emaki.'
TRIAL_FAILED = 'The new boot loader did not complete a boot; the old loader was kept.'
OWN_ESP_MISMATCH = ('The partition mounted at /efi is not the Emaki startup partition recorded '
                    'during installation. Mount the Emaki startup partition (label EMAKI) at /efi; '
                    'the startup partition of Windows must never be mounted there.')
SMALL_SHARED = ('The boot menu was updated. The boot loader was kept: the startup partition '
                'shared with Windows is smaller than 256 MiB.')
TORN_FINISHED = ('An interrupted boot loader update left its record unreadable; the update was '
                 'finished from the files it had already written.')
TORN_UNDONE = ('An interrupted boot loader update left its record unreadable; it was undone and '
               'the previous boot files were kept.')
# What to do about a startup partition that is too small: the installer's advice (make it
# bigger after a backup, never delete Windows' files). The installation USB carries no tool that
# resizes partitions (its partition editor creates, deletes and formats), so the advice names a
# partitioning tool on a live USB stick; the command then runs on the installed system, which
# still starts from its active slot. Turning Secure Boot off changes nothing here: with the machine key this computer keeps
# signing its loaders either way.
SMALL_ESP_ADVICE = ('The startup partition is too small for Secure Boot updates and must be made '
                    'bigger: back up the computer, start it from a live USB stick with a partitioning '
                    'tool and enlarge the partition. Do not delete Windows startup files. '
                    'Then start Emaki again and run: sudo emaki-boot-refresh')
SLOT_NO_ROOM = ('The boot loader was kept: its update needs {needed} MiB on the startup partition '
                'and {free} MiB is available. ' + SMALL_ESP_ADVICE)
SLOT_NO_ENTRY = ('The boot loader was kept: no firmware startup entry starts a complete Emaki startup '
                 'copy on this disk. The entry is restored after the next completed start of Emaki. '
                 'If Emaki does not start, start the computer from the Emaki installation USB and '
                 'run: sudo emaki-boot-repair')
# A lost-entry boot's proof is missing (see fallback_slot): the entries were not recreated.
SLOT_NOT_PROVEN = ('The firmware startup entries of Emaki were not recreated: this start could not '
                   'be proven to come through a complete startup copy. Start the computer from the Emaki '
                   'installation USB and run: sudo emaki-boot-repair')
# The ESP's fallback copies are being replaced while this marker exists (refresh_fallback).
FALLBACK_MARKER = '/efi/EFI/Emaki/fallback-update'
# EFI_OUT_OF_RESOURCES reaches efibootmgr as ENOSPC (Linux efi_status_to_err).
NVRAM_FULL = ('The boot loader was kept: the firmware has no room for another startup entry. '
              'Remove startup entries you no longer use in the firmware setup, then run: '
              'sudo emaki-boot-refresh')
NVRAM_NOT_KEPT = 'The firmware did not keep the change to {change}; nothing else was changed.'
ORDER_RESTORED = 'Emaki was put first in the startup order again; {label} had moved before it.'
ORDER_LOCKED = ('{label} starts before Emaki and the firmware did not accept a new startup order. '
                'Choose Emaki in the firmware startup menu, or allow boot order changes in the '
                'firmware setup; Emaki puts itself first again after its next start.')
KEY_MISSING = ('The boot loader was kept: this computer was set up to sign its boot files, '
               'but its Secure Boot machine key is missing from /var/lib/emaki/secureboot.')
NO_KEY = ('The boot loader was kept: Secure Boot is on or unreadable, and this machine has no '
          'Emaki signing key.')
SBAT_DATA = 'emaki-sbat.csv'
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
# Frozen migration baseline, independent of future packaged menu changes.
LEGACY = {
    'GRUB_DEFAULT': '0', 'GRUB_TIMEOUT': '5', 'GRUB_DISTRIBUTOR': 'Emaki',
    'GRUB_TOP_LEVEL': '/boot/vmlinuz-linux', 'GRUB_EARLY_INITRD_LINUX_STOCK': '',
    'GRUB_DISABLE_RECOVERY': 'true', 'GRUB_DISABLE_BOOTNEXT': 'true',
    'GRUB_DISABLE_SUBMENU': 'y', 'GRUB_TERMINAL_OUTPUT': 'gfxterm',
    'GRUB_GFXMODE': '1024x768,800x600,640x480,auto', 'GRUB_GFXPAYLOAD_LINUX': 'keep',
    'GRUB_BACKGROUND': '/usr/share/emaki/grub/background.png',
}
KERNELS = ('linux', 'linux-lts')
INITRAMFS = tuple(f'initramfs-{kernel}{suffix}.img' for kernel in KERNELS for suffix in ('', '-fallback'))


class Refuse(RuntimeError):
    pass


class Kept(Refuse):
    """An expected refusal which does not fail a package transaction."""


class Transient(Refuse):
    """An incomplete operation eligible for a bounded boot retry."""


class BoundedLog:
    """Keep the latest output, including output from the isolated generator."""

    def __init__(self, path):
        self.path = path
        self.write('')

    def write(self, text):
        data = text.encode('utf-8')[-LOG_LIMIT:]
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            size = output.seek(0, os.SEEK_END)
            if size + len(data) > LOG_LIMIT:
                output.seek(max(0, size - (LOG_LIMIT - len(data))))
                tail = output.read()
                output.seek(0)
                output.write(tail + data)
                output.truncate()
            else:
                output.write(data)

    def flush(self):
        pass

    def close(self):
        pass


def remove_previous_boot_lock(path, boot_id):
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ELOOP):
            return
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return
        token = os.read(fd, 128)
        match = re.fullmatch(rb'emaki-boot-refresh:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})', token)
        if match and match[1].decode() != boot_id:
            unlink_same_file(path, fd)
    finally:
        os.close(fd)


def unlink_same_file(path, fd):
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    owned = os.fstat(fd)
    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
        path.unlink()
        sync_directory(path.parent)


def open_run_lock(mode):
    """The shared run lock, owner-only whatever umask the caller (sleep hook, sudo) has.

    Anyone who can open the file can hold the lock, and rollback refuses while it is held.
    """
    previous = os.umask(0o077)
    try:
        lock = open('/run/emaki-boot-refresh.lock', mode)
    finally:
        os.umask(previous)
    os.fchmod(lock.fileno(), 0o600)
    return lock


def wait_run_lock(lock, timeout=0):
    """Acquire without ever entering a blocking flock syscall."""
    end = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


@contextmanager
def sleep_run_lock(phase, saved, label='Boot loader trial'):
    # Both commands run sequentially with user.slice frozen. Never wait for a
    # full refresh (which includes image generation) or for its file log lock.
    with open_run_lock('a') as lock:
        try:
            wait_run_lock(lock, timeout=SLEEP_LOCK_TIMEOUT)
        except BlockingIOError:
            # A receipt belongs only to this sleep cycle, including when post
            # is skipped. Removing volatile state needs no ESP/package lock.
            saved.unlink(missing_ok=True)
            print(f'{label}: sleep {phase} bookkeeping skipped: boot maintenance is busy '
                  f'after {SLEEP_LOCK_TIMEOUT:g} seconds; continuing sleep or wake.',
                  file=sys.stderr, flush=True)
            yield False
        else:
            yield True


def current_boot_id():
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(re.fullmatch(UUID, boot_id) and boot_id == boot_id.lower(),
            'Cannot identify the current boot for the package lock.')
    return boot_id


@contextmanager
def pacman_lock():
    path = Path('/var/lib/pacman/db.lck')
    boot_id = current_boot_id()
    remove_previous_boot_lock(path, boot_id)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        if LOG is not None:
            LOG.write('The package database is locked; the boot loader update will wait.\n')
        # A running change only means waiting; a lock without a verified owner keeps its advice.
        advice = lock_message(path)
        raise Transient('The boot loader update will wait for other software changes to finish.'
                        if advice == LOCK_LIVE else advice) from error
    try:
        os.write(fd, ('emaki-boot-refresh:' + boot_id).encode())
        os.fsync(fd)
        yield
    finally:
        try:
            unlink_same_file(path, fd)
        finally:
            os.close(fd)


def clear_pending():
    path = Path(PENDING)
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


def refresh_attempt(retry):
    # The caller holds the run lock; a cleared marker must not strand an old lock.
    remove_previous_boot_lock(Path('/var/lib/pacman/db.lck'), current_boot_id())
    pending = Path(PENDING)
    if retry and not pending.exists():
        return 0
    attempts = 0
    try:
        if retry:
            record = json.loads(regular(pending))
            require(isinstance(record, dict) and 'boots' in record,
                    'The boot loader retry record is invalid; run emaki-boot-refresh again.')
            attempts = record['boots']
            require(type(attempts) is int and 0 <= attempts <= BOOT_RETRIES,
                    'The boot loader retry count is invalid; run emaki-boot-refresh again.')
            if attempts == BOOT_RETRIES:
                # Retain exhausted requests so boot-time discovery cannot rearm them.
                return 0
            attempts += 1
        # Persist the budget before work, so interrupted boots also consume it.
        atomic(pending, json.dumps({'boots': attempts}) + '\n')
        with pacman_lock() if retry else nullcontext():
            identity = discover()
            if identity is not None:
                check_refresh_storage(identity)
                with pause_snapshots():
                    refresh(identity)
        clear_pending()
        return 0
    except (OSError, Transient) as error:
        LOG.write(str(error) + '\n')
        if retry and attempts >= BOOT_RETRIES:
            message = 'The boot loader retry limit was reached; run emaki-boot-refresh to try again.'
        else:
            message = f'The boot loader update did not finish; details are in {LOG_PATH}.'
        LOG.write(message + '\n')
        print(message, file=sys.stderr)
        return 0 if retry else 1
    except (Refuse, ValueError) as error:
        if retry:
            atomic(pending, json.dumps({'boots': BOOT_RETRIES}) + '\n')
        else:
            clear_pending()
        LOG.write(str(error) + '\n')
        print(str(error), file=sys.stderr if not isinstance(error, Kept) else sys.stdout, flush=True)
        return 0 if retry or isinstance(error, Kept) else 1


def require(condition, message):
    if not condition:
        raise Refuse(message)


def run(argv, env=None):
    result = subprocess.run([str(a) for a in argv], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={
                                'PATH': '/usr/bin', 'LC_ALL': 'C', **(env or {})})
    if LOG is not None:
        LOG.write('$ ' + shlex.join([str(a) for a in argv]) + '\n')
        LOG.write(result.stdout + result.stderr)
        LOG.flush()
    if result.returncode:
        raise Transient(f'{Path(argv[0]).name} failed: {result.stderr.strip()[-1800:]}')
    return result.stdout.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    require(path.is_file() and not path.is_symlink(), f'{path} is missing or is a symbolic link.')
    return path.read_bytes()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def esp_writable(path, allocation=None):
    """Check capacity before opening the shared ESP's EFI/Emaki write gate."""
    if not path.is_relative_to(Path('/efi')):
        return
    alongside = shared_esp()
    if alongside:
        require(path.is_relative_to(Path('/efi/EFI/Emaki')),
                f'{path} is outside EFI/Emaki on a startup partition shared with Windows.')
    if allocation is not None:
        # The sidecar needs its full allocation while the old file still exists.
        # Keep one MiB for FAT metadata and concurrent small boot-state writes.
        room(Path('/efi'), allocation, MIB)
    if alongside:
        shared.open_writes()


def write(path, data, mode=0o600):
    """Durable creation for staging and same-directory replacement sidecars."""
    data = data.encode() if isinstance(data, str) else data
    esp_writable(path, allocation=len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        sync_directory(path.parent)
    except BaseException:
        path.unlink(missing_ok=True)
        sync_directory(path.parent)
        raise


def durable_read(path):
    """Flush a surviving FAT name before freeing any possibly aliased sidecar."""
    require(path.is_file() and not path.is_symlink(), f'{path} is missing or is a symbolic link.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        os.fsync(source.fileno())
        data = source.read()
    sync_directory(path.parent)
    return data


def discard_sidecars(target, sidecars):
    sidecars = [path for path in sidecars if path.exists()]
    if not sidecars:
        return
    if target.exists():
        durable_read(target)
    for path in sidecars:
        esp_writable(path)
        path.unlink(missing_ok=True)
    sync_directory(target.parent)


def atomic(path, data):
    sidecar = path.with_name(path.name + '.emaki-' + uuid.uuid4().hex)
    try:
        write(sidecar, data)
        os.replace(sidecar, path)
        sync_directory(path.parent)
    except BaseException:
        if sidecar.exists():
            discard_sidecars(path, [sidecar])
        raise
    expected = data.encode() if isinstance(data, str) else data
    require(durable_read(path) == expected, f'{path} could not be read back after replacement.')
    sync_grub_menu(path)
    discard_sidecars(path, [sidecar])


def sync_grub_menu(path):
    if path in (Path('/boot/grub/grub.cfg'), Path('/boot/grub/grub-btrfs.cfg')):
        # Btrfs fsync can persist only the tree log. GRUB reads the committed
        # filesystem trees without Linux's mount-time replay, so commit this
        # rename before publishing ESP state or dropping the recovery intent.
        run(['sync', '-f', path])


def room(path, needed, reserve):
    info = os.statvfs(path)
    require(info.f_bavail * info.f_frsize >= needed + reserve,
            f'{path} needs at least {(needed + reserve + MIB - 1) // MIB} MiB free; '
            'free some space before running emaki-boot-refresh again.')


def assignments(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(GRUB_[A-Z0-9_]+)=(.*)', line)
        if match:
            try:
                words = shlex.split(match[2], comments=True)
            except ValueError:
                continue
            if len(words) <= 1:
                result[match[1]] = words[0] if words else ''
    return result


def migrate_defaults(text, defaults):
    if INCLUDE in text.splitlines():
        return text
    values = assignments(text)
    require(OLD_INCLUDE in text.splitlines() or values.get('GRUB_DISTRIBUTOR') == 'Emaki',
            '/etc/default/grub is not a recognized Emaki installation.')
    # These are the installer's pre-migration defaults. Different values remain
    # explicit overrides after the include; disk/resume/prober settings stay intact.
    managed = assignments(defaults)
    retained = []
    for line in text.splitlines():
        if line == OLD_INCLUDE:
            continue
        parsed = assignments(line)
        if parsed and all(k in managed and v == LEGACY.get(k) for k, v in parsed.items()):
            continue
        retained.append(line)
    fallback = ''.join(f'{key}={shlex.quote(value)}\n' for key, value in LEGACY.items())
    return fallback + INCLUDE + '\n' + '\n'.join(retained).lstrip('\n') + '\n'


def verify_image(image, prefix, early=None, memdisk=None, luks=None):
    require(64 * 1024 <= len(image) <= 32 * MIB and image[:2] == b'MZ',
            'The staged EFI image has an invalid size or DOS header.')
    offset = struct.unpack_from('<I', image, 0x3c)[0]
    require(offset + 26 <= len(image) and image[offset:offset + 4] == b'PE\0\0'
            and struct.unpack_from('<H', image, offset + 4)[0] == 0x8664
            and struct.unpack_from('<H', image, offset + 24)[0] == 0x20b,
            'The staged loader is not an x86-64 EFI image.')
    require(prefix.encode() + b'\0' in image, 'The staged EFI prefix does not match its boot generation.')
    if early is not None:
        require(early in image and memdisk in image,
                'The staged EFI image does not contain its verified early config and module memdisk.')
    if luks:
        require(f'cryptomount -u {luks}'.encode() in image,
                'The staged EFI image does not contain the verified unlock payload and disk UUID.')


def plain_load_config(identity, generation):
    """Search the verified root UUID regardless of the ESP's disk or partition."""
    require(re.fullmatch(UUID, identity['uuid']) and identity['fsroot'] in ('', '/@'),
            'The staged loader searches for the wrong root.')
    return (f'search.fs_uuid {identity["uuid"]} root\n'
            f'set prefix=($root){identity["fsroot"]}{generation / "grub"}\n')


def loader_payload(grub, generation, identity, load_cfg, font, extra=None):
    """Keep module ABI independent of both package upgrades and root rollback.

Every normal menu uses the live kernels and initramfs of its root. If an older
root has replaced @, its canonical menu chooses its restored kernels. Modules
always come from the EFI image, including on an old root with old /boot/grub.
"""
    for path in grub.rglob('*'):
        require(not path.is_symlink(), f'{path} is a symbolic link.')
    try:
        return boot.grub_loader_payload(grub, generation, identity['fsroot'], identity['luks'],
                                        load_cfg, font, extra)
    except boot.InstallError as error:
        raise Refuse(error.message) from error


def mount_info(path, exact=False):
    result = json.loads(run(['findmnt', '--json', '--output',
                           'TARGET,SOURCE,FSTYPE,FSROOT,UUID,PARTUUID,OPTIONS',
                           '--mountpoint' if exact else '--target', path]))
    value = result.get('filesystems', [])
    require(len(value) == 1 and isinstance(value[0], dict)
            and all(isinstance(value[0].get(key), str) and value[0][key]
                    for key in ('target', 'source', 'fstype', 'fsroot', 'options')),
            f'Cannot identify one mounted filesystem at {path}.')
    return value[0]


def fstab_path():
    return Path('/etc/fstab')


def esp_mount_path():
    return Path('/efi')


def own_esp_record():
    """The alongside record when it names Emaki's own ESP (0.9 layout), else None.

    A record that cannot be read or validated is None too: the caller then keeps the
    conservative shared-ESP rules.
    """
    if not (SHARED_ESP.exists() or SHARED_ESP.is_symlink()):
        return None
    try:
        value = shared.record(SHARED_ESP)
    except shared.Refused:
        return None
    return value if 'emaki_esp_partuuid' in value else None


def shared_esp():
    # Losing the installation record must not turn a shared ESP into an
    # unrestricted target. The installation's read-only fstab policy and
    # Microsoft files are evidence; a filesystem-error remount is not.
    # A record of Emaki's own ESP is not evidence; the other evidence still is
    # (discover() also requires that ESP at /efi).
    try:
        if shared.stop_record().exists() or shared.stop_record().is_symlink():
            return True
        if (SHARED_ESP.exists() or SHARED_ESP.is_symlink()) and own_esp_record() is None:
            return True
        fstab = fstab_path()
        if fstab.exists():
            for line in fstab.read_text().splitlines():
                row = line.split()
                if len(row) >= 4 and not row[0].startswith('#') and row[1] == '/efi':
                    if 'ro' in row[3].split(','):
                        return True
        esp = esp_mount_path()
        if esp.exists():
            for directory in esp.iterdir():
                if directory.name.casefold() == 'efi':
                    if directory.is_symlink():
                        raise shared.Refused(shared.UNCHECKED)
                    for vendor in directory.iterdir():
                        if vendor.name.casefold() == 'microsoft':
                            return True
        return False
    except (OSError, UnicodeError) as error:
        raise shared.Refused(shared.UNCHECKED) from error


def fallback_loader():
    """The removable path belongs to Windows' boot setup on a shared ESP; never write it.

    A machine that signs its boot files starts shim as BOOTX64.EFI, and shim starts the
    GRUB next to it: that GRUB is the fallback loader the boot trial promotes.
    """
    if shared_esp():
        return None
    if signing():
        return Path('/efi') / secureboot.REMOVABLE_DIR / secureboot.GRUB
    return Path('/efi/EFI/BOOT/BOOTX64.EFI')


def signing():
    """True when this machine signs its boot files: the installer created its key."""
    return secureboot.enabled(Path('/'))


def key_missing():
    """True when the machine was set up for signing but its private key is gone."""
    return secureboot.damaged(Path('/'))


def esp_partuuid():
    """The partition GUID of the mounted ESP, as firmware boot entries name it."""
    try:
        value = mount_info('/efi', True).get('partuuid')
    except Refuse:
        return None
    return value.lower() if isinstance(value, str) and re.fullmatch(UUID, value) else None


def load_option_target(option):
    """(partition GUID or None, file or None, optional data) of an EFI_LOAD_OPTION's first
    device path, or None when it is malformed.

    The option holds Attributes (4 bytes), FilePathListLength (2), a NUL-terminated UTF-16
    description, the device path nodes (type, subtype, 2-byte length), then its optional data
    (what shim reads as load options). Controller nodes may come first; then one hard drive
    node names the partition by its GUID and the file path nodes after it name the loader.
    Anything else after the partition is not a disk loader. A path naming a device and no
    file is a firmware's removable-media entry (it starts \\EFI\\BOOT\\BOOTX64.EFI there).
    """
    if len(option) < 6:
        return None
    size = int.from_bytes(option[4:6], 'little')
    position = 6
    while option[position:position + 2] != b'\0\0':
        if position + 2 > len(option):
            return None
        position += 2
    paths = option[position + 2:position + 2 + size]
    if len(paths) != size:
        return None
    optional = option[position + 2 + size:]
    partition, parts, loose, position = None, [], [], 0
    while True:
        if position + 4 > len(paths):
            return None
        kind, subtype = paths[position], paths[position + 1]
        length = int.from_bytes(paths[position + 2:position + 4], 'little')
        if length < 4 or position + length > len(paths):
            return None
        body = paths[position + 4:position + length]
        position += length
        if kind == 0x7f:
            break  # the end of this instance or of the list
        if kind == 4 and subtype == 1:
            # Number, start, size, signature, partition format 2 (GUID table), signature type 2 (GUID).
            if partition is not None or len(body) != 38 or body[36:38] != bytes((2, 2)):
                return None
            partition = str(uuid.UUID(bytes_le=body[20:36]))
        elif kind == 4 and subtype == 4 and not len(body) % 2:
            try:
                text = body.decode('utf-16-le')
            except UnicodeError:
                return None
            if not text.endswith('\0') or '\0' in text[:-1]:
                return None
            (parts if partition is not None else loose).append(text[:-1])
        elif partition is not None:
            return None
    found = parts or loose
    path = '\\' + '\\'.join(filter(None, (part.strip('\\') for part in found))) if found else None
    return partition, path, optional


def load_option_path(option):
    """(partition GUID, file) of an EFI_LOAD_OPTION's first device path, or None
    (load_option_target with both a partition and a file)."""
    target = load_option_target(option)
    if target is None or target[0] is None or target[1] is None:
        return None
    return target[0], target[1]


def boot_current():
    """(partition GUID or None, file or None, optional data) of the entry this boot came
    through (BootCurrent), or None when it cannot be read."""
    root = Path('/')
    try:
        current = secureboot.efi_variable(root, 'BootCurrent', secureboot.GLOBAL_GUID)
        if current is None or len(current) != 2:
            return None
        option = secureboot.efi_variable(root, 'Boot%04X' % int.from_bytes(current, 'little'),
                                         secureboot.GLOBAL_GUID)
    except (OSError, secureboot.SecureBootError):
        return None
    return load_option_target(option) if option is not None else None


def booted_slot():
    """The slot whose shim on this ESP the firmware started this boot from, or None.

    BootCurrent names the entry; its device path (load_option_target) names the partition
    and the loader. FAT ignores case; the partition must be this ESP, not another disk's copy.
    An entry with optional data proves nothing: shim may have loaded another second stage
    from it (load-options.c), so the slot's GRUB did not necessarily run.
    """
    target = boot_current()
    if target is None or target[0] is None or target[1] is None or target[2]:
        return None
    partuuid = esp_partuuid()
    if partuuid is None or target[0] != partuuid:
        return None
    return secureboot.entry_slot(target[1])


# EDK2's marker on the boot options its boot manager creates by itself (BmBoot.c
# mBmAutoCreateBootOptionGuid 8108ac4e-9f11-4d59-850e-e21a522c59b2, little-endian bytes).
# EfiBootManagerBoot gives such an option's data to the started image only when the option is
# NOT auto-created ("if (!BmIsAutoCreateBootOption (BootOption))"), so shim sees no load options.
AUTO_CREATED_OPTION = bytes.fromhex('4eac0881119f594d850ee21a522c59b2')


def booted_removable():
    """True when this boot came through the removable path of this ESP: BootCurrent names
    \\EFI\\BOOT\\BOOTX64.EFI on it, or this ESP with no file, or a device with no partition and
    no file (a firmware's removable-media entry). Optional data is no proof, except EDK2's
    auto-created marker on a firmware removable-media entry, which the firmware never passes on
    (OVMF's "UEFI Misc Device", S9V H-13f)."""
    target = boot_current()
    if target is None:
        return False
    partition, path, optional = target
    if optional and not (optional == AUTO_CREATED_OPTION and path is None):
        return False
    removable = '\\' + secureboot.REMOVABLE_DIR.replace('/', '\\') + '\\' + secureboot.REMOVABLE_SHIM
    if partition is None:
        return path is None
    return partition == esp_partuuid() and (path is None or path.casefold() == removable.casefold())


fat_bytes = secureboot.fat_bytes


def signed_pe_image(data):
    """True when data is a whole signed x86-64 PE image, read from its headers only.

    DOS header, 'PE' signature, an x86-64 COFF header with sections, a PE32+ optional header
    with SizeOfHeaders, every section's raw data inside the file, and a certificate table
    (data directory 4) holding well-formed Authenticode entries (secureboot.
    pe_certificates_present). Nothing is run, and whether firmware trusts the signature is
    not decided here: this only tells a damaged, truncated or unsigned file apart.
    """
    try:
        if len(data) < 64 or data[:2] != b'MZ':
            return False
        pe = int.from_bytes(data[0x3c:0x40], 'little')
        if data[pe:pe + 4] != b'PE\0\0':
            return False
        machine, sections = struct.unpack_from('<HH', data, pe + 4)
        optional_size, = struct.unpack_from('<H', data, pe + 20)
        optional = pe + 24
        if machine != 0x8664 or not sections or optional_size < 112 + 5 * 8 \
                or optional + optional_size > len(data):
            return False
        magic, = struct.unpack_from('<H', data, optional)
        headers, = struct.unpack_from('<I', data, optional + 60)
        directories, = struct.unpack_from('<I', data, optional + 108)
        if magic != 0x20b or not 0 < headers <= len(data) or directories < 5:
            return False
        certificate, certificate_size = struct.unpack_from('<II', data, optional + 112 + 4 * 8)
        if not certificate_size or certificate < headers or certificate + certificate_size > len(data):
            return False
        table = optional + optional_size
        if table + 40 * sections > len(data):
            return False
        for index in range(sections):
            size, offset = struct.unpack_from('<II', data, table + 40 * index + 16)
            if size and offset + size > len(data):
                return False
        return secureboot.pe_certificates_present(data)
    except struct.error:
        return False


def slot_payload(image):
    """The complete slot a refresh writes: the package's shim and MokManager, this signed GRUB
    and the machine key's certificate."""
    shim_dir, der = Path(secureboot.SHIM_DIR), Path(secureboot.DER)
    for path in (shim_dir / secureboot.SHIM, shim_dir / secureboot.MOK_MANAGER, der):
        require(path.is_file() and not path.is_symlink(),
                f'{path} is missing; reinstall emaki-shim-signed or restore the machine key.')
    return secureboot.slot_payload(image, shim_dir, der)


def check_slot_file(path, data):
    """Refuse a slot file that is not what the slot must start: shim and MokManager whole
    images with Authenticode entries (and a signature table sbverify --list can read, where
    sbverify exists), GRUB signed by this machine's key (sbverify --cert, which reads the
    whole image and its signature)."""
    if path.name == secureboot.ESP_CERT:
        return
    if path.name == secureboot.GRUB:
        if not secureboot.signed(secure_run, path, Path(secureboot.STATE)):
            raise secureboot.SecureBootError(f'{path} is not signed with this machine\'s key.')
        return
    if not signed_pe_image(data):
        raise secureboot.SecureBootError(f'{path} is not a whole signed EFI image.')
    if shutil.which('sbverify'):
        listing = secure_run(['sbverify', '--list', str(path)])
        if not re.search(r'(?m)^signature \d+', listing):
            raise secureboot.SecureBootError(f'{path} has no signature table.')


def firmware():
    """The parsed firmware boot entries; refuse rather than guess when they cannot be read."""
    parsed = secureboot.firmware_entries(run(['efibootmgr', '-v']))
    require(parsed is not None, 'The firmware startup entries could not be read; nothing was changed.')
    return parsed


def firmware_write(args, confirmed, change):
    """One efibootmgr write, then the firmware variables read back: refuse unless confirmed(parsed)
    holds. A full variable store (EFI_OUT_OF_RESOURCES, ENOSPC to efibootmgr) keeps the loader
    with advice instead of a bare error."""
    try:
        run(['efibootmgr', *args])
    except Transient as error:
        if 'No space left on device' in str(error):
            raise Kept(NVRAM_FULL) from error
        raise
    parsed = firmware()
    require(confirmed(parsed), NVRAM_NOT_KEPT.format(change=change))
    return parsed


def esp_location():
    """(disk, partition number) of the mounted ESP, for efibootmgr --create-only."""
    source = Path(os.path.realpath(mount_info('/efi', True)['source']))
    block = Path('/sys/class/block') / source.name
    number = regular(block / 'partition').decode().strip()
    disk = Path(os.path.realpath(block)).parent.name
    require(number.isdigit() and disk and disk != 'block',
            "The startup partition's disk could not be identified.")
    return '/dev/' + disk, number


def labelled(parsed, partuuid, slot, label):
    """The usable entries (active, no optional data) of slot with this label; an unusable one
    is never kept as Emaki's entry: it is replaced and deleted (arrange_slots)."""
    return [number for number in secureboot.slot_entries(parsed, partuuid, usable=True)[slot]
            if parsed['entries'][number][1] == label]


def create_slot_entry(slot, label):
    """A new firmware entry for slot, outside BootOrder; (its number, the new listing)."""
    partuuid = esp_partuuid()
    before = firmware()
    disk, number = esp_location()
    # No optional data: shim then starts the GRUB beside it (load-options.c returns early).
    after = firmware_write(['--create-only', '--disk', disk, '--part', number, '--label', label,
                            '--loader', secureboot.slot_entry(slot)],
                           lambda parsed: len([entry for entry in labelled(parsed, partuuid, slot, label)
                                               if entry not in before['entries']]) == 1
                           and parsed['order'] == before['order'],
                           f'the new startup entry for startup copy {slot}')
    new, = [entry for entry in labelled(after, partuuid, slot, label) if entry not in before['entries']]
    return new, after


def withdraw_slot(slot):
    """Delete every firmware entry naming slot, and a BootNext to it: nothing starts it now."""
    partuuid = esp_partuuid()
    parsed = firmware()
    numbers = secureboot.slot_entries(parsed, partuuid)[slot]
    # Out of BootOrder first (a candidate stands there for firmware that needs it, publish_slot),
    # so no firmware fallback reaches the slot while it is rewritten.
    order = [number for number in parsed['order'] if number not in numbers]
    if order and order != parsed['order']:
        firmware_write(['--bootorder', ','.join(order)], lambda p: p['order'] == order, 'the startup order')
    if parsed['next'] in numbers:
        firmware_write(['--delete-bootnext'], lambda p: p['next'] is None, 'the next start')
    for number in numbers:
        firmware_write(['--bootnum', number, '--delete-bootnum'],
                       lambda p, number=number: number not in p['entries'], 'a startup entry')
    parsed = firmware()
    require(not secureboot.slot_entries(parsed, partuuid)[slot] and parsed['next'] not in numbers,
            f'The firmware still starts startup copy {slot}; it was not rewritten.')


def arrange_slots(active, keep_previous):
    """Bring the firmware entries to: 'Emaki' -> active, where Emaki's entries stand in
    BootOrder, then 'Emaki (previous)' -> the other slot when keep_previous (a slot that has
    completed a boot); an untried complete slot keeps its entry outside BootOrder (BootNext
    only); entries of an incomplete slot and duplicates go.

    The single BootOrder write is the only step that can change which slot BootOrder starts,
    and it only ever puts active first; every other step creates or deletes an entry outside
    BootOrder. A cut anywhere leaves BootOrder starting a complete slot, and the next run
    finishes the labels.
    """
    partuuid = esp_partuuid()
    require(partuuid is not None, 'The startup partition could not be identified.')
    other = secureboot.other_slot(active)
    parsed = firmware()
    first = labelled(parsed, partuuid, active, secureboot.ACTIVE_LABEL)
    if not first:
        _, parsed = create_slot_entry(active, secureboot.ACTIVE_LABEL)
        first = labelled(parsed, partuuid, active, secureboot.ACTIVE_LABEL)
    complete = secureboot.read_slot(Path('/efi'), other) is not None
    previous = labelled(parsed, partuuid, other, secureboot.PREVIOUS_LABEL) if complete else []
    if keep_previous and complete and not previous:
        _, parsed = create_slot_entry(other, secureboot.PREVIOUS_LABEL)
        previous = labelled(parsed, partuuid, other, secureboot.PREVIOUS_LABEL)
    owned = {number for numbers in secureboot.slot_entries(parsed, partuuid).values() for number in numbers}
    order = parsed['order']
    first_owned = next((index for index, number in enumerate(order) if number in owned), 0)
    # A number whose entry is gone (deleted, or a firmware's leftover) is dropped, never written back.
    rest = [number for number in order if number not in owned and number in parsed['entries']]
    position = len([number for number in order[:first_owned] if number in rest])
    ours = first[:1] + (previous[:1] if keep_previous else [])
    # Order drift: a Windows upgrade (BCDBoot without /p) or another installer put its entry
    # before Emaki's. This boot completed through Emaki, so Emaki goes first again; every
    # other entry stays, in its order. Firmware that refuses the order (a boot order lock)
    # still gets the switch at the old position, and the person is told.
    drifted = [parsed['entries'][number][1] or number for number in rest[:position]]
    kept = rest[:position] + ours + rest[position:]
    wanted = ours + rest
    if wanted != order:
        try:
            firmware_write(['--bootorder', ','.join(wanted)], lambda p: p['order'] == wanted, 'the startup order')
        except (Transient, Refuse) as error:
            if not drifted or isinstance(error, Kept):
                raise
            if kept != firmware()['order']:
                firmware_write(['--bootorder', ','.join(kept)], lambda p: p['order'] == kept, 'the startup order')
            print(ORDER_LOCKED.format(label=drifted[0]))
        else:
            if drifted:
                print(ORDER_RESTORED.format(label=drifted[0]))
    keep = set(first[:1]) | set(previous[:1])
    for number in sorted(owned - keep):
        firmware_write(['--bootnum', number, '--delete-bootnum'],
                       lambda p, number=number: number not in p['entries'], 'a startup entry')


def slot_usage(slot, cluster):
    directory = secureboot.slot_dir(Path('/efi'), slot)
    if not directory.is_dir() or directory.is_symlink():
        return 0
    return cluster + sum(fat_bytes(path.stat().st_size, cluster) + cluster
                         for path in directory.iterdir() if path.is_file() and not path.is_symlink())


def removable_files(slot):
    """EFI/BOOT path -> bytes the fallback copies of a slot must have, or {} where the ESP
    is shared with Windows (Windows owns the removable path there)."""
    if fallback_loader() is None:
        return {}
    directory = secureboot.slot_dir(Path('/efi'), slot)
    removable = Path('/efi') / secureboot.REMOVABLE_DIR
    return {removable / secureboot.REMOVABLE_SHIM: regular(directory / secureboot.SHIM),
            removable / secureboot.MOK_MANAGER: regular(directory / secureboot.MOK_MANAGER),
            removable / secureboot.GRUB: regular(directory / secureboot.GRUB)}


def check_slot_space(sizes, stage=0):
    """Refuse an update before anything changes unless Emaki's whole Secure Boot layout fits:
    secureboot.slots_need (two slots, the removable copies where Emaki owns the ESP, the
    grub-install stage) plus SHARED_ESP_RESERVE next to Windows, against the real free space
    plus what Emaki's slots and removable copies already hold. The installer admits by the
    same rule, so what it admitted keeps updating."""
    info = os.statvfs(Path('/efi'))
    cluster = max(info.f_frsize, 1)
    removable = fallback_loader() is not None
    held = sum(slot_usage(slot, cluster) for slot in secureboot.SLOTS)
    if removable:
        held += sum(fat_bytes(path.stat().st_size, cluster) for path in
                    (Path('/efi') / secureboot.REMOVABLE_DIR / name
                     for name in (secureboot.REMOVABLE_SHIM, secureboot.MOK_MANAGER, secureboot.GRUB))
                    if path.is_file() and not path.is_symlink())
    available = info.f_bavail * info.f_frsize + held
    needed = secureboot.slots_need(sizes, cluster, removable, stage)
    if shared_esp():
        needed += secureboot.SHARED_ESP_RESERVE
    if available < needed:
        raise Kept(SLOT_NO_ROOM.format(free=available // MIB, needed=-(-needed // MIB)))


def publish_slot(tag, image, active):
    """Write the slot no BootOrder entry starts, completely, and offer it once via BootNext.

    Its firmware entries go first (withdraw_slot), then secureboot.write_slot writes every
    file, checks it and writes the manifest last; only then does it get an entry, outside
    BootOrder, and BootNext. The active slot is never touched: a cut at any point leaves it
    and BootOrder as they were, and an incomplete slot is rewritten by the next update.
    """
    other = secureboot.other_slot(active)
    payload = slot_payload(image)
    check_slot_space({name: len(data) for name, data in payload.items()})
    withdraw_slot(other)
    esp_writable(secureboot.slot_dir(Path('/efi'), other))
    try:
        secureboot.write_slot(Path('/efi'), other, payload, tag, check_slot_file)
    except secureboot.SecureBootError as error:
        raise Refuse(str(error)) from error
    number, parsed = create_slot_entry(other, secureboot.PREVIOUS_LABEL)
    # Some firmware starts BootNext only for an entry that BootOrder lists (fwupdate 9 adds its
    # own for that reason). The slot is complete now, so its entry goes right after the
    # known-good one: never before it, and never while the slot is written (withdraw_slot).
    # The usable entry firmware starts the active slot by: an inactive one before it is skipped.
    known = secureboot.slot_entries(parsed, esp_partuuid(), usable=True)[active]
    anchor = next(index for index, entry in enumerate(parsed['order']) if entry in known)
    order = parsed['order'][:anchor + 1] + [number] + parsed['order'][anchor + 1:]
    firmware_write(['--bootorder', ','.join(order)], lambda p: p['order'] == order, 'the startup order')
    firmware_write(['--bootnext', number], lambda p: p['next'] == number, 'the next start')
    return other


def current_slots():
    """(active slot, its manifest), refusing when BootOrder starts no complete slot here."""
    partuuid = esp_partuuid()
    require(partuuid is not None, 'The startup partition could not be identified.')
    active = secureboot.active_slot(firmware(), partuuid)
    record = secureboot.read_slot(Path('/efi'), active) if active else None
    if record is None:
        raise Kept(SLOT_NO_ENTRY)
    return active, record


def refresh_fallback(slot):
    """Copy a slot that has completed a boot to EFI/BOOT, where Emaki owns the ESP.

    The removable path is the firmware's last resort, not the boot path: NVRAM starts the
    slots. Its three files are replaced one by one, so a cut there can leave them mixed or
    torn (a FAT rename cut can leave a file empty); booting through the firmware entries is
    never affected. FALLBACK_MARKER is created before the first replacement and removed after
    the last: while it exists, EFI/BOOT is Emaki's half-written copy, so the next completed
    boot rewrites it whatever a cut left there (R9S9 #6). Without the marker, files there
    that are not a slot's shim are not Emaki's and are kept (FOREIGN).
    """
    try:
        wanted = removable_files(slot)
    except Refuse:
        return
    if not wanted:
        return
    shim = Path('/efi') / secureboot.REMOVABLE_DIR / secureboot.REMOVABLE_SHIM
    ours = {regular(secureboot.slot_dir(Path('/efi'), name) / secureboot.SHIM)
            for name in secureboot.SLOTS if secureboot.read_slot(Path('/efi'), name)}
    marker = Path(FALLBACK_MARKER)
    interrupted = marker.exists() or marker.is_symlink()
    if shim.is_symlink() or shim.exists() and not interrupted and regular(shim) not in ours:
        print(FOREIGN)
        return
    stale = [path for path, data in wanted.items()
             if not path.exists() or path.is_symlink() or regular(path) != data]
    if not stale and not interrupted:
        return
    if not interrupted:
        write(marker, b'')
    for path, data in sorted(wanted.items(), key=lambda item: item[0].name == secureboot.REMOVABLE_SHIM):
        if interrupted:
            # Sidecars a cut left behind (atomic's <name>.emaki-<id>).
            discard_sidecars(path, [sidecar for sidecar in path.parent.glob(path.name + '.emaki-*')
                                    if re.fullmatch(re.escape(path.name) + r'\.emaki-[0-9a-f]{32}', sidecar.name)])
        if path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic(path, data)
    marker.unlink(missing_ok=True)
    sync_directory(marker.parent)
    if stale:
        print('The fallback loader was updated after a completed boot.')


def esp_sector_size():
    """The ESP's FAT logical sector size: bytes per sector in its boot sector (BPB offset 11),
    the block size Linux writes FAT files in (fs/fat/inode.c fat_fill_super). The smallest,
    512, when the device cannot be read or names no FAT size: the zero padding a record must
    have then covers only what every FAT sector size guarantees (esp_records)."""
    try:
        source = mount_info('/efi', True)['source']
        with open(source, 'rb') as device:
            boot = device.read(512)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError):
        return esp_records.SECTOR
    size = int.from_bytes(boot[11:13], 'little') if len(boot) == 512 else 0
    return size if size in (512, 1024, 2048, 4096) else esp_records.SECTOR


def tidy_fsck_records():
    """The FSCKnnnn.REC files fsck.fat saved in the ESP root after a power cut (S9V F-F1).

    On an ESP Emaki owns, a record is removed only when it is a lost copy
    (esp_records.emaki_copy) of content Emaki trusts, never of whatever a cut left on the ESP
    (R9S10 #1): the four files of a COMPLETE slot (read_slot checked every file against its
    manifest), the shim package's shim and MokManager, and the images of boot generations
    whose manifest is readable: emaki-early.efi only when its sha256 is the manifest's
    efi_sha256, and grub-install's core.efi (the generation is synced before its manifest is
    written). A slot file of an incomplete slot, a temporary or sidecar file, the EFI/BOOT
    copies and a repair image are never references; a lost copy of one of them is a copy of
    one of the trusted files anyway. Runs before cleanup prunes generations, so the image of
    an update a cut stopped is still known. Every other record is kept and logged. On an ESP
    shared with Windows nothing is touched, only reported: its room counts against the free
    space the slot rule measures (check_slot_space reads the real free space after this)."""
    esp = Path('/efi')
    try:
        if not esp_records.records(esp):
            return
        shared_esp_found = bool(shared_esp())
    except (OSError, shared.Refused):
        return
    known, seen = {}, set()

    def add(label, path, sha256=None):
        try:
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                if data and data not in seen and (sha256 is None or digest(data) == sha256):
                    seen.add(data)
                    known[label] = data
        except OSError:
            pass

    if not shared_esp_found:
        for slot in secureboot.SLOTS:
            record = secureboot.read_slot(esp, slot)
            if record is not None:
                for name in secureboot.slot_files_names():
                    add(f'EFI/Emaki/{slot}/{name}', secureboot.slot_dir(esp, slot) / name,
                        record['files'][name]['sha256'])
        for name in (secureboot.SHIM, secureboot.MOK_MANAGER):
            add(f'the shim package\'s {name}', Path(secureboot.SHIM_DIR) / name)
        generations = sorted(path for path in Path('/boot/emaki').glob('*')
                             if re.fullmatch(r'[0-9a-f]{32}', path.name) and not path.is_symlink())
        for generation in generations:
            try:
                manifest = json.loads(regular(generation / 'manifest.json'))
            except (Refuse, ValueError, UnicodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            platform = generation / 'grub/x86_64-efi'
            if isinstance(manifest.get('efi_sha256'), str):
                add(f'the emaki-early.efi of {generation.name}', platform / 'emaki-early.efi',
                    manifest['efi_sha256'])
            add(f'the core.efi of {generation.name}', platform / 'core.efi')

    def log(line):
        print(line)
        if LOG is not None:
            LOG.write(line + '\n')

    try:
        cluster = max(os.statvfs(esp).f_frsize, 1)
    except OSError:
        return
    esp_records.tidy(esp, known, shared_esp_found, cluster, log, esp_sector_size())


def slot_generations():
    """Boot generations the complete slots start: prune keeps them."""
    return {record['generation'] for record in (secureboot.read_slot(Path('/efi'), slot)
                                                for slot in secureboot.SLOTS)
            if record and record['generation']}


def boot_state():
    """boot-state.json for recovery and cleanup. With the machine key it is only a note (BootOrder
    and the slots are the record), so one a cut left unreadable never stops a boot (slot_state)."""
    if signing():
        return slot_state()
    path = Path('/efi/EFI/Emaki/boot-state.json')
    return json.loads(regular(path)) if path.exists() else {'good': None, 'newest': None}


def slot_state():
    """good/newest for the menus and prune, from boot-state.json; a record a cut left
    unreadable becomes what the slots say (good = the active slot's generation)."""
    path = Path('/efi/EFI/Emaki/boot-state.json')
    try:
        state = json.loads(regular(path)) if path.exists() else None
    except (ValueError, UnicodeError, Refuse):
        state = None
    if not (isinstance(state, dict) and all(
            state.get(key) is None or isinstance(state.get(key), str) and re.fullmatch(r'[0-9a-f]{32}', state[key])
            for key in ('good', 'newest'))):
        state = None
    if state is None:
        try:
            _, record = current_slots()
            good = record['generation']
        except Refuse:
            good = None
        state = {'good': good, 'newest': good}
    return {'good': state.get('good'), 'newest': state.get('newest'), 'fallback_sha256': None}


def slot_proven(slot, tag, identity):
    """True when a completed boot carrying emaki.generation=tag proves slot: the slot is
    complete and its manifest names tag, its GRUB is that generation's own image (the
    efi_sha256 of its manifest, or the image emaki-boot-repair rebuilt for it), the generation
    belongs to this installed root, and the running kernel's modules are installed. False when
    this boot is not about slot; Refuse when the slot claims it but something does not match.
    The same proof serves a switch and the recreation of lost entries (R9S9 #2, #8)."""
    record = secureboot.read_slot(Path('/efi'), slot) if slot else None
    if record is None or tag is None or record['generation'] != tag:
        return False
    generation = Path('/boot/emaki') / tag
    if not (generation / 'manifest.json').is_file():
        return False
    try:
        manifest = json.loads(regular(generation / 'manifest.json'))
    except (ValueError, UnicodeError) as error:
        raise Refuse(f'The manifest of boot generation {tag} is unreadable.') from error
    require(isinstance(manifest, dict)
            and all(manifest.get(key) == identity[key] for key in ('uuid', 'esp_uuid', 'fsroot', 'luks')),
            'The booted generation belongs to a different installed root.')
    images = {manifest.get('efi_sha256')}
    repaired = generation / 'grub/x86_64-efi/emaki-repair.efi'
    if repaired.is_file() and not repaired.is_symlink():
        images.add(digest(regular(repaired)))
    if record['files'][secureboot.GRUB]['sha256'] not in images:
        if LOG is not None:
            LOG.write(f'The GRUB in boot slot {slot} is not the image of boot generation {tag}; '
                      'the slot was not switched.\n')
        raise Refuse('The startup files do not match; the startup copy was not switched.')
    if not (Path('/usr/lib/modules') / os.uname().release).is_dir():
        if LOG is not None:
            LOG.write('The running kernel has no installed modules; the boot slot was not switched.\n')
        raise Refuse('Files needed by the running system are missing; the startup copy was not switched.')
    return True


def fallback_slot(tag):
    """The slot a boot through the removable path proves, or None: BootCurrent names this
    ESP's \\EFI\\BOOT\\BOOTX64.EFI (or a firmware's removable-media entry, booted_removable),
    and EFI/BOOT's shim and GRUB are byte for byte the ones of the one complete slot whose
    manifest names the booted generation, so that slot's loaders are what ran.

    EFI/BOOT must hold nothing else that changes what shim starts: shim reads options.csv
    from its own directory and may take another second stage from it, and starts fbx64.efi
    instead of GRUB when that is there (shim 16.1 shim.c). So only Emaki's three copies and
    atomic's sidecars of them may be there (FAT ignores case); anything else is no proof
    (R9S10 #2)."""
    if tag is None or fallback_loader() is None or not booted_removable():
        return None
    removable = Path('/efi') / secureboot.REMOVABLE_DIR
    names = [name.casefold() for name in (secureboot.REMOVABLE_SHIM, secureboot.GRUB, secureboot.MOK_MANAGER)]
    try:
        for entry in removable.iterdir():
            name = entry.name.casefold()
            if name not in names and not any(re.fullmatch(re.escape(own) + r'\.emaki-[0-9a-f]{32}', name)
                                             for own in names):
                return None
        shim, grub = regular(removable / secureboot.REMOVABLE_SHIM), regular(removable / secureboot.GRUB)
    except (OSError, Refuse):
        return None
    holders = []
    for slot in secureboot.SLOTS:
        record = secureboot.read_slot(Path('/efi'), slot)
        directory = secureboot.slot_dir(Path('/efi'), slot)
        if record and record['generation'] == tag and regular(directory / secureboot.SHIM) == shim \
                and regular(directory / secureboot.GRUB) == grub:
            holders.append(slot)
    return holders[0] if len(holders) == 1 else None


def mark_good_slots(identity):
    """After a completed boot: switch BootOrder to the slot this boot came through when it is
    the complete slot holding the booted generation; otherwise only repair the entries.

    Proof of the new slot is BootCurrent's device path naming its shim on this ESP with no
    optional data, and slot_proven: its manifest complete and naming the generation on the
    kernel command line, its GRUB that generation's image, this root, the running modules.
    """
    tags = re.findall(r'(?:^|\s)emaki\.generation=([0-9a-f]{32})(?=\s|$)',
                      regular(Path('/proc/cmdline')).decode())
    require(len(tags) <= 1, 'The running boot generation is ambiguous.')
    tag = tags[0] if tags else None
    partuuid = esp_partuuid()
    require(partuuid is not None, 'The startup partition could not be identified.')
    parsed = firmware()
    active = secureboot.active_slot(parsed, partuuid)
    booted = booted_slot()
    if booted is not None and booted != active and slot_proven(booted, tag, identity):
        newest = slot_state()['newest']
        restored_previous = newest is not None and tag != newest
        arrange_slots(booted, keep_previous=active is not None)
        atomic(Path('/efi/EFI/Emaki/boot-state.json'),
               json.dumps({'good': tag, 'newest': tag, 'fallback_sha256': None}) + '\n')
        refresh_fallback(booted)
        print('The previous Emaki startup copy completed this boot and is now the default.'
              if restored_previous else 'The Emaki boot loader was updated after a completed boot.')
        return
    if active is None:
        # NVRAM lost Emaki's usable entries (a firmware reset). Only a start through this
        # ESP's removable path whose loaders are a complete slot's, with the same proof as a
        # switch, recreates them; anything else is left to emaki-boot-repair.
        slot = fallback_slot(tag) if booted is None else None
        if slot is not None and slot_proven(slot, tag, identity):
            arrange_slots(slot, keep_previous=False)
            atomic(Path('/efi/EFI/Emaki/boot-state.json'),
                   json.dumps({'good': tag, 'newest': tag, 'fallback_sha256': None}) + '\n')
        else:
            print(SLOT_NOT_PROVEN)
        return
    other = secureboot.other_slot(active)
    numbers = secureboot.slot_entries(parsed, partuuid)[other]
    in_order = any(number in parsed['order'] for number in numbers)
    # The candidate stands in BootOrder only for its trial (publish_slot): while BootNext still
    # names it, or once it has been the active slot. An untried one whose trial has passed
    # (the newest generation, not the active one) leaves BootOrder.
    candidate = (secureboot.read_slot(Path('/efi'), other) or {}).get('generation')
    untried = candidate is not None and candidate == slot_state()['newest'] \
        and candidate != (secureboot.read_slot(Path('/efi'), active) or {}).get('generation')
    arrange_slots(active, keep_previous=in_order and (parsed['next'] in numbers or not untried))
    if booted == active:
        try:
            proven = slot_proven(active, tag, identity)
        except Refuse as error:
            if LOG is not None:
                LOG.write(f'The fallback loader was not refreshed: {error}\n')
            proven = False
        if proven:
            refresh_fallback(active)


def secure_run(argv, input=None):
    """run() in the shape the Secure Boot helpers call: nothing is passed on stdin."""
    require(input is None, 'The boot refresh passes nothing to signing tools on stdin.')
    return run(argv)


def build_signed_image(mkimage, sbat_path):
    """grub-mkimage with Emaki's SBAT data, then the machine key's signature, proven."""
    try:
        secureboot.build_signed_grub(secure_run, atomic, mkimage, sbat_path,
                                     regular(Path(secureboot.SBAT_SOURCE)).decode(),
                                     state=Path(secureboot.STATE))
    except secureboot.SecureBootError as error:
        raise Refuse(str(error)) from error


def secure_boot_fonts():
    """The menu faces the signed loader carries, since GRUB refuses fonts from disk."""
    try:
        return boot.secure_boot_fonts(Path('/'))
    except boot.InstallError as error:
        raise Refuse(error.message) from error


def stage_parent():
    return Path('/efi/EFI/Emaki') if shared_esp() else Path('/efi')


def discover():
    root = mount_info('/', True)
    if root['fstype'] == 'overlay' or (root['fstype'] == 'btrfs'
            and root['fsroot'].rstrip('/') not in ('', '/@')):
        return None
    require(Path('/sys/firmware/efi').is_dir(), 'This system was not started in UEFI mode.')
    # A machine with its signing key builds signed loaders whatever the firmware switch
    # says; without the key, firmware that checks signatures keeps the loader it has. A
    # machine set up for signing whose key is gone boots through shim: an unsigned loader
    # would lock it out once Secure Boot is on, so nothing is rebuilt.
    require(not key_missing(), KEY_MISSING)
    if not signing():
        for variable in Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'):
            data = variable.read_bytes()
            if len(data) < 5 or data[4] != 0:
                raise Kept(NO_KEY)
    boot_mount, esp = mount_info('/boot'), mount_info('/efi', True)
    for path, mount in (('/', root), ('/boot', boot_mount), ('/efi', esp)):
        require(isinstance(mount.get('uuid'), str) and mount['uuid'],
                f'Cannot identify one mounted filesystem at {path}.')
    require(root['fstype'] in ('btrfs', 'ext4') and 'rw' in root['options'].split(','),
            'Refresh must run from the normal writable installed root.')
    require(root['target'] == boot_mount['target'] and root['uuid'] == boot_mount['uuid'],
            'A separate /boot filesystem is not supported by the Emaki installer.')
    fsroot = root['fsroot'].rstrip('/')
    require(fsroot in ('', '/@'), 'Refresh from a snapshot or an unfamiliar root subvolume is refused.')
    # A shared ESP stays read-only; each write session checks Windows and remounts it.
    require(esp['fstype'] == 'vfat' and ('rw' in esp['options'].split(',') or shared_esp()),
            'The startup partition at /efi must be mounted, writable and formatted as FAT.')
    own = own_esp_record()
    require(own is None or str(esp.get('partuuid') or '').lower() == own['emaki_esp_partuuid'],
            OWN_ESP_MISMATCH)
    rows = [line.split() for line in Path('/etc/fstab').read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    esp_rows = [row for row in rows if len(row) >= 4 and row[1] == '/efi']
    require(len(esp_rows) == 1 and esp_rows[0][0].lower() in (
        'uuid=' + str(esp['uuid']).lower(), 'partuuid=' + str(esp['partuuid']).lower()),
        'The mounted EFI partition does not match /etc/fstab.')
    luks = run(['grub-probe', '--target=cryptodisk_uuid', '/boot']).split()
    require(len(luks) <= 1 and all(re.fullmatch(UUID, item) for item in luks),
            'Cannot identify one supported LUKS disk for /boot.')
    luks = luks[0] if luks else None
    require(set(run(['grub-probe', '--target=fs_uuid', '/boot']).lower().split()) == {root['uuid'].lower()},
            'GRUB and the mounted root disagree about the filesystem UUID.')
    settings = assignments(Path('/etc/default/grub').read_text())
    configured = re.findall(r'cryptdevice=UUID=(' + UUID + r'):emaki-root',
                            settings.get('GRUB_CMDLINE_LINUX', ''))
    require(configured == ([luks] if luks else []),
            'The configured encrypted disk and the mounted root disagree.')
    return {'fsroot': fsroot, 'luks': luks, 'uuid': root['uuid'], 'fstype': root['fstype'],
            'esp_uuid': esp['uuid']}


def check_refresh_storage(identity):
    # Full device scans belong to image preparation, never boot confirmation.
    if not identity['luks']:
        require(not run(['grub-probe', '--target=abstraction', '/boot']).strip(),
                'A plain root on LVM or software RAID is not supported.')
        require(set(run(['grub-probe', '--target=partmap', '/boot']).split()) == {'gpt'},
                'A plain root must use a GPT partition table.')
    def devices(arguments):
        return {os.path.realpath(device) for device in run(arguments).splitlines() if device}
    members = devices(['grub-probe', '--target=device', '/boot'])
    found = devices(['blkid', '-c', '/dev/null', '-t', 'UUID=' + identity['uuid'], '-o', 'device'])
    require(members and found == members,
            'The root filesystem UUID must match its mounted block devices; check for cloned disks.')
    if identity['luks']:
        encrypted = devices(['blkid', '-c', '/dev/null', '-t', 'UUID=' + identity['luks'], '-o', 'device'])
        require(len(encrypted) == 1,
                'The LUKS UUID must belong to exactly one block device; check for cloned disks.')


def bind(source, target):
    run(['mount', '--bind', source, target])


def generate(stage):
    """Private mount namespace: generators cannot overwrite the active menu."""
    stage = Path(stage)
    (stage / 'grub/.emaki-snapshots').mkdir(exist_ok=True)
    bind(stage / 'defaults', Path('/etc/default/grub'))
    if (stage / 'btrfs-config').exists():
        bind(stage / 'btrfs-config', Path('/etc/default/grub-btrfs/config'))
    # 41_snapshots-btrfs has output side effects even when mkconfig uses stdout.
    bind(stage / 'grub', Path('/boot/grub'))
    # A GRUB_FONT in the machine's defaults is sourced later and wins over this one.
    font, theme = menu_font(), menu_theme()
    env = {**({'GRUB_FONT': font} if font else {}), **({'GRUB_THEME': theme} if theme else {})}
    run(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], env or None)
    menu = Path('/boot/grub/grub.cfg')
    original = regular(menu).decode()
    safe = boot.quiet_linux_entries(boot.menu_font_fallback(original))
    own = own_esp_record()
    if own is not None:
        # Emaki's own ESP next to Windows: the Windows entry still restarts into Windows
        # Boot Manager on Windows' ESP, named by the installer's record (never mounted).
        try:
            safe = boot.windows_bootnext_menu(safe, own['esp_partuuid'], own['windows_esp_uuid'],
                                              run(['efibootmgr', '-v']))
        except RuntimeError as error:
            raise Refuse(str(error)) from error
    elif shared_esp():
        partuuid = shared.marker()
        shared.mounted_esp(partuuid)
        esp = mount_info('/efi', True)
        require(str(esp.get('partuuid', '')).lower() == partuuid, shared.WRONG_ESP)
        try:
            safe = boot.windows_bootnext_menu(safe, partuuid, esp.get('uuid'),
                                              run(['efibootmgr', '-v']))
        except RuntimeError as error:
            raise Refuse(str(error)) from error
    if signing():
        # The signed loader carries the menu faces; GRUB refuses fonts from disk.
        safe = boot.secure_boot_menu(safe)
    safe = boot.menu_layout(safe)
    if safe != original:
        # This menu is private staging, never the active canonical menu.
        menu.unlink()
        write(menu, safe)


def menu_font():
    """The packaged face for the mode GRUB drew this boot's menu in, if installed."""
    font = boot.menu_font(boot.boot_framebuffer(Path(boot.BOOT_PARAMS)))
    return font if font and Path(font).is_file() and not Path(font).is_symlink() else None


def menu_theme():
    """The packaged theme scale for the mode GRUB drew this boot's menu in, if installed."""
    theme = boot.menu_theme(boot.boot_framebuffer(Path(boot.BOOT_PARAMS)))
    return theme if Path(theme).is_file() and not Path(theme).is_symlink() else None


def menu_font_needs_repair():
    """True when the canonical menu's face was chosen for another mode than this boot's.

    Fresh installs choose from the live system's mode; firmware that also offers the
    menu's preferred 1024x768 then draws the installed menu there. A local GRUB_FONT stays.
    """
    if boot.boot_framebuffer(Path(boot.BOOT_PARAMS)) is None:
        return False
    defaults, menu = Path('/etc/default/grub'), Path('/boot/grub/grub.cfg')
    if any(not path.is_file() or path.is_symlink() for path in (defaults, menu)):
        return False
    local = assignments(regular(defaults).decode())
    text = regular(menu).decode()
    current = boot.generated_menu_font(text)
    font = 'GRUB_FONT' not in local and current != '' and current != menu_font()
    # A theme drawn for another scale; a local GRUB_THEME or a menu not themed by us stays.
    theme = boot.generated_menu_theme(text)
    return font or ('GRUB_THEME' not in local and theme not in ('', None) and theme != menu_theme())


def rewrite_menu(text, generation, fsroot):
    # Kernels and initramfs belong to stock mkinitcpio, including after refusal.
    # Only normal-root boots may certify this loader; snapshot boots never do.
    lines = []
    for line in text.splitlines(keepends=True):
        if re.match(r'\s*linux(?:efi)?\s+' + re.escape(fsroot + '/boot/vmlinuz-')
                    + r'(?:linux|linux-lts)\s', line):
            line = re.sub(r'\s+emaki\.generation=\S+', '', line.rstrip('\n'))
            line += ' emaki.generation=${emaki_generation}\n'
        lines.append(line)
    text = ''.join(lines)
    if generation is not None:
        text = text.replace(fsroot + '/boot/grub/', fsroot + str(generation / 'grub') + '/')
    # Snapshot discovery remains live: grub-btrfsd maintains this common file.
    text = text.replace('${prefix}/grub-btrfs.cfg', f'($root){fsroot}/boot/grub/grub-btrfs.cfg')
    text = text.replace('$prefix/grub-btrfs.cfg', f'($root){fsroot}/boot/grub/grub-btrfs.cfg')
    return text


def trial_menu(tag, esp_uuid, text):
    """Try inside the ordinary default entry, with its old body as fallback."""
    require(re.fullmatch(r'[0-9a-f]{32}', tag) and re.fullmatch(r'[0-9A-Fa-f-]+', esp_uuid),
            'The trial loader has an invalid disk identity.')
    entry = re.search(r'(?m)^[ \t]*menuentry[^\n]*\{\n', text)
    require(entry is not None, 'The staged menu has no default entry for the boot trial.')
    prepare = f'''# The old loader remains the fallback if the trial cannot start.
set emaki_try=
set emaki_trial_failure=
set emaki_esp=
search --no-floppy --fs-uuid --set=emaki_esp {esp_uuid}
if [ -n "$emaki_esp" ]; then
  set emaki_trial=
  set emaki_candidate=
  if load_env -f ($emaki_esp)/EFI/Emaki/trial.env emaki_candidate emaki_trial; then
    if [ "$emaki_generation" != "{tag}" -a "$emaki_candidate" = "{tag}" ]; then
      if [ "$emaki_trial" = "0" -o "$emaki_trial" = "1" ]; then
        set emaki_try=1
        set default=0
        set fallback=0
      fi
    fi
  else
    set emaki_trial_failure=unreadable
  fi
else
  set emaki_trial_failure=unreadable
fi
'''
    attempt = f'''  if [ "$emaki_try" = "1" ]; then
    set emaki_try=
    if [ "$emaki_trial" = "0" ]; then
      set emaki_trial=1
    else
      set emaki_trial=2
    fi
    set emaki_trial_source=$cmdpath
    if save_env -f ($emaki_esp)/EFI/Emaki/trial.env emaki_trial emaki_trial_source; then
      if chainloader ($emaki_esp)/EFI/Emaki/loader-{tag}.efi; then
        boot
      fi
    else
      set emaki_trial_failure=unwritable
    fi
  fi
'''
    text = re.sub(r'(?m)^(\s*linux(?:efi)?\s+[^\n]* emaki\.generation=[^\n]*)$',
                  lambda match: match[0] + ' emaki.trial=${emaki_trial_failure}'
                  + ' emaki.trial_candidate=' + tag, text)
    return text[:entry.start()] + prepare + entry[0] + attempt + text[entry.end():]


def trial_state():
    path = Path('/efi/EFI/Emaki/trial.env')
    if not path.exists():
        return {}
    data = regular(path)
    if len(data) != 1024 or not data.startswith(b'# GRUB Environment Block\n'):
        return {}
    try:
        return dict(line.split('=', 1) for line in data.decode('ascii').splitlines()
                    if '=' in line and not line.startswith('#'))
    except UnicodeError:
        return {}


def trial_block(values):
    data = ('# GRUB Environment Block\n' + ''.join(f'{k}={v}\n' for k, v in sorted(values.items()))).encode('ascii')
    require(len(data) <= 1024, 'The boot trial counter is too large.')
    return data + b'#' * (1024 - len(data))


def sleep_trial(phase):
    """A restored kernel did not boot the candidate: return its trial attempt."""
    saved = Path(SLEEP_STATE)
    trial = trial_state()
    if phase == 'pre':
        # /run is restored with the hibernation image, unlike the ESP counter.
        atomic(saved, json.dumps(trial) + '\n')
        return
    if not saved.exists():
        return
    previous = json.loads(regular(saved))
    saved.unlink()
    if (isinstance(previous, dict)
            and re.fullmatch(r'[0-9a-f]{32}', previous.get('emaki_candidate', ''))
            and previous.get('emaki_trial') in ('0', '1')
            and trial.get('emaki_candidate') == previous['emaki_candidate']
            and trial.get('emaki_trial') in ('1', '2')
            and int(trial['emaki_trial']) > int(previous['emaki_trial'])):
        atomic(Path('/efi/EFI/Emaki/trial.env'), trial_block(previous))
        print('Hibernation resumed; the boot loader trial attempt was restored.')


def failed_trial(state):
    trial = trial_state()
    return (state['newest'] != state['good'] and
            (trial.get('emaki_candidate') != state['newest'] or
             trial.get('emaki_trial') not in ('0', '1')))


def input_signature(defaults):
    """Only inputs embedded in the EFI loader can authorize another trial."""
    # Menu policy, kernel arguments and initramfs contents are read from the
    # current root. The image embeds disk identity separately in its manifest.
    embedded = {key: value for key, value in assignments(defaults).items()
                if key in ('GRUB_ENABLE_CRYPTODISK', 'GRUB_PRELOAD_MODULES')}
    value = hashlib.sha256(json.dumps(embedded, sort_keys=True).encode())
    paths = [*MODULES.rglob('*'), *Path('/usr/share/emaki/grub').rglob('*')]
    paths += [p for p in Path('/usr/lib/emaki/boot').rglob('*')
              if '__pycache__' not in p.parts and p.suffix != '.pyc']
    paths += [Path('/usr/bin/grub-mkimage'), Path('/usr/bin/grub-install'),
              Path('/usr/bin/emaki-boot-refresh')]
    if signing():
        # A shim package update, a new machine key or new SBAT data needs a new loader.
        paths += [*Path(secureboot.SHIM_DIR).rglob('*'), Path(secureboot.DER),
                  Path(secureboot.SBAT_SOURCE)]
    for path in sorted(paths):
        if path.is_file() and not path.is_symlink() and path.name != 'defaults.cfg':
            value.update(str(path).encode() + b'\0' + regular(path))
    return value.hexdigest()


def manifest(state):
    path = Path('/boot/emaki') / str(state['newest']) / 'manifest.json'
    return json.loads(regular(path)) if path.exists() else {}


def live_kernels():
    kernels = tuple(kernel for kernel in KERNELS if (Path('/boot') / ('vmlinuz-' + kernel)).exists())
    require(kernels, 'No supported live kernel is installed.')
    for kernel in kernels:
        for name in ('vmlinuz-' + kernel, 'initramfs-' + kernel + '.img'):
            require(len(regular(Path('/boot') / name)) > MIB, f'/boot/{name} is incomplete.')
    return kernels


def snapshot_inventory():
    """List numbered snapshots without reading metadata or walking their contents."""
    return {int(entry.name) for entry in Path('/.snapshots').iterdir()
            if entry.name.isdecimal() and not entry.is_symlink() and entry.is_dir()
            and (entry / 'snapshot').is_dir()}


def snapshot_menu_needs_repair():
    if (not Path('/etc/grub.d/41_snapshots-btrfs').is_file()
            or not Path('/.snapshots').is_dir()):
        return False
    menu = Path('/boot/grub/grub-btrfs.cfg')
    require(not menu.is_symlink(), 'The snapshot menu must not be a symbolic link.')
    if not menu.exists():
        return True
    content = regular(menu)
    if not content.strip():
        return True
    present = {int(number) for number in re.findall(rb'/@snapshots/([0-9]+)/snapshot(?:/|[\s\"\'])', content)}
    # Upstream can omit existing snapshots for kernel availability or settings.
    # Only references to deleted snapshots require boot-time regeneration.
    return not present.issubset(snapshot_inventory())


def repair_snapshot_menu(identity):
    """Use the normal staged publication path when boot finds a lost or stale menu."""
    snapshots = identity['fstype'] == 'btrfs' and snapshot_menu_needs_repair()
    if not snapshots and not menu_font_needs_repair():
        return
    stage = Path('/boot/grub/.emaki-snapshots')
    require(not stage.parent.is_symlink() and not stage.is_symlink(),
            'The snapshot staging path must be a real directory.')
    stage.mkdir(parents=True, exist_ok=True)
    sync_directory(stage.parent)
    with pause_snapshots():
        recover(identity)
        state_path = Path('/efi/EFI/Emaki/boot-state.json')
        if state_path.exists() or state_path.is_symlink():
            state = read_state(Path('/efi/EFI/Emaki/grubx64.efi'), fallback_loader())
        else:
            # A fresh alongside install can retain a foreign fallback forever.
            # Menu-only repair needs no loader ownership or generation state.
            state = {'good': None, 'newest': None}
        refresh_menu(identity, state, snapshots_required=snapshots)
    print('The snapshot boot menu was repaired.' if snapshots else 'The boot menu lettering was resized.')


def refresh_menu(identity, state, recovery=False, snapshots_required=False):
    """Generate against the current root, preserving the loader and trial budget."""
    require(not Path('/boot/grub').is_symlink(), 'A customized /boot/grub symbolic link needs manual review.')
    kernels = live_kernels()
    tag = uuid.uuid4().hex
    stage = Path('/boot/emaki') / tag
    defaults = migrate_defaults(regular(Path('/etc/default/grub')).decode(),
                                regular(Path('/usr/share/emaki/grub/defaults.cfg')).decode())
    room(Path('/boot'), tree_size(Path('/boot/grub')) * 2, 64 * MIB)
    transaction = Transaction(tag, None if recovery else Path('/efi/EFI/Emaki/boot-intent.json'))
    try:
        shutil.copytree(Path('/boot/grub'), stage / 'grub', symlinks=True,
                        ignore=shutil.ignore_patterns('*.emaki-*'))
        if snapshots_required:
            (stage / 'grub/grub-btrfs.cfg').unlink(missing_ok=True)
        write(stage / 'defaults', defaults)
        btrfs_config = Path('/etc/default/grub-btrfs/config')
        if btrfs_config.is_file():
            write(stage / 'btrfs-config', boot.grub_btrfs_config(regular(btrfs_config).decode()))
        run(['unshare', '--mount', '--propagation', 'private', '/usr/bin/emaki-boot-refresh',
             '--generate', str(stage)])
        generation = Path('/boot/emaki') / str(state['newest'])
        if not (generation / 'manifest.json').exists():
            generation = None
        text = rewrite_menu(regular(stage / 'grub/grub.cfg').decode(), generation, identity['fsroot'])
        if generation and state['newest'] != state['good'] and not failed_trial(state):
            text = trial_menu(state['newest'], identity['esp_uuid'], text)
        if identity['fstype'] == 'btrfs':
            text = update_menu.wrap_menu(text, identity['esp_uuid'])
        verify_menu(text, identity, generation, kernels)
        atomic(stage / 'grub/grub.cfg', text)
        run(['grub-script-check', stage / 'grub/grub.cfg'])
        snapshots = stage / 'grub/grub-btrfs.cfg'
        if snapshots_required and not snapshots.exists():
            require(not snapshot_inventory(), 'The snapshot generator did not produce a menu.')
            # Upstream exits before its checker when no snapshots remain.
            write(snapshots, '# No snapshots available.\n')
        if snapshots.exists():
            run(['grub-script-check', snapshots])
            transaction.add(Path('/boot/grub/grub-btrfs.cfg'), regular(snapshots))
        transaction.add(Path('/etc/default/grub'), defaults)
        if btrfs_config.is_file():
            transaction.add(btrfs_config, regular(stage / 'btrfs-config'))
        transaction.add(Path('/boot/grub/grub.cfg'), text)
        # Broken counters are exhausted once, so GRUB no longer reads them.
        if not signing() and failed_trial(state):
            block = trial_block({'emaki_candidate': state['newest'], 'emaki_trial': '2'})
            if not Path('/efi/EFI/Emaki/trial.env').exists() or regular(Path('/efi/EFI/Emaki/trial.env')) != block:
                transaction.add(Path('/efi/EFI/Emaki/trial.env'), block)
        transaction.publish()
    finally:
        transaction.cleanup()
        if stage.exists():
            shutil.rmtree(stage)


def verify_menu(text, identity, generation, kernels=KERNELS):
    fsroot = identity['fsroot']
    for kernel in kernels:
        path = fsroot + '/boot/vmlinuz-' + kernel
        lines = [line for line in text.splitlines()
                 if re.match(r'\s*linux(?:efi)?\s+' + re.escape(path) + r'\s', line)]
        require(lines, f'The staged menu has no live {kernel} entry.')
        for line in lines:
            require('root=UUID=' + identity['uuid'] in line,
                    'The staged menu has an unexpected root filesystem.')
            require('emaki.generation=${emaki_generation}' in line,
                    'The staged menu lost its boot generation argument.')
            if identity['luks']:
                require('cryptdevice=UUID=' + identity['luks'] + ':emaki-root' in line,
                        'The staged menu lost its encrypted root argument.')
        require(re.search(r'^\s*initrd(?:efi)?\s+.*(?<!\S)'
                          + re.escape(fsroot + '/boot/initramfs-' + kernel + '.img')
                          + r'(?:\s|$)', text, re.M),
                f'The staged menu lost its live {kernel} initramfs.')
    require(not re.search(r'/boot/emaki/[^\s]+/(?:vmlinuz|initramfs)-', text),
            'The staged menu refers to frozen kernel files.')


class Transaction:
    """Persist ownership of every old/new combination before replacing a file."""
    def __init__(self, tag, intent=None):
        self.tag = tag
        self.intent = intent
        self.entries = []
        self.changed = []
        self.complete = False

    def add(self, target, data):
        old = regular(target) if target.exists() else None
        new = target.with_name(target.name + '.emaki-new-' + self.tag)
        saved = target.with_name(target.name + '.emaki-old-' + self.tag)
        self.entries.append((target, new, saved, old))
        write(new, data)
        if old is not None:
            write(saved, old)

    def publish(self):
        if self.intent is not None:
            require(not self.intent.exists(), 'An unfinished boot update needs recovery first.')
            record = {'tag': self.tag, 'entries': []}
            for target, new, _, old in self.entries:
                data = regular(new)
                entry = {'target': str(target), 'old': digest(old) if old is not None else None,
                         'new': digest(data)}
                # Small ESP metadata must survive a lost FAT directory entry too.
                if target.is_relative_to(Path('/efi')) and target.suffix.lower() != '.efi':
                    entry.update(old_data=old.hex() if old is not None else None, new_data=data.hex())
                record['entries'].append(entry)
            atomic(self.intent, json.dumps(record) + '\n')
        try:
            for target, new, saved, old in self.entries:
                expected = regular(new)
                require((regular(target) if target.exists() else None) == old,
                        f'{target} changed during preparation; refresh stopped.')
                os.replace(new, target)
                self.changed.append((target, saved, old))
                sync_directory(target.parent)
                require(durable_read(target) == expected,
                        f'{target} could not be read back after replacement.')
                sync_grub_menu(target)
            self.complete = True
            self.forget()
        except BaseException:
            if self.intent is not None:
                # A second interruption during rollback must not attempt forward
                # recovery with a replacement already restored to its old bytes.
                record['rollback'] = True
                atomic(self.intent, json.dumps(record) + '\n')
            errors = []
            for target, saved, old in reversed(self.changed):
                try:
                    if old is None:
                        esp_writable(target)
                        target.unlink()
                    else:
                        atomic(target, regular(saved))
                    sync_directory(target.parent)
                except (OSError, Refuse) as error:
                    errors.append(str(error))
            if errors:
                raise Refuse('Rollback could not restore every path; both boot generations were retained. '
                             + '; '.join(errors))
            self.forget()
            self.complete = True
            raise

    def forget(self):
        if self.intent is not None:
            if self.intent.exists():
                esp_writable(self.intent)
            self.intent.unlink(missing_ok=True)
            sync_directory(self.intent.parent)

    def cleanup(self):
        if self.intent is not None and self.intent.exists():
            return
        for target, new, saved, _ in self.entries:
            discard_sidecars(target, [new, saved] if self.complete or self.intent is not None else [new])


def intent_record(data):
    """The parsed intent, or None when it is empty, cut short or not in its shape."""
    try:
        record = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    def digest_or_none(value):
        return value is None or isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)

    def hex_or_none(value):
        return value is None or isinstance(value, str) and re.fullmatch(r'(?:[0-9a-f]{2})*', value)

    # Every field recovery reads is checked here, so a wrongly shaped record takes the
    # conservative path (recover_without_intent) instead of failing on a missing key.
    if (isinstance(record, dict) and isinstance(record.get('tag'), str)
            and re.fullmatch(r'[0-9a-f]{32}', record['tag'])
            and isinstance(record.get('rollback', False), bool)
            and isinstance(record.get('entries'), list) and record['entries']
            and all(isinstance(entry, dict) and isinstance(entry.get('target'), str)
                    and entry['target'].startswith('/') and '\0' not in entry['target']
                    and 'old' in entry and 'new' in entry
                    and digest_or_none(entry['old']) and digest_or_none(entry['new'])
                    and hex_or_none(entry.get('old_data')) and hex_or_none(entry.get('new_data'))
                    for entry in record['entries'])):
        return record
    return None


def recover_without_intent(identity, intent):
    """Finish or undo a transaction whose intent a cut left empty or cut short.

    FAT renames are not atomic: a power cut around the intent's rename leaves boot-intent.json
    with no bytes (S9V F-E1). The transaction's files say what happened: Transaction.add wrote
    every new file (.emaki-new-<tag>) and a copy of every old one (.emaki-old-<tag>), all made
    durable, before the intent; publish renames the new files only after the intent is durable.
    With every new file still there nothing was replaced, so the old state stands and the
    sidecars go. With some renamed, every remaining new file is completed, unless the new boot
    state names a generation whose files are gone, then the old copies come back. The shim
    record goes too (the pair may have changed after this boot started), and the menus are
    rebuilt when the transaction held them.
    """
    groups = {}
    for path in Path('/efi').rglob('*.emaki-*'):
        match = re.fullmatch(r'(.+)\.emaki-(new|old)-([0-9a-f]{32})', path.name)
        if match and path.is_file() and not path.is_symlink():
            groups.setdefault(match[3], {}).setdefault(path.with_name(match[1]), {})[match[2]] = path
    outcome = []
    state_path = Path('/efi/EFI/Emaki/boot-state.json')
    for tag, targets in sorted(groups.items()):
        forward = any('new' not in sidecars for sidecars in targets.values())
        if forward and state_path in targets:
            source = targets[state_path].get('new', state_path)
            try:
                state = json.loads(regular(source)) if source.exists() else {}
            except (ValueError, UnicodeError):
                state = {}
            state = state if isinstance(state, dict) else {}
            if state.get('newest') != state.get('good') and not (
                    Path('/boot/emaki') / str(state.get('newest')) / 'manifest.json').is_file():
                forward = False
        for target, sidecars in sorted(targets.items()):
            if forward and 'new' in sidecars:
                atomic(target, regular(sidecars['new']))
            elif not forward and 'old' in sidecars and (
                    not target.exists() or regular(target) != regular(sidecars['old'])):
                atomic(target, regular(sidecars['old']))
        # Files this transaction created have no old copy: its trial loader, and the trial and
        # boot state when there were none before, which name its tag.
        created = [Path('/efi/EFI/Emaki') / ('loader-' + tag + '.efi')]
        created += [path for path in (Path('/efi/EFI/Emaki/trial.env'), state_path)
                    if path not in targets and path.exists() and tag.encode() in regular(path)]
        for path in created if not forward else []:
            if path not in targets and path.exists():
                esp_writable(path)
                path.unlink()
                sync_directory(path.parent)
        for target, sidecars in targets.items():
            discard_sidecars(target, list(sidecars.values()))
        outcome.append('finished' if forward else 'undone')
    # A menu transaction keeps all its files on the root; its menus are rebuilt either way.
    menus = any(re.fullmatch(r'.+\.emaki-(?:new|old)-[0-9a-f]{32}', path.name)
                for path in Path('/boot/grub').glob('*.emaki-*'))
    strays =[path for path in intent.parent.glob(intent.name + '.emaki-*')
              if path.is_file() and not path.is_symlink()]
    esp_writable(intent)
    intent.unlink()
    sync_directory(intent.parent)
    discard_sidecars(intent, strays)
    if menus:
        state = boot_state()
        refresh_menu(identity, state, recovery=True)
    message = (TORN_FINISHED if 'finished' in outcome else TORN_UNDONE)
    print(message)
    if LOG is not None:
        LOG.write(message + '\n')


def recover(identity):
    """Recover ESP publication independently of a restored or regenerated root."""
    intent = Path('/efi/EFI/Emaki/boot-intent.json')
    if not intent.exists():
        return
    record = intent_record(regular(intent))
    if record is None:
        # A cut while the intent was written: the replacement's own sidecar may still hold it.
        copies = [intent_record(regular(path)) for path in intent.parent.glob(intent.name + '.emaki-*')
                  if re.fullmatch(re.escape(intent.name) + r'\.emaki-[0-9a-f]{32}', path.name)
                  and path.is_file() and not path.is_symlink()]
        copies = [copy for copy in copies if copy is not None]
        if len(copies) == 1:
            atomic(intent, json.dumps(copies[0]) + '\n')
            return recover(identity)
        recover_without_intent(identity, intent)
        return
    tag = record['tag']
    pending = []
    copies = {}
    for entry in record['entries']:
        target = Path(entry['target'])
        new = target.with_name(target.name + '.emaki-new-' + tag)
        saved = target.with_name(target.name + '.emaki-old-' + tag)
        loader = target.is_relative_to(Path('/efi')) and target.suffix.lower() == '.efi'
        esp = target.is_relative_to(Path('/efi'))
        actual = digest(durable_read(target)) if esp and target.exists() else None
        if esp:
            for source in (target, new, saved):
                if source.exists():
                    data = durable_read(source)
                    copies[digest(data)] = data
            for direction in ('old', 'new'):
                if not loader and entry.get(direction + '_data') is not None:
                    data = bytes.fromhex(entry[direction + '_data'])
                    require(digest(data) == entry[direction], 'The unfinished boot metadata is invalid.')
                    copies[entry[direction]] = data
        pending.append((entry, target, new, saved, actual, loader))
    # A surviving firmware copy can replace a lost FAT rename sidecar during
    # promotion. Its bytes must match the recorded hash, just like any sidecar.
    for source in (Path('/efi/EFI/Emaki/grubx64.efi'), *filter(None, [fallback_loader()]),
                   *Path('/efi/EFI/Emaki').glob('loader-*.efi')):
        if source.exists():
            data = durable_read(source)
            copies[digest(data)] = data
    rollback = bool(record.get('rollback'))
    for entry, target, _, _, _, loader in pending:
        if (loader or target.is_relative_to(Path('/efi'))) and entry['new'] not in copies:
            rollback = True
    # Without its root generation a pending candidate cannot be confirmed.
    # Roll back its publication; the next refresh builds for the restored root.
    state_entry = next((entry for entry, target, *_ in pending
                        if target == Path('/efi/EFI/Emaki/boot-state.json')), None)
    if state_entry and state_entry['new'] in copies:
        state = json.loads(copies[state_entry['new']])
        if state.get('newest') != state.get('good') and not (
                Path('/boot/emaki') / str(state.get('newest')) / 'manifest.json').is_file():
            rollback = True
    if rollback and not record.get('rollback'):
        record['rollback'] = True
        atomic(intent, json.dumps(record) + '\n')
    direction = 'old' if rollback else 'new'
    # Check that every required ESP copy is recoverable before changing one.
    for entry, target, _, _, _, _ in pending:
        if target.is_relative_to(Path('/efi')):
            require(entry[direction] is None or entry[direction] in copies,
                    f'{target} has no verified replacement for the unfinished boot update.')
    for entry, target, _, _, actual, _ in pending:
        if not target.is_relative_to(Path('/efi')):
            # Root snapshots and generators own these files. Preserve restored
            # settings; regenerate menus below instead of replaying stale bytes.
            continue
        wanted = entry[direction]
        if actual != wanted:
            if wanted is None:
                esp_writable(target)
                target.unlink(missing_ok=True)
                sync_directory(target.parent)
            else:
                atomic(target, copies[wanted])
                require(digest(durable_read(target)) == wanted,
                        f'{target} could not be read back after replacement.')
    state_path = Path('/efi/EFI/Emaki/boot-state.json')
    state = boot_state()
    if any(target.is_relative_to(Path('/boot')) for _, target, *_ in pending):
        refresh_menu(identity, state, recovery=True)
    esp_writable(intent)
    intent.unlink()
    sync_directory(intent.parent)
    for _, target, new, saved, _, _ in pending:
        discard_sidecars(target, [new, saved])


@contextmanager
def pause_snapshots():
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'grub-btrfsd.service'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if active:
        run(['systemctl', 'stop', 'grub-btrfsd.service'])
    try:
        yield
    finally:
        if active:
            try:
                run(['systemctl', 'start', 'grub-btrfsd.service'])
            except Refuse as error:
                if LOG is not None:
                    LOG.write(f'Snapshot menu service restart failed: {error}\n')
                raise


def tree_size(path):
    """Include nested generations and assets; never follow directory symlinks."""
    return sum(p.stat().st_size for p in path.rglob('*')
               if p.is_file() and not p.is_symlink())


def read_state(vendor, fallback):
    """Generation state; a shared ESP (fallback None) records no fallback loader."""
    path = Path('/efi/EFI/Emaki/boot-state.json')
    if not path.exists():
        if fallback is None:
            return {'good': None, 'newest': None, 'fallback_sha256': None}
        if regular(fallback) != regular(vendor):
            raise Kept(FOREIGN)
        return {'good': None, 'newest': None, 'fallback_sha256': digest(regular(fallback))}
    state = json.loads(regular(path))
    require(isinstance(state, dict) and {'good', 'newest'} <= state.keys() and all(
        state.get(key) is None or re.fullmatch(r'[0-9a-f]{32}', str(state[key]))
        for key in ('good', 'newest')) and
        (state.get('fallback_sha256', '') is None if fallback is None else
         re.fullmatch(r'[0-9a-f]{64}', str(state.get('fallback_sha256', '')))),
        'The boot generation state is invalid; nothing was replaced.')
    if fallback is None:
        return state
    actual = digest(regular(fallback))
    if actual != state['fallback_sha256']:
        # Power loss after fallback promotion but before its state rename.
        newest = Path('/boot/emaki') / str(state['newest']) / 'manifest.json'
        record = json.loads(regular(newest)) if newest.exists() else {}
        if actual != record.get('efi_sha256'):
            raise Kept(FOREIGN)
        state.update(good=state['newest'], fallback_sha256=actual)
    return state


def prune(state):
    """Keep the good and pending generations even when the latest refresh failed."""
    if Path('/efi/EFI/Emaki/boot-intent.json').exists():
        return
    keep = {state['good'], state['newest']}
    if signing():
        # Both slots' generations stay while either slot can start them.
        keep |= slot_generations()
    parent = Path('/boot/emaki')
    for directory in parent.glob('*'):
        if re.fullmatch(r'[0-9a-f]{32}', directory.name) and directory.name not in keep:
            require(directory.is_dir() and not directory.is_symlink(),
                    'A boot generation is not a regular directory; cleanup stopped.')
            shutil.rmtree(directory)
    if parent.exists():
        sync_directory(parent)
    for image in Path('/efi/EFI/Emaki').glob('loader-*.efi'):
        match = re.fullmatch(r'loader-([0-9a-f]{32})\.efi', image.name)
        if match and match[1] not in keep and not image.is_symlink():
            esp_writable(image)
            image.unlink()
    for target in (Path('/efi/EFI/Emaki/grubx64.efi'), *filter(None, [fallback_loader()]),
                   Path('/efi/EFI/Emaki/boot-state.json'), Path('/etc/default/grub'),
                   Path('/etc/mkinitcpio.conf'), Path('/etc/default/grub-btrfs/config'),
                   Path('/boot/grub/grub.cfg'), Path('/boot/grub/grub-btrfs.cfg'),
                   Path('/efi/EFI/Emaki/trial.env'), Path('/efi/EFI/Emaki/boot-intent.json'),
                   *(p.with_name(p.name.split('.emaki-')[0])
                     for p in Path('/efi/EFI/Emaki').glob('loader-*.efi*'))):
        sidecars = []
        for sidecar in target.parent.glob(target.name + '.emaki-*'):
            if re.fullmatch(re.escape(target.name) + r'\.emaki-(?:(?:old|new)-)?[0-9a-f]{32}',
                            sidecar.name) and sidecar.is_file() and not sidecar.is_symlink():
                sidecars.append(sidecar)
        discard_sidecars(target, sidecars)
        if target.parent.exists():
            sync_directory(target.parent)
    for directory in stage_parent().glob('.emaki-stage-*'):
        if re.fullmatch(r'\.emaki-stage-[0-9a-f]{32}', directory.name) and not directory.is_symlink():
            esp_writable(directory)
            shutil.rmtree(directory)
    if Path('/efi').exists():
        sync_directory(Path('/efi'))


def cleanup():
    if Path('/efi/EFI/Emaki/boot-intent.json').exists():
        return
    path = Path('/efi/EFI/Emaki/boot-state.json')
    state = boot_state()
    require(isinstance(state, dict) and {'good', 'newest'} <= state.keys(),
            'The boot generation state is invalid; cleanup stopped.')
    prune(state)


def mark_good(identity):
    if signing():
        # Before cleanup prunes the generations whose images prove a record is Emaki's.
        tidy_fsck_records()
    recover(identity)
    cleanup()
    if signing():
        # Secure Boot loaders live in A/B slots that firmware entries switch; no file is
        # promoted over another (mark_good_slots).
        try:
            mark_good_slots(identity)
        finally:
            cleanup()
        return
    vendor, fallback = Path('/efi/EFI/Emaki/grubx64.efi'), fallback_loader()
    state = read_state(vendor, fallback)
    cmdline = regular(Path('/proc/cmdline')).decode()
    tags = re.findall(r'(?:^|\s)emaki\.generation=([0-9a-f]{32})(?=\s|$)', cmdline)
    require(len(tags) <= 1, 'The running boot generation is ambiguous.')
    tag = tags[0] if tags else None
    if tag != state['newest'] or tag is None:
        if state['newest'] != state['good']:
            trial = trial_state()
            evidence = re.search(r'(?:^|\s)emaki\.trial=(?:unwritable|unreadable)(?=\s|$)', cmdline)
            candidates = re.findall(r'(?:^|\s)emaki\.trial_candidate=([0-9a-f]{32})(?=\s|$)', cmdline)
            if (trial.get('emaki_trial') == '0' and evidence and candidates == [state['newest']]
                    or failed_trial(state)):
                exhausted = {'emaki_candidate': state['newest'], 'emaki_trial': '2'}
                if trial != exhausted:
                    atomic(Path('/efi/EFI/Emaki/trial.env'), trial_block(exhausted))
                    refresh_menu(identity, state)
        return
    generation = Path('/boot/emaki') / tag
    # A restored root may predate the loader. That boot does not certify a tree
    # which no longer exists, and never changes the ESP's last good fallback.
    if not (generation / 'manifest.json').exists():
        return
    record = json.loads(regular(generation / 'manifest.json'))
    require(all(record.get(key) == identity[key] for key in ('uuid', 'esp_uuid', 'fsroot', 'luks')),
            'The booted generation belongs to a different installed root.')
    require((Path('/usr/lib/modules') / os.uname().release).is_dir(),
            'The running kernel has no installed modules; fallback promotion was refused.')
    image = regular(generation / 'grub/x86_64-efi/emaki-early.efi')
    candidate = Path('/efi/EFI/Emaki') / ('loader-' + tag + '.efi')
    require(digest(image) == record['efi_sha256'] and regular(candidate) == image,
            'The trial loader changed since this boot; fallback promotion was refused.')
    require(state['newest'] == tag, 'The running loader is not the newest published generation.')
    transaction = Transaction(uuid.uuid4().hex, Path('/efi/EFI/Emaki/boot-intent.json'))
    # A shared ESP promotes only Emaki's own loader; Windows owns the removable path.
    targets = (vendor,) if fallback is None else (vendor, fallback)
    updated = regular(targets[-1]) != image
    try:
        for target in targets:
            if regular(target) != image:
                room(Path('/efi'), len(image) * 2, 32 * MIB)
                transaction.add(target, image)
        trial = trial_state()
        if trial.get('emaki_candidate') == tag and trial.get('emaki_trial') != '0':
            trial['emaki_trial'] = '0'
            transaction.add(Path('/efi/EFI/Emaki/trial.env'), trial_block(trial))
        new_state = {**state, 'good': tag, 'fallback_sha256': None if fallback is None else digest(image)}
        if new_state != json.loads(regular(Path('/efi/EFI/Emaki/boot-state.json'))):
            transaction.add(Path('/efi/EFI/Emaki/boot-state.json'), json.dumps(new_state) + '\n')
        if transaction.entries:
            transaction.publish()
        if updated:
            print('The fallback loader was updated after a completed boot.' if fallback is not None
                  else 'The Emaki boot loader was updated after a completed boot.')
    finally:
        transaction.cleanup()
        cleanup()


def refresh(identity):
    # Failed PostTransaction hooks do not stop later hooks in the transaction.
    pending = Path('/var/lib/emaki/migrations/initramfs-pending')
    require(not pending.exists() and not pending.is_symlink(),
            'Migrated initramfs images are still pending. Run '
            '/usr/share/libalpm/scripts/emaki-initramfs-refresh successfully '
            'before retrying emaki-boot-refresh.')
    if signing():
        tidy_fsck_records()
    recover(identity)
    cleanup()
    try:
        _refresh(identity)
    finally:
        cleanup()


def _refresh(identity):
    vendor = Path('/efi/EFI/Emaki/grubx64.efi')
    fallback = fallback_loader()
    slots = signing()
    if slots:
        # Secure Boot: the loaders live in A/B slots (publish_slot); the active slot's
        # files are the previous loader, and the slot rule replaces the 256 MiB floor.
        active, active_record = current_slots()
        active_dir = secureboot.slot_dir(Path('/efi'), active)
        previous = regular(active_dir / secureboot.GRUB)
        state = slot_state()
        if not (Path('/boot/emaki') / str(state['newest']) / 'manifest.json').is_file():
            state['newest'] = state['good'] = active_record['generation']
    else:
        previous = regular(vendor)
        require(previous[:2] == b'MZ', 'The Emaki EFI loader is not recognized.')
        state = read_state(vendor, fallback)
        esp_space = os.statvfs(Path('/efi'))
        if esp_space.f_blocks * esp_space.f_frsize < 256 * MIB:
            if fallback is None:
                # Windows' usual 100 MiB ESP: keep the installed loader, still follow
                # kernel and menu changes on the root filesystem (never the removable
                # path, which is Windows').
                refresh_menu(identity, state)
                print(SMALL_SHARED, flush=True)
                return
            raise Kept('The startup partition is smaller than 256 MiB; the existing boot loaders were kept.')
    defaults = migrate_defaults(regular(Path('/etc/default/grub')).decode(),
                                regular(Path('/usr/share/emaki/grub/defaults.cfg')).decode())
    signature = input_signature(defaults)
    record = manifest(state)
    unchanged = record.get('inputs_sha256') == signature and all(
        record.get(key) == identity[key] for key in ('uuid', 'esp_uuid', 'fsroot', 'luks'))
    if slots:
        # The newest generation's slot must still be complete; a slot a cut left incomplete
        # is rewritten. Its trial is pending while BootNext names its entry.
        holder = next((slot for slot in secureboot.SLOTS
                       if (secureboot.read_slot(Path('/efi'), slot) or {}).get('generation') == state['newest']),
                      None)
        unchanged = unchanged and holder is not None
        if unchanged:
            parsed = firmware()
            pending = parsed['next'] in secureboot.slot_entries(parsed, esp_partuuid())[holder]
            refresh_menu(identity, state)
            print(TRIAL_FAILED if holder != active and not pending else 'The boot menu was updated.')
            return
    elif unchanged:
        failed = failed_trial(state)
        refresh_menu(identity, state)
        print(TRIAL_FAILED if failed else 'The boot menu was updated.')
        return
    require(not Path('/boot/grub').is_symlink(), 'A customized /boot/grub symbolic link needs manual review.')
    kernels = live_kernels()
    signed = signing()
    # A signed image builds in every module the menus use: GRUB refuses insmod from disk.
    modules = secureboot.signed_image_modules(IMAGE_MODULES) if signed else IMAGE_MODULES
    missing = [name for name in modules if not (MODULES / (name + '.mod')).is_file()]
    require(not missing, 'Required GRUB modules are missing: ' + ', '.join(missing))
    if slots:
        # The same rule the installer admitted by, from the active slot's sizes, before the
        # grub-install stage takes its share of the ESP.
        check_slot_space({path.name: path.stat().st_size for path in
                          (active_dir / name for name in secureboot.slot_files_names())},
                         stage=len(previous))
    else:
        room(Path('/efi'), len(previous) * 3, 32 * MIB)
    room(Path('/boot'), tree_size(Path('/boot')) + tree_size(MODULES) * 3, 64 * MIB)
    tag = uuid.uuid4().hex
    generation = Path('/boot/emaki') / tag
    generation.mkdir(parents=True, mode=0o700)
    # This is only grub-install's disposable output, never a firmware boot path.
    # Keep its newly allocated directories on the root filesystem: a cut while FAT
    # creates them can leave invalid entries even after the next boot's fsck.
    # --force permits this non-FAT scratch directory; --no-nvram keeps it private.
    # The separately rebuilt and verified image is the only one we publish to EFI.
    esp_stage = generation / 'efi-stage'
    esp_stage.mkdir(mode=0o700)
    transaction = Transaction(tag, Path('/efi/EFI/Emaki/boot-intent.json'))
    try:
        write(generation / 'defaults', defaults)
        btrfs_config = Path('/etc/default/grub-btrfs/config')
        if btrfs_config.is_file():
            write(generation / 'btrfs-config', boot.grub_btrfs_config(regular(btrfs_config).decode()))
        command = ['grub-install', '--target=x86_64-efi', '--efi-directory=' + str(esp_stage),
                   '--boot-directory=' + str(generation), '--bootloader-id=Emaki', '--no-nvram', '--force']
        if identity['luks']:
            command += ['--modules=part_gpt cryptodisk luks2 argon2 gcry_rijndael gcry_sha256 pbkdf2']
        run(command)
        platform = generation / 'grub/x86_64-efi'
        verify_modules(platform)
        expected_path = identity['fsroot'] + str(generation / 'grub')
        if identity['luks']:
            load_cfg = regular(platform / 'load.cfg').decode()
            expected = '(cryptouuid/' + identity['luks'].replace('-', '') + ')' + expected_path
            require(expected.encode() + b'\0' in regular(esp_stage / 'EFI/Emaki/grubx64.efi'),
                    'grub-install did not use the expected encrypted root prefix.')
        else:
            # On one disk grub-install can omit load.cfg and use a partition prefix.
            # Build our published image from the UUID verified by discover(),
            # without disk hints; verify_image checks this exact payload below.
            load_cfg = plain_load_config(identity, generation)
        prefix, early, memdisk = loader_payload(
            generation / 'grub', generation, identity, load_cfg,
            regular(Path('/usr/share/emaki/grub/unlock-24.pf2')),
            secure_boot_fonts() if signed else None)
        write(platform / 'emaki-early.cfg', early)
        write(platform / 'emaki-early.tar', memdisk)
        image_path = platform / 'emaki-early.efi'
        # Uncompressed, so verify_image still finds the early config and memdisk.
        mkimage = ['grub-mkimage', '--directory=' + str(MODULES), '--format=x86_64-efi',
                   '--compression=none', '--memdisk=' + str(platform / 'emaki-early.tar'),
                   '--prefix=' + prefix, '--config=' + str(platform / 'emaki-early.cfg'),
                   '--output=' + str(image_path), *modules]
        if signed:
            build_signed_image(mkimage, platform / SBAT_DATA)
        else:
            run(mkimage)
        verify_modules(platform)
        image = regular(image_path)
        verify_image(image, prefix, early, memdisk, identity['luks'])
        # Copy local GRUB additions into the isolated prefix; never follow links.
        for name in ('custom.cfg', 'grubenv'):
            source = Path('/boot/grub') / name
            if source.exists():
                atomic(generation / 'grub' / name, regular(source))
        run(['unshare', '--mount', '--propagation', 'private', '/usr/bin/emaki-boot-refresh',
             '--generate', str(generation)])
        config = generation / 'grub/grub.cfg'
        text = rewrite_menu(regular(config).decode(), generation, identity['fsroot'])
        if not slots:
            # A Secure Boot slot is tried by firmware BootNext instead (publish_slot).
            text = trial_menu(tag, identity['esp_uuid'], text)
        if identity['fstype'] == 'btrfs':
            text = update_menu.wrap_menu(text, identity['esp_uuid'])
        verify_menu(text, identity, generation, kernels)
        atomic(config, text)
        run(['grub-script-check', config])
        snapshots = generation / 'grub/grub-btrfs.cfg'
        if snapshots.exists():
            run(['grub-script-check', snapshots])
        # Flush the entire generation, including grub-install's copies, before
        # any EFI rename. The two filesystems cannot share an atomic rename.
        run(['sync', '-f', generation])
        if slots:
            candidate = secureboot.slot_dir(Path('/efi'), secureboot.other_slot(active)) / secureboot.GRUB
        else:
            room(Path('/efi'), len(image) + len(previous), 32 * MIB)
            candidate = Path('/efi/EFI/Emaki') / ('loader-' + tag + '.efi')
            transaction.add(candidate, image)
            transaction.add(Path('/efi/EFI/Emaki/trial.env'),
                            trial_block({'emaki_candidate': tag, 'emaki_trial': '0'}))
        transaction.add(Path('/etc/default/grub'), defaults)
        if btrfs_config.is_file():
            transaction.add(btrfs_config, regular(generation / 'btrfs-config'))
        if snapshots.exists():
            transaction.add(Path('/boot/grub/grub-btrfs.cfg'), regular(snapshots))
        # For administration and grub-btrfsd, keep the conventional menu current.
        # Every loader reads this menu so both firmware paths can offer a trial.
        transaction.add(Path('/boot/grub/grub.cfg'), text)
        write(generation / 'manifest.json', json.dumps({
            **identity, 'generation': str(generation), 'prefix': prefix,
            'efi_sha256': digest(image), 'loaders': [str(candidate)], 'inputs_sha256': signature,
            'grub_version': run(['grub-install', '--version']),
        }, indent=2) + '\n')
        run(['sync', '-f', generation])
        if slots:
            # The slot first, complete and offered once by BootNext; then the root's menus.
            # A cut before the slot's manifest leaves an incomplete slot no entry starts.
            shutil.rmtree(esp_stage)
            publish_slot(tag, image, active)
            state['newest'] = tag
            atomic(Path('/efi/EFI/Emaki/boot-state.json'), json.dumps(state) + '\n')
        else:
            state['newest'] = tag
            transaction.add(Path('/efi/EFI/Emaki/boot-state.json'), json.dumps(state) + '\n')
        transaction.publish()
        transaction.cleanup()
        prune(state)
        print('The boot loader update is ready for the next start.')
    finally:
        transaction.cleanup()
        if esp_stage.exists():
            shutil.rmtree(esp_stage)


def verify_modules(platform):
    require({path.name for path in platform.glob('*.mod')} == {path.name for path in MODULES.glob('*.mod')},
            'The staged GRUB module set differs from its package.')
    for path in MODULES.glob('*.mod'):
        require(regular(platform / path.name) == regular(path),
                f'The staged GRUB module {path.name} differs from its package.')


# The repair from the installation USB (emaki-boot-repair runs emaki-boot-refresh
# --repair-slots inside the installed root): Emaki cannot start because both slots are
# damaged, its firmware entries are gone, or another system put itself first.
REPAIR_NOT_SIGNING = ('This Emaki does not sign its boot files with a Secure Boot machine key, so it '
                      'has no startup copies to repair. Nothing was changed.')
REPAIR_UNFINISHED = ('The installation of this Emaki did not finish, so its boot loader cannot be '
                     'repaired. Install Emaki again from the installation USB.')
REPAIR_NO_GENERATION = ('The startup files of this Emaki could not be rebuilt, so no '
                        'startup copy was written. Details are in /var/log/emaki-boot-refresh.log.')
REPAIR_NVRAM = ('The firmware did not keep the startup entries as they were written ({detail}). '
                'Choose Emaki in the firmware boot menu, or run the repair again.')
REPAIR_KEPT = 'Startup copy {slot} is complete and was kept.'
REPAIR_WRITTEN = 'Startup copy {slot} was rebuilt and checked.'
REPAIR_DONE = 'Emaki is the first startup entry again and starts startup copy {slot}.'
REPAIR_FAILED = 'The boot loader repair did not finish: {error}'


def repair_generations(identity):
    """Boot generations a repaired slot may start, the best first: boot-state.json's good and
    newest, then every other generation of this root, newest first, then the installer's
    first loader (None: /boot/grub). Each must belong to this installed root."""
    state = slot_state()
    parent = Path('/boot/emaki')
    found = sorted((path for path in parent.glob('*') if re.fullmatch(r'[0-9a-f]{32}', path.name)
                    and (path / 'manifest.json').is_file()),
                   key=lambda path: (path / 'manifest.json').stat().st_mtime, reverse=True)
    names = list(dict.fromkeys([name for name in (state['good'], state['newest']) if name]
                               + [path.name for path in found]))
    for name in names:
        generation = parent / name
        try:
            record = json.loads(regular(generation / 'manifest.json'))
        except (Refuse, ValueError, UnicodeError):
            continue
        if isinstance(record, dict) and all(record.get(key) == identity[key]
                                            for key in ('uuid', 'esp_uuid', 'fsroot', 'luks')):
            yield generation
    yield None


def repair_image(identity, generation):
    """The signed GRUB image of a boot generation (None: the installer's /boot/grub), rebuilt
    with this root's grub-mkimage and modules and signed with the machine key, as _refresh
    builds it. Its files go next to the generation's own as emaki-repair.*."""
    repair_root_files(identity, generation)
    grub = Path('/boot/grub') if generation is None else generation / 'grub'
    platform = grub / 'x86_64-efi'
    require(platform.is_dir() and not platform.is_symlink(), f'{platform} is missing.')
    verify_modules(platform)
    modules = secureboot.signed_image_modules(IMAGE_MODULES)
    missing = [name for name in modules if not (MODULES / (name + '.mod')).is_file()]
    require(not missing, 'Required GRUB modules are missing: ' + ', '.join(missing))
    if identity['luks']:
        load_cfg = regular(platform / 'load.cfg').decode()
    else:
        load_cfg = plain_load_config(identity, Path('/boot') if generation is None else generation)
    prefix, early, memdisk = loader_payload(grub, generation, identity, load_cfg,
                                            regular(Path('/usr/share/emaki/grub/unlock-24.pf2')),
                                            secure_boot_fonts())
    names = {suffix: platform / ('emaki-repair' + suffix) for suffix in ('.cfg', '.tar', '.efi', '-sbat.csv')}
    for path in names.values():
        path.unlink(missing_ok=True)
    write(names['.cfg'], early)
    write(names['.tar'], memdisk)
    build_signed_image(['grub-mkimage', '--directory=' + str(MODULES), '--format=x86_64-efi',
                        '--compression=none', '--memdisk=' + str(names['.tar']), '--prefix=' + prefix,
                        '--config=' + str(names['.cfg']), '--output=' + str(names['.efi']), *modules],
                       names['-sbat.csv'])
    image = regular(names['.efi'])
    verify_image(image, prefix, early, memdisk, identity['luks'])
    return image


def repair_entries(active, complete):
    """'Emaki' -> active first in BootOrder, 'Emaki (previous)' -> the other slot when it is
    complete (in BootOrder right after 'Emaki' when its entry stood there before, otherwise
    outside it), every other entry of a slot on this ESP deleted, and every foreign entry kept
    in BootOrder in its relative order. Entries are created with --create-only and no optional
    data (create_slot_entry). A BootNext naming an Emaki entry is cleared (another system's
    one-time start stays). NVRAM is read back at the end, BootNext included."""
    partuuid = esp_partuuid()
    require(partuuid is not None, 'The startup partition could not be identified.')
    other = secureboot.other_slot(active)
    parsed = firmware()
    before = secureboot.slot_entries(parsed, partuuid)
    previous_in_order = any(number in parsed['order'] for number in before[other])

    def usable(parsed, slot, label):
        # An inactive entry (efibootmgr -A) is skipped by firmware, and one with optional data
        # may start another file: it is replaced, not kept (labelled).
        return labelled(parsed, partuuid, slot, label)[:1]

    def kept(listing, number, slot, label):
        entry = listing['entries'].get(number)
        return (entry is not None and entry[0] and not listing['data'].get(number) and entry[1] == label
                and entry[2] == partuuid and secureboot.entry_slot(entry[3]) == slot)

    first = usable(parsed, active, secureboot.ACTIVE_LABEL)
    if not first:
        number, parsed = create_slot_entry(active, secureboot.ACTIVE_LABEL)
        first = [number]
    previous = []
    if other in complete:
        previous = usable(parsed, other, secureboot.PREVIOUS_LABEL)
        if not previous:
            number, parsed = create_slot_entry(other, secureboot.PREVIOUS_LABEL)
            previous = [number]
    owned = {number for numbers in secureboot.slot_entries(parsed, partuuid).values() for number in numbers}
    stale = sorted(owned - set(first) - set(previous))
    # A pending Emaki trial (BootNext naming any Emaki entry on this ESP, also "Emaki
    # (previous)") would start another slot than the one the repair selects: it is cleared
    # and read back at once (R9S10 #4). Another system's one-time start is kept.
    if parsed['next'] in owned:
        run(['efibootmgr', '--delete-bootnext'])
        if firmware()['next'] in owned:
            raise Refuse(REPAIR_NVRAM.format(detail='the next start still names an Emaki entry'))
    foreign = [number for number in parsed['order'] if number not in owned and number in parsed['entries']]
    wanted = first + (previous if previous_in_order else []) + foreign
    if wanted != parsed['order']:
        run(['efibootmgr', '--bootorder', ','.join(wanted)])
    for number in stale:
        run(['efibootmgr', '--bootnum', number, '--delete-bootnum'])
    after = firmware()
    problems = []
    if after['order'][:1] != first:
        problems.append('"Emaki" is not first in the startup order')
    if not kept(after, first[0], active, secureboot.ACTIVE_LABEL):
        problems.append('the "Emaki" entry changed')
    if previous and not kept(after, previous[0], other, secureboot.PREVIOUS_LABEL):
        problems.append('the "Emaki (previous)" entry changed')
    if {number for numbers in secureboot.slot_entries(after, partuuid).values() for number in numbers} \
            != set(first) | set(previous):
        problems.append('other Emaki entries remain')
    if [number for number in after['order'] if number not in first + previous] != foreign:
        problems.append('the other systems\' entries are not in their order')
    if after['next'] is not None and (after['next'] in first + previous
                                      or after['next'] in {number for numbers in
                                                           secureboot.slot_entries(after, partuuid).values()
                                                           for number in numbers}):
        problems.append('the next start names an Emaki entry')
    if problems:
        raise Refuse(REPAIR_NVRAM.format(detail='; '.join(problems)))


def repair_required_references(menu, pattern):
    """Keep required references separate from existence-guarded menu sources.

    Mask individual test/source arguments, not filenames: another unguarded use of
    the same path must still be required. Unknown conditions remain conservative.
    """
    required = set(re.findall(pattern, menu))
    try:
        tokens = list(boot.grub_words(menu))
    except ValueError:
        return required
    guards, words, optional = [], [], []
    controls = ('if', 'elif', 'then', 'else', 'fi')
    for token in [*tokens, (';', ';', len(menu), len(menu))]:
        kind, value, start, end = token
        if kind == 'word':
            if not words and value in controls:
                # Quoting or escaping turns a reserved word into a command name.
                if menu[start:end] != value:
                    return required
                if value in ('then', 'else', 'fi'):
                    if not guards:
                        return required
                    guard = guards[-1]
                    if value == 'then':
                        if guard['phase'] != 'condition':
                            return required
                        guard['phase'] = 'body'
                    elif value == 'else':
                        if guard['phase'] != 'body':
                            return required
                        guard['present'] = not guard['present']
                        guard['phase'] = 'else'
                    else:
                        if guard['phase'] == 'condition':
                            return required
                        guards.pop()
                    continue
            words.append(token)
            continue
        if not words:
            continue
        values = [word[1] for word in words]
        # Only a single command is understood between if/elif and then. Its
        # result is not the condition's result if another command follows it.
        if guards and guards[-1]['phase'] == 'condition':
            return required
        if values[0] in ('if', 'elif'):
            if kind != ';':
                return required
            if values[0] == 'elif' and (not guards or guards[-1]['phase'] != 'body'):
                return required
            condition = words[1:]
            negated = len(condition) > 1 and condition[1][1] == '!'
            if negated:
                condition = [condition[0], *condition[2:]]
            guard = {'path': None, 'present': False, 'phase': 'condition'}
            if (len(condition) == 4 and condition[0][1] == '['
                    and condition[1][1] in ('-e', '-f') and condition[3][1] == ']'):
                argument = condition[2]
                raw = menu[argument[2]:argument[3]]
                # Preserve variable spellings such as ($root) while removing quotes.
                try:
                    path = shlex.split(raw)[0]
                except (ValueError, IndexError):
                    return required
                guard.update(path=path, present=not negated)
                optional.append((argument[2], argument[3]))
            if values[0] == 'elif' and guards:
                guards[-1] = guard
            else:
                guards.append(guard)
        elif values[0] in (*boot.GRUB_MENU_READERS, '.') and len(words) == 2:
            argument = words[1]
            try:
                path = shlex.split(menu[argument[2]:argument[3]])[0]
            except (ValueError, IndexError):
                return required
            if any(guard['present'] and guard['path'] == path for guard in guards):
                optional.append((argument[2], argument[3]))
        words = []
    if guards:
        return required
    return {match[1] for match in re.finditer(pattern, menu)
            if not any(start <= match.start() and match.end() <= end for start, end in optional)}


def repair_root_files(identity, generation):
    """An ESP manifest proves its own files, not the root files its loader will read."""
    canonical = Path('/boot/grub/grub.cfg')
    path = canonical
    if generation is not None and not (canonical.exists() or canonical.is_symlink()):
        require(generation.is_dir() and not generation.is_symlink(),
                f'The boot generation {generation.name} is missing or is a symbolic link.')
        try:
            manifest = json.loads(regular(generation / 'manifest.json'))
        except (ValueError, UnicodeError) as error:
            raise Refuse(f'The manifest of boot generation {generation.name} is unreadable.') from error
        require(isinstance(manifest, dict) and all(manifest.get(key) == identity[key]
                for key in ('uuid', 'esp_uuid', 'fsroot', 'luks')),
                'The boot slot references a generation of a different installed root.')
        path = generation / 'grub/grub.cfg'
    live_kernels()
    try:
        menu = regular(path).decode()
    except UnicodeError as error:
        raise Refuse(f'The boot menu {path} is unreadable.') from error
    require(menu.strip(), f'The boot menu {path} is empty.')
    # The canonical menu wins when present; a generation's fallback menu may predate
    # an intentional kernel removal, or disappear after an older-root restore.
    # Include resources rewrite_menu moved under /boot/emaki, not only live kernels.
    pattern = (r'(?<![\w/.-])' + re.escape(identity['fsroot'] + '/boot/')
               + r'([\w.+/-]+)(?=[\s\'\";]|$)')
    required = repair_required_references(menu, pattern)
    for name in set(re.findall(pattern, menu)):
        parts = name.split('/')
        require(all(part not in ('', '.', '..') for part in parts),
                f'The boot menu references an invalid path: /boot/{name}.')
        target = Path('/boot')
        for part in parts:
            require(not target.is_symlink(), f'{target} is a symbolic link.')
            target /= part
        if name not in required and not target.exists() and not target.is_symlink():
            continue
        require(regular(target), f'/boot/{name} is empty.')


def repair_slots(identity):
    """Make Emaki start again from a complete slot: keep a complete slot, or rebuild slot a
    from the installed packages, the machine key and a generation of this root; then repair
    the firmware entries (repair_entries)."""
    require(signing(), REPAIR_NOT_SIGNING)
    partuuid = esp_partuuid()
    require(partuuid is not None, 'The startup partition could not be identified.')
    complete = []
    for slot in secureboot.SLOTS:
        record = secureboot.read_slot(Path('/efi'), slot)
        if record is None:
            continue
        tag = record['generation']
        try:
            repair_root_files(identity, Path('/boot/emaki') / tag if tag is not None else None)
        except Refuse as error:
            if LOG is not None:
                LOG.write(f'Boot slot {slot}: {error}\n')
        else:
            complete.append(slot)
    if complete:
        active = secureboot.active_slot(firmware(), partuuid)
        if active not in complete:
            good = slot_state()['good']
            active = next((slot for slot in complete
                           if good and secureboot.read_slot(Path('/efi'), slot)['generation'] == good),
                          complete[0])
        print(REPAIR_KEPT.format(slot=active))
    else:
        active = secureboot.SLOTS[0]
        for generation in repair_generations(identity):
            try:
                image = repair_image(identity, generation)
                break
            except Refuse as error:
                if LOG is not None:
                    LOG.write(f'{generation or "/boot/grub"}: {error}\n')
        else:
            if LOG is not None:
                LOG.write('No boot generation of this Emaki can be rebuilt with its GRUB package, '
                          'so no boot slot was written.\n')
            raise Refuse(REPAIR_NO_GENERATION)
        tag = generation.name if generation is not None else None
        payload = slot_payload(image)
        check_slot_space({name: len(data) for name, data in payload.items()})
        esp_writable(secureboot.slot_dir(Path('/efi'), active))
        withdraw_slot(active)
        try:
            secureboot.write_slot(Path('/efi'), active, payload, tag, check_slot_file)
        except secureboot.SecureBootError as error:
            raise Refuse(str(error)) from error
        if tag is not None:
            atomic(Path('/efi/EFI/Emaki/boot-state.json'),
                   json.dumps({'good': tag, 'newest': tag, 'fallback_sha256': None}) + '\n')
        print(REPAIR_WRITTEN.format(slot=active))
        complete = [active]
    repair_entries(active, complete)
    print(REPAIR_DONE.format(slot=active))


def repair_main():
    """emaki-boot-refresh --repair-slots: allowed in a chroot and on the installation USB."""
    global LOG
    try:
        require(os.geteuid() == 0, 'Run the boot loader repair as root.')
        require(not Path('/.emaki-install-incomplete').exists(), REPAIR_UNFINISHED)
        log_path = Path(LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        LOG = BoundedLog(log_path)
        identity = discover()
        require(identity is not None, 'The installed Emaki root could not be identified.')
        # The boot refresh's own check for copied disks (R9S10 #3): emaki-boot-repair refuses
        # them before mounting; this one sees the devices from inside the installed root too.
        check_refresh_storage(identity)
        with open_run_lock('w') as lock:
            wait_run_lock(lock)
            # No cleanup() here: with both slots damaged the state names no generation, and
            # pruning would remove the ones a repair can still rebuild from.
            with shared.session():
                repair_slots(identity)
        return 0
    except BlockingIOError:
        print(REPAIR_FAILED.format(error='another boot loader operation is running.'), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        if LOG is not None:
            LOG.write(str(error) + '\n')
        print(REPAIR_FAILED.format(error=error), file=sys.stderr)
        return 1
    finally:
        if LOG is not None:
            LOG.close()
            LOG = None


def main(argv=None):
    global LOG
    parser = argparse.ArgumentParser(description='Safely refresh the installed Emaki boot loader and menu.')
    parser.add_argument('--hook', action='store_true')
    parser.add_argument('--retry', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repair-snapshots', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--sleep-phase', choices=('pre', 'post'), help=argparse.SUPPRESS)
    parser.add_argument('--mark-good', action='store_true', help='promote the successfully booted loader')
    parser.add_argument('--check', action='store_true', help='check the installed disk identity without writing')
    parser.add_argument('--generate', help=argparse.SUPPRESS)
    parser.add_argument('--repair-slots', action='store_true',
                        help='rebuild a damaged Secure Boot boot slot and its firmware entries '
                             '(run by emaki-boot-repair from the installation USB)')
    args = parser.parse_args(argv)
    if args.repair_slots:
        # The one mode allowed in a chroot and on the installation USB: the installed system
        # cannot start, and emaki-boot-repair runs this inside its root.
        return repair_main()
    previous_sigterm = None
    try:
        if (Path('/.emaki-install-incomplete').exists() or Path('/run/archiso').exists()
                or subprocess.run(['systemd-detect-virt', '--chroot', '--quiet'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0):
            return 0
        if shared_esp() and args.sleep_phase:
            # The hibernate drop-in (emaki-update-boot hibernate-pre/post) does
            # this around the unmount; plain suspend never starts GRUB.
            return 0
        if args.repair_snapshots and not snapshot_menu_needs_repair() and not menu_font_needs_repair():
            return 0
        require(os.geteuid() == 0, 'Run emaki-boot-refresh as root on the installed system.')
        if args.sleep_phase:
            with sleep_run_lock(args.sleep_phase, Path(SLEEP_STATE)) as acquired:
                if acquired:
                    sleep_trial(args.sleep_phase)
            return 0
        if args.retry or args.mark_good or args.repair_snapshots:
            def interrupted(signum, frame):
                raise TimeoutError('The boot loader operation was interrupted.')
            previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
        log_path = Path(LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        LOG = BoundedLog(log_path)
        if args.generate:
            require(re.fullmatch(r'/boot/emaki/[0-9a-f]{32}', args.generate), 'Invalid staging path.')
            generate(args.generate)
            return 0
        if args.check:
            identity = discover()
            if identity is not None:
                print(json.dumps(identity, indent=2))
            return 0
        with open_run_lock('w') as lock:
            try:
                wait_run_lock(lock)
            except BlockingIOError:
                if args.retry or args.repair_snapshots:
                    LOG.write('Another boot loader operation is running; retry at the next boot.\n')
                    return 0
                raise
            # A shared ESP returns to read-only before the run lock is released.
            with shared.session():
                if args.repair_snapshots:
                    identity = discover()
                    if identity is not None:
                        remove_previous_boot_lock(Path('/var/lib/pacman/db.lck'), current_boot_id())
                        with pacman_lock():
                            repair_snapshot_menu(identity)
                    return 0
                if not args.mark_good:
                    return refresh_attempt(args.retry)
                identity = discover()
                if identity is None:
                    return 0
                with pause_snapshots():
                    mark_good(identity)
        return 0
    except shared.Stopped as error:
        if LOG is not None:
            LOG.write(str(error) + '\n')
        print(str(error), file=sys.stderr)
        return 1
    except shared.Refused as error:
        # A pre-write refusal can be checked again at a later start.
        if LOG is not None:
            LOG.write(str(error) + '\n')
        print(str(error), file=sys.stderr)
        return 0 if args.retry or args.mark_good or args.repair_snapshots else 1
    except Kept as error:
        if LOG is not None:
            LOG.write(str(error) + '\n')
        print(str(error), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        if args.sleep_phase:
            print(f'Sleep {args.sleep_phase} bookkeeping did not finish: {error}',
                  file=sys.stderr, flush=True)
        elif LOG is not None:
            LOG.write(str(error) + '\n')
            print(f'The boot loader update did not finish; details are in {LOG_PATH}.', file=sys.stderr)
        else:
            print('The boot loader update did not finish because its log could not be opened.', file=sys.stderr)
        return 1
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        if LOG is not None:
            LOG.close()
            LOG = None
