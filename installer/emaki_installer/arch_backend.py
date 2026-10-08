"""The pinned 4.5 adapter. No archinstall import until daemon startup.

4.5's global DeviceHandler discovers and mounts every existing btrfs filesystem.
Suppress that discovery while importing, then use a job-scoped handler. Its
manual MODIFY also deletes/recreates GPT entries, so our handler formats in
place and does not call the upstream partition() for manual layouts.
"""
import importlib
from importlib.metadata import version as dist_version
import json
import os
from pathlib import Path
import shlex
from types import SimpleNamespace
from unittest.mock import patch

from .constants import GRUB_VISIBLE_FONT, OFFLINE_CONF, OFFLINE_REPO, TARGET, WORK
from . import inventory as alongside
from .errors import Code, InstallError, require
from .runtime import TargetFiles
from .graphics import console_font
from .render import GRUB_EARLY_MODULES, validate_xkb_layouts, vconsole_conf


# Stock GRUB derives the passphrase slot without SIMD, about 0.17 s per 64 MiB Argon2id pass
# under KVM on a quiet host (2026-10-05). A fixed pass count makes the wait depend on the machine: a
# 500 ms Linux calibration gave 11 passes on one load and 17 on another (3.3 s and 5.5 s).
# cryptsetup's minimum for Argon2 is 4.
GRUB_ARGON2_PASSES = 5


def resize_partition(plan, runner, changed):
    """Shrink NTFS before its GPT extent; verify before allocating Emaki root."""
    p = next(p for p in plan.disk['partitions'] if p['id'] == plan.config['partition_id'])
    disk, dev, n = plan.disk['path'], p['path'], p['number']
    sector = plan.disk['_sector_size']
    new_size = p['size_bytes'] - plan.config['shrink_bytes']
    metadata = alongside.read_gpt(runner, disk, n)
    require(metadata == p['_gpt'], Code.DISK_CHANGED, 'Windows GPT metadata changed after review; disk untouched.')
    alongside.verify_gpt(runner, disk)
    runner.log('Alongside: dry-run the exact NTFS size; no disk writes yet.')
    output = runner.run(['ntfsresize', '--no-action', '--size', str(new_size), dev])
    require(not alongside.ntfs_error(output) and 'The read-only test run ended successfully.' in output,
            Code.SHRINK_BOUNDS, 'NTFS resize dry run did not pass; disk untouched.')
    runner.log('Alongside: shrink the Windows filesystem; answering y after the successful dry run.')
    changed()
    output = runner.run(['ntfsresize', '--size', str(new_size), dev], input='y\n')
    require('Successfully resized NTFS on device' in output, Code.UNSAFE_DISK,
            'STOP: NTFS resize did not confirm success. Partition table untouched; inspect Windows before retrying.')
    end = (p['start_bytes'] + new_size) // sector - 1
    require((end + 1) * sector % alongside.MIB == 0, Code.UNSAFE_DISK, 'Unaligned Windows end.')
    runner.log('Alongside: shrink only the Windows GPT entry, preserving its start, GUID, type, name and attributes.')
    runner.run(['sgdisk', f'--delete={n}', f'--new={n}:{metadata["start"]}:{end}',
                f'--partition-guid={n}:{metadata["guid"]}', f'--typecode={n}:{metadata["type"]}',
                f'--change-name={n}:{metadata["name"]}',
                f'--attributes={n}:=:0x{metadata["attributes"]}', disk])
    runner.run(['partprobe', disk])
    runner.run(['udevadm', 'settle', '--timeout=30'])
    runner.log('Alongside: verify resized NTFS geometry and read-only metadata. '
               'Windows chkdsk on first boot is expected; the dirty flag is preserved.')
    try:
        alongside.verify_resized_ntfs(runner, dev, new_size)
    except (OSError, RuntimeError) as exc:
        raise InstallError(Code.UNSAFE_DISK,
                           'STOP: Windows was resized but the post-shrink NTFS check failed. '
                           'No Emaki root was created or formatted. Do not retry the installer; '
                           'boot Windows for a consistency check and inspect the installation log.',
                           retryable=False) from exc
    expected = dict(metadata, end=end)
    require(alongside.read_gpt(runner, disk, n) == expected, Code.UNSAFE_DISK,
            'STOP: Windows GPT metadata differs after shrink. No Emaki root was created.')
    for original in plan.disk['partitions']:
        if original['number'] != n:
            require(alongside.read_gpt(runner, disk, original['number']) == original['_gpt'],
                    Code.UNSAFE_DISK, 'STOP: preserved GPT metadata changed. No Emaki root was created.')
    root = plan.root
    runner.log('Alongside: create the Emaki root only inside the freed Windows extent.')
    runner.run(['sgdisk', f'--new={root.number}:{root.start // sector}:{(root.start + root.size) // sector - 1}',
                f'--typecode={root.number}:8300', f'--change-name={root.number}:Emaki', disk])
    runner.run(['partprobe', disk])
    runner.run(['udevadm', 'settle', '--timeout=30'])
    alongside.verify_gpt(runner, disk)


