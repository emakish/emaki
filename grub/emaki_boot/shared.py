# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Change an EFI system partition shared with Windows only while that is safe.

A shared ESP stays mounted read-only. One boot operation may make it writable
after three checks: it is still the partition recorded at installation, Windows
on each possible Windows system disk is fully shut down (not hibernated by Fast Startup), and nothing is
written outside EFI/Emaki. A hibernated Windows keeps its own view of the FAT
and writes it back on resume, so a Linux allocation, rename or deletion made in
between could cross-link Windows' boot files with Emaki's.

GRUB's boot counters are not affected: save_env rewrites the sectors of an
existing, fixed-size file in place and never changes the FAT or a directory.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

MARKER = Path('/var/lib/emaki/alongside-boot.json')
MOUNT = Path('/efi')
CHECKS = Path('/run/emaki-shared-esp')
GUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
BASIC_DATA = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
NTFS_OPTIONS = 'ro,norecover,nodev,nosuid,noexec,show_sys_files'
FILE_LIMIT = 4096
BYTE_LIMIT = 1024 * 1024 * 1024
REMOUNT_TRIES = 10

HIBERNATED = ('Windows is hibernated or its shutdown state is unclear. '
              'Emaki cannot update the shared startup partition. In Windows, open Command Prompt '
              'as administrator, run powercfg /hibernate off, shut down Windows, and try again.')
ENCRYPTED = ('Emaki found a BitLocker volume and cannot check whether Windows '
             'is fully shut down. The shared startup partition cannot be updated.')
NO_WINDOWS = ('Emaki could not find a Windows system to check on the attached disks. '
              'The shared startup partition cannot be updated.')
UNCHECKED = ('Emaki could not complete the safety checks for the shared startup '
             'partition. The operation stopped. Details: ')
WRONG_ESP = ('The partition mounted at /efi does not match the shared startup '
             'partition recorded during installation. Mount the original partition at /efi.')
BAD_RECORD = ('The record of the startup partition shared with Windows is missing '
              'or damaged. Emaki cannot safely update that partition. Firmware entries can be repaired, '
              'but startup files cannot be rebuilt there without the installation record.')
OWN_ESP = ('Emaki has its own startup partition next to Windows and never writes the startup '
           'partition of Windows. Mount the Emaki startup partition (label EMAKI) at /efi.')
STOPPED = ('STOP: a previous write to the shared startup partition did not finish '
           'safely, or files outside EFI/Emaki changed. Further writes are blocked. Save '
           '/var/log/emaki-boot-refresh.log and /var/lib/emaki/shared-esp-stop.json.\n'
           'Restore shared startup files: stop updates and back up the startup partition. '
           'From recovery media, repair damaged FAT offline and restore files outside EFI/Emaki '
           'from the known-good backup matching the saved hashes. Leave EFI/Emaki intact. '
           'Verify Windows boots, turn off Windows hibernation/Fast Startup, shut Windows down '
           'fully, and return to Emaki. With the recorded partition at /efi, run as root:\n'
           'mount -o remount,ro /efi\nemaki-update-boot guard-alongside\n'
           'An unchanged baseline clears the stop automatically. If the baseline cannot be '
           'restored or the record is damaged or old, first repair Windows startup from trusted '
           'recovery media and independently verify the foreign files and both firmware entries '
           'against a trusted backup or the repaired Windows installation. Record the repair '
           'and both successful boots. Fully shut down Windows with hibernation off. Then, in '
           'Emaki, mount the recorded partition at /efi and run as root:\n'
           'mount -o remount,ro /efi\n'
           'mv -n -- /var/lib/emaki/shared-esp-stop.json /var/lib/emaki/shared-esp-stop.reviewed.json\n'
           'emaki-update-boot guard-alongside\n'
           'Use a new archive name if .reviewed.json exists. This adopts independently repaired '
           'files; it does not prove the old hashes were preserved. Never move the stop just '
           'to bypass a failure. A missing or damaged alongside-boot.json must be restored '
           'with the original startup partition identity.')

# Managed Windows layouts that cannot be checked through a plain NTFS mount.
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
MS_RESERVED = 'e3c9e316-0b5c-4db8-817d-f92df00215ae'
MS_RECOVERY = 'de94bba4-06d1-4d40-a16a-bfd50179d6ac'
MANAGED_WINDOWS = {
    'af9b60a0-1431-4f62-bc68-3311714a69ad',  # Logical Disk Manager data
    '5808c8aa-7e8f-42e0-85d2-e1e90434cfb3',  # Logical Disk Manager metadata
    'e75caf8f-f680-4cee-afa3-b001e56efc2d',  # Storage Spaces
}
LINUX_FILESYSTEMS = {'ext2', 'ext3', 'ext4', 'btrfs', 'xfs', 'f2fs', 'swap', 'crypto_luks'}

