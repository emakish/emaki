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
import io
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
import tarfile
import uuid

from . import boot

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
FOREIGN = 'The existing boot loaders were kept because the fallback loader is not recognized as Emaki.'
TRIAL_FAILED = 'The new boot loader did not complete a boot; the old loader was kept.'
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
        raise Transient('The package database is locked; the boot loader update will wait.') from error
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
                # A killed final attempt can leave its lock even with no budget left.
                with pacman_lock():
                    pass
                raise Kept('The boot loader retry limit was reached; run emaki-boot-refresh to try again.')
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
            clear_pending()
            message = 'The boot loader retry limit was reached; run emaki-boot-refresh to try again.'
        else:
            message = f'The boot loader update did not finish; details are in {LOG_PATH}.'
        LOG.write(message + '\n')
        print(message, file=sys.stderr)
        return 0 if retry else 1
    except (Refuse, ValueError) as error:
        clear_pending()
        LOG.write(str(error) + '\n')
        print(str(error), file=sys.stderr if not isinstance(error, Kept) else sys.stdout)
        return 0 if retry or isinstance(error, Kept) else 1


def require(condition, message):
    if not condition:
        raise Refuse(message)


def run(argv):
    result = subprocess.run([str(a) for a in argv], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={
                                'PATH': '/usr/bin', 'LC_ALL': 'C'})
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


def write(path, data, mode=0o600):
    """Durable creation for staging and same-directory replacement sidecars."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as output:
        output.write(data.encode() if isinstance(data, str) else data)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(path.parent)


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
        path.unlink(missing_ok=True)
    sync_directory(target.parent)


def atomic(path, data):
    sidecar = path.with_name(path.name + '.emaki-' + uuid.uuid4().hex)
    write(sidecar, data)
    os.replace(sidecar, path)
    sync_directory(path.parent)
    expected = data.encode() if isinstance(data, str) else data
    require(durable_read(path) == expected, f'{path} could not be read back after replacement.')
    sync_grub_menu(path)
    discard_sidecars(path, [sidecar])


def sync_grub_menu(path):
    if path == Path('/boot/grub/grub.cfg'):
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


def loader_payload(grub, generation, identity, load_cfg, font):
    """Keep module ABI independent of both package upgrades and root rollback.

