# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""emaki-boot-repair: make an installed Emaki start again, from the installation USB.

For a Secure Boot install that cannot start: both boot slots damaged, its firmware entries
lost, or another system put itself first. This finds the installed root and its startup
partition, mounts them (the machine key's @secureboot subvolume read-only, in place: the key
is never copied), and runs the installed system's own `emaki-boot-refresh --repair-slots`
inside it through arch-chroot. That code builds the signed GRUB from the installed packages
and the key, writes a complete slot and repairs the firmware entries. Everything mounted here
is unmounted on every way out.
"""
from .i18n import _ as translate
from .i18n import _, N_
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LIVE = Path('/run/archiso')
# Mountpoints next to the installer's own target (/mnt/emaki-target), never inside /run, which
# arch-chroot binds into the root.
BASE = Path('/mnt')
# Paths inside the installed root.
KEY_STATE = 'var/lib/emaki/secureboot'
OS_RELEASE = 'usr/lib/emaki/os-release'
REFRESH = 'usr/lib/emaki/boot/emaki_boot/refresh.py'
# The installer's record of an install next to Windows: "esp_partuuid" is the ESP holding
# Windows Boot Manager; "emaki_esp_partuuid" (since 0.9.0) is Emaki's own ESP. Without it the
# install may be the old shared layout; it does not prove ownership of a writable ESP.
ALONGSIDE = 'var/lib/emaki/alongside-boot.json'
OWN_ESP = 'var/lib/emaki/boot-esp.json'
REPAIR = ['emaki-boot-refresh', '--repair-slots']
ROOT_SUBVOLUME = '@'
ROOT_FILESYSTEMS = ('btrfs', 'ext4')

NOT_ROOT = N_('Run the repair as root: sudo emaki-boot-repair')
NOT_LIVE = N_('Start the computer from the Emaki installation USB and run sudo emaki-boot-repair there.')
NONE_FOUND = N_("No installed Emaki was found on this computer's disks. Nothing was changed.")
LOCKED = (N_('No installed Emaki was found on the unlocked disks. If Emaki is on the encrypted disk {device}, unlock it with: sudo cryptsetup open {device} emaki-root and run sudo emaki-boot-repair again.'))
SEVERAL = (N_('More than one installed Emaki was found: {devices}. Name the one to repair, for example: sudo emaki-boot-repair --root {first}'))
NAMED_NOT_EMAKI = N_('{device} does not hold an installed Emaki. Nothing was changed.')
CLONED = (N_('More than one disk holds the same file system or encrypted disk ({devices}), as after copying a whole disk. Disconnect or change all but one of them, then run sudo emaki-boot-repair again. Nothing was changed.'))
NO_ESP = (N_('The startup partition of the installed Emaki ({source}) was not found. Name it, for example: sudo emaki-boot-repair --esp /dev/nvme0n1p5 (next to Windows, the partition labelled EMAKI)'))
ESP_NOT_FAT = N_('{device} is not a FAT startup partition. Nothing was changed.')
WINDOWS_ESP = (N_("{device} is Windows' startup partition. Emaki starts from its own startup partition "
               "and never changes Windows'. Nothing was changed."))
ESP_UNPROVEN = (N_("Emaki's startup partition could not be verified from its saved partition identity. Nothing was changed."))
TOO_OLD = (N_('The installed Emaki is too old for this repair. Nothing was changed.'))
KEY_NOT_READ_ONLY = N_('The Secure Boot key could not be mounted read-only, so nothing was changed.')
REPAIRING = N_('Repairing the installed Emaki on {root} with the startup partition {esp}.')
DONE = N_('The repair finished. Remove the installation USB and restart the computer.')
FAILED = N_('The repair did not finish; the messages above say why.')
UNMOUNT_FAILED = N_('Warning: {target} could not be unmounted: {error}')
PROBE_CLEANUP_FAILED = (N_('The disk inspection stopped because {target} could not be unmounted: {error}. No more disks were inspected.'))
UNSAFE_DIRECTORY = N_('The installed directory {path} is not a safe directory. The repair stopped.')


class RepairError(RuntimeError):
    pass


class ProbeCleanupError(RepairError):
    pass


def run(argv):
    """A command whose output is read; its failure stops the repair."""
    result = subprocess.run([str(item) for item in argv], text=True, capture_output=True,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise RepairError(_('{value1} failed: {value2}').format(value1=Path(str(argv[0])).name, value2=result.stderr.strip()[-1200:]))
    return result.stdout


def call(argv):
    """A command whose messages the person reads; its exit status. What this command printed
    goes out first: without a terminal (ssh without a tty, a log) Python buffers it, and the
    child's lines came before "Repairing the installed Emaki on …" (S9V H-6x)."""
    sys.stdout.flush()
    sys.stderr.flush()
    return subprocess.run([str(item) for item in argv]).returncode


