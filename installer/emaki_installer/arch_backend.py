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

from .constants import OFFLINE_CONF, OFFLINE_REPO, TARGET, WORK
from .errors import Code, InstallError, require
from .runtime import TargetFiles
from .render import validate_xkb_layouts


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


def offline_config(path=OFFLINE_CONF):
    """Reject includes and additional repos, including accidental online fallback."""
    sections, active, values = [], None, {}
    try:
        for line in path.read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('[') and line.endswith(']'):
                active = line[1:-1]
                sections.append(active)
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            require(key not in ('Include', 'XferCommand', 'HookDir', 'GPGDir', 'DBPath', 'RootDir', 'CacheServer'),
                    Code.OFFLINE_REPO, f'Unsafe offline pacman directive: {key}.')
            values.setdefault((active, key), []).append(value)
    except OSError as exc:
        raise InstallError(Code.OFFLINE_REPO, 'Offline pacman configuration is missing or unreadable.') from exc
    require(sections.count('emaki-offline') == 1 and set(sections) <= {'options', 'emaki-offline'},
            Code.OFFLINE_REPO, 'Only [emaki-offline] is permitted for pacstrap.')
    require(values.get(('emaki-offline', 'Server')) == [OFFLINE_REPO], Code.OFFLINE_REPO,
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
        status = (d.ModificationStatus.CREATE if plan.config['mode'] == 'erase' else
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


def validate_live_plan(api, plan):
    offline_config()
    try:
        rules = Path('/usr/share/X11/xkb/rules/evdev.lst').read_text()
    except OSError as exc:
        raise InstallError(Code.BAD_CONFIG, 'The ISO XKB layout inventory is unavailable.') from exc
    validate_xkb_layouts(plan.config['layouts'], rules)
    return build_disk_config(api, plan)


class DeviceOperations:
    def __init__(self, plan, runner, target=TARGET):
        self.plan, self.runner, self.target = plan, runner, target
        self.temp = WORK / 'btrfs-top'

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

    def format(self, fs_type, path):
        commands = {'btrfs': ['mkfs.btrfs', '-f'], 'ext4': ['mkfs.ext4', '-F'],
                    'fat32': ['mkfs.fat', '-F', '32']}
        self.runner.run([*commands[fs_type.value], str(path)])

    def fetch_part_info(self, path):
        data = json.loads(self.runner.run(['lsblk', '-J', '-b', '-o', 'PATH,PARTN,PARTUUID,UUID', str(path)]))
        row = data['blockdevices'][0]
        require(row.get('uuid') and row.get('partuuid'), Code.UNSAFE_DISK,
                'Formatted partition has no UUID or PARTUUID.')
        p = next(p for p in self.plan.partitions if p.path == str(path))
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
        self.runner.run(['mount', '-t', 'btrfs', '-o', 'subvolid=5,' + ','.join(p.options), p.path, str(self.temp)])
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
    def __init__(self, runner, target):
        self.runner, self.target = runner, target

    def strap(self, packages, **kwargs):
        if isinstance(packages, str):
            packages = [packages]
        self.runner.run(['pacstrap', '-C', str(offline_config()), '-K', str(self.target),
                         *sorted(set(packages)), '--noconfirm', '--needed'])


class OfflinePacmanConfig:
    """minimal_installation must not mutate live /etc/pacman.conf or persist USB URLs."""
    def __init__(self, target):
        self.target = target

    def enable(self, repositories):
        require(not repositories, Code.OFFLINE_REPO, 'Online repositories are forbidden during pacstrap.')

    def apply(self):
        offline_config()

    def persist(self):
        pass  # Keep /etc/pacman.conf supplied by the target pacman package.


class Backend:
    def __init__(self, api, plan, runner, target=TARGET):
        self.api, self.plan, self.runner, self.target = api, plan, runner, target
        self.files = TargetFiles(target)
        self.disk_config = build_disk_config(api, plan)
        self.devices = DeviceOperations(plan, runner, target)
        self.instance = None

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
                                        part.path, str(destination)])

            def arch_chroot(self, command, **kwargs):
                return backend.runner.chroot(shlex.split(command) if isinstance(command, str) else command,
                                             backend.target)

            def _get_microcode(self):
                return None  # Both vendor packages are explicitly strapped.

            def set_hostname(self, hostname):
                backend.files.write('/etc/hostname', hostname + '\n')

            def set_vconsole(self, locale):
                backend.files.write('/etc/vconsole.conf', 'KEYMAP=' + locale.kb_layout + '\n')

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
                                           base_packages=['base', 'mkinitcpio'],
                                           kernels=['linux', 'linux-lts'], silent=True)
        self.instance.pacman = OfflinePacman(self.runner, self.target)

        self.instance.mount_ordered_layout()
        self.instance.sanity_check(skip_ntp=True, skip_wkd=True)

    def minimal(self, locale):
        with patch.object(self.api.installer, 'PacmanConfig', OfflinePacmanConfig):
            self.instance.minimal_installation(mkinitcpio=False,
                                               hostname=self.plan.config['hostname'], locale_config=locale)