Every normal menu uses the live kernels and initramfs of its root. If an older
root has replaced @, its canonical menu chooses its restored kernels. Modules
always come from the EFI image, including on an old root with old /boot/grub.
"""
    files = {}
    if identity['luks']:
        initial = boot.grub_unlock_memdisk(font, load_cfg, identity['luks'])
        with tarfile.open(fileobj=io.BytesIO(initial)) as archive:
            files.update((member.name, archive.extractfile(member).read()) for member in archive)
        start = boot.grub_early_config(load_cfg, identity['luks'])
        root = '(cryptouuid/' + identity['luks'].replace('-', '') + ')'
    else:
        files['start.cfg'] = load_cfg.encode()
        start = 'source (memdisk)/start.cfg\n'
        root = '($root)'
    prefix = '(memdisk)/boot/grub'
    start += ('set prefix=' + prefix + '\nset emaki_generation=' + generation.name
              + '\nexport emaki_generation\n')
    generation_menu = root + identity['fsroot'] + str(generation / 'grub/grub.cfg')
    ordinary_menu = root + identity['fsroot'] + '/boot/grub/grub.cfg'
    files['boot/grub/grub.cfg'] = (
        f'if [ -f {ordinary_menu} ]; then\n  configfile {ordinary_menu}\n'
        f'else\n  configfile {generation_menu}\nfi\n').encode()
    files['boot/grub/grub-btrfs.cfg'] = (
        f'configfile {root}{identity["fsroot"]}/boot/grub/grub-btrfs.cfg\n').encode()
    for path in sorted(grub.rglob('*')):
        if path.is_file() and (path.suffix in ('.mod', '.lst', '.mo', '.pf2') or path.name == 'modinfo.sh'):
            files['boot/grub/' + path.relative_to(grub).as_posix()] = regular(path)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    return prefix, start.encode(), stream.getvalue()


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


def discover():
    root = mount_info('/', True)
    if root['fstype'] == 'overlay' or (root['fstype'] == 'btrfs'
            and root['fsroot'].rstrip('/') not in ('', '/@')):
        return None
    require(Path('/sys/firmware/efi').is_dir(), 'This system was not started in UEFI mode.')
    for variable in Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'):
        data = variable.read_bytes()
        require(len(data) >= 5 and data[4] == 0,
                'Secure Boot is enabled or its state is unreadable; the existing signed loader is kept.')
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
    require(esp['fstype'] == 'vfat' and 'rw' in esp['options'].split(','),
            '/efi must be the mounted, writable FAT EFI system partition.')
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
    bind(stage / 'defaults', Path('/etc/default/grub'))
    if (stage / 'btrfs-config').exists():
        bind(stage / 'btrfs-config', Path('/etc/default/grub-btrfs/config'))
    # 41_snapshots-btrfs has output side effects even when mkconfig uses stdout.
    bind(stage / 'grub', Path('/boot/grub'))
    run(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'])


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
    saved = Path('/run/emaki-boot-sleep.json')
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


def refresh_menu(identity, state, recovery=False):
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
        verify_menu(text, identity, generation, kernels)
        atomic(stage / 'grub/grub.cfg', text)
        run(['grub-script-check', stage / 'grub/grub.cfg'])
        snapshots = stage / 'grub/grub-btrfs.cfg'
        if snapshots.exists():
            run(['grub-script-check', snapshots])
            transaction.add(Path('/boot/grub/grub-btrfs.cfg'), regular(snapshots))
        transaction.add(Path('/etc/default/grub'), defaults)
        if btrfs_config.is_file():
            transaction.add(btrfs_config, regular(stage / 'btrfs-config'))
        transaction.add(Path('/boot/grub/grub.cfg'), text)
        # Broken counters are exhausted once, so GRUB no longer reads them.
        if failed_trial(state):
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
            self.intent.unlink(missing_ok=True)
            sync_directory(self.intent.parent)

    def cleanup(self):
        if self.intent is not None and self.intent.exists():
            return
        for target, new, saved, _ in self.entries:
            discard_sidecars(target, [new, saved] if self.complete or self.intent is not None else [new])


def recover(identity):
    """Recover ESP publication independently of a restored or regenerated root."""
    intent = Path('/efi/EFI/Emaki/boot-intent.json')
    if not intent.exists():
        return
    record = json.loads(regular(intent))
    tag = record.get('tag', '')
    require(re.fullmatch(r'[0-9a-f]{32}', tag) and isinstance(record.get('entries'), list)
            and record['entries'], 'The unfinished boot update record is invalid.')
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
    for source in (Path('/efi/EFI/Emaki/grubx64.efi'), Path('/efi/EFI/BOOT/BOOTX64.EFI'),
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
                target.unlink(missing_ok=True)
                sync_directory(target.parent)
            else:
                atomic(target, copies[wanted])
                require(digest(durable_read(target)) == wanted,
                        f'{target} could not be read back after replacement.')
    state_path = Path('/efi/EFI/Emaki/boot-state.json')
    state = json.loads(regular(state_path)) if state_path.exists() else {'good': None, 'newest': None}
    if any(target.is_relative_to(Path('/boot')) for _, target, *_ in pending):
        refresh_menu(identity, state, recovery=True)
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
    path = Path('/efi/EFI/Emaki/boot-state.json')
    if not path.exists():
        if regular(fallback) != regular(vendor):
            raise Kept(FOREIGN)
        return {'good': None, 'newest': None, 'fallback_sha256': digest(regular(fallback))}
    state = json.loads(regular(path))
    require(isinstance(state, dict) and {'good', 'newest'} <= state.keys() and all(
        state.get(key) is None or re.fullmatch(r'[0-9a-f]{32}', str(state[key]))
        for key in ('good', 'newest')) and
        re.fullmatch(r'[0-9a-f]{64}', str(state.get('fallback_sha256', ''))),
        'The boot generation state is invalid; nothing was replaced.')
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
            image.unlink()
    for target in (Path('/efi/EFI/Emaki/grubx64.efi'), Path('/efi/EFI/BOOT/BOOTX64.EFI'),
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
    for directory in Path('/efi').glob('.emaki-stage-*'):
        if re.fullmatch(r'\.emaki-stage-[0-9a-f]{32}', directory.name) and not directory.is_symlink():
            shutil.rmtree(directory)
    if Path('/efi').exists():
        sync_directory(Path('/efi'))


def cleanup():
    if Path('/efi/EFI/Emaki/boot-intent.json').exists():
        return
    path = Path('/efi/EFI/Emaki/boot-state.json')
    state = json.loads(regular(path)) if path.exists() else {'good': None, 'newest': None}
    require(isinstance(state, dict) and {'good', 'newest'} <= state.keys(),
            'The boot generation state is invalid; cleanup stopped.')
    prune(state)


def mark_good(identity):
    recover(identity)
    cleanup()
    vendor, fallback = Path('/efi/EFI/Emaki/grubx64.efi'), Path('/efi/EFI/BOOT/BOOTX64.EFI')
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
    updated = regular(fallback) != image
    try:
        for target in (vendor, fallback):
            if regular(target) != image:
                room(Path('/efi'), len(image) * 2, 32 * MIB)
                transaction.add(target, image)
        trial = trial_state()
        if trial.get('emaki_candidate') == tag and trial.get('emaki_trial') != '0':
            trial['emaki_trial'] = '0'
            transaction.add(Path('/efi/EFI/Emaki/trial.env'), trial_block(trial))
        new_state = {**state, 'good': tag, 'fallback_sha256': digest(image)}
        if new_state != json.loads(regular(Path('/efi/EFI/Emaki/boot-state.json'))):
            transaction.add(Path('/efi/EFI/Emaki/boot-state.json'), json.dumps(new_state) + '\n')
        if transaction.entries:
            transaction.publish()
        if updated:
            print('The fallback loader was updated after a completed boot.')
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
    recover(identity)
    cleanup()
    try:
        _refresh(identity)
    finally:
        cleanup()


def _refresh(identity):
    vendor = Path('/efi/EFI/Emaki/grubx64.efi')
    fallback = Path('/efi/EFI/BOOT/BOOTX64.EFI')
    previous = regular(vendor)
    require(previous[:2] == b'MZ', 'The Emaki EFI loader is not recognized.')
    state = read_state(vendor, fallback)
    esp_space = os.statvfs(Path('/efi'))
    if esp_space.f_blocks * esp_space.f_frsize < 256 * MIB:
        raise Kept('The EFI system partition is smaller than 256 MiB; the existing boot loaders were kept.')
    defaults = migrate_defaults(regular(Path('/etc/default/grub')).decode(),
                                regular(Path('/usr/share/emaki/grub/defaults.cfg')).decode())
    signature = input_signature(defaults)
    record = manifest(state)
    unchanged = record.get('inputs_sha256') == signature and all(
        record.get(key) == identity[key] for key in ('uuid', 'esp_uuid', 'fsroot', 'luks'))
    if unchanged:
        failed = failed_trial(state)
        refresh_menu(identity, state)
        print(TRIAL_FAILED if failed else 'The boot menu was updated.')
        return
    require(not Path('/boot/grub').is_symlink(), 'A customized /boot/grub symbolic link needs manual review.')
    kernels = live_kernels()
    missing = [name for name in IMAGE_MODULES if not (MODULES / (name + '.mod')).is_file()]
    require(not missing, 'Required GRUB modules are missing: ' + ', '.join(missing))
    room(Path('/efi'), len(previous) * 3, 32 * MIB)
    room(Path('/boot'), tree_size(Path('/boot')) + tree_size(MODULES) * 3, 64 * MIB)
    tag = uuid.uuid4().hex
    generation = Path('/boot/emaki') / tag
    generation.mkdir(parents=True, mode=0o700)
    esp_stage = Path('/efi') / ('.emaki-stage-' + tag)
    esp_stage.mkdir(mode=0o700)
    transaction = Transaction(tag, Path('/efi/EFI/Emaki/boot-intent.json'))
    try:
        write(generation / 'defaults', defaults)
        btrfs_config = Path('/etc/default/grub-btrfs/config')
        if btrfs_config.is_file():
            write(generation / 'btrfs-config', boot.grub_btrfs_config(regular(btrfs_config).decode()))
        command = ['grub-install', '--target=x86_64-efi', '--efi-directory=' + str(esp_stage),
                   '--boot-directory=' + str(generation), '--bootloader-id=Emaki', '--no-nvram']
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
            regular(Path('/usr/share/emaki/grub/unlock-24.pf2')))
        write(platform / 'emaki-early.cfg', early)
        write(platform / 'emaki-early.tar', memdisk)
        image_path = platform / 'emaki-early.efi'
        run(['grub-mkimage', '--directory=' + str(MODULES), '--format=x86_64-efi',
             '--compression=none', '--memdisk=' + str(platform / 'emaki-early.tar'),
             '--prefix=' + prefix, '--config=' + str(platform / 'emaki-early.cfg'),
             '--output=' + str(image_path), *IMAGE_MODULES])
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
        text = trial_menu(tag, identity['esp_uuid'], text)
        verify_menu(text, identity, generation, kernels)
        atomic(config, text)
        run(['grub-script-check', config])
        snapshots = generation / 'grub/grub-btrfs.cfg'
        if snapshots.exists():
            run(['grub-script-check', snapshots])
        # Flush the entire generation, including grub-install's copies, before
        # any EFI rename. The two filesystems cannot share an atomic rename.
        run(['sync', '-f', generation])
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


def main(argv=None):
    global LOG
    parser = argparse.ArgumentParser(description='Safely refresh the installed Emaki boot loader and menu.')
    parser.add_argument('--hook', action='store_true')
    parser.add_argument('--retry', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--sleep-phase', choices=('pre', 'post'), help=argparse.SUPPRESS)
    parser.add_argument('--mark-good', action='store_true', help='promote the successfully booted loader')
    parser.add_argument('--check', action='store_true', help='check the installed disk identity without writing')
    parser.add_argument('--generate', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    previous_sigterm = None
    try:
        if (Path('/.emaki-install-incomplete').exists() or Path('/run/archiso').exists()
                or subprocess.run(['systemd-detect-virt', '--chroot', '--quiet'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0):
            return 0
        require(os.geteuid() == 0, 'Run emaki-boot-refresh as root on the installed system.')
        if args.retry or args.mark_good:
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
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if args.sleep_phase else fcntl.LOCK_NB))
            except BlockingIOError:
                if args.retry:
                    LOG.write('Another boot loader operation is running; retry at the next boot.\n')
                    return 0
                raise
            if args.sleep_phase:
                sleep_trial(args.sleep_phase)
                return 0
            if not args.mark_good:
                return refresh_attempt(args.retry)
            identity = discover()
            if identity is None:
                return 0
            with pause_snapshots():
                mark_good(identity)
        return 0
    except Kept as error:
        if LOG is not None:
            LOG.write(str(error) + '\n')
        print(str(error))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        if LOG is not None:
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