def block_devices(run):
    """Every block device lsblk lists, children flattened."""
    listing = json.loads(run(['lsblk', '--json', '--paths', '--output',
                              'PATH,TYPE,FSTYPE,UUID,PARTUUID']) or '{}')
    found = []

    def walk(items, parent):
        for item in items or []:
            if isinstance(item, dict) and isinstance(item.get('path'), str):
                found.append({**{key: item.get(key) for key in ('path', 'type', 'fstype', 'uuid', 'partuuid')},
                              'parent': parent, 'opened': bool(item.get('children'))})
                walk(item.get('children'), item['path'])
    walk(listing.get('blockdevices'), None)
    return found


def fstab_rows(text):
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and not fields[0].startswith('#'):
            rows.append({'source': fields[0], 'target': fields[1], 'fstype': fields[2],
                         'options': fields[3].split(',')})
    return rows


def subvolume(options):
    value = next((item.split('=', 1)[1] for item in options if item.startswith('subvol=')), None)
    return value.strip('/') if value is not None else None


def regular_text(path):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def esp_identity(tree):
    """Read both identity records; malformed or conflicting records cannot authorize writes."""
    windows, own, invalid = None, None, False

    def guid(value):
        return isinstance(value, str) and re.fullmatch(
            r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value)

    for relative in (ALONGSIDE, OWN_ESP):
        path = tree / relative
        if not path.exists() and not path.is_symlink():
            continue
        try:
            record = json.loads(regular_text(path) or 'null')
        except ValueError:
            record = None
        if not isinstance(record, dict) or record.get('version') != 1:
            invalid = True
            continue
        if relative == ALONGSIDE:
            value = record.get('esp_partuuid')
            if not guid(value):
                invalid = True
            else:
                windows = value.lower()
        if relative == OWN_ESP or 'emaki_esp_partuuid' in record:
            value = record.get('emaki_esp_partuuid')
            if not guid(value) or (own is not None and own != value.lower()):
                invalid = True
            else:
                own = value.lower()
    if own is not None and own == windows:
        invalid = True
    return windows, None if invalid else own, invalid


def installed_in(tree, device):
    """The installed Emaki whose root is tree (a mounted filesystem or its @ subvolume), or None."""
    release = regular_text(tree / OS_RELEASE)
    fstab = regular_text(tree / 'etc/fstab')
    if release is None or fstab is None or 'VARIANT_ID=emaki' not in release.splitlines():
        return None
    rows = fstab_rows(fstab)
    root = next((row for row in rows if row['target'] == '/'), None)
    if root is None or root['source'].lower() != 'uuid=' + str(device['uuid']).lower():
        return None
    windows, own, invalid = esp_identity(tree)
    return {'device': device['path'], 'uuid': device['uuid'], 'fstype': device['fstype'],
            'subvolume': subvolume(root['options']) if device['fstype'] == 'btrfs' else None,
            'rows': rows, 'repairable': 'repair-slots' in (regular_text(tree / REFRESH) or ''),
            'windows_esp': windows, 'own_esp': own, 'invalid_esp_record': invalid}


def probe(run, device, mountpoint, mounts=None):
    """Mount a filesystem read-only and look for an installed Emaki in it."""
    options = 'ro,subvolid=5' if device['fstype'] == 'btrfs' else 'ro'
    mounts = mounts if mounts is not None else Mounts(run)
    mounts.mount(['-t', device['fstype'], '-o', options, device['path']], mountpoint)
    try:
        trees = [mountpoint / ROOT_SUBVOLUME, mountpoint] if device['fstype'] == 'btrfs' else [mountpoint]
        for tree in trees:
            system = installed_in(tree, device)
            # The tree must be the subvolume the installed fstab mounts as /.
            if system is not None and tree == (mountpoint / system['subvolume'] if system['subvolume']
                                               else mountpoint):
                return system
        return None
    finally:
        try:
            mounts.unmount(mountpoint)
        except (RepairError, OSError) as error:
            raise ProbeCleanupError(translate(PROBE_CLEANUP_FAILED).format(target=mountpoint, error=error)) from error


