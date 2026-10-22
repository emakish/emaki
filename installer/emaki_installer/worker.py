# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ordered installation phases. All mutations start only after confirmation."""
from .i18n import _ as translate
from .i18n import _, N_, LOCALES
import contextlib
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

from . import __version__
from .arch_backend import Backend, offline_config
from . import inventory as alongside
from .constants import (DESKTOP_APPS, GRUB_VISIBLE_FONT, LOG, MARKER, PACKAGES, PHASES, RICH_APPS,
                        SNAPSHOT_PACKAGES, TARGET, TEST_PACKAGES, WORK)
from .errors import Code, InstallError, require
from .update_errors import details as update_error_details
from .copy_progress import CopyProgress
from .boot import (boot_framebuffer, firmware_boot_order, grub_loader_payload, menu_font, menu_font_fallback,
                   menu_layout, menu_theme, quiet_linux_entries, secure_boot_fonts, secure_boot_menu,
                   windows_bootnext_menu)
from . import grub_screen, secureboot
from .clock import ensure_clock
from .media import discover_repo, package_source
from .network_profiles import WifiNotCopied, capture_wifi, choose_wifi, install_wifi
from .planner import ESP_SECURE_BOOT_ROOM, make_plan, probed_removable, removable_fallback, shared_esp
from .render import (GRUB_EARLY_MODULES, console_keymap,
                     grub_btrfs_package_config, grub_defaults, grub_early_config, grub_image_carries, grub_prefix,
                     grub_unlock_memdisk, mkinitcpio_machine_config, mkinitcpio_package_preset, niri_config,
                     managed_niri_config, home_files_manifest, kept_windows_esp,
                     normalize_fstab, snapper_config, vconsole_conf, verify_grub, wireless_regdom)
from .runtime import Runner, TargetFiles, cleanup, processes_under, safe_log

# Seconds for the optional update's downloads: a captive portal or a dead mirror
# must not hold the installation. Installing is never limited (never cut in half).
UPDATE_TIMEOUT = 900
# Ten HTTPS probes plus the mirror-status request must not hold up installation.
MIRROR_TIMEOUT = 60
# The update's pacman configuration without [emaki], used only when that repository
# alone cannot be reached. arch-chroot bind-mounts the live /run onto the target's
# /run, so the file has the same path inside the chroot and never reaches the target.
UPDATE_CONFIG = 'update-pacman.conf'
# The update's pacman inside the chroot. The snapshot phase has made the root snapper
# config, and snap-pac would snapshot / around each update transaction while the live
# root still carries the marker; a rollback to such a snapshot brings the marker back.
# Snapshot 1 is already the state before the update. snap-pac 3.0.1 skips on this
# variable, and pacman hands its environment to the hooks (libalpm _alpm_run_chroot).
UPDATE_PACMAN = ['env', 'SNAP_PAC_SKIP=y', 'pacman']
# The update's downloads. Through a pipe pacman 7.1 prints ' <file> downloading...' without
# flushing (src/pacman/callback.c dload_init_event; util.c colon_printf does flush), so the
# lines arrived in one burst at the end (VM walk run E). Line-buffered, each arrives as its
# download starts (measured on the host with the same pacman, 7.1.0.r9.g54d9411-2).
DOWNLOAD_PACMAN = ['env', 'SNAP_PAC_SKIP=y', 'stdbuf', '-oL', 'pacman']
# emaki-config's snapshot boot hook, as named inside its archive; a btrfs image runs it.
SNAPSHOT_HOOK = ('usr/lib/initcpio/hooks/emaki-snapshot-fstab', 'usr/lib/initcpio/install/emaki-snapshot-fstab')
# Completion advice never asks for a refresh-only or individual package update.
UPGRADE_FIRST = (N_('Emaki needs one full update before you install apps; when online, use the terminal to update the whole system [pacman].'))
PARTIAL_UPDATE = (N_('The online update did not finish; use the terminal to update the whole system before installing apps [pacman].'))
WIFI_NOT_COPIED = N_('Wi-Fi was not copied; join it again after restarting.')


def process_name(pid):
    try:
        return Path(f'/proc/{pid}/comm').read_text().strip()
    except OSError:
        return ''


def regular_id(value):
    """A regular account's UID/GID: UID_MIN..UID_MAX (GID_*) of shadow's /etc/login.defs."""
    return 1000 <= int(value) <= 60000


def emaki_unreachable(output):
    """True when the [emaki] database is the only file pacman failed to fetch while syncing.

    Matches pacman 7.1 (lib/libalpm/dload.c, src/pacman/util.c sync_syncdbs); the
    Runner's captured output has no line breaks.
    """
    failed = set(re.findall(r"error: failed retrieving file '([^']+)' from ", output))
    return failed == {'emaki.db'} and 'error: failed to synchronize all databases (' in output


def without_emaki(text):
    output, skip = [], False
    for line in text.splitlines():
        if re.match(r'^\s*\[', line):
            skip = line.strip() == '[emaki]'
        if not skip:
            output.append(line)
    return '\n'.join(output).rstrip() + '\n'


EMAKI_LOADER = r'\EFI\Emaki\grubx64.efi'
# The signed image's own modules (the boot refresh builds the same), before secureboot's list.
SIGNED_IMAGE_BASE = (*GRUB_EARLY_MODULES, 'search_fs_uuid')
# A kept (manual) ESP with Secure Boot: checked before the first disk write.
ESP_UNCHECKED = (N_("The free space Secure Boot updates need on the disk's startup partition could not be checked."))
# Secure Boot: the firmware entries around the boot slots (R9S9 #5).
FIRMWARE_UNREADABLE = N_('The firmware startup entries could not be read, so the startup copies were not replaced.')
ESP_UNIDENTIFIED = N_('The startup partition could not be identified, so the startup copies were not replaced.')
SLOT_STILL_STARTED = (N_('The firmware still starts an earlier Emaki startup copy on this disk, so the startup copies were not replaced.'))
SLOT_B_STILL_STARTED = N_('A firmware startup entry still points to a startup copy removed by this installation.')
SLOT_A_NOT_FIRST = N_('Emaki could not verify that the firmware starts the new installation first.')
REVIEW_BOUNDS = (N_('WARNING: plan review could not read the boot loader packages, so it checks the startup '
                 'partition by lower bounds; the exact check runs before the first disk write: '))
# Next to Windows: Windows' ESP is read whole before the first disk write and after the bootloader phase.
WINDOWS_ESP_UNREADABLE = N_("Windows' startup partition could not be read, so Emaki stopped before changing the disk.")
# A manual layout's kept /efi gained or lost Windows' files between the review and a recheck.
SHARED_ESP_CHANGED = N_("The startup partition's contents changed after the review. The disk was not changed; start a new review.")


def device_sha256(path):
    """sha256 of a whole block device, opened read-only (O_RDONLY): Windows' ESP next to Windows."""
    digest = hashlib.sha256()
    with open(path, 'rb', buffering=0) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def same_shared_esp(fresh, plan):
    """Whether a fresh plan keeps the reviewed plan's shared-layout choice (render.kept_windows_esp).

    The fingerprint leaves out the kept ESP's EFI/Microsoft evidence, and settings() writes the
    shared record, the read-only /efi row and the drop-ins from the reviewed plan: installing after
    that evidence changed would leave a record the package hook refuses, or none where it needs one.
    """
    old, new = kept_windows_esp(plan), kept_windows_esp(fresh)
    if old is None or new is None:
        return old is new
    return (old.path, old.number) == (new.path, new.number)


def windows_esp(plan):
    """Windows' ESP from the reviewed disk (alongside: the planner admitted exactly one)."""
    return next(part for part in plan.disk['partitions'] if part['esp'])


def os_prober_possible(plan):
    """Whether the installation may list Windows through os-prober (Worker.os_prober): next to
    Windows, or a manual layout that keeps its /efi, which shared_layout() decides only once that
    ESP is mounted. The preflight resolves the packages before the first disk write, so it proves
    os-prober and ntfs-3g for this superset; copy_packages installs them only on the decision."""
    if plan.config['mode'] == 'alongside':
        return True
    esp = next((p for p in plan.partitions if p.esp and p.mountpoint == '/efi'), None)
    return esp is not None and not esp.format and not esp.create


def secure_boot_extra(encrypted):
    """What an encrypted root adds to the signed image's memdisk (grub_unlock_memdisk): the
    visible font, the unlock script and the pictures; None without encryption."""
    if not encrypted:
        return None
    require(GRUB_VISIBLE_FONT.is_file(), Code.BOOT_VERIFY, _('The GRUB unlock-screen font is missing from this ISO.'))
    return {'visible.pf2': GRUB_VISIBLE_FONT.stat().st_size, 'unlock.cfg': 16384,
            **{name: len(data) for name, data in grub_screen.assets().items() if not name.startswith('checking-')}}


def review_esp_need(root=Path('/'), log=lambda line: None):
    """Plan review's Secure Boot need for a kept ESP: need(cluster, removable, encrypted) is
    secure_boot_esp_need, the worker's final rule with its modules and unlock files, applied to
    the boot loader packages of the installation USB's repository (secureboot.repo_archives).
    For an unchanged USB those are the archives the worker verifies before the first disk write,
    so both gates count the same bytes. When they cannot be read (no repository on the USB: the
    packages are downloaded only after Install), it logs why and returns None; plan review then
    uses the lower bounds (planner.plan_esp_need) and the worker's check stays the final gate."""
    def need(cluster, removable, encrypted):
        try:
            repo = discover_repo(root)
            if repo is None:
                raise secureboot.SecureBootError(_('the installation USB holds no package repository.'))
            return secureboot.secure_boot_esp_need(
                secureboot.repo_archives(repo), cluster, secureboot.signed_image_modules(SIGNED_IMAGE_BASE),
                secure_boot_extra(encrypted), removable=removable)
        except (secureboot.SecureBootError, InstallError, OSError) as error:
            log(translate(REVIEW_BOUNDS) + (error.message if isinstance(error, InstallError) else str(error)))
            return None
    return need


# One installed build per line of `dkms status`: dkms 3.x prints
# 'nvidia/580.82.09, 6.17.1-arch1-1, x86_64: installed', dkms 2.x 'nvidia, 580.82.09, ...';
# a note in parentheses may follow. 'installed-weak' is another kernel's build, not rebuilt.
_DKMS_NAME = r'[A-Za-z0-9][A-Za-z0-9_.+~-]*'
DKMS_INSTALLED = re.compile(rf'^({_DKMS_NAME})(?:/|, )({_DKMS_NAME}), ({_DKMS_NAME}), ([A-Za-z0-9_]+): installed(?:\s|$)')
KERNEL_MODULE = re.compile(r'\.ko(?:\.(?:gz|xz|zst))?$')


def dkms_installed(text):
    """(module, version, kernel, arch) of each installed DKMS build in `dkms status` output."""
    return [match.groups() for match in map(DKMS_INSTALLED.match, text.splitlines())
            if match and '..' not in match.group(0)]


# FAT32 needs at least 65,525 data clusters; Microsoft's FAT specification (1.03, p.15) asks
# to stay at least 16 clusters away from that limit. Linux vfat's statfs reports the data
# clusters as f_blocks (fs/fat/inode.c fat_statfs: max_cluster - FAT_START_ENT).
FAT32_MIN_CLUSTERS = 65525 + 16
ESP_NOT_FAT32 = (N_('The new startup partition did not get a valid FAT32 file system, so Emaki stopped before writing its boot files.'))


def fat32_esp(path):
    """False when the mounted file system is unreadable or has too few FAT32 data clusters."""
    try:
        return os.statvfs(path).f_blocks >= FAT32_MIN_CLUSTERS
    except OSError:
        return False


def windows_kept(plan, machine):
    """True when a Windows installation is left on any disk after this plan.

    The evidence is the reviewed inventory's: an ESP with Microsoft's loader or an NTFS
    Windows system (os_hint 'windows'), or a BitLocker volume. Erase destroys only the
    target disk; a manual layout destroys only the partitions it formats or deletes.
    """
    mode = plan.config['mode']
    if mode == 'alongside':
        return True
    formatted = {p.path for p in plan.partitions if p.format and p.path and not p.create}
    formatted |= {p['path'] for p in (plan.edits.deleted if plan.edits else [])}
    for disk in (machine or {}).get('disks', []):
        target = disk.get('id') == plan.disk.get('id')
        if target and mode == 'erase':
            continue
        for part in disk.get('partitions', []):
            if (part.get('os_hint') == 'windows' or part.get('fs') == 'BitLocker') and not (
                    target and part.get('path') in formatted):
                return True
    return False