_session = {'depth': 0, 'baseline': None}


class Refused(RuntimeError):
    """A shared-ESP operation that cannot safely continue."""


class Stopped(Refused):
    """Persistent hard failure: never downgrade this to a skipped operation."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def run(argv):
    try:
        result = subprocess.run([str(item) for item in argv], capture_output=True, text=True,
                                timeout=120, check=False, env={'PATH': os.defpath, 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(UNCHECKED + f'{Path(str(argv[0])).name}: {error}') from error
    if result.returncode:
        raise Refused(UNCHECKED + f'{Path(str(argv[0])).name}: {result.stderr.strip()[-400:]}')
    return result.stdout


def record(path=None):
    """The installer's root-owned alongside record, validated.

    esp_partuuid is always the ESP that holds Windows Boot Manager. Since 0.9 an
    installation next to Windows has its own ESP: emaki_esp_partuuid names it and
    windows_esp_uuid is the FAT volume id of Windows' ESP. A record without
    emaki_esp_partuuid is the older layout, where Windows' ESP is also /efi.
    """
    try:
        fd = os.open(MARKER if path is None else path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise Refused(BAD_RECORD) from error
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        data = stream.read(4097)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and len(data) <= 4096, BAD_RECORD)
    try:
        value = json.loads(data)
    except ValueError as error:
        raise Refused(BAD_RECORD) from error
    require(isinstance(value, dict) and value.get('version') == 1
            and isinstance(value.get('esp_partuuid'), str)
            and re.fullmatch(GUID, value['esp_partuuid']), BAD_RECORD)
    result = {'esp_partuuid': value['esp_partuuid'].lower()}
    if 'emaki_esp_partuuid' in value:
        own, windows = value.get('emaki_esp_partuuid'), value.get('windows_esp_uuid')
        require(isinstance(own, str) and re.fullmatch(GUID, own)
                and own.lower() != result['esp_partuuid']
                and isinstance(windows, str)
                and re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', windows), BAD_RECORD)
        result.update(emaki_esp_partuuid=own.lower(), windows_esp_uuid=windows.upper())
    return result


def marker():
    """The installer's root-owned record of the shared partition's GPT identity."""
    # A record of Emaki's own ESP names no shared partition, so the write gate never
    # opens on Windows' ESP, whatever is mounted at /efi.
    value = record()
    require('emaki_esp_partuuid' not in value, OWN_ESP)
    return value['esp_partuuid']


def mounted_esp(partuuid):
    """The mounted /efi must be the recorded FAT partition; returns its disk."""
    try:
        mounts = json.loads(run(['findmnt', '--json', '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS',
                                 '--mountpoint', MOUNT])).get('filesystems', [])
    except Refused as error:
        raise Refused(WRONG_ESP) from error
    require(len(mounts) == 1 and mounts[0].get('fstype') == 'vfat'
            and isinstance(mounts[0].get('source'), str), WRONG_ESP)
    source = mounts[0]['source']
    devices = json.loads(run(['lsblk', '--json', '--nodeps', '--output', 'PATH,TYPE,PARTUUID,PKNAME',
                              source])).get('blockdevices', [])
    require(len(devices) == 1 and devices[0].get('type') == 'part'
            and str(devices[0].get('partuuid') or '').lower() == partuuid
            and re.fullmatch(r'[A-Za-z0-9_.-]+', str(devices[0].get('pkname') or '')), WRONG_ESP)
    return {'source': source, 'disk': '/dev/' + devices[0]['pkname'], 'partuuid': partuuid,
            'options': mounts[0].get('options', '').split(',')}


def named(directory, name):
    """FAT and NTFS names are case-insensitive; refuse ambiguous or linked names."""
    matches = [path for path in directory.iterdir() if path.name.casefold() == name.casefold()]
    if len(matches) != 1 or matches[0].is_symlink():
        return None
    return matches[0]


def windows_system(root):
    """Same rule as the installer: a Windows system hive marks a Windows volume."""
    path = root
    for component in ('Windows', 'System32', 'config', 'SYSTEM'):
        path = named(path, component) if path.is_dir() else None
        if path is None:
            return False
    return path.is_file() and path.stat().st_size > 0


def hibernated(root):
    """Same rule as the installer: no hiberfil.sys, or a 4 KiB header of zeros."""
    matches = [path for path in root.iterdir() if path.name.casefold() == 'hiberfil.sys']
    if not matches:
        return False
    require(len(matches) == 1 and not matches[0].is_symlink() and matches[0].is_file(),
            UNCHECKED + 'the Windows hibernation file is not a regular file.')
    with matches[0].open('rb') as stream:
        header = stream.read(4096)
    return len(header) != 4096 or any(header)


@contextmanager
def readable(part):
    """An existing mount, or a private read-only mount that is always removed."""
    mounted = [point for point in part.get('mountpoints') or [] if point]
    if mounted:
        require(Path(mounted[0]).is_dir(), UNCHECKED + 'the Windows mount is unavailable.')
        yield Path(mounted[0])
        return
    CHECKS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(CHECKS, 0o700)
    root = Path(tempfile.mkdtemp(prefix='windows-', dir=CHECKS))
    try:
        run(['ntfs-3g', '-o', NTFS_OPTIONS, part['path'], root])
        try:
            yield root
        finally:
            run(['umount', '--', root])
    finally:
        # Never remove recursively: a failed unmount leaves a real filesystem here.
        root.rmdir()


def windows_shut_down(disk):
    # This ESP may boot Windows on another disk with a GUID partition table. Probe possible
    # system volumes, not unrelated filesystems, installation media or zram.
    tree = json.loads(run(['lsblk', '--json', '--tree', '--bytes', '--output',
                           'PATH,TYPE,FSTYPE,PARTTYPE,MOUNTPOINTS'])).get('blockdevices', [])
    disks = [item for item in tree if item.get('type') == 'disk']
    require(sum(item.get('path') == disk for item in disks) == 1,
            UNCHECKED + f'{disk}: the disk of the startup partition is not listed once.')
    candidates = []
    for item in disks:
        for part in item.get('children') or []:
            device = str(part.get('path') or item.get('path'))
            kind = str(part.get('parttype') or '').lower()
            filesystem = str(part.get('fstype') or '').lower()
            require(kind not in MANAGED_WINDOWS,
                    UNCHECKED + f'{device}: managed Windows storage cannot be checked.')
            # A mapped Windows volume is not covered by a plain NTFS probe,
            # regardless of the enclosing partition's type or filesystem.
            pending = list(part.get('children') or [])
            while pending:
                child = pending.pop()
                require(str(child.get('fstype') or '').lower() not in {'ntfs', 'bitlocker'},
                        UNCHECKED + f'{device}: mapped Windows storage cannot be checked.')
                pending.extend(child.get('children') or [])
            if kind in {MS_RESERVED, MS_RECOVERY} and not filesystem:
                # Without a filesystem these hold neither Windows nor its hibernation file.
                continue
            if kind in {BASIC_DATA, MS_RECOVERY}:
                # FAT/exFAT basic-data volumes are data or OEM media, not a
                # Windows system. An unreadable type may hide a Windows volume.
                if filesystem in {'vfat', 'exfat', *LINUX_FILESYSTEMS}:
                    continue
                require(filesystem != 'bitlocker', ENCRYPTED + f' Device: {device}.')
                require(part.get('type') == 'part' and filesystem == 'ntfs'
                        and not part.get('children'),
                        UNCHECKED + f'{device}: the Windows volume cannot be inspected.')
                candidates.append(part)
    found = False
    for part in candidates:
        try:
            with readable(part) as root:
                if not windows_system(root):
                    continue
                found = True
                require(not hibernated(root), HIBERNATED)
        except (Refused, OSError) as error:
            message = str(error) if isinstance(error, Refused) else UNCHECKED + str(error)
            raise Refused(message + f" Device: {part['path']}.") from error
    require(found, NO_WINDOWS + f' Startup device: {disk}.')


def stop_record():
    return MARKER.with_name('shared-esp-stop.json')


def refuse_stopped(esp):
    """Recover an interrupted session only against its durable original baseline."""
    record = stop_record()
    if not os.path.lexists(record):
        if 'ro' not in esp['options']:
            remount('ro')
        return
    try:
        # Even an unreadable or foreign record must leave the verified ESP read-only.
        # A failed sync must not prevent attempting to close the write gate.
        try:
            run(['sync', '-f', MOUNT])
        finally:
            remount('ro')
        fd = os.open(record, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and not info.st_mode & 0o022, STOPPED)
            data = stream.read(4 * 1024 * 1024 + 1)
        require(len(data) <= 4 * 1024 * 1024, STOPPED)
        value = json.loads(data)
        require(isinstance(value, dict) and value.get('version') == 2
                and value.get('state') == 'write-in-progress'
                and value.get('esp_partuuid') == esp['partuuid']
                and isinstance(value.get('baseline'), dict), STOPPED)
        require(foreign_files(MOUNT) == value['baseline'], STOPPED)
        record.unlink()
        sync_record_directory()
    except Exception as error:
        raise Stopped(STOPPED) from error


def record_write(baseline, partuuid):
    """Publish the complete durable baseline immediately before the writable remount."""
    record = stop_record()
    fd, name = tempfile.mkstemp(prefix='.shared-esp-stop-', dir=record.parent)
    temporary = Path(name)
    published = False
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'version': 2, 'state': 'write-in-progress',
                       'esp_partuuid': partuuid, 'baseline': baseline}, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # No partial record, and never overwrite an unresolved earlier session.
        os.link(temporary, record)
        published = True
        sync_record_directory()
    except BaseException:
        # No ESP write was possible yet; failed persistence must not latch a stop.
        if published:
            record.unlink()
            sync_record_directory()
        raise
    finally:
        temporary.unlink(missing_ok=True)


def sync_record_directory():
    fd = os.open(stop_record().parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def foreign_files(root):
    """Every directory and file outside EFI/Emaki, with each file's SHA-256."""
    result, total = {}, 0
    for directory, directories, files in os.walk(root):
        relative = Path(directory).relative_to(root)
        if [part.casefold() for part in relative.parts] == ['efi']:
            directories[:] = [name for name in directories if name.casefold() != 'emaki']
        directories.sort()
        for name in directories:
            path = Path(directory) / name
            require(not path.is_symlink(), UNCHECKED + 'the startup partition contains a link.')
            result[str(relative / name) + '/'] = 'directory'
        for name in sorted(files):
            path = Path(directory) / name
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode), UNCHECKED + 'the startup partition contains a special file.')
            total += info.st_size
            require(len(result) < FILE_LIMIT and total <= BYTE_LIMIT,
                    UNCHECKED + 'the startup partition holds more files than Emaki can verify.')
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(block)
            result[str(relative / name)] = digest.hexdigest()
    return result