def load_archinstall():
    try:
        # These are archinstall dependencies, not additional Emaki dependencies.
        import parted

        def no_device(*args, **kwargs):
            raise parted.IOException('Emaki owns device discovery')

        # pyparted 3.13 replaces its module with a 'Wrapper' object whose attributes are not
        # in __dict__, so unittest.mock.patch.object fails on exit (delattr). Swap and
        # restore the two functions by hand instead.
        saved = (parted.getAllDevices, parted.getDevice)
        parted.getAllDevices, parted.getDevice = (lambda *args, **kwargs: []), no_device
        try:
            arch = importlib.import_module('archinstall')
            version = dist_version('archinstall')
            require(version == '4.5', Code.UNSUPPORTED_VERSION,
                    'Emaki requires archinstall distribution version 4.5.')
            models = importlib.import_module('archinstall.lib.models.device')
            fs_module = importlib.import_module('archinstall.lib.disk.filesystem')
            installer_module = importlib.import_module('archinstall.lib.installer')
            locale_module = importlib.import_module('archinstall.lib.models.locale')
        finally:
            parted.getAllDevices, parted.getDevice = saved
        # Never call this handler's discovery later; restore aliases only so the
        # source module is not left holding temporary mock objects.
        dh = importlib.import_module('archinstall.lib.disk.device_handler')
        dh.getAllDevices, dh.getDevice = parted.getAllDevices, parted.getDevice
        return SimpleNamespace(arch=arch, version=version, models=models, filesystem=fs_module,
                               installer=installer_module, locale=locale_module)
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(Code.UNSUPPORTED_VERSION,
                           'Cannot load archinstall 4.5 and its dependencies: ' + type(exc).__name__) from exc