def boot_order(listing):
    """Return an order only when every numbered entry is present and structurally parsed."""
    return firmware_boot_order(listing)


def emaki_boot_entry(listing, partuuid, *, active=True, loader=EMAKI_LOADER):
    """The first entry of the requested active state for Emaki's loader on this ESP.

    efibootmgr 18 prints the path bare after HD(...)/, older versions inside
    File(...); some firmware puts hardware nodes first. With Secure Boot the loader
    is shim in boot slot a (secureboot.slot_entry('a'))."""
    loader = re.escape(loader)
    hardware = r'(?:(?!(?:HD|File)\()[A-Za-z][A-Za-z0-9]*\([^()\t\n]*\)/)*'
    state = r'\*' if active else ' '
    pattern = (rf'^Boot([0-9A-Fa-f]{{4}}){state} [^\t\n]*\t{hardware}'
               rf'HD\([1-9][0-9]*,GPT,{re.escape(partuuid)},0x[0-9a-f]+,0x[0-9a-f]+\)/'
               rf'(?:File\({loader}\)|{loader})$')
    found = re.search(pattern, listing, re.M | re.I)
    return found.group(1).upper() if found else None


class DownloadCount:
    """What pacman says while it downloads, through a pipe with LC_ALL=C (pacman 7.1).

    ':: Synchronizing package databases...' starts the package lists, 'Packages (N) ...'
    names the transaction, then ' <file> downloading...' starts each package download
    (signatures are fetched without a line). report(done, total) gets (None, None) for the
    lists, then (0, N) and each started download.
    """
    def __init__(self, report):
        self.report, self.total, self.started = report, None, 0

    def line(self, text):
        packages = re.match(r'Packages \((\d+)\) ', text)
        if text == ':: Synchronizing package databases...':
            self.total, self.started = None, 0
            self.report(None, None)
        elif packages:
            self.total, self.started = int(packages[1]), 0
            self.report(0, self.total)
        elif self.total and self.started < self.total and re.fullmatch(r' \S+ downloading\.\.\.', text):
            self.started += 1
            self.report(self.started, self.total)


def enable_units(runner, target, units):
    for unit in units:
        # list-unit-files succeeds with no matches; systemctl enable does not.
        result = runner.chroot(['systemctl', 'list-unit-files', '--no-legend', unit], target)
        if not result.strip():
            runner.log(_('WARNING: optional unit {value1} is missing; skipped.').format(value1=unit))
            continue
        runner.chroot(['systemctl', 'enable', unit], target)


def software_packages(software='rich', *, btrfs=True, graphics=(), secure_boot=False):
    return (PACKAGES + DESKTOP_APPS + ['mkinitcpio'] + (SNAPSHOT_PACKAGES if btrfs else [])
            + (['emaki-apps'] + RICH_APPS if software == 'rich' else []) + list(graphics)
            + (list(secureboot.PACKAGES) if secure_boot else []))


def verify_graphics(runner, plan, target):
    # A failing PostTransaction hook does not necessarily make pacman fail.
    # Require usable module sets before progressing or reporting completion.
    if plan.graphics_packages:
        runner.chroot(['python3', '-I', '/usr/lib/emaki/nvidia/runtime.py', 'check'], target)


def preflight_repo(runner, test_mode=False, software='rich', *, os_prober=False, btrfs=True,
                   graphics=(), secure_boot=False, online=False, notice=lambda message: None, source_context=None,
                   source_ready=lambda source: None, root=Path("/")):
    """Resolve the complete transaction without touching the target or live DB."""
    repo = discover_repo(root) if source_context is not None else Path('/run/archiso/bootmnt/emaki/repo')
    ensure_clock(runner, online=online, repo=repo, notice=notice)
    try:
        packages = (software_packages(software, btrfs=btrfs, graphics=graphics, secure_boot=secure_boot)
                    + (TEST_PACKAGES if test_mode else []))
        if os_prober:
            packages += ['os-prober', 'ntfs-3g']
        if source_context is None:
            conf = offline_config()
        else:
            source = source_context.enter_context(package_source(
                runner, packages, online=online, root=root, notice=notice))
            source_ready(source)
            conf = source.validate()
        with tempfile.TemporaryDirectory(prefix='emaki-repo-preflight-') as temporary:
            work = Path(temporary)
            for name in ('db', 'cache'):
                (work / name).mkdir()
            options = ['--config', str(conf), '--dbpath', str(work / 'db'),
                       '--cachedir', str(work / 'cache'), '--logfile', str(work / 'pacman.log')]
            runner.run(['pacman', '-Sy', '--noconfirm', *options])
            output = runner.run(['pacman', '-Sp', '--print-format', '%l', *options, *packages])
            urls = [line.strip() for line in output.splitlines() if line.startswith('file://')]
            require(urls, Code.OFFLINE_REPO_INCOMPLETE, _('Offline package transaction is empty.'))
            archives = [Path(unquote(urlsplit(url).path)) for url in urls]
            for package in archives:
                require(package.is_file() and package.stat().st_size > 0
                        and Path(str(package) + '.sig').is_file(), Code.OFFLINE_REPO_INCOMPLETE,
                        _('Offline package or signature is missing: {value1}.').format(value1=package.name))
    except (InstallError, OSError) as exc:
        raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                           _('Offline repository preflight failed') +
                           (_(' (Rich software requires emaki-apps)') if software == 'rich' else '') +
                           _('; disk untouched: ') + str(exc)) from exc
    return archives


def verify_packages(runner, packages, progress=None):
    """Check every archive against its detached signature with the live keyring.

    pacstrap verifies the same files only after the disk has been rewritten.
    progress(checked, total) follows the check, which takes minutes from a slow USB stick.
    """
    for checked, package in enumerate(packages):
        if progress:
            progress(checked, len(packages))
        try:
            runner.run(['pacman-key', '--verify', str(package) + '.sig', str(package)])
        except InstallError as exc:
            raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                               _('Offline repository preflight failed; disk untouched: package signature verification failed: {value1}.').format(value1=package.name)) from exc
    if progress:
        progress(len(packages), len(packages))


def verify_account_name(runner, packages, login):
    """Check identities from the exact verified target package closure before erasing."""
    def inspect(package, *options):
        try:
            return runner.run(['bsdtar', *options], quiet=True)
        except InstallError as exc:
            message = (_('The login name check could not read a package ({value1}; bsdtar). The disk has not been changed.').format(value1=package.name))
            safe_log(runner.log, message + ' ' + exc.message)
            if exc.output:
                safe_log(runner.log, exc.output)
            raise InstallError(Code.OFFLINE_REPO_INCOMPLETE, message) from exc

    for package in packages:
        names = inspect(package, '-tf', str(package)).splitlines()
        for member in names:
            name = member.removeprefix('./')
            if name not in ('etc/passwd', 'etc/group') and not (
                    name.startswith('usr/lib/sysusers.d/') and name.endswith('.conf')):
                continue
            contents = inspect(package, '-xOf', str(package), member)
            for line in contents.splitlines():
                if name in ('etc/passwd', 'etc/group'):
                    reserved = line.split(':', 1)[0]
                else:
                    fields = line.split()
                    if len(fields) < 2 or fields[0] not in ('u', 'u!', 'g', 'm'):
                        continue
                    identities = fields[1:3] if fields[0] == 'm' else fields[1:2]
                    if login not in identities:
                        continue
                    reserved = login
                require(login != reserved, Code.LOGIN_NAME_RESERVED,
                        _('This login name belongs to the system. Choose another name; the disk has not been changed.'))

    safe_log(runner.log, _('Login name checked against {value1} packages.').format(value1=len(packages)))

def verify_snapshot_hook(runner, packages):
    """The resolved emaki-config archive carries the snapshot boot hook a btrfs image needs.

    An ISO assembled with an older emaki-config of the same version has none, and the
    bootloader phase would find that out only after the disk has been rewritten. The archive
    is named like every pacman package file, <name>-<pkgver>-<pkgrel>-<arch>.pkg.tar.*;
    bsdtar (libarchive, a pacman dependency) lists only the named members and fails when
    one is missing.
    """
    failed = _('Offline repository preflight failed; disk untouched: ')
    archives = [package for package in packages if package.name.rsplit('-', 3)[0] == 'emaki-config']
    require(len(archives) == 1, Code.OFFLINE_REPO_INCOMPLETE,
            failed + _('no single emaki-config archive in the resolved package set.'))
    try:
        listed = runner.run(['bsdtar', '-tf', str(archives[0]), *SNAPSHOT_HOOK]).splitlines()
    except InstallError as exc:
        if 'Not found in archive' not in exc.output:
            raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                               failed + _('cannot list {value1}: {value2}').format(value1=archives[0].name, value2=exc.message)) from exc
        listed = []
    require(all(member in listed for member in SNAPSHOT_HOOK), Code.OFFLINE_REPO_INCOMPLETE,
            failed + _('{value1} has no snapshot boot hook (/{value2}, /{value3}).').format(value1=archives[0].name, value2=SNAPSHOT_HOOK[0], value3=SNAPSHOT_HOOK[1]))