def check():
    """Check safety under the boot lock, recovering an unchanged prior write if needed."""
    esp = mounted_esp(marker())
    refuse_stopped(esp)
    windows_shut_down(esp['disk'])
    return esp


def remount(mode):
    for attempt in range(REMOUNT_TRIES):
        try:
            run(['mount', '-o', 'remount,' + mode, MOUNT])
            break
        except Refused:
            if attempt == REMOUNT_TRIES - 1:
                raise
            time.sleep(0.5)
    options = json.loads(run(['findmnt', '--json', '--output', 'OPTIONS', '--mountpoint', MOUNT]))
    require(mode in options['filesystems'][0]['options'].split(','),
            UNCHECKED + f'the startup partition did not become {mode}.')


@contextmanager
def session():
    """One boot operation. The partition is made writable only on its first write."""
    _session['depth'] += 1
    try:
        yield
    finally:
        _session['depth'] -= 1
        if _session['depth'] == 0 and _session['baseline'] is not None:
            close()


def open_writes():
    """Called before every write below /efi; checks and remounts once per session."""
    if _session['baseline'] is not None:
        return
    require(_session['depth'] > 0, UNCHECKED + 'a write was attempted outside a boot operation.')
    esp = check()
    baseline = foreign_files(MOUNT)
    record_write(baseline, esp['partuuid'])
    _session['baseline'] = baseline
    remount('rw')


def close():
    baseline, _session['baseline'] = _session['baseline'], None
    failure = None
    try:
        run(['sync', '-f', MOUNT])
        if foreign_files(MOUNT) != baseline:
            failure = Stopped(STOPPED)
    except Exception as error:
        failure = error
    finally:
        try:
            remount('ro')
        except Exception as error:
            failure = failure or error
    if failure is not None:
        raise Stopped(STOPPED) from failure
    # Only verified preservation and a verified read-only mount clear the stop.
    try:
        stop_record().unlink()
        sync_record_directory()
    except OSError as error:
        raise Stopped(STOPPED) from error