def twins(devices, item, fstypes):
    """The distinct devices (by their real path) of these types that carry item's UUID."""
    uuid = str(item.get('uuid') or '').lower()
    return sorted({os.path.realpath(device['path']) for device in devices
                   if device['fstype'] in fstypes and uuid and str(device['uuid'] or '').lower() == uuid})


def cloned(devices, system):
    """The devices that make the installed system ambiguous, or []: its root file system's UUID
    on more than one device, or its encrypted disk's UUID on more than one (a copied disk). The
    installed fstab, GRUB and the boot refresh name them by UUID, so a repair could write to one
    while the system starts from the other (the boot refresh's check_refresh_storage refuses the
    same on the installed system)."""
    found = twins(devices, system, ROOT_FILESYSTEMS)
    if len(found) > 1:
        return found
    parent = next((device for device in devices if device['path'] == system.get('parent')), None)
    if parent is not None and str(parent['fstype']).lower() == 'crypto_luks':
        found = twins(devices, parent, (parent['fstype'],))
        if len(found) > 1:
            return found
    return []


def find_system(run, devices, mountpoint, named=None, mounts=None):
    """The one installed Emaki on this computer's disks, or the one on the named device.

    Each device is looked at once, by its real path (lsblk can list a device under several
    parents); two devices with the same UUID are two devices (R9S10 #3). A system whose file
    system or encrypted disk appears on more than one device is refused before anything is
    mounted writable."""
    seen, systems = set(), []
    for device in devices:
        identity = os.path.realpath(device['path'])
        if device['fstype'] not in ROOT_FILESYSTEMS or not device['uuid'] or identity in seen:
            continue
        if named is not None and identity != os.path.realpath(named):
            continue
        seen.add(identity)
        try:
            system = probe(run, device, mountpoint, mounts)
        except ProbeCleanupError:
            raise
        except RepairError:
            system = None
        if system is not None:
            systems.append(system)
    if named is not None and not systems:
        raise RepairError(translate(NAMED_NOT_EMAKI).format(device=named))
    if not systems:
        locked = [device for device in devices
                  if str(device['fstype']).lower() == 'crypto_luks' and not device['opened']]
        for device in locked:
            copies = twins(devices, device, (device['fstype'],))
            if len(copies) > 1:
                raise RepairError(translate(CLONED).format(devices=', '.join(copies)))
        raise RepairError(translate(LOCKED).format(device=locked[0]['path']) if locked else translate(NONE_FOUND))
    for system in systems:
        clones = cloned(devices, {**system, 'parent': next(
            (device['parent'] for device in devices if device['path'] == system['device']), None)})
        if clones:
            raise RepairError(translate(CLONED).format(devices=', '.join(clones)))
    if len(systems) > 1:
        names = [system['device'] for system in systems]
        raise RepairError(translate(SEVERAL).format(devices=', '.join(names), first=names[0]))
    return systems[0]