class Worker:
    def __init__(self, api, inventory, redactor, emit, log, *, test_mode=False,
                 target=TARGET, backend_factory=Backend, runner_factory=Runner, skip_update=None):
        self.api, self.inventory, self.redactor = api, inventory, redactor
        self.emit, self.log, self.test_mode = emit, log, test_mode
        self.target, self.files = target, TargetFiles(target)
        self.backend_factory, self.runner_factory = backend_factory, runner_factory
        self.phase = 'prepare_disk'
        self.completed = 0
        self.phase_pct = 0
        self.skipped_phases = []
        self.last_progress = 0
        self.disk_changed = False
        self.wifi_profile = None
        self.update_attempted = False
        self.skip_update = skip_update if skip_update is not None else threading.Event()
        # Track synchronized package lists separately from a completed full upgrade.
        self.online = False
        self.synced = False
        self.upgraded = False
        # What the person must know after a successful installation; sent with `done`.
        self.warnings = []
        # The one-time MokManager password, shown on the final screen with Secure Boot on.
        self.mok_password = None
        # Next to Windows: sha256 of Windows' whole ESP, read before the first disk write.
        self.windows_esp_sha256 = None
        # Whether /efi gets the shared layout's policy: decided once, from the mounted target ESP
        # (shared_layout), for os-prober, the fstab row, the record and the drop-ins alike.
        self.shared_esp_layout = None
        # The inventory probed when the run started: every disk, for windows_kept().
        self.machine = None
        self.cleanup_required = False
        self.kept_home = None
        self.package_total = None
        self.installed = 0
        self.copy_progress = CopyProgress(self)

    def notice(self, message):
        self.warnings.append(message)
        self.log('NOTICE: ' + message)
        self.emit('progress', phase=self.phase, phase_pct=self.phase_pct,
                  total_pct=self.completed, skipped_phases=list(self.skipped_phases), indeterminate=True, notice='\n'.join(self.warnings))

    def progress(self, pct=None, *, package=None, line=None, command=None, command_done=None):
        if self.phase != 'copy_packages':
            return
        if command is not None:
            self.copy_progress.command(command)
        elif command_done is not None:
            self.copy_progress.command(command_done, done=True)
        elif line is not None:
            self.copy_progress.line(line)
        elif package is not None:
            self.copy_progress.line(f'installing {package}...')

    def activity(self, name, done=None, total=None):
        """What a long step of the current phase is doing, as a count for the window.

        name: 'signatures' (the preflight's checks) or 'downloads' (the update's, with no
        count while pacman downloads the package lists); None ends it, as does the phase's
        next state event. Signature counts are sent at most four times a second, except the
        first and the last; a download count with every line, since a download can take
        minutes and pacman already sends each such line to the log.
        """
        now = time.monotonic()
        if name == 'signatures' and done not in (0, total) and now - self.last_progress < 0.25:
            return
        self.last_progress = now
        self.emit('progress', phase=self.phase, phase_pct=self.phase_pct, total_pct=self.completed,
                  skipped_phases=list(self.skipped_phases), indeterminate=True, activity={'name': name, 'done': done, 'total': total} if name else None)

    def run(self, plan, cancelled):
        started = time.monotonic()
        self.plan, self.cancelled = plan, cancelled
        self.runner = self.runner_factory(self.log, self.redactor, self.progress)
        failure, success = None, False
        sources = contextlib.ExitStack()
        self.package_source = None
        try:
            # This is intentionally repeated after token validation: a USB can
            # disappear or another program can change the disk between plan and actual execution.
            current = self.inventory.probe()
            self.machine = current
            fresh = make_plan(plan.config, current, self.test_mode,
                              esp_need=getattr(self.inventory, 'esp_need', None))
            require(fresh.fingerprint == plan.fingerprint, Code.DISK_CHANGED,
                    _('Disk identity or partition layout changed after the review.'))
            require(same_shared_esp(fresh, plan), Code.DISK_CHANGED, translate(SHARED_ESP_CHANGED))
            require(fresh.swap_bytes == plan.swap_bytes, Code.DISK_CHANGED,
                    _('RAM size changed after the review; prepare a new installation plan.'))
            require(fresh.graphics_packages == plan.graphics_packages, Code.DISK_CHANGED,
                    _('Graphics hardware or driver availability changed; prepare a new installation plan.'))
            require(fresh.secure_boot == plan.secure_boot, Code.SECURE_BOOT,
                    _('The Secure Boot setting changed after the review; prepare a new installation plan.'))
            self.plan.disk['_vmd'] = fresh.disk.get('_vmd', False)
            self.online = current['network']['online']
            if self.test_mode:
                # Checked again in settings(); failing here leaves the disk untouched.
                keys = Path('/etc/emaki-test/authorized_keys')
                require(keys.is_file() and keys.stat().st_size > 0, Code.BAD_CONFIG,
                        _('Test mode requires /etc/emaki-test/authorized_keys.'))
            self.capture_live_wifi(plan.config.get('wifi_uuid'))
            packages = preflight_repo(self.runner, self.test_mode, plan.config.get('software', 'rich'),
                                      os_prober=os_prober_possible(plan), btrfs=plan.btrfs,
                                      graphics=plan.graphics_packages, secure_boot=bool(plan.secure_boot),
                                      online=self.online, notice=self.notice, source_context=sources,
                                      source_ready=lambda source: setattr(self, "package_source", source),
                                      root=getattr(self.inventory, "root", Path("/")))
            # Every byte that pacstrap will install is proven before the first disk write.
            verify_packages(self.runner, packages,
                            progress=lambda checked, total: self.activity('signatures', checked, total))
            if plan.btrfs:
                # The bootloader phase checks the installed hook again.
                verify_snapshot_hook(self.runner, packages)
            verify_account_name(self.runner, packages, plan.config['user']['login'])
            self.check_secure_boot_esp(packages, current)
            # The resolved closure is what the copy phase will announce (os-prober and ntfs-3g
            # at most: copy_packages adds them only on shared_layout(), os_prober_possible).
            self.package_total = len(packages) if isinstance(packages, list) and packages else None
            self.cleanup_required = True
            cleanup(self.runner, [self.target, WORK / 'btrfs-top'], lazy=True)
            self.backend = self.backend_factory(self.api, plan, self.runner, self.target)
            if self.package_source is not None:
                self.backend.package_source = self.package_source
            for phase in PHASES:
                self.phase, self.phase_pct = phase, 0
                if cancelled.is_set():
                    raise InstallError(Code.CANCELLED, _('Installation cancelled at a phase boundary; disk changes are not undone.'))
                skipped = ((phase == 'snapshot' and not plan.btrfs) or
                           (phase == 'update' and not (plan.config['online_update'] and self.online)))
                if skipped:
                    self.skipped_phases.append(phase)
                # Only copy_packages has a counter; the other phases show the busy indicator.
                counted = phase == 'copy_packages' and bool(self.package_total)
                self.emit('state', phase=phase, phase_pct=None if skipped else 0,
                          total_pct=self.completed, skipped_phases=list(self.skipped_phases), indeterminate=not skipped and not counted)
                if skipped:
                    self.log(f'{phase}: skipped.')
                else:
                    getattr(self, phase)()
                self.completed += PHASES[phase]
                self.emit('state', phase=phase, phase_pct=None if skipped else 100,
                          total_pct=self.completed, skipped_phases=list(self.skipped_phases), indeterminate=False)
            success = True
        except InstallError as exc:
            failure = exc
        except Exception as exc:
            # Do not print repr(config) or tracebacks containing local variables.
            failure = InstallError(Code.INTERNAL, _('Installation failed: {value1}: {value2}').format(value1=type(exc).__name__, value2=self.redactor.text(exc)))
        finally:
            try:
                problems = []
                if self.cleanup_required:
                    try:
                        # A pacman that outlived its stopped download kept its lock; by now
                        # every target process was stopped, and no later check would see it.
                        cleanup(self.runner, [self.target, WORK / 'btrfs-top'],
                                before_unmount=self.release_pacman_lock)
                    except Exception as exc:
                        problems.append(exc)
                    try:
                        # The LUKS mapping is closed even when an unmount failed.
                        if hasattr(self, 'backend') and hasattr(self.backend, 'devices'):
                            self.backend.devices.close()
                    except Exception as exc:
                        problems.append(exc)
                for problem in problems:
                    # A full log must not replace the cleanup result.
                    safe_log(self.log, _('Cleanup failed: ') + self.redactor.text(problem))
                if problems:
                    success = False
                    failure = InstallError(Code.CLEANUP_FAILED, _('Could not unmount all target filesystems; inspect the log before retrying.'))
            finally:
                # Drop the password as soon as the account operation/job has finished.
                plan.config['user']['password'] = ''
                plan.config['disk_password'] = ''
                self.wifi_profile = None
                try:
                    sources.close()
                except OSError as exc:
                    safe_log(self.log, _('Package cache cleanup failed: ') + self.redactor.text(exc))
        if failure:
            if failure.code == Code.CANCELLED:
                self.emit('cancel_ack', disk_changed=self.disk_changed)
            # A fresh plan can retry after cleanup, even if the disk was changed.
            # A failed cleanup needs the log inspected first.
            retryable = (failure.code != Code.CLEANUP_FAILED and
                         (failure.retryable if plan.config['mode'] == 'alongside' else True))
            self.emit('error', code=failure.code.value, phase=self.phase,
                      message=self.redactor.text(failure.message), retryable=retryable,
                      log_path=str(LOG))
        elif success:
            self.emit('done', log_path=str(LOG), seconds=round(time.monotonic() - started, 2),
                      warnings=list(self.warnings), **self.secure_boot_done())

    def secure_boot_done(self):
        """The done page's MokManager steps: the one-time password and the ESP's label.

        esp_label is None on an ESP Emaki did not format (a kept manual ESP): its label is
        not Emaki's to name, so the page names no label. Next to Windows Emaki formats its own.
        """
        if not self.mok_password:
            return {}
        return {'secure_boot': {'password': self.mok_password, 'esp_label': self.plan.esp_label}}

    def capture_live_wifi(self, joined):
        """Choose and read the Wi-Fi profile before disk work; every step goes to the log.

        A link that dropped after the join still copies that network. A Network page join or
        saved Wi-Fi that ends up copying nothing warns instead of staying silent.
        """
        self.wifi_uuid = self.wifi_profile = None
        try:
            choice = choose_wifi(joined)
            safe_log(self.log, _('Wi-Fi: the live session has {value1} Wi-Fi profile(s); Network page join: {value2}.').format(value1=choice.saved, value2=joined or 'none'))
            if choice.uuid is None:
                safe_log(self.log, _('Wi-Fi: nothing chosen: {value1}.').format(value1=choice.reason))
                if choice.saved or joined:
                    self.wifi_copy_failed()
                return
            safe_log(self.log, _('Wi-Fi: chose {value1}: {value2}.').format(value1=choice.uuid, value2=choice.reason))
            self.wifi_uuid = choice.uuid
            self.wifi_profile = capture_wifi(choice.uuid, note=lambda line: safe_log(self.log, _('Wi-Fi: ') + line))
        except WifiNotCopied as exc:
            safe_log(self.log, _('Wi-Fi: not copied: {value1}').format(value1=exc.reason))
            self.wifi_copy_failed()
        except (InstallError, OSError) as exc:
            # The message may name a keyfile path; only the type goes to the log.
            safe_log(self.log, _('Wi-Fi: not copied: {value1}.').format(value1=type(exc).__name__))
            self.wifi_copy_failed()

    def wifi_copy_failed(self):
        self.wifi_profile = None
        if translate(WIFI_NOT_COPIED) not in self.warnings:
            self.warnings.append(translate(WIFI_NOT_COPIED))
            safe_log(self.log, translate(WIFI_NOT_COPIED))

    def prepare_disk(self):
        if self.plan.config['mode'] == 'alongside':
            alongside.require_verified()
        # Package verification can take minutes. Recheck every mode at the write
        # boundary, including encrypted-volume confirmations against fresh UUIDs.
        fresh = make_plan(self.plan.config, self.inventory.probe(), self.test_mode,
                          esp_need=getattr(self.inventory, 'esp_need', None))
        require(fresh.fingerprint == self.plan.fingerprint, Code.DISK_CHANGED,
                _('Windows shrink bounds, disk identity or startup partition state changed after review; disk untouched.')
                if self.plan.config['mode'] == 'alongside' else
                _('Disk identity or partition layout changed after the review.'))
        require(same_shared_esp(fresh, self.plan), Code.DISK_CHANGED, translate(SHARED_ESP_CHANGED))
        require(fresh.swap_bytes == self.plan.swap_bytes, Code.DISK_CHANGED,
                _('RAM size changed after the review; prepare a new installation plan.'))
        require(fresh.graphics_packages == self.plan.graphics_packages, Code.DISK_CHANGED,
                _('Graphics hardware or driver availability changed; prepare a new installation plan.'))
        if self.plan.config['mode'] == 'alongside':
            # The proof that Windows' ESP stays byte-identical starts here, before any write.
            try:
                self.windows_esp_sha256 = device_sha256(windows_esp(self.plan)['path'])
            except OSError as exc:
                self.log(_("Windows' startup partition read failed: ") + type(exc).__name__)
                raise InstallError(Code.UNSAFE_DISK, translate(WINDOWS_ESP_UNREADABLE)) from exc
            self.backend.devices.changed = self.mark_disk_changed
        else:
            self.mark_disk_changed()
        try:
            self.backend.prepare()
        finally:
            devices = getattr(self.backend, 'devices', None)
            if getattr(devices, 'pending_reread', None):
                pending = getattr(self.inventory, 'pending_partition_refresh', {})
                pending[self.plan.disk['path']] = devices.recover_edits
                self.inventory.pending_partition_refresh = pending
        self.files.write(MARKER, f'Emaki {__version__} installation in progress\n', 0o600)

    def mark_disk_changed(self):
        self.disk_changed = True

    def copy_packages(self):
        layouts = self.plan.config['layouts']
        locale = self.api.locale.LocaleConfiguration(console_keymap(layouts[0]), self.plan.config.get('language', 'en_US.UTF-8').split('.')[0], 'UTF-8')
        self.backend.minimal(locale)
        # The minimal install leaves the console map alone in this file. The whole text must
        # be there before the first mkinitcpio run (bootloader phase) reads it for the image.
        font = getattr(self.backend, 'console_font', None)
        self.files.write('/etc/vconsole.conf', vconsole_conf(layouts, font))
        packages = software_packages(self.plan.config.get('software', 'rich'), btrfs=self.plan.btrfs,
                                     graphics=self.plan.graphics_packages, secure_boot=bool(self.plan.secure_boot))
        if self.os_prober():
            packages += ['os-prober', 'ntfs-3g']
        self.backend.instance.pacman.strap(packages)
        if self.plan.graphics_packages:
            verify_graphics(self.runner, self.plan, self.target)
        self.populate_keyring()

    def populate_keyring(self):
        try:
            self.runner.chroot(['pacman-key', '--init'], self.target)
            self.runner.chroot(['pacman-key', '--populate', 'archlinux', 'emaki'], self.target)
        finally:
            # GnuPG helpers retain a chroot root after pacman-key exits.
            self.runner.chroot(['gpgconf', '--homedir', '/etc/pacman.d/gnupg', '--kill', 'all'],
                               self.target, check=False)

    def bootloader(self):
        # An ESP Emaki formatted (erase, or its own one next to Windows) must really be FAT32:
        # a too small or 4Kn-limited one would be FAT16, which not every firmware starts from.
        esp = next((p for p in self.plan.partitions if p.esp and p.mountpoint == '/efi'), None)
        if esp is not None and esp.format:
            require(fat32_esp(self.files.path('/efi')), Code.BOOT_VERIFY, translate(ESP_NOT_FAT32))
        root = self.plan.root
        if self.plan.encrypted:
            self.files.mkdir('/etc/cryptsetup-keys.d', 0o700).chmod(0o700)
            key = '/etc/cryptsetup-keys.d/emaki-root.key'
            self.files.write(key, os.urandom(64), 0o600)
            # 64 random bytes need no slow KDF: PBKDF2 at cryptsetup's minimum keeps the
            # wrong-passphrase cost in GRUB and the keyfile unlock to one Argon2id run.
            self.runner.run(['cryptsetup', 'luksAddKey', '--pbkdf', 'pbkdf2',
                             '--pbkdf-force-iterations', '1000',
                             '--key-file', '-', root.path, str(self.files.path(key))],
                            input=self.plan.disk_password, secret=True)
            # The partition editor's other encrypted partitions (/home, data, swap) open at startup
            # from systemd's crypttab with their own key, kept inside the encrypted root; the disk
            # password stays in their first slot for recovery.
            crypttab = []
            for part in (p for p in self.plan.partitions if p.encrypt):
                key = f'/etc/cryptsetup-keys.d/{part.mapper_name}.key'
                self.files.write(key, os.urandom(64), 0o600)
                self.runner.run(['cryptsetup', 'luksAddKey', '--pbkdf', 'pbkdf2',
                                 '--pbkdf-force-iterations', '1000',
                                 '--key-file', '-', part.path, str(self.files.path(key))],
                                input=self.plan.disk_password, secret=True)
                crypttab.append(f'{part.mapper_name}\tUUID={part.luks_uuid}\t{key}\tluks\n')
            if crypttab:
                existing = self.files.read('/etc/crypttab') if self.files.path('/etc/crypttab').is_file() else ''
                self.files.write('/etc/crypttab', existing + ''.join(crypttab), 0o600)
            self.plan.config['disk_password'] = ''
            self.files.path('/boot').chmod(0o700)
        offset = self.create_swap() if self.plan.swap_bytes else None
        self.files.write('/etc/mkinitcpio.conf', mkinitcpio_machine_config(
            self.plan.btrfs, self.plan.encrypted, bool(self.plan.swap_bytes), vmd=self.plan.disk.get("_vmd", False)))
        if self.plan.btrfs:
            # emaki-config (copy_packages) owns the snapshot hook, so updates reach it. Nothing
            # goes to /etc/initcpio: mkinitcpio reads it first, so a copy there would hide the
            # packaged one. The preflight has read the archive; this checks what was installed.
            for directory in ('hooks', 'install'):
                require(self.files.path(f'/usr/lib/initcpio/{directory}/emaki-snapshot-fstab').is_file(),
                        Code.BOOT_VERIFY, _('emaki-config did not install the snapshot boot hook.'))
        for kernel in ('linux', 'linux-lts'):
            require(self.files.path(f'/etc/mkinitcpio.d/{kernel}.preset').is_file(),
                    Code.BOOT_VERIFY, _('Missing {value1} mkinitcpio preset.').format(value1=kernel))
            self.files.write(f'/etc/mkinitcpio.d/{kernel}.preset', mkinitcpio_package_preset(kernel))
        self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
        for kernel in ('linux', 'linux-lts'):
            for name in (f'vmlinuz-{kernel}', f'initramfs-{kernel}.img'):
                p = self.files.path('/boot/' + name)
                require(p.is_file() and p.stat().st_size > 0, Code.BOOT_VERIFY, _('Missing /boot/{value1}.').format(value1=name))
        self.files.write('/etc/default/grub', grub_defaults(
            self.os_prober(), root.luks_uuid,
            root.uuid if self.plan.swap_bytes else None, offset))
        # Recognize a previous Emaki copy before updating the vendor binary.
        # FAT is case insensitive; Path works on the actual mounted filesystem.
        fallback = self.files.path('/efi/EFI/BOOT/BOOTX64.EFI')
        vendor = self.files.path('/efi/EFI/Emaki/grubx64.efi')
        own_fallback = (fallback.is_file() and vendor.is_file()
                        and fallback.read_bytes() == vendor.read_bytes())
        command = ['grub-install', '--target=x86_64-efi', '--efi-directory=/efi',
                   '--boot-directory=/boot', '--bootloader-id=Emaki']
        if self.plan.encrypted:
            command += ['--modules=part_gpt cryptodisk luks2 argon2 gcry_rijndael gcry_sha256 pbkdf2']
        nvram_ok = vendor_ok = True
        try:
            self.runner.chroot(command, self.target)
        except InstallError:
            nvram_ok = False
            self.log(_('WARNING: GRUB NVRAM registration failed; retrying vendor files without NVRAM.'))
            try:
                self.runner.chroot([*command, '--no-nvram'], self.target)
            except InstallError:
                vendor_ok = False
                self.log(_('WARNING: vendor GRUB retry failed; attempting the removable loader.'))
        removable_ok = False
        sidecars = [self.files.path(f'/efi/{secureboot.REMOVABLE_DIR}/{name}')
                    for name in (secureboot.GRUB, secureboot.MOK_MANAGER)]
        # check_secure_boot_esp budgeted EFI/BOOT by the same rule (probed_removable). An ESP this
        # install formatted (erase, Emaki's own next to Windows) is empty: EFI/BOOT is written there.
        esp = next(part for part in self.plan.partitions if part.esp and part.mountpoint == '/efi')
        probed = next((part for part in self.plan.disk['partitions'] if part.get('path') == esp.path), {})
        shared = not esp.format and shared_esp(probed)
        removable = removable_fallback(bool(self.plan.secure_boot), fallback.exists(),
                                       own_fallback, any(path.exists() for path in sidecars), shared=shared)
        if removable:
            try:
                self.runner.chroot([*command, '--removable', '--no-nvram'], self.target)
                removable_ok = True
            except InstallError:
                self.log(_('WARNING: removable GRUB installation failed.'))
        else:
            self.log(_("WARNING: preserving another system's startup files; removable GRUB copy skipped.")
                     if shared else _('WARNING: preserving existing fallback EFI boot file; removable GRUB copy skipped.'))
        require(nvram_ok or removable_ok, Code.BOOT_VERIFY,
                _('GRUB NVRAM registration failed and no removable loader could be installed.'))
        if self.plan.secure_boot:
            # The signed image carries the unlock screen too (grub_loader_payload). Slot a is
            # complete on the ESP before a firmware entry names it.
            self.secure_boot_loaders(vendor_ok, removable_ok)
        first = self.boot_entry_first() if nvram_ok else False
        if self.plan.secure_boot:
            self.verify_slot_entries(first)
        if not nvram_ok and removable_ok:
            self.notice(_('The firmware could not save a startup entry (NVRAM/efibootmgr). Emaki installed the standard fallback loader (EFI/BOOT/BOOTX64.EFI). If needed, choose this disk in your firmware boot menu.'))
        if self.plan.encrypted and not self.plan.secure_boot:
            self.unlock_screen([name for name, written in (('/efi/EFI/Emaki/grubx64.efi', vendor_ok),
                                                          ('/efi/EFI/BOOT/BOOTX64.EFI', removable_ok)) if written])
        self.grub_config()
        if self.plan.config['mode'] == 'alongside':
            # After the last step that could reach it: os-prober mounts it read-only for GRUB's menu.
            self.check_windows_esp()
        self.write_fstab()

    def write_fstab(self):
        """/etc/fstab from the mounted target, with shared_layout()'s decision."""
        text = self.runner.run(['genfstab', '-U', '-f', str(self.target), str(self.target)])
        fstab = normalize_fstab(text, self.plan, shared=self.shared_layout())
        if self.plan.swap_bytes:
            fstab += '/swap/swapfile\tnone\tswap\tdefaults,pri=10\t0 0\n'
        # A swap partition from the partition editor; zram keeps the higher priority.
        for part in (p for p in self.plan.partitions if p.swap):
            require(bool(part.uuid), Code.FSTAB_VERIFY, _('The swap partition could not be identified.'))
            source = f'/dev/mapper/{part.mapper_name}' if part.encrypt else f'UUID={part.uuid}'
            fstab += f'{source}\tnone\tswap\tdefaults,pri=10\t0 0\n'
        self.files.write('/etc/fstab', fstab)

    def secure_boot_loaders(self, vendor_ok, removable_ok):
        """Machine key, one signed self-contained GRUB, shim and MokManager on the ESP.

        Under Secure Boot GRUB refuses modules and fonts read from disk, so grub-install's
        image is replaced by one whose memdisk carries every module, the menu faces and,
        when encrypted, the unlock screen; the audited module list is built in, the SBAT
        data names grub.emaki, and a key made for this machine signs it. Microsoft-signed
        shim starts it once MokManager has enrolled the key (the final screen explains how).
        The kernels are signed here too, so the first snapshot already holds signed ones.
        """
        require(vendor_ok or removable_ok, Code.BOOT_VERIFY, _('No GRUB loader was written.'))
        run = self.target_run
        self.files.mkdir(str(secureboot.STATE), 0o700).chmod(0o700)
        secureboot.generate_key(run)
        # Before any later module build (the update's DKMS hook) and the rebuild below.
        self.files.write(str(secureboot.DKMS_CONFIG), secureboot.dkms_config())
        self.sign_dkms_modules()
        root = self.plan.root
        subvolume = next((name for name, mp in root.subvolumes.items() if mp == '/'), None)
        fsroot = '/' + subvolume if subvolume else ''
        platform = '/boot/grub/x86_64-efi'
        if root.luks_uuid:
            load_cfg = self.files.read(platform + '/load.cfg')
            require(GRUB_VISIBLE_FONT.is_file(), Code.BOOT_VERIFY, _('The GRUB unlock-screen font is missing from this ISO.'))
            font = GRUB_VISIBLE_FONT.read_bytes()
        else:
            require(isinstance(root.uuid, str) and re.fullmatch(r'[0-9a-fA-F-]{36}', root.uuid), Code.BOOT_VERIFY,
                    _('The system partition could not be identified.'))
            # As the boot refresh does: find the root by its UUID, whatever disk the ESP is on.
            load_cfg = f'search.fs_uuid {root.uuid} root\nset prefix=($root){fsroot}/boot/grub\n'
            font = b''
        prefix, early, memdisk = grub_loader_payload(self.files.path('/boot/grub'), None, fsroot, root.luks_uuid,
                                                     load_cfg, font, secure_boot_fonts(self.target))
        self.files.write(platform + '/emaki-secureboot.cfg', early)
        self.files.write(platform + '/emaki-secureboot.tar', memdisk)
        output = platform + '/emaki-secureboot.efi'
        secureboot.build_signed_grub(
            run, lambda path, text: self.files.write(str(path), text),
            ['grub-mkimage', '--directory=/usr/lib/grub/x86_64-efi', '--format=x86_64-efi', '--compression=none',
             '--memdisk=' + platform + '/emaki-secureboot.tar', '--prefix=' + prefix,
             '--config=' + platform + '/emaki-secureboot.cfg', '--output=' + output,
             *secureboot.signed_image_modules(SIGNED_IMAGE_BASE)],
            platform + '/emaki-sbat.csv', self.files.read(str(secureboot.SBAT_SOURCE)))
        image = self.files.path(output).read_bytes()
        require(image[:2] == b'MZ' and early in image and memdisk in image, Code.BOOT_VERIFY,
                _('The signed GRUB image does not carry its early config and memdisk.'))
        shim = {'shim_dir': self.files.path(str(secureboot.SHIM_DIR)), 'der': self.files.path(str(secureboot.DER))}
        esp = self.files.path('/efi')

        def check(path, data):
            inside = '/efi/' + path.relative_to(esp).as_posix()
            if path.name == secureboot.GRUB:
                require(secureboot.signed(run, inside), Code.BOOT_VERIFY,
                        _('{value1} does not verify with the machine key.').format(value1=inside))
            elif path.name in (secureboot.SHIM, secureboot.MOK_MANAGER):
                require(secureboot.pe_certificates_present(data), Code.BOOT_VERIFY, _('{value1} carries no signature.').format(value1=inside))

        # No firmware entry may start a slot while it is removed or rewritten (R9S9 #5).
        self.withdraw_slots()
        self.clear_slot(esp, 'b')
        # Slot a, manifest last: the loaders the firmware entry names and, for MokManager's
        # "Enroll key from disk", the certificate, on every path.
        try:
            secureboot.write_slot(esp, 'a', secureboot.slot_payload(image, **shim), generation=None, check=check)
        except (OSError, secureboot.SecureBootError) as error:
            self.log(_('The boot slot a could not be written: {value1}').format(value1=error))
            raise InstallError(Code.BOOT_VERIFY, _('The new startup copy could not be written. Check the installation log for details.')) from error
        loaders = {}
        if removable_ok:
            # The same bytes as slot a.
            loaders.update({f'/efi/{secureboot.REMOVABLE_DIR}/{name}': data
                            for name, data in secureboot.esp_files(True, **shim).items()})
            loaders[f'/efi/{secureboot.REMOVABLE_DIR}/{secureboot.GRUB}'] = image
        for name, data in loaders.items():
            self.files.write(name, data, 0o600)
            require(self.files.path(name).read_bytes() == data, Code.BOOT_VERIFY,
                    _('{value1} could not be read back from the startup partition.').format(value1=name))
        self.sign_boot_files()

    def esp_partuuid(self):
        esp = next(part for part in self.plan.partitions if part.esp)
        return (esp.partuuid or self.runner.run(['lsblk', '-dno', 'PARTUUID', esp.path])).strip().lower()

    def firmware(self):
        """efibootmgr -v parsed by secureboot.firmware_entries; a listing that cannot be read or
        trusted stops the step: nothing proves then that no entry starts a slot."""
        try:
            parsed = secureboot.firmware_entries(self.runner.run(['efibootmgr', '-v']))
        except InstallError as error:
            raise InstallError(Code.BOOT_VERIFY, translate(FIRMWARE_UNREADABLE)) from error
        require(parsed is not None, Code.BOOT_VERIFY, translate(FIRMWARE_UNREADABLE))
        return parsed

    def withdraw_slots(self):
        """Before slot a is rewritten and slot b removed: every firmware entry naming a slot's
        shim on this ESP (secureboot.slot_entries; FAT ignores case) leaves BootOrder in one write
        that keeps every other entry in its order, a BootNext to one is deleted, then the entries
        themselves. Entries of other partitions and foreign ones are never deleted or reordered.
        The listing read back afterwards must name no slot on this ESP."""
        partuuid = self.esp_partuuid()
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', partuuid), Code.BOOT_VERIFY,
                translate(ESP_UNIDENTIFIED))
        parsed = self.firmware()
        found = secureboot.slot_entries(parsed, partuuid)
        numbers = [number for slot in secureboot.SLOTS for number in found[slot]]
        if not numbers:
            return
        self.log(_('Secure Boot: removing the firmware entries of an earlier Emaki boot slot on this disk: ')
                 + ', '.join(numbers) + '.')
        order = [number for number in parsed['order'] if number not in numbers]
        if order != parsed['order']:
            self.runner.run(['efibootmgr', '--bootorder', ','.join(order)])
        if parsed['next'] in numbers:
            self.runner.run(['efibootmgr', '--delete-bootnext'])
        for number in numbers:
            self.runner.run(['efibootmgr', '-b', number, '-B'])
        parsed = self.firmware()
        require(not any(secureboot.slot_entries(parsed, partuuid).values())
                and not set(parsed['order']) & set(numbers) and parsed['next'] not in numbers,
                Code.BOOT_VERIFY, translate(SLOT_STILL_STARTED))

    def verify_slot_entries(self, first):
        """After the bootloader phase: no entry on this ESP names slot b, which the install
        removed, and at most one names slot a. When boot_entry_first made "Emaki" -> a the
        default (first), that entry is the only one naming a slot, active and first in BootOrder.
        Its own failure (first False) has already told the user with a notice."""
        partuuid = self.esp_partuuid()
        parsed = self.firmware()
        found = secureboot.slot_entries(parsed, partuuid)
        require(not found['b'], Code.BOOT_VERIFY, translate(SLOT_B_STILL_STARTED))
        if first:
            number = parsed['order'][0] if parsed['order'] else None
            require(found['a'] == [number] and parsed['entries'][number][0], Code.BOOT_VERIFY, translate(SLOT_A_NOT_FIRST))
        else:
            require(len(found['a']) <= 1, Code.BOOT_VERIFY, translate(SLOT_A_NOT_FIRST))

    def clear_slot(self, esp, slot):
        """Remove a slot an earlier Emaki left on a kept ESP, manifest first: after the install
        only slot a exists, and the first boot refresh writes slot b. Anything there that is not
        a slot's own file stays, and so does the directory."""
        directory = secureboot.slot_dir(esp, slot)
        if not directory.is_dir():
            return
        names = [secureboot.SLOT_MANIFEST, *secureboot.slot_files_names()]
        for name in names + [name + '.tmp' for name in names]:
            path = directory / name
            if path.is_file() and not path.is_symlink():
                path.unlink()
        try:
            directory.rmdir()
        except OSError:
            self.log(_('WARNING: {value1}/{value2} on the EFI partition holds other files; they were kept.').format(value1=secureboot.VENDOR_DIR, value2=slot))

    def target_run(self, argv, input=None):
        return self.runner.chroot(argv, self.target, input=input)

    def sign_boot_files(self):
        """Both kernels and fwupd's capsule loader, each proven with the machine certificate."""
        for kernel in secureboot.KERNELS:
            if not secureboot.signed(self.target_run, kernel):
                secureboot.sign(self.target_run, kernel)
        if self.files.path(str(secureboot.FWUPD_EFI)).is_file():
            secureboot.sign(self.target_run, secureboot.FWUPD_EFI, secureboot.FWUPD_SIGNED)

    def sign_dkms_modules(self):
        """Every installed DKMS module signed by the machine key, proven with modinfo.

        The NVIDIA modules are built during pacstrap, before the machine key exists, and
        DKMS signs them with its own generated key. A build whose modules another key
        signed is rebuilt: `dkms build --force` unbuilds and builds again, signing with the
        configured key (`install --force` alone reinstalls the old build). DKMS only warns
        when signing fails, so each module's signer is checked against the certificate's
        subject name (self-signed: the signer kmod reports). The initramfs carries the
        NVIDIA modules (emaki-nvidia's mkinitcpio drop-in), so it is built again.
        """
        if not self.files.path('/usr/bin/dkms').is_file():
            return
        names = secureboot.subject_common_names(self.files.path(str(secureboot.DER)).read_bytes())
        require(len(names) == 1, Code.BOOT_VERIFY, _('The machine certificate has no single subject name.'))
        rebuilt = False
        for module, version, kernel, arch in dkms_installed(self.runner.chroot(['dkms', 'status'], self.target)):
            if not self.dkms_unsigned(module, version, kernel, arch, names[0]):
                continue
            build = ['-m', module, '-v', version, '-k', kernel]
            self.runner.chroot(['dkms', 'build', '--force', *build], self.target)
            self.runner.chroot(['dkms', 'install', '--force', *build], self.target)
            rebuilt = True
            unsigned = self.dkms_unsigned(module, version, kernel, arch, names[0])
            require(not unsigned, Code.BOOT_VERIFY,
                    _('DKMS module {value1}/{value2} for {value3} is not signed by the machine key: ').format(value1=module, value2=version, value3=kernel)
                    + ', '.join(unsigned) + '.')
        if rebuilt:
            self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)

    def dkms_unsigned(self, module, version, kernel, arch, signer):
        """The kernel modules of one DKMS build that the machine key did not sign."""
        built = self.files.path(f'/var/lib/dkms/{module}/{version}/{kernel}/{arch}/module')
        names = sorted({KERNEL_MODULE.sub('', path.name) for path in built.iterdir()
                        if KERNEL_MODULE.search(path.name)}) if built.is_dir() else []
        require(bool(names), Code.BOOT_VERIFY, _('DKMS module {value1}/{value2} for {value3} has no built modules.').format(value1=module, value2=version, value3=kernel))
        unsigned = []
        for name in names:
            try:
                found = self.runner.chroot(['modinfo', '-k', kernel, '-F', 'signer', name], self.target).strip()
            except InstallError:
                found = ''
            if found != signer:
                unsigned.append(name)
        return unsigned

    def secure_boot_finish(self):
        """Re-check the signatures after the update, then queue the key last.

        The DKMS config was written with the key (secure_boot_loaders), so the update's
        module builds sign with it; sign_dkms_modules proves that again.
        """
        self.sign_dkms_modules()
        self.sign_boot_files()
        password = secureboot.one_time_password()
        self.redactor.add(password)
        secureboot.request_enrollment(
            lambda argv, input=None: self.runner.chroot(argv, self.target, input=input, secret=True), password)
        self.mok_password = password
        self.log(_('Secure Boot: the machine key waits for confirmation in MokManager at the next start.'))

    def check_secure_boot_esp(self, packages, machine):
        """Before any disk write: a kept ESP must hold this install and every later boot update.

        Only a kept (manual) ESP: a formatted one, the erase layout's or Emaki's own next to Windows,
        is OWN_ESP_BYTES. The need is the boot refresh's rule from the
        verified archives (secureboot.secure_boot_esp_need): two complete slots, EFI/BOOT's
        copies when this install writes them (the bootloader phase's rule, probed_removable),
        the refresh's GRUB stage and SHARED_ESP_RESERVE for Windows. Plan review applied the same
        rule to the USB's archives (review_esp_need); this is the final gate, from the archives
        just verified. Admitting less would leave a machine whose boot files can never be updated.
        """
        esp = next((part for part in self.plan.partitions if part.esp), None)
        if not self.plan.secure_boot or esp is None or esp.format:
            return
        probed = next((part for disk in (machine or {}).get('disks', [])
                       for part in disk.get('partitions', []) if part.get('path') == esp.path), {})
        free = probed.get('_esp_free_bytes')
        require(free is not None, Code.ESP_SPACE, translate(ESP_UNCHECKED))
        # Inventories from before the probe recorded the cluster size: FAT32's usual 4 KiB.
        cluster = probed.get('_esp_cluster_bytes') or 4096
        extra = secure_boot_extra(self.plan.encrypted)
        try:
            need = secureboot.secure_boot_esp_need(
                packages, cluster, secureboot.signed_image_modules(SIGNED_IMAGE_BASE), extra,
                removable=probed_removable(probed))
        except secureboot.SecureBootError as error:
            raise InstallError(Code.ESP_SPACE, translate(ESP_UNCHECKED) + ' ' + str(error)) from error
        self.log(_('Secure Boot on a kept startup partition: it has {value1} bytes free, this installation and later boot updates need {value2}.').format(value1=free, value2=need))
        require(free >= need, Code.ESP_SPACE, translate(ESP_SECURE_BOOT_ROOM).format(
            need=-(-need // 1000000), free=free // 1000000))

    def fwupd_keep_keys(self):
        """While Windows stays on the machine (any disk), fwupd must not change the firmware's
        KEK and db: BitLocker measures them."""
        name = str(secureboot.FWUPD_CONFIG)
        if self.files.path(name).is_file():
            self.files.write(name, secureboot.fwupd_config(self.files.read(name)))

    def check_windows_esp(self):
        """Next to Windows: Windows' whole ESP still has the bytes read before the first disk write.
        Emaki never writes it; os-prober only mounts it read-only."""
        before = self.windows_esp_sha256
        try:
            after = device_sha256(windows_esp(self.plan)['path'])
        except OSError as exc:
            self.log(_("Windows' startup partition read failed: ") + type(exc).__name__)
            self.efi_preservation_failed(_("Windows' startup partition could not be read after loader installation."))
        if not before or after != before:
            self.log(_("Windows' startup partition sha256 before the first disk write: {value1}; now: {value2}.").format(value1=before, value2=after))
            self.efi_preservation_failed(_("Windows' startup partition changed during the installation."))

    def efi_preservation_failed(self, detail):
        # Reaching bootloader means prepare completed its post-shrink checks. This
        # says nothing about Windows booting or the current state of the ESP.
        raise InstallError(
            Code.BOOT_VERIFY, 'STOP: ' + detail +
            _(' The Windows data partition passed post-shrink verification before loader installation. Disk changes have already been made. Do not retry installation. Save the installation log to another device and seek recovery help before changing this disk.'))

    def unlock_screen(self, loaders):
        """Rebuild GRUB's EFI image so it asks for the disk password in graphics mode.

        grub-install's image asks on the firmware text console: one small line, or nothing a
        person can see. The same image rebuilt with an embedded early config draws the prompt
        with gfxterm; the font and the picture travel inside the image as its memdisk, because
        before the unlock GRUB's $root is the locked root and nothing else is read. The early
        config and the memdisk stay next to grub-install's files under /boot (its clean-up
        there deletes .mod, .lst, .img, .efi and .mo files, modinfo.sh and efiemu*.o, not
        .cfg or .tar), so the image can be rebuilt later from the installed system, but only
        right after grub-install from the same grub package: the image comes from
        /usr/lib/grub. Its normal parser is embedded for the retry loop; after unlocking,
        the menu and further modules come from the copies grub-install made under /boot.
        A grub upgrade alone changes neither the
        loaders nor those copies and needs no rebuild. A failure here fails the install: a
        prompt nobody can see is the fault this repairs, not a fallback.
        """
        platform = '/boot/grub/x86_64-efi'
        prefix = grub_prefix(self.plan)
        # grub-install hardcodes the prefix into its image; the rebuilt image must carry the same.
        require(loaders, Code.BOOT_VERIFY, _('No GRUB loader was written.'))
        for name in loaders:
            require(grub_image_carries(self.files.path(name).read_bytes(), prefix), Code.BOOT_VERIFY,
                    _('GRUB loader {value1} does not carry the expected prefix; the unlock screen was not built.').format(value1=name))
        load_cfg = self.files.read(platform + '/load.cfg')
        early = grub_early_config(load_cfg, self.plan.root.luks_uuid)
        require(GRUB_VISIBLE_FONT.is_file(), Code.BOOT_VERIFY, _('The GRUB unlock-screen font is missing from this ISO.'))
        font = GRUB_VISIBLE_FONT.read_bytes()
        require(font.startswith(b'FILE\x00\x00\x00\x04PFF2'), Code.BOOT_VERIFY, _('The GRUB unlock-screen font is not a PFF2 font.'))
        self.files.write(platform + '/emaki-early.cfg', early)
        self.files.write(platform + '/emaki-early.tar',
                         grub_unlock_memdisk(font, load_cfg, self.plan.root.luks_uuid))
        output = platform + '/emaki-early.efi'
        # --memdisk first: grub-mkimage resets the prefix to (memdisk)/boot/grub on --memdisk,
        # and the later option wins.
        self.runner.chroot(['grub-mkimage', '--directory=/usr/lib/grub/x86_64-efi', '--format=x86_64-efi',
                            '--memdisk=' + platform + '/emaki-early.tar', '--prefix=' + prefix,
                            '--config=' + platform + '/emaki-early.cfg',
                            '--output=' + output, *GRUB_EARLY_MODULES], self.target)
        image = self.files.path(output)
        require(image.is_file() and grub_image_carries(image.read_bytes(), prefix), Code.BOOT_VERIFY,
                _('grub-mkimage did not produce a GRUB image with the expected prefix.'))
        for name in loaders:
            self.files.write(name, image.read_bytes(), 0o600)

    def create_swap(self):
        self.files.mkdir('/swap', 0o700).chmod(0o700)
        path = self.files.path('/swap/swapfile')
        require(not path.exists(), Code.MANUAL_LAYOUT,
                _('A swapfile already exists at /swap/swapfile; move it before installing.'))
        size = self.plan.swap_bytes
        require(os.statvfs(path.parent).f_bavail * os.statvfs(path.parent).f_frsize >= size,
                Code.ROOT_TOO_SMALL, _('Not enough free space for the RAM-sized swapfile.'))
        if self.plan.btrfs:
            self.runner.run(['chattr', '+C', str(path.parent)])
            self.runner.run(['btrfs', 'filesystem', 'mkswapfile', '--size', str(size), str(path)])
            output = self.runner.run(['btrfs', 'inspect-internal', 'map-swapfile', '-r', str(path)])
            require(output.strip().isdigit(), Code.BOOT_VERIFY, _('Cannot determine btrfs resume offset.'))
            offset = int(output.strip())
        else:
            self.files.write('/swap/swapfile', b'', 0o600)
            self.runner.run(['fallocate', '-l', str(size), str(path)])
            self.runner.run(['mkswap', str(path)])
            # filefrag reports physical blocks in explicitly requested page units.
            output = self.runner.run(['filefrag', '-v', '-b4096', str(path)])
            match = re.search(r'^\s*0:\s+0\.\.\s*\d+:\s+(\d+)\.\.', output, re.M)
            require(match is not None, Code.BOOT_VERIFY, _('Cannot determine ext4 resume offset.'))
            offset = int(match[1])
        require(offset > 0 and path.stat().st_size == size, Code.BOOT_VERIFY,
                _('Swapfile size or resume offset is invalid.'))
        path.chmod(0o600)
        return offset

    def grub_config(self):
        if self.plan.btrfs:
            self.files.mkdir('/boot/grub/.emaki-snapshots', 0o755)
            config = '/etc/default/grub-btrfs/config'
            self.files.write(config, grub_btrfs_package_config(self.files.read(config)))
        title_output = self.runner.chroot(
            ['/usr/share/libalpm/scripts/emaki-grub-title'], self.target, check=False)
        if title_output.strip():
            self.log(_('WARNING: GRUB menu title adjustment reported a problem; installation continues.'))
        # The live mode is a first guess. The generated header retries a smaller face if
        # needed; boot-time repair corrects the choice for the following menu.
        mode = boot_framebuffer()
        font, theme = menu_font(mode), menu_theme(mode)
        env = ['GRUB_FONT=' + font] if font and self.files.path(font).is_file() else []
        env += ['GRUB_THEME=' + theme] if self.files.path(theme).is_file() else []
        self.runner.chroot([*(['env', *env] if env else []), 'grub-mkconfig', '-o', '/boot/grub/grub.cfg'],
                           self.target)
        original = self.files.read('/boot/grub/grub.cfg')
        if self.plan.config['mode'] == 'alongside':
            # Discovery must identify Windows on its reviewed ESP (not Emaki's own) before
            # replacing its chainload with a restart through the firmware entry.
            require(re.search(r'^menuentry [^\n]*Windows Boot Manager', original, re.M), Code.BOOT_VERIFY,
                    _('Windows Boot Manager was not discovered by os-prober; inspect GRUB before rebooting.'))
            esp = windows_esp(self.plan)
            original_windows = windows_bootnext_menu(
                original, str(esp.get('_partuuid') or '').lower(), esp.get('uuid'),
                self.runner.run(['efibootmgr', '-v']))
        elif self.shared_layout():
            # Windows' ESP kept as /efi: every update's menu refresh (grub/emaki_boot/refresh.py
            # generate) makes this same replacement and refuses without exactly one os-prober
            # Windows entry on this ESP, so a menu the first update would refuse stops here.
            require(re.search(r'^menuentry [^\n]*Windows Boot Manager', original, re.M), Code.BOOT_VERIFY,
                    _('Windows Boot Manager was not discovered by os-prober; inspect GRUB before rebooting.'))
            esp = next(part for part in self.plan.partitions if part.esp and part.mountpoint == '/efi')
            original_windows = windows_bootnext_menu(original, self.esp_partuuid(), esp.uuid,
                                                     self.runner.run(['efibootmgr', '-v']))
        else:
            original_windows = original
        safe = menu_layout(quiet_linux_entries(menu_font_fallback(original_windows)))
        if self.plan.secure_boot:
            safe = secure_boot_menu(safe)
        if safe != original:
            self.files.write('/boot/grub/grub.cfg', safe)
        verify_grub(self.files.read('/boot/grub/grub.cfg'))

    def account(self):
        user = self.plan.config['user']
        # A kept /home may already hold this login's directory; remember whose it was.
        home = self.files.path('/home/' + user['login'])
        if home.exists():
            info = home.stat()
            self.kept_home = (str(info.st_uid), str(info.st_gid))
        self.runner.chroot(['useradd', '-m', '-d', '/home/' + user['login'], '-G', 'wheel',
                            '-s', '/usr/bin/zsh', '-c', user['name'], user['login']], self.target)
        self.runner.chroot(['chpasswd'], self.target, input=user['login'] + ':' + user['password'] + '\n', secret=True)
        user['password'] = ''
        self.runner.chroot(['passwd', '-l', 'root'], self.target)
        wheel = self.files.path('/etc/emaki/sudoers.d/wheel')
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.symlink_to('/usr/share/emaki/defaults/wheel')
        self.runner.chroot(['visudo', '-cf', '/etc/sudoers.d/10-emaki-wheel'], self.target)

    def settings(self):
        c, user = self.plan.config, self.plan.config['user']
        if self.test_mode:
            self.backend.instance.pacman.strap(TEST_PACKAGES)
            self.populate_keyring()
        self.files.write('/etc/locale.gen', ''.join(language + ' UTF-8\n' for language in LOCALES))
        self.files.write('/etc/locale.conf', 'LANG=' + self.plan.config.get('language', 'en_US.UTF-8') + '\n')
        font = getattr(getattr(self, 'backend', None), 'console_font', None)
        self.files.write('/etc/vconsole.conf', vconsole_conf(c['layouts'], font))
        self.files.write('/etc/hostname', c['hostname'] + '\n')
        # A target-relative absolute symlink is correct after boot/chroot.
        parent = self.files.path('/etc')
        localtime = parent / 'localtime'
        localtime.unlink(missing_ok=True)
        localtime.symlink_to('/usr/share/zoneinfo/' + c['timezone'])
        zone_tab = self.files.path('/usr/share/zoneinfo/zone.tab')
        regdom = wireless_regdom(c['timezone'], zone_tab.read_text()) if zone_tab.is_file() else None
        if regdom:
            self.files.write('/etc/conf.d/wireless-regdom', regdom)
        else:
            self.log(_('Wi-Fi country left unset: timezone ') + c['timezone'] + _(' has no country.'))
        if self.wifi_profile is not None:
            uuid = getattr(self, 'wifi_uuid', None) or c['wifi_uuid']
            try:
                install_wifi(uuid, self.wifi_profile, self.files)
                self.log(_('Wi-Fi: installed {value1} into /etc/NetworkManager/system-connections (0600).').format(value1=uuid))
            except (InstallError, OSError) as exc:
                safe_log(self.log, _('Wi-Fi: not installed: {value1}.').format(value1=type(exc).__name__))
                self.wifi_copy_failed()
            finally:
                self.wifi_profile = None
        self.runner.chroot(['locale-gen'], self.target)
        self.runner.chroot(['hwclock', '--systohc'], self.target)
        outputs = []
        if not c.get('output_scales') and c.get('scale_guess') is not None and c['scale_guess'] != 1:
            for status in Path('/sys/class/drm').glob('card*-*/status'):
                if status.read_text().strip() == 'connected':
                    outputs.append(status.parent.name.split('-', 1)[1])
            if not outputs:
                self.log(_('WARNING: no connected DRM output identified; scale override omitted.'))
        home = '/home/' + user['login']
        # Everything the installer writes into the home as root is listed for the chown below.
        self.files.write('/etc/emaki/niri.kdl', managed_niri_config())
        personal = home + '/.config/niri/config.kdl'
        written, owned = [], {}
        if not self.files.path(personal).exists():
            data = niri_config(c.get('scale_guess'), sorted(set(outputs)), c.get('output_scales')).encode()
            self.files.write(personal, data)
            owned[personal] = data
            written += [home + '/.config', home + '/.config/niri', personal]
        if self.test_mode:
            keys = Path('/etc/emaki-test/authorized_keys')
            require(keys.is_file() and keys.stat().st_size > 0, Code.BAD_CONFIG,
                    _('Test mode requires /etc/emaki-test/authorized_keys.'))
            ssh = home + '/.ssh'
            self.files.mkdir(ssh, 0o700).chmod(0o700)
            owned[ssh + '/authorized_keys'] = keys.read_bytes()
            self.files.write(ssh + '/authorized_keys', owned[ssh + '/authorized_keys'], 0o600)
            written += [ssh, ssh + '/authorized_keys']
        account = next((row.split(':') for row in self.files.read('/etc/passwd').splitlines()
                        if row.split(':')[0] == user['login']), None)
        require(account is not None and len(account) == 7 and account[2].isdigit() and account[3].isdigit(),
                Code.BAD_CONFIG, _('Cannot determine the new account UID/GID.'))
        uid, gid = account[2], account[3]
        if self.kept_home is not None:
            # Never a blanket recursive chown over a kept home: only the previous
            # owner's files move to the new account; root's, other accounts' and
            # sub-uid container files keep their owner. A previous owner outside the
            # regular account range (root after a directory made by hand, nobody) is
            # not remapped at all: every file of that system account would move.
            old_uid, old_gid = self.kept_home
            # The warning says what was done, not what was meant.
            done = []
            if old_uid != uid and regular_id(old_uid):
                self.runner.chroot(['chown', '-R', '-h', '--from=' + old_uid, uid, home], self.target)
                done.append(_('files of user {value1} now belong to {value2} ({value3})').format(value1=old_uid, value2=user['login'], value3=uid))
            elif not regular_id(old_uid):
                done.append(_('files of user {value1} keep that owner (not a regular account)').format(value1=old_uid))
            if old_gid != gid and regular_id(old_gid):
                self.runner.chroot(['chown', '-R', '-h', '--from=:' + old_gid, ':' + gid, home], self.target)
                done.append(_('files of group {value1} now have group {value2}').format(value1=old_gid, value2=gid))
            elif not regular_id(old_gid):
                done.append(_('files of group {value1} keep that group (not a regular group)').format(value1=old_gid))
            if not (regular_id(old_uid) and regular_id(old_gid)):
                self.log('; '.join([f'WARNING: the kept {home} belonged to {old_uid}:{old_gid}', *done,
                                    'the directory itself and the files the installer wrote now belong to '
                                    f'{user["login"]}.']))
            written.insert(0, home)
        if written:
            self.runner.chroot(['chown', '-h', uid + ':' + gid, *written], self.target)
        self.files.write('/var/lib/emaki-installer/home-files.json', home_files_manifest(owned), 0o600)
        self.runner.run(['emaki-greeter-provision', '--root', str(self.target), '--user', user['login']])
        # The first login screen shows the default wallpaper: the session's own publisher runs once
        # as the new account with the default wpaperd config, never reading the home. Each login
        # publishes again from the account's own config (DECISIONS: the published copy).
        answer = self.runner.chroot(['setpriv', '--reuid', uid, '--regid', gid, '--clear-groups', '--no-new-privs',
                                     '--', 'env', '-i', 'XDG_CONFIG_HOME=/etc/skel/.config', '/usr/bin/python3', '-B',
                                     '-s', '/usr/share/emaki/shell/helpers/publish-wallpaper.py'],
                                    self.target, check=False)
        state = re.search(r'"state":"(\w+)"(?:,"reason":"(\w+)")?', answer)
        if not state or state[1] != 'published':
            self.log(_('WARNING: the login screen shows its plain background until the first login (wallpaper copy: ')
                     + (', '.join(filter(None, state.groups())) if state else _('no answer')) + ').')
        self.repositories()
        self.runner.chroot(['systemctl', 'preset-all'], self.target)
        units = ['greetd.service', 'NetworkManager.service', 'bluetooth.service', 'fstrim.timer']
        if self.plan.btrfs:
            units += ['grub-btrfsd.service', 'snapper-timeline.timer', 'snapper-cleanup.timer']
        else:
            # A metapackage preset must not start snapshot services on ext4.
            for unit in ('grub-btrfsd.service', 'snapper-timeline.timer', 'snapper-cleanup.timer'):
                self.runner.chroot(['systemctl', 'disable', unit], self.target, check=False)
            self.log(_('ext4 recovery: use linux-lts, cached packages, or the live ISO; snapshots are unavailable.'))
        if self.test_mode:
            units.append('sshd.service')
        enable_units(self.runner, self.target, units)
        # Every layout keeps its ESP identity in the first snapshot as well as the live root.
        self.record_boot_esp()
        if self.plan.config['mode'] == 'alongside':
            # Include the record in the first snapshot as well as the live root.
            self.record_alongside_boot()
        elif self.shared_layout():
            # A manual layout that keeps Windows' ESP as /efi: the older shared layout's record
            # and policy, in the first snapshot too. Without the record the package hook
            # (00-emaki-alongside.hook) refuses every transaction on the installed system.
            self.record_shared_esp()
        if windows_kept(self.plan, self.machine):
            # Before the first snapshot and the optional update: restoring "Fresh Emaki"
            # must not let fwupd change the KEK and db that BitLocker measures.
            self.fwupd_keep_keys()

    def repositories(self):
        text = self.files.read('/etc/pacman.conf')
        # Retain package defaults for options/core/extra; never leave USB URLs.
        output, skip = [], False
        for line in text.splitlines():
            if re.match(r'^\s*\[', line):
                skip = line.strip() in ('[emaki]', '[emaki-offline]')
            if not skip:
                output.append(line)
        text = '\n'.join(output).rstrip() + '\n\n[emaki]\nInclude = /etc/pacman.d/emaki-mirrorlist\n'
        require('[core]' in text and '[extra]' in text, Code.OFFLINE_REPO,
                _('Target pacman.conf is missing standard Arch repositories.'))
        self.files.write('/etc/pacman.conf', text)
        if self.plan.config.get('repo_server'):
            # Test mode only (planner). The packaged chain requires a signed database; so does this.
            self.files.write('/etc/pacman.d/emaki-mirrorlist',
                             'SigLevel = Required\nServer = ' + self.plan.config['repo_server'] + '\n')
        mirrors = Path('/etc/pacman.d/mirrorlist')
        require(mirrors.is_file(), Code.OFFLINE_REPO, _('The live Arch mirrorlist is missing.'))
        self.files.write('/etc/pacman.d/mirrorlist', mirrors.read_bytes())
        if self.plan.config['online_update'] and self.online:
            self.refresh_arch_mirrors()
        self.runner.chroot(['/usr/bin/emaki-refresh-mirrors', '--record'], self.target)

    def refresh_arch_mirrors(self):
        """Rank recent HTTPS mirrors; an unsuccessful probe retains the offline list."""
        try:
            with tempfile.TemporaryDirectory(prefix='emaki-mirrors-') as temporary:
                result = Path(temporary) / 'mirrorlist'
                self.runner.run(['reflector', '--protocol', 'https', '--latest', '10', '--sort', 'rate',
                                 '--connection-timeout', '3', '--download-timeout', '3', '--save', str(result)],
                                timeout=MIRROR_TIMEOUT)
                text = result.read_text()
                servers = [line.strip() for line in text.splitlines()
                           if line.strip() and not line.lstrip().startswith('#')]
                require(len({line.partition('=')[2].strip() for line in servers}) >= 3
                        and all(re.fullmatch(r'Server\s*=\s*https://\S+', line)
                                and '$repo' in line and '$arch' in line for line in servers),
                        Code.COMMAND_FAILED, _('Mirror ranking returned no usable HTTPS list.'))
                self.files.write('/etc/pacman.d/mirrorlist', text)
        except (InstallError, OSError, UnicodeError) as error:
            self.log(_('WARNING: Arch mirror ranking failed; the USB mirror list is retained: ') + str(error))

    def snapshot(self):
        root = self.plan.root
        name = next(name for name, mp in root.subvolumes.items() if mp == '/.snapshots')
        mountpoint = self.files.path('/.snapshots')
        config = self.files.path('/etc/snapper/configs/root')
        if not config.exists():
            self.runner.run(['umount', '--', str(mountpoint)])
            try:
                # Refuse nonempty preexisting data; only snapper's freshly made
                # temporary subvolume may be deleted here.
                mountpoint.rmdir()
                self.runner.chroot(['snapper', '--no-dbus', '-c', 'root', 'create-config', '/'], self.target)
                self.runner.chroot(['btrfs', 'subvolume', 'delete', '/.snapshots'], self.target)
            finally:
                self.files.mkdir('/.snapshots')
                self.runner.run(['mount', '-t', 'btrfs', '-o', ','.join(root.options + ['subvol=' + name]),
                                 root.device, str(mountpoint)])
        else:
            require(re.search(r'^SUBVOLUME="/"$', config.read_text(), re.M), Code.MANUAL_LAYOUT,
                    _('Existing snapper root config points at a different subvolume.'))
        mountpoint.chmod(0o750)
        self.files.write('/etc/snapper/configs/root', snapper_config(config.read_text()), 0o640)
        # The first snapshot must not carry the marker: a rollback to it would bring the
        # marker back. The live root keeps it until finish(), also while snapper runs, so a
        # power loss here still leaves an installation that says it is unfinished. The
        # snapshot is made writable, loses the marker, then becomes read-only as snapper
        # makes it by default: snapper keeps no read-only flag of its own (info.xml) and
        # reads the subvolume's (snapper 0.13.2, Snapshot.cc:318, Btrfs.cc:457-472).
        output = self.runner.chroot(['snapper', '--no-dbus', '-c', 'root', 'create', '--read-write',
                                     '--print-number', '-d', _('Fresh Emaki {value1} install').format(value1=__version__)], self.target)
        # The number is the last line it prints (client/snapper/cmd-create.cc).
        number = (output.strip().splitlines() or [''])[-1].strip()
        require(number.isdigit(), Code.COMMAND_FAILED, _('snapper did not print the new snapshot number.'))
        snapshot = f'/.snapshots/{int(number)}/snapshot'
        self.files.path(snapshot + MARKER).unlink(missing_ok=True)
        self.runner.run(['btrfs', 'property', 'set', '-ts', str(self.files.path(snapshot)), 'ro', 'true'])
        # grub-btrfsd is enabled for the first boot, not running in this chroot.
        self.grub_config()

    def update(self):
        # Extra pacman options for the rest of the update ([emaki] left out).
        self.update_options = []
        try:
            self.refresh_keyring()
            # The documented two-step: nothing between the download and the install
            # touches the sync databases, so -Su installs exactly what -Syuw fetched.
            try:
                self.download(['-Syuw', '--noconfirm'])
            except InstallError as exc:
                self.log(_('WARNING: online update failed; the offline installation is retained: ') + exc.message)
                self.release_pacman_lock()
                return
            # Excluding a repository cannot establish a fully upgraded system.
            if self.update_options:
                self.log(_('WARNING: the full update is deferred until every repository is reachable.'))
                return
            # From here on the target changes: finish() rebuilds the boot images.
            self.update_attempted = True
            try:
                output = self.runner.chroot([*UPDATE_PACMAN, '-Su', '--noconfirm'], self.target)
                if re.search(r'^error: command failed to execute correctly\s*$', output, re.M):
                    raise InstallError(Code.COMMAND_FAILED, _('Package setup did not finish [pacman].'), output=output)
            except InstallError as exc:
                self.warnings.append(translate(PARTIAL_UPDATE) + ' ' + update_error_details(exc.output or exc.message))
                self.log(f'WARNING: {translate(PARTIAL_UPDATE)} ({exc.message})')
                return
            self.upgraded = True
        finally:
            if self.update_options:
                try:
                    Path(self.update_options[1]).unlink(missing_ok=True)
                except OSError as error:
                    self.log(_('WARNING: could not remove {value1}: {value2}').format(value1=self.update_options[1], value2=error.strerror))

    def download(self, args, *, allow_emaki_fallback=True):
        """A limited download; when only [emaki] is unreachable, it is retried once without it.

        The target's /etc/pacman.conf keeps [emaki]: the installed system tries it again
        on its own first update.
        """
        if self.skip_update.is_set():
            raise InstallError(Code.CANCELLED, _('Update download stopped.'))
        self.activity('downloads')
        # The window shows what pacman reports: the package lists, then "n of N" downloads.
        watch = DownloadCount(lambda done, total: self.activity('downloads', done, total)).line
        try:
            try:
                self.runner.chroot([*DOWNLOAD_PACMAN, *args, *self.update_options], self.target,
                                   timeout=UPDATE_TIMEOUT, watch=watch, cancelled=self.skip_update)
            except InstallError as exc:
                if not allow_emaki_fallback or self.update_options or not emaki_unreachable(exc.output):
                    raise
                path = WORK / UPDATE_CONFIG
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
                    with os.fdopen(fd, 'w') as stream:
                        stream.write(without_emaki(self.files.read('/etc/pacman.conf')))
                except OSError as error:
                    with contextlib.suppress(OSError):
                        path.unlink(missing_ok=True)
                    raise InstallError(Code.COMMAND_FAILED, _('Cannot write {value1}: {value2}.').format(value1=path, value2=error.strerror)) from error
                self.update_options = ['--config', str(path)]
                self.runner.chroot([*DOWNLOAD_PACMAN, *args, *self.update_options], self.target,
                                   timeout=UPDATE_TIMEOUT, watch=watch, cancelled=self.skip_update)
        finally:
            self.record_package_lists()
            # What follows (the keyring's or the update's installation) shows the busy word again.
            self.activity(None)
        # Downloads sync the lists, or reuse those synced by the Arch keyring step (after the retry
        # without [emaki], the installed pacman names that one list in a warning).
        self.synced = True
        if self.skip_update.is_set():
            raise InstallError(Code.CANCELLED, _('Update download stopped.'))

    def refresh_keyring(self):
        """Packages newer than the image can be signed by keys the image does not know yet."""
        try:
            # Downloaded with the same limit, installed without one, like the update itself.
            self.download(['-Syw', '--needed', '--noconfirm', 'archlinux-keyring'])
            self.runner.chroot([*UPDATE_PACMAN, '-S', '--needed', '--noconfirm', 'archlinux-keyring',
                                *self.update_options], self.target)
        except InstallError as exc:
            self.log(_('WARNING: the Arch keyring was not refreshed; the update is still attempted: ') + exc.message)
            self.release_pacman_lock()
            return
        if self.update_options:
            # An Arch-only retry has no repository supplying Emaki's keyring.
            return
        try:
            # Use the lists just refreshed above. Pacman's dependency checks preserve an
            # old release marker's exact keyring pin; the full upgrade may replace both.
            self.download(['-Sw', '--needed', '--noconfirm', 'emaki-keyring'],
                          allow_emaki_fallback=False)
            self.runner.chroot([*UPDATE_PACMAN, '-S', '--needed', '--noconfirm', 'emaki-keyring'], self.target)
        except InstallError as exc:
            self.log(_('WARNING: the Emaki keyring was not refreshed; the update is still attempted: ') + exc.message)
            self.release_pacman_lock()

    def release_pacman_lock(self):
        """A stopped pacman leaves its lock behind; remove it only when no pacman runs in the target."""
        lock = self.files.path('/var/lib/pacman/db.lck')
        if not lock.exists():
            return
        if any(process_name(pid) == 'pacman' for pid, ignored in processes_under(self.target)):
            self.log(_('WARNING: pacman is still running in the target; its lock is kept.'))
            return
        lock.unlink()
        self.log(_('Removed the stale pacman lock; no pacman runs in the target.'))

    def record_package_lists(self):
        """A cancelled or failed refresh may already have replaced some databases."""
        if any(path.is_file() and path.stat().st_size > 0
               for path in self.files.path('/var/lib/pacman/sync').glob('*.db')):
            self.synced = True

    def update_notice(self):
        """Explain the full update needed before installing apps, without changing packages."""
        if not self.upgraded and not any(warning.startswith(translate(PARTIAL_UPDATE)) for warning in self.warnings):
            self.warnings.append(translate(UPGRADE_FIRST))

    def finish(self):
        if self.plan.graphics_packages:
            verify_graphics(self.runner, self.plan, self.target)
        # Catch a restrictive umask leaking into the target (0700 /etc locks everyone out).
        # Arch's own root directory is 0555 (host and pacstrap targets alike), so both
        # world-readable modes are accepted.
        for name in ('/', '/etc', '/usr', '/var', '/boot'):
            path = self.files.path(name)
            modes = (0o700,) if name == '/boot' and self.plan.encrypted else (0o755, 0o555)
            require(path.is_dir() and stat.S_IMODE(path.stat().st_mode) in modes,
                    Code.BOOT_VERIFY, _('Target {value1} has unsafe directory permissions.').format(value1=name))
        if self.update_attempted:
            # Optional update failure is a warning. A subsequently broken boot
            # configuration is independently fatal at the final verification.
            self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
            self.grub_config()
        if windows_kept(self.plan, self.machine):
            # Already in settings(); again in case the optional update brought a new fwupd.conf.
            self.fwupd_keep_keys()
        if self.plan.secure_boot:
            self.secure_boot_finish()
        self.update_notice()
        self.record_installation()
        self.files.path(MARKER).unlink(missing_ok=True)
        self.log(_('Installation complete; syncing and unmounting target filesystems.'))
        destination = '/var/log/emaki-install/install-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.log'
        # LOG is flushed by the broker on each line. It never contains stdin.
        self.files.write(destination, LOG.read_bytes(), 0o600)

    def record_installation(self):
        """Record only the Emaki version this system was installed with. A problem report
        compares it with the running version to tell a fresh installation from an updated one."""
        release = self.files.path('/usr/lib/emaki-release')
        version = ''
        if release.is_file():
            for line in release.read_text(errors='replace').splitlines():
                key, sep, value = line.partition('=')
                if sep and key == 'VERSION':
                    version = value.strip().strip('"\'')
        if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+~_-]{0,31}', version):
            self.log(_('WARNING: the installed Emaki version could not be read; problem reports will not say whether this is a fresh installation.'))
            return
        self.files.write('/var/lib/emaki/installed.json',
                         json.dumps({'version': 1, 'emaki_version': version}) + '\n', 0o644)

    def boot_entry_first(self):
        """grub-install keeps an older entry with the same loader path even when it names an ESP that no
        longer exists (Emaki installed before on a wiped disk): then nothing starts this Emaki, and the
        firmware starts Windows. Make sure an entry for this ESP exists and comes first, so Emaki is the
        default as planned. A listing without BootOrder (no EFI variables) changes nothing.
        True when the entry ends first; a failure is logged and told in a notice (False)."""
        esp = next(part for part in self.plan.partitions if part.esp)
        try:
            partuuid = self.esp_partuuid()
            listing = self.runner.run(['efibootmgr', '-v'])
            order = boot_order(listing)
            if order is None or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', partuuid):
                self.log(_('WARNING: the firmware boot entries could not be read; the startup order is unchanged.'))
                return False
            loader = secureboot.slot_entry('a') if self.plan.secure_boot else EMAKI_LOADER
            label = secureboot.ACTIVE_LABEL if self.plan.secure_boot else 'Emaki'
            entry = emaki_boot_entry(listing, partuuid, loader=loader)
            if entry is None:
                inactive = emaki_boot_entry(listing, partuuid, active=False, loader=loader)
                if inactive:
                    self.runner.run(['efibootmgr', '-b', inactive, '-a'])
                    listing = self.runner.run(['efibootmgr', '-v'])
                    entry, order = emaki_boot_entry(listing, partuuid, loader=loader), boot_order(listing)
                    require(entry == inactive and order, Code.BOOT_VERIFY,
                            _('The firmware entry for Emaki could not be activated.'))
            if entry is None:
                self.log(_('No firmware entry starts this Emaki (an older one names another disk); creating one.'))
                self.runner.run(['efibootmgr', '--create', '--disk', self.plan.disk['path'],
                                 '--part', str(esp.number), '--label', label, '--loader', loader])
                listing = self.runner.run(['efibootmgr', '-v'])
                entry, order = emaki_boot_entry(listing, partuuid, loader=loader), boot_order(listing)
                require(entry and order, Code.BOOT_VERIFY, _('The new firmware entry for Emaki is not listed.'))
            if order[0] != entry:
                self.runner.run(['efibootmgr', '--bootorder', ','.join([entry] + [n for n in order if n != entry])])
            if self.plan.secure_boot:
                self.drop_unsigned_entries(partuuid, entry)
            return True
        except InstallError as exc:
            self.log(_('WARNING: firmware startup order: ') + exc.message)
            self.notice(_("Emaki could not make itself the computer's default startup choice. If another system starts instead, start the installation USB and use its GRUB console to load EFI/Emaki/grubx64.efi from this installation's startup partition.")
                        if not self.plan.secure_boot else
                        _("Emaki could not make itself the computer's default startup choice. If another system starts instead, choose Emaki in the firmware boot menu."))
            return False

    def drop_unsigned_entries(self, partuuid, shim):
        """Remove grub-install's entries for its unsigned image on this ESP, which Secure Boot
        refuses, only once slot a's shim entry exists and comes first; then check that it still does."""
        listing = self.runner.run(['efibootmgr', '-v'])
        for ignored in range(8):
            stale = emaki_boot_entry(listing, partuuid) or emaki_boot_entry(listing, partuuid, active=False)
            if stale is None or stale == shim:
                break
            self.runner.run(['efibootmgr', '-b', stale, '-B'])
            listing = self.runner.run(['efibootmgr', '-v'])
        order = boot_order(listing)
        require(order and order[0] == shim
                and emaki_boot_entry(listing, partuuid, loader=secureboot.slot_entry('a')) == shim,
                Code.BOOT_VERIFY, _('The firmware entry for Emaki is not first after removing the unsigned one.'))

    def record_boot_esp(self):
        """Record the installation's EFI partition for strict USB repair ownership checks."""
        partuuid = self.esp_partuuid()
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', partuuid),
                Code.BOOT_VERIFY, _("The disk's startup partitions could not be identified safely, so Emaki stopped."))
        self.files.write('/var/lib/emaki/boot-esp.json', json.dumps({
            'version': 1, 'emaki_esp_partuuid': partuuid}) + '\n', 0o644)

    def record_alongside_boot(self):
        """Next to Windows: record both ESPs for the installed boot code. esp_partuuid is Windows'
        ESP (its firmware entry and GRUB's Windows entry), emaki_esp_partuuid Emaki's own at /efi.
        Windows never writes Emaki's own ESP, so no hibernation guard is needed; the boot order
        service still puts Emaki first again when Windows makes itself the default."""
        windows, own = windows_esp(self.plan), next(part for part in self.plan.partitions if part.esp)
        guid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
        ids = [str(windows.get('_partuuid') or ''), str(own.partuuid or ''), str(windows.get('uuid') or '')]
        require(re.fullmatch(guid, ids[0]) and re.fullmatch(guid, ids[1]) and ids[0].lower() != ids[1].lower()
                and re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', ids[2]),
                Code.BOOT_VERIFY, _("The disk's startup partitions could not be identified safely, so Emaki stopped."))
        self.files.write('/var/lib/emaki/alongside-boot.json', json.dumps({
            'version': 1, 'esp_partuuid': ids[0].lower(), 'emaki_esp_partuuid': ids[1].lower(),
            'windows_esp_uuid': ids[2]}) + '\n', 0o644)
        enable_units(self.runner, self.target, ['emaki-boot-order.service'])

    def os_prober(self):
        """Whether GRUB's menu lists Windows through os-prober: next to Windows, or on Windows' ESP
        kept as /efi (shared_layout), where the installed menu refresh needs that entry. The
        packages (copy_packages), GRUB's defaults and the Windows entry check follow it."""
        return self.plan.config['mode'] == 'alongside' or self.shared_layout()

    def shared_layout(self):
        """Whether /efi is a kept ESP shared with Windows. os-prober (copy_packages), the fstab
        row (bootloader phase), then the record and the drop-ins (settings) take this one
        decision, before the first snapshot.

        The rechecks compare probes taken before the target is mounted. This decision is made
        once, when it is first needed, from the mounted target ESP with the installed boot code's
        own test (inventory.efi_microsoft, refresh.py shared_esp), so wherever the package hook
        will find EFI/Microsoft at /efi it also finds the record. Evidence the review saw keeps
        the shared policy too: a record with a read-only row is consistent for the hook and the
        more careful choice. An ESP this install formats or creates holds no Windows files. Where
        the boot code could not decide either (EFI a link, or unreadable) it refuses every write,
        so the installation stops here rather than finish on an ESP it could never update.
        """
        if self.shared_esp_layout is None:
            esp = next((p for p in self.plan.partitions if p.esp and p.mountpoint == '/efi'), None)
            if esp is None or esp.format or esp.create:
                self.shared_esp_layout = False
            else:
                reviewed = kept_windows_esp(self.plan) is not None
                mounted = alongside.efi_microsoft(self.files.path('/efi'))
                if mounted is None:
                    self.log('Shared startup partition check: the EFI directory of the mounted /efi '
                             'is a link or cannot be read.')
                    raise InstallError(Code.BOOT_VERIFY, translate(alongside.EFI_MANIFEST_UNSAFE))
                if mounted != reviewed:
                    self.log('Shared startup partition check: the mounted /efi '
                             + ('now holds' if mounted else 'no longer holds')
                             + ' EFI/Microsoft after the last recheck; it is installed as shared with Windows.')
                self.shared_esp_layout = mounted or reviewed
        return self.shared_esp_layout

    def record_shared_esp(self):
        """A manual layout that keeps Windows' ESP as /efi (shared_layout): the older
        shared layout, one ESP for both systems. Its record names that ESP alone (esp_partuuid;
        grub/emaki_boot/shared.py record()): the package hook and every boot write then verify the
        partition and Windows' shutdown state before a temporary writable remount; the fstab row is
        read-only (normalize_fstab). The hibernate drop-ins unmount the shared ESP around
        hibernation (a failed unmount cancels sleep, and the counters GRUB changes are saved
        first); the boot order service puts Emaki first again when Windows makes itself the default."""
        partuuid = self.esp_partuuid()
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', partuuid),
                Code.BOOT_VERIFY, _("The disk's startup partitions could not be identified safely, so Emaki stopped."))
        self.files.write('/var/lib/emaki/alongside-boot.json',
                         json.dumps({'version': 1, 'esp_partuuid': partuuid}) + '\n', 0o644)
        # The sleep service does not start if the unmount fails. ExecStopPost also restores the
        # read-only mount after a failed sleep attempt. A system-sleep hook cannot provide this
        # barrier: failures there do not cancel sleep, and it runs too late to save the counters.
        dropin = ('# Copyright (C) 2026 Artur Yakymenko\n'
                  '# SPDX-License-Identifier: GPL-3.0-or-later\n'
                  '[Service]\nExecStartPre=emaki-update-boot hibernate-pre\n'
                  'ExecStopPost=emaki-update-boot hibernate-post\n')
        for mode in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate'):
            self.files.write(f'/etc/systemd/system/systemd-{mode}.service.d/50-emaki-shared-esp.conf', dropin)
        enable_units(self.runner, self.target, ['emaki-boot-order.service'])