def offline_config(path=OFFLINE_CONF, *, repo=OFFLINE_REPO, hookdir=None):
    """Reject includes and additional repos, including accidental online fallback."""
    sections, active, values = [], None, {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('[') and line.endswith(']'):
                active = line[1:-1]
                sections.append(active)
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            require(key not in ('Include', 'XferCommand', 'GPGDir', 'DBPath', 'RootDir', 'CacheServer'),
                    Code.OFFLINE_REPO, f'Unsafe offline pacman directive: {key}.')
            if key == 'HookDir':
                require(hookdir is not None and active == 'options' and value == str(hookdir)
                        and not any(char.isspace() for char in value),
                        Code.OFFLINE_REPO, 'Unsafe offline pacman directive: HookDir.')
            values.setdefault((active, key), []).append(value)
    except OSError as exc:
        raise InstallError(Code.OFFLINE_REPO, 'Offline pacman configuration is missing or unreadable.') from exc
    require(sections.count('emaki-offline') == 1 and set(sections) <= {'options', 'emaki-offline'},
            Code.OFFLINE_REPO, 'Only [emaki-offline] is permitted for pacstrap.')
    require(values.get(('options', 'HookDir'), []) == ([] if hookdir is None else [str(hookdir)]),
            Code.OFFLINE_REPO, 'Unsafe offline pacman directive: HookDir.')
    require(values.get(('emaki-offline', 'Server')) == [repo], Code.OFFLINE_REPO,
            'Offline repository must be the live USB file:// repository.')
    sig = values.get(('emaki-offline', 'SigLevel'), values.get(('options', 'SigLevel'), []))
    require(len(sig) == 1 and set(sig[0].split()) == {'Required', 'DatabaseOptional', 'TrustedOnly'},
            Code.OFFLINE_REPO, 'Offline package signatures must be required and trusted.')
    return path


def build_disk_config(api, plan):
    d = api.models
    sector = d.SectorSize(plan.disk['_sector_size'], d.Unit.B)
    size = lambda n: d.Size(n, d.Unit.B, sector)
    # BDevice is a data container here; the injected handler owns actual I/O.
    device = d.BDevice(
        disk=SimpleNamespace(type='gpt', device=SimpleNamespace(path=plan.disk['path'])),
        device_info=SimpleNamespace(path=Path(plan.disk['path']),
                                    total_size=size(plan.disk['size_bytes']), sector_size=sector),
        partition_infos=[])
    parts = []
    for p in plan.partitions:
        status = (d.ModificationStatus.CREATE if plan.config['mode'] == 'erase' or p.path is None else
                  d.ModificationStatus.MODIFY if p.format else d.ModificationStatus.EXIST)
        parts.append(d.PartitionModification(
            status=status, type=d.PartitionType.PRIMARY, start=size(p.start), length=size(p.size),
            fs_type={'vfat': d.FilesystemType.FAT32, 'btrfs': d.FilesystemType.BTRFS,
                     'ext4': d.FilesystemType.EXT4}[p.fs],
            mountpoint=Path(p.mountpoint) if p.mountpoint else None,
            mount_options=p.options, flags=[d.PartitionFlag.ESP] if p.esp else [],
            btrfs_subvols=[d.SubvolumeModification(Path(name), Path(mp)) for name, mp in p.subvolumes.items()],
            dev_path=Path(p.path) if p.path else None, partn=p.number,
            partuuid=p.partuuid, uuid=p.uuid))
    return d.DiskLayoutConfiguration(
        config_type=d.DiskLayoutType.Manual,
        device_modifications=[d.DeviceModification(device, plan.config['mode'] == 'erase', parts)])


def missing_grub_modules(directory=Path('/usr/lib/grub/x86_64-efi')):
    """Names of GRUB_EARLY_MODULES without a .mod file in the live GRUB; empty when all are there."""
    return [name for name in GRUB_EARLY_MODULES if not (directory / (name + '.mod')).is_file()]


def validate_live_plan(api, plan):
    # The worker validates discovered media or downloads a frozen signed repository
    # before its first disk write; planning must also work without mounted media.
    try:
        rules = Path('/usr/share/X11/xkb/rules/evdev.lst').read_text()
    except OSError as exc:
        raise InstallError(Code.BAD_CONFIG, 'The ISO XKB layout inventory is unavailable.') from exc
    validate_xkb_layouts(plan.config['layouts'], rules)
    if plan.encrypted:
        # The target gets the same grub package from the offline repository; what the unlock
        # screen needs is checked here, before any disk write.
        missing = missing_grub_modules()
        require(not missing, Code.BOOT_VERIFY,
                'This ISO lacks GRUB modules for the encrypted unlock screen: ' + ', '.join(missing) + '.')
        require(GRUB_VISIBLE_FONT.is_file(), Code.BOOT_VERIFY, 'This ISO lacks the GRUB unlock-screen font.')
    return build_disk_config(api, plan)


class DeviceOperations:
    def __init__(self, plan, runner, target=TARGET):
        self.plan, self.runner, self.target = plan, runner, target
        self.temp = WORK / 'btrfs-top'
        self.mapping_open = False
        self.changed = lambda: None

    def umount_all_existing(self, device_path):
        # The upstream routine actively unmounts arbitrary existing mounts.
        # Refuse them instead, with a kernel exclusive-open check just before I/O.
        require(str(device_path) == self.plan.disk['path'], Code.UNSAFE_DISK, 'Unexpected device.')
        for path in [str(device_path), *(p['path'] for p in self.plan.disk['partitions'])]:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_EXCL | os.O_CLOEXEC)
                os.close(fd)
            except OSError as exc:
                raise InstallError(Code.DISK_BUSY, f'Device cannot be opened exclusively: {path}.') from exc

    def partition(self, mod):
        if self.plan.config['mode'] == 'alongside':
            alongside.require_verified()
            resize_partition(self.plan, self.runner, self.changed)
            self.verify_alongside_layout(mod)
            return
        if not mod.wipe:
            self.runner.log('Manual mode: preserving the partition table, starts, GUIDs and flags.')
            return
        sector = self.plan.disk['_sector_size']
        lines = ['label: gpt', 'unit: sectors', '']
        for p in self.plan.partitions:
            lines.append(f'start={p.start // sector}, size={p.size // sector}, type={"U" if p.esp else "L"}')
        self.runner.run(['sfdisk', '--lock=yes', '--wipe=always', '--wipe-partitions=always',
                         self.plan.disk['path']], input='\n'.join(lines) + '\n')
        self.runner.run(['partprobe', self.plan.disk['path']])
        self.runner.run(['udevadm', 'settle', '--timeout=30'])
        data = json.loads(self.runner.run(['lsblk', '--tree', '-J', '-b', '-o',
                                          'PATH,TYPE,PARTN,START,SIZE,PTTYPE', self.plan.disk['path']]))
        disk = data['blockdevices'][0]
        require(disk.get('pttype') == 'gpt', Code.UNSAFE_DISK, 'New partition table is not GPT.')
        children = disk.get('children', [])
        require(len(children) == len(mod.partitions), Code.UNSAFE_DISK, 'Unexpected partition count after write.')
        for p, archpart in zip(self.plan.partitions, mod.partitions, strict=True):
            found = next((x for x in children if int(x.get('partn') or 0) == p.number), None)
            require(found is not None and int(found['start']) * 512 == p.start
                    and int(found['size']) == p.size, Code.UNSAFE_DISK,
                    'Partition geometry differs from the approved plan.')
            p.path = found['path']
            archpart.dev_path = Path(p.path)

    def verify_alongside_layout(self, mod):
        data = json.loads(self.runner.run(['lsblk', '--tree', '-J', '-b', '-o',
                                          'PATH,TYPE,PARTN,START,SIZE,PTTYPE,PARTUUID,PARTTYPE', self.plan.disk['path']]))
        disk = data['blockdevices'][0]
        children = disk.get('children', [])
        require(disk.get('pttype') == 'gpt' and len(children) == len(self.plan.disk['partitions']) + 1,
                Code.UNSAFE_DISK, 'STOP: unexpected partition table after shrinking Windows.')
        for original in self.plan.disk['partitions']:
            found = next((p for p in children if int(p.get('partn') or 0) == original['number']), None)
            size = original['size_bytes'] - (self.plan.config['shrink_bytes']
                    if original['id'] == self.plan.config['partition_id'] else 0)
            require(found and int(found['start']) * 512 == original['start_bytes']
                    and int(found['size']) == size and found['path'] == original['path']
                    and str(found.get('partuuid')).lower() == str(original.get('_partuuid')).lower()
                    and str(found.get('parttype')).lower() == str(original.get('_parttype')).lower(),
                    Code.UNSAFE_DISK, 'STOP: an existing partition changed unexpectedly; formatting refused.')
        p = self.plan.root
        found = next((row for row in children if int(row.get('partn') or 0) == p.number), None)
        require(found and int(found['start']) * 512 == p.start and int(found['size']) == p.size
                and found.get('partuuid') and found['path'] not in {p['path'] for p in self.plan.disk['partitions']},
                Code.UNSAFE_DISK, 'STOP: Emaki root differs from the approved freed extent; formatting refused.')
        p.path, p.partuuid = found['path'], found['partuuid']
        for archpart in mod.partitions:
            if archpart.partn == p.number:
                archpart.dev_path = Path(p.path)

    def format(self, fs_type, path):
        if self.plan.config['mode'] == 'alongside':
            alongside.require_verified()
            require(self.plan.root.path == str(path) and fs_type.value == self.plan.root.fs,
                    Code.UNSAFE_DISK, 'Alongside can format only the newly created Emaki root.')
        # No discard of the device while formatting (mkfs.fat has none): a broken one left no
        # superblock behind a successful mkfs. The installed system trims with fstrim.timer.
        commands = {'btrfs': ['mkfs.btrfs', '-f', '-K'], 'ext4': ['mkfs.ext4', '-F', '-E', 'nodiscard'],
                    'fat32': ['mkfs.fat', '-F', '32']}
        if self.plan.encrypted and str(path) == self.plan.root.path:
            root = self.plan.root
            require(not Path('/dev/mapper/emaki-root').exists(), Code.DISK_BUSY,
                    'The installer root mapping is already in use.')
            self.runner.run(['cryptsetup', 'luksFormat', '--batch-mode', '--type', 'luks2',
                             '--pbkdf', 'argon2id', '--pbkdf-memory', '65536',
                             '--pbkdf-parallel', '1',
                             '--pbkdf-force-iterations', str(GRUB_ARGON2_PASSES),
                             '--key-file', '-', str(path)], input=self.plan.disk_password, secret=True)
            self.runner.run(['cryptsetup', 'open', '--key-file', '-', str(path), 'emaki-root'],
                            input=self.plan.disk_password, secret=True)
            self.mapping_open = True
            root.mapper = '/dev/mapper/emaki-root'
            root.luks_uuid = self.runner.run(['cryptsetup', 'luksUUID', str(path)]).strip()
            require(bool(root.luks_uuid), Code.BOOT_VERIFY, 'Encrypted root has no LUKS UUID.')
            path = root.device
        self.runner.run([*commands[fs_type.value], str(path)])

    def close(self):
        if self.mapping_open:
            self.runner.run(['cryptsetup', 'close', 'emaki-root'])
            self.mapping_open = False

    def fetch_part_info(self, path):
        data = json.loads(self.runner.run(['lsblk', '-J', '-b', '-o', 'PATH,PARTN,PARTUUID,UUID', str(path)]))
        row = next((row for row in data['blockdevices'] if row['path'] == str(path)), {})
        require(row.get('uuid') and row.get('partuuid'), Code.UNSAFE_DISK,
                'Formatted partition has no UUID or PARTUUID.')
        p = next(p for p in self.plan.partitions if p.path == str(path))
        if p.mapper:
            row['uuid'] = self.runner.run(['blkid', '-s', 'UUID', '-o', 'value', p.device]).strip()
            require(bool(row['uuid']), Code.UNSAFE_DISK, 'Root filesystem UUID is missing.')
        p.uuid, p.partuuid = row['uuid'], row['partuuid']
        return SimpleNamespace(partn=int(row['partn']), partuuid=row['partuuid'], uuid=row['uuid'])

    def create_btrfs_volumes(self, part_mod, enc_conf=None):
        p = next(p for p in self.plan.partitions if p.path == str(part_mod.dev_path))
        self.ensure_subvolumes(p)

    def ensure_subvolumes(self, p):
        if not p.subvolumes:
            return
        require(not self.temp.is_symlink(), Code.UNSAFE_DISK, 'Unsafe temporary mountpoint.')
        self.temp.mkdir(parents=True, exist_ok=True)
        self.runner.run(['mount', '-t', 'btrfs', '-o', 'subvolid=5,' + ','.join(p.options), p.device, str(self.temp)])
        try:
            for name in p.subvolumes:
                child = self.temp / name
                if child.exists() or child.is_symlink():
                    require(not child.is_symlink(), Code.MANUAL_LAYOUT, 'Subvolume name is a symlink.')
                    self.runner.run(['btrfs', 'subvolume', 'show', str(child)])
                else:
                    self.runner.run(['btrfs', 'subvolume', 'create', str(child)])
        finally:
            self.runner.run(['umount', '--', str(self.temp)])