def find_esp(system, devices, named=None):
    """Resolve one FAT partition by the saved GUID, or the installed fstab identity.

    A name can select only that partition; it never bypasses missing or ambiguous evidence.
    Legacy shared layouts keep their installed read-only policy.
    """
    windows, own = system.get('windows_esp'), system.get('own_esp')
    rows = [row for row in system['rows'] if row['target'] == '/efi']
    if len(rows) != 1 or rows[0]['fstype'] != 'vfat':
        raise RepairError(translate(ESP_UNPROVEN))
    row = rows[0]
    readonly = 'ro' in row['options']
    source = row['source']
    key, ignored, value = source.partition('=')
    field = {'UUID': 'uuid', 'PARTUUID': 'partuuid'}.get(key.upper())
    named_device = None
    if named is not None:
        named_device = next((item for item in devices
                             if os.path.realpath(item['path']) == os.path.realpath(named)), None)
        if named_device is None or named_device['fstype'] != 'vfat':
            raise RepairError(translate(ESP_NOT_FAT).format(device=named))
        if not readonly and windows and str(named_device.get('partuuid') or '').lower() == windows:
            raise RepairError(translate(WINDOWS_ESP).format(device=named))
    found = {os.path.realpath(item['path']): item for item in devices
             if item['fstype'] == 'vfat'
             and ((own is not None and named_device is not None)
                  or (field and value and str(item.get(field) or '').lower() == value.lower()))
             and (own is None or str(item.get('partuuid') or '').lower() == own)}
    if len(found) != 1:
        raise RepairError(translate(ESP_UNPROVEN) if own is not None and named_device is not None
                          else translate(NO_ESP).format(source=source))
    selected = next(iter(found.values()))
    if named_device is not None and os.path.realpath(named_device['path']) != os.path.realpath(selected['path']):
        raise RepairError(translate(ESP_UNPROVEN))
    if not readonly:
        if system.get('invalid_esp_record'):
            raise RepairError(translate(ESP_UNPROVEN))
        if windows and str(selected.get('partuuid') or '').lower() == windows:
            raise RepairError(translate(WINDOWS_ESP).format(device=selected['path']))
        if own and len({os.path.realpath(item['path']) for item in devices
                        if str(item.get('partuuid') or '').lower() == own}) != 1:
            raise RepairError(translate(ESP_UNPROVEN))
    return selected


def inspect_esp(mounts, system, esp, mountpoint):
    """Inspect every writable candidate read-only, even when its identity was recorded."""
    row = next(row for row in system['rows'] if row['target'] == '/efi')
    if 'ro' in row['options']:
        return
    mounts.mount(['-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', esp['path']], mountpoint)
    try:
        if not read_only(mounts.run, mountpoint):
            raise RepairError(translate(ESP_UNPROVEN))
        directory = installed_directory(mountpoint, 'EFI/Microsoft')
        if directory is not None:
            # Match the installed gate even when Windows' loader is missing.
            # A saved Emaki GUID does not make a shared partition writable.
            system['shared_esp'] = os.path.realpath(esp['path'])
    finally:
        try:
            mounts.unmount(mountpoint)
        except (RepairError, OSError) as error:
            raise ProbeCleanupError(translate(PROBE_CLEANUP_FAILED).format(target=mountpoint, error=error)) from error
    system['inspected_esp'] = os.path.realpath(esp['path'])


class Mounts:
    """What the repair mounted, unmounted in reverse order on every way out."""

    def __init__(self, run):
        self.run, self.targets = run, []

    def mount(self, argv, target):
        self.run(['mount', *argv, target])
        self.targets.append(target)

    def unmount(self, target):
        self.run(['umount', target])
        self.targets.remove(target)

    def close(self):
        failed = False
        for target in reversed(self.targets.copy()):
            try:
                self.unmount(target)
            except (RepairError, OSError) as error:
                failed = True
                print(translate(UNMOUNT_FAILED).format(target=target, error=error), file=sys.stderr)
        return not failed


def read_only(run, target):
    options = run(['findmnt', '--noheadings', '--output', 'OPTIONS', '--mountpoint', target]).strip()
    return 'ro' in options.split(',')


def installed_directory(root, relative, create=False):
    """Check every component without following links; create only through checked parents.

    The root lives under the repair's private temporary directory. Descriptor-relative
    opens and mkdir keep each lookup anchored to the parent just checked, including when
    a directory is absent. Never use recursive Path.mkdir on the installed tree.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = None
    try:
        descriptor = os.open(root, flags)
        for component in Path(relative).parts:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    return None
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return root / relative
    except OSError as error:
        raise RepairError(translate(UNSAFE_DIRECTORY).format(path='/' + relative)) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def mount_system(mounts, system, esp, root):
    """The installed root, its log, its machine key read-only and its ESP, under root."""
    device, rows = system['device'], {row['target']: row for row in system['rows']}
    row = rows.get('/efi')
    mode = 'ro' if ((row and 'ro' in row['options'])
                    or system.get('shared_esp') == os.path.realpath(esp['path'])) else 'rw'
    if mode == 'rw' and (system.get('invalid_esp_record')
                         or system.get('inspected_esp') != os.path.realpath(esp['path'])):
        raise RepairError(translate(ESP_UNPROVEN))
    if system['fstype'] == 'btrfs':
        options = ['subvol=/' + system['subvolume']] if system['subvolume'] else ['subvolid=5']
        mounts.mount(['-t', 'btrfs', '-o', ','.join(options), device], root)
    else:
        mounts.mount(['-t', system['fstype'], device], root)
    # Refuse corrupt mountpoints before creating anything or mounting a child filesystem.
    # Include the API filesystems mounted by arch-chroot and every nested target.
    # Resolver binding is disabled with -r below, so its usual symlink is harmless.
    for relative in ('var/log', KEY_STATE, 'efi', 'proc', 'sys/firmware/efi/efivars',
                     'dev/pts', 'dev/shm', 'run', 'tmp'):
        installed_directory(root, relative)
    log = rows.get('/var/log')
    if system['fstype'] == 'btrfs' and log and subvolume(log['options']) \
            and log['source'].lower() == 'uuid=' + system['uuid'].lower():
        mounts.mount(['-t', 'btrfs', '-o', 'nosuid,nodev,noexec,subvol=/' + subvolume(log['options']), device],
                     installed_directory(root, 'var/log', create=True))
    key = rows.get('/' + KEY_STATE)
    state = installed_directory(root, KEY_STATE)
    if key and system['fstype'] == 'btrfs' and subvolume(key['options']):
        state = installed_directory(root, KEY_STATE, create=True)
        mounts.mount(['-t', 'btrfs', '-o', 'ro,nosuid,nodev,noexec,subvol=/' + subvolume(key['options']), device],
                     state)
    elif state is not None:
        # The key directory inside the root itself: the same read-only view through a bind mount.
        mounts.mount(['--bind', '-o', 'ro', state], state)
    else:
        state = None
    if state is not None and not read_only(mounts.run, state):
        raise RepairError(translate(KEY_NOT_READ_ONLY))
    mounts.mount(['-t', 'vfat', '-o', mode + ',nosuid,nodev,noexec', esp['path']],
                 installed_directory(root, 'efi', create=True))


def repair(run, call, root_device=None, esp_device=None, base=BASE):
    """Find, mount, repair inside the installed root, unmount. Returns the exit status."""
    work = Path(tempfile.mkdtemp(prefix='emaki-boot-repair.', dir=base))
    mounts = Mounts(run)
    status = 1
    try:
        probe_point, root = work / 'probe', work / 'root'
        probe_point.mkdir()
        root.mkdir()
        devices = block_devices(run)
        system = find_system(run, devices, probe_point, root_device, mounts)
        if not system['repairable']:
            raise RepairError(translate(TOO_OLD))
        esp = find_esp(system, devices, esp_device)
        inspect_esp(mounts, system, esp, probe_point)
        print(translate(REPAIRING).format(root=system['device'], esp=esp['path']))
        mount_system(mounts, system, esp, root)
        status = 0 if call(['arch-chroot', '-r', root, *REPAIR]) == 0 else 1
        print(translate(DONE) if status == 0 else translate(FAILED), file=sys.stdout if status == 0 else sys.stderr)
    except (RepairError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        status = 1
    finally:
        if not mounts.close():
            status = 1
        for path in (work / 'probe', work / 'root', work):
            try:
                path.rmdir()
            except OSError:
                pass
    return status


def main(argv=None, run=run, call=call, live=LIVE, base=BASE):
    parser = argparse.ArgumentParser(
        prog='emaki-boot-repair',
        description=_('Make an installed Emaki start again with Secure Boot: rebuild its boot loader and firmware startup entries. Run it from the Emaki installation USB.'))
    parser.add_argument('--root', metavar='DEVICE', help=_('the filesystem of the installed Emaki to repair'))
    parser.add_argument('--esp', metavar='DEVICE', help=_('its startup partition'))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print(translate(NOT_ROOT), file=sys.stderr)
        return 1
    if not Path(live).exists():
        print(translate(NOT_LIVE), file=sys.stderr)
        return 1
    return repair(run, call, args.root, args.esp, base)