class OfflinePacman:
    def __init__(self, runner, target, source=None):
        self.runner, self.target, self.source = runner, target, source

    def strap(self, packages, **kwargs):
        if isinstance(packages, str):
            packages = [packages]
        if self.source is None:
            from .media import offline_source
            with offline_source() as source:
                OfflinePacman(self.runner, self.target, source).strap(packages, **kwargs)
            return
        self.runner.run(['pacstrap', '-C', str(self.source.validate()), '-K', str(self.target),
                         *sorted(set(packages)), '--noconfirm', '--needed'])


class OfflinePacmanConfig:
    """minimal_installation must not mutate live /etc/pacman.conf or persist USB URLs."""
    def __init__(self, target, source=None):
        self.target, self.source = target, source

    def enable(self, repositories):
        require(not repositories, Code.OFFLINE_REPO, 'Online repositories are forbidden during pacstrap.')

    def apply(self):
        self.source.validate() if self.source else offline_config()

    def persist(self):
        pass  # Keep /etc/pacman.conf supplied by the target pacman package.


class Backend:
    def __init__(self, api, plan, runner, target=TARGET):
        self.api, self.plan, self.runner, self.target = api, plan, runner, target
        self.files = TargetFiles(target)
        self.console_font = console_font(plan)
        self.disk_config = build_disk_config(api, plan)
        self.devices = DeviceOperations(plan, runner, target)
        self.instance = None
        self.package_source = None

    def prepare(self):
        fs = self.api.filesystem
        # Preserve the 4.5 operation ordering and formatting-status semantics.
        with patch.object(fs, 'device_handler', self.devices), \
                patch.object(fs, 'udev_sync', lambda: self.runner.run(['udevadm', 'settle', '--timeout=30'])):
            fs.FilesystemHandler(self.disk_config).perform_filesystem_operations()
        for p in self.plan.partitions:
            if p.fs == 'btrfs' and not p.format:
                self.devices.ensure_subvolumes(p)
        module, backend = self.api.installer, self

        class EmakiInstaller(module.Installer):
            def _mount_partition_layout(self, luks_handlers):
                # Upstream groups mounts per partition; manual rows can span
                # several btrfs filesystems with nested mountpoints. Sort the
                # flattened layout so no child mount is hidden by a later parent.
                for part, mountpoint, subvolume in backend.plan.mounts:
                    destination = backend.files.mkdir(mountpoint)
                    options = part.options + (['subvol=' + subvolume] if subvolume else [])
                    backend.runner.run(['mount', '-t', part.fs, '-o', ','.join(options),
                                        part.device, str(destination)])

            def arch_chroot(self, command, **kwargs):
                return backend.runner.chroot(shlex.split(command) if isinstance(command, str) else command,
                                             backend.target)

            def _get_microcode(self):
                return None  # Both vendor packages are explicitly strapped.

            def set_hostname(self, hostname):
                backend.files.write('/etc/hostname', hostname + '\n')

            def set_vconsole(self, locale):
                # 4.5 calls this before strapping the kernels, whose mkinitcpio hook builds
                # images from the file: write it whole, from the one producer.
                backend.files.write('/etc/vconsole.conf', vconsole_conf(backend.plan.config['layouts'], backend.console_font))

            def set_locale(self, locale):
                backend.files.write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
                backend.files.write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
                backend.runner.chroot(['locale-gen'], backend.target)

            def set_keyboard_language(self, language):
                return True  # vconsole and per-user XKB are owned by Emaki.

            def enable_service(self, services):
                # Preset and explicit enable are sequenced together in settings.
                return None

            def enable_periodic_trim(self):
                return None

        with patch.object(module, 'accessibility_tools_in_use', return_value=False):
            self.instance = EmakiInstaller(self.target, self.disk_config,
                                           base_packages=['base', 'mkinitcpio', 'terminus-font'],
                                           kernels=['linux', 'linux-lts'], silent=True)
        self.instance.pacman = OfflinePacman(self.runner, self.target, self.package_source)

        self.instance.mount_ordered_layout()
        self.instance.sanity_check(skip_ntp=True, skip_wkd=True)

    def minimal(self, locale):
        with patch.object(self.api.installer, 'PacmanConfig',
                          lambda target: OfflinePacmanConfig(target, self.package_source)):
            self.instance.minimal_installation(mkinitcpio=False,
                                               hostname=self.plan.config['hostname'], locale_config=locale)
