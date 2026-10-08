"""Ordered installation phases. All mutations start only after confirmation."""
import contextlib
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
from .constants import GRUB_VISIBLE_FONT, LOG, MARKER, PACKAGES, PHASES, SNAPSHOT_PACKAGES, TARGET, TEST_PACKAGES, WORK
from .errors import Code, InstallError, require
from .update_errors import details as update_error_details
from .copy_progress import CopyProgress
from .clock import ensure_clock
from .media import discover_repo, package_source
from .network_profiles import active_wifi, capture_wifi, install_wifi
from .planner import make_plan
from .render import (GRUB_EARLY_MODULES, console_keymap,
                     grub_btrfs_package_config, grub_defaults, grub_early_config, grub_image_carries, grub_prefix,
                     grub_unlock_memdisk, mkinitcpio_machine_config, mkinitcpio_package_preset, niri_config,
                     managed_niri_config, home_files_manifest,
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
UPGRADE_FIRST = ('Emaki needs one full update before you install apps; when online, '
                 'use the terminal to update the whole system [pacman].')
PARTIAL_UPDATE = ('The online update did not finish; use the terminal to update '
                  'the whole system before installing apps [pacman].')
WIFI_NOT_COPIED = 'Wi-Fi was not copied; join it again after restarting.'


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
            runner.log(f'WARNING: optional unit {unit} is missing; skipped.')
            continue
        runner.chroot(['systemctl', 'enable', unit], target)


def software_packages(software='rich', *, btrfs=True, graphics=()):
    return (PACKAGES + ['mkinitcpio'] + (SNAPSHOT_PACKAGES if btrfs else [])
            + (['emaki-apps'] if software == 'rich' else []) + list(graphics))


def verify_graphics(runner, plan, target):
    # A failing PostTransaction hook does not necessarily make pacman fail.
    # Require usable module sets before progressing or reporting completion.
    if plan.graphics_packages:
        runner.chroot(['python3', '-I', '/usr/lib/emaki/nvidia/runtime.py', 'check'], target)


def preflight_repo(runner, test_mode=False, software='rich', *, alongside_mode=False, btrfs=True,
                   graphics=(), online=False, notice=lambda message: None, source_context=None,
                   source_ready=lambda source: None, root=Path("/")):
    """Resolve the complete transaction without touching the target or live DB."""
    repo = discover_repo(root) if source_context is not None else Path('/run/archiso/bootmnt/emaki/repo')
    ensure_clock(runner, online=online, repo=repo, notice=notice)
    try:
        packages = software_packages(software, btrfs=btrfs, graphics=graphics) + (TEST_PACKAGES if test_mode else [])
        if alongside_mode:
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
            require(urls, Code.OFFLINE_REPO_INCOMPLETE, 'Offline package transaction is empty.')
            archives = [Path(unquote(urlsplit(url).path)) for url in urls]
            for package in archives:
                require(package.is_file() and package.stat().st_size > 0
                        and Path(str(package) + '.sig').is_file(), Code.OFFLINE_REPO_INCOMPLETE,
                        f'Offline package or signature is missing: {package.name}.')
    except (InstallError, OSError) as exc:
        raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                           'Offline repository preflight failed' +
                           (' (Rich software requires emaki-apps)' if software == 'rich' else '') +
                           '; disk untouched: ' + str(exc)) from exc
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
                               'Offline repository preflight failed; disk untouched: '
                               f'package signature verification failed: {package.name}.') from exc
    if progress:
        progress(len(packages), len(packages))


def verify_account_name(runner, packages, login):
    """Check identities from the exact verified target package closure before erasing."""
    def inspect(package, *options):
        try:
            return runner.run(['bsdtar', *options], quiet=True)
        except InstallError as exc:
            message = ('The login name check could not read a package '
                       f'({package.name}; bsdtar). The disk has not been changed.')
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
                        'This login name belongs to the system. Choose another name; the disk has not been changed.')

    safe_log(runner.log, f'Login name checked against {len(packages)} packages.')

def verify_snapshot_hook(runner, packages):
    """The resolved emaki-config archive carries the snapshot boot hook a btrfs image needs.

    An ISO assembled with an older emaki-config of the same version has none, and the
    bootloader phase would find that out only after the disk has been rewritten. The archive
    is named like every pacman package file, <name>-<pkgver>-<pkgrel>-<arch>.pkg.tar.*;
    bsdtar (libarchive, a pacman dependency) lists only the named members and fails when
    one is missing.
    """
    failed = 'Offline repository preflight failed; disk untouched: '
    archives = [package for package in packages if package.name.rsplit('-', 3)[0] == 'emaki-config']
    require(len(archives) == 1, Code.OFFLINE_REPO_INCOMPLETE,
            failed + 'no single emaki-config archive in the resolved package set.')
    try:
        listed = runner.run(['bsdtar', '-tf', str(archives[0]), *SNAPSHOT_HOOK]).splitlines()
    except InstallError as exc:
        if 'Not found in archive' not in exc.output:
            raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                               failed + f'cannot list {archives[0].name}: {exc.message}') from exc
        listed = []
    require(all(member in listed for member in SNAPSHOT_HOOK), Code.OFFLINE_REPO_INCOMPLETE,
            failed + f'{archives[0].name} has no snapshot boot hook (/{SNAPSHOT_HOOK[0]}, /{SNAPSHOT_HOOK[1]}).')


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
        self.cleanup_required = False
        self.kept_home = None
        self.package_total = None
        self.installed = 0
        self.copy_progress = CopyProgress(self)

    def notice(self, message):
        self.warnings.append(message)
        self.log('NOTICE: ' + message)
        self.emit('progress', phase=self.phase, phase_pct=self.phase_pct,
                  total_pct=self.completed, indeterminate=True, notice='\n'.join(self.warnings))

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
                  indeterminate=True, activity={'name': name, 'done': done, 'total': total} if name else None)

    def run(self, plan, cancelled):
        started = time.monotonic()
        self.plan, self.cancelled = plan, cancelled
        self.runner = self.runner_factory(self.log, self.redactor, self.progress)
        failure, success = None, False
        sources = contextlib.ExitStack()
        self.package_source = None
        try:
            # This is intentionally repeated after token validation: a USB can
            # disappear or GParted can finish between plan and actual execution.
            current = self.inventory.probe()
            fresh = make_plan(plan.config, current)
            require(fresh.fingerprint == plan.fingerprint, Code.DISK_CHANGED,
                    'Disk identity or partition layout changed after the review.')
            require(fresh.swap_bytes == plan.swap_bytes, Code.DISK_CHANGED,
                    'RAM size changed after the review; prepare a new installation plan.')
            require(fresh.graphics_packages == plan.graphics_packages, Code.DISK_CHANGED,
                    'Graphics hardware or driver availability changed; prepare a new installation plan.')
            self.plan.disk['_vmd'] = fresh.disk.get('_vmd', False)
            self.online = current['network']['online']
            if self.test_mode:
                # Checked again in settings(); failing here leaves the disk untouched.
                keys = Path('/etc/emaki-test/authorized_keys')
                require(keys.is_file() and keys.stat().st_size > 0, Code.BAD_CONFIG,
                        'Test mode requires /etc/emaki-test/authorized_keys.')
            try:
                try:
                    self.wifi_uuid = active_wifi()
                except InstallError:
                    # Multiple active adapters cannot identify one profile, but
                    # a recorded installer join still identifies the user's choice.
                    self.wifi_uuid = plan.config.get('wifi_uuid')
                    if not self.wifi_uuid:
                        raise
                self.wifi_uuid = self.wifi_uuid or plan.config.get('wifi_uuid')
                if self.wifi_uuid:
                    self.wifi_profile = capture_wifi(self.wifi_uuid)
            except (InstallError, OSError):
                self.wifi_copy_failed()
            packages = preflight_repo(self.runner, self.test_mode, plan.config.get('software', 'rich'),
                                      alongside_mode=plan.config['mode'] == 'alongside', btrfs=plan.btrfs,
                                      graphics=plan.graphics_packages,
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
            # The resolved closure is exactly what the copy phase will announce.
            self.package_total = len(packages) if isinstance(packages, list) and packages else None
            self.cleanup_required = True
            cleanup(self.runner, [self.target, WORK / 'btrfs-top'], lazy=True)
            self.backend = self.backend_factory(self.api, plan, self.runner, self.target)
            if self.package_source is not None:
                self.backend.package_source = self.package_source
            for phase in PHASES:
                self.phase, self.phase_pct = phase, 0
                if cancelled.is_set():
                    raise InstallError(Code.CANCELLED, 'Installation cancelled at a phase boundary; disk changes are not undone.')
                skipped = ((phase == 'snapshot' and not plan.btrfs) or
                           (phase == 'update' and not (plan.config['online_update'] and self.online)))
                # Only copy_packages has a counter; the other phases show the busy indicator.
                counted = phase == 'copy_packages' and bool(self.package_total)
                self.emit('state', phase=phase, phase_pct=None if skipped else 0,
                          total_pct=self.completed, indeterminate=not skipped and not counted)
                if skipped:
                    self.log(f'{phase}: skipped.')
                else:
                    getattr(self, phase)()
                self.completed += PHASES[phase]
                self.emit('state', phase=phase, phase_pct=None if skipped else 100,
                          total_pct=self.completed, indeterminate=False)
            success = True
        except InstallError as exc:
            failure = exc
        except Exception as exc:
            # Do not print repr(config) or tracebacks containing local variables.
            failure = InstallError(Code.INTERNAL, f'Installation failed: {type(exc).__name__}: {self.redactor.text(exc)}')
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
                    safe_log(self.log, 'Cleanup failed: ' + self.redactor.text(problem))
                if problems:
                    success = False
                    failure = InstallError(Code.CLEANUP_FAILED, 'Could not unmount all target filesystems; inspect the log before retrying.')
            finally:
                # Drop the password as soon as the account operation/job has finished.
                plan.config['user']['password'] = ''
                plan.config['disk_password'] = ''
                self.wifi_profile = None
                try:
                    sources.close()
                except OSError as exc:
                    safe_log(self.log, 'Package cache cleanup failed: ' + self.redactor.text(exc))
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
                      warnings=list(self.warnings))

    def wifi_copy_failed(self):
        self.wifi_profile = None
        if WIFI_NOT_COPIED not in self.warnings:
            self.warnings.append(WIFI_NOT_COPIED)
            safe_log(self.log, WIFI_NOT_COPIED)

    def prepare_disk(self):
        if self.plan.config['mode'] == 'alongside':
            alongside.require_verified()
        # Package verification can take minutes. Recheck every mode at the write
        # boundary, including encrypted-volume confirmations against fresh UUIDs.
        fresh = make_plan(self.plan.config, self.inventory.probe())
        require(fresh.fingerprint == self.plan.fingerprint, Code.DISK_CHANGED,
                'Windows shrink bounds, disk identity or ESP state changed after review; disk untouched.'
                if self.plan.config['mode'] == 'alongside' else
                'Disk identity or partition layout changed after the review.')
        require(fresh.swap_bytes == self.plan.swap_bytes, Code.DISK_CHANGED,
                'RAM size changed after the review; prepare a new installation plan.')
        require(fresh.graphics_packages == self.plan.graphics_packages, Code.DISK_CHANGED,
                'Graphics hardware or driver availability changed; prepare a new installation plan.')
        if self.plan.config['mode'] == 'alongside':
            self.backend.devices.changed = self.mark_disk_changed
        else:
            self.mark_disk_changed()
        self.backend.prepare()
        self.files.write(MARKER, f'Emaki {__version__} installation in progress\n', 0o600)

    def mark_disk_changed(self):
        self.disk_changed = True

    def copy_packages(self):
        layouts = self.plan.config['layouts']
        locale = self.api.locale.LocaleConfiguration(console_keymap(layouts[0]), 'en_US', 'UTF-8')
        self.backend.minimal(locale)
        # The minimal install leaves the console map alone in this file. The whole text must
        # be there before the first mkinitcpio run (bootloader phase) reads it for the image.
        font = getattr(self.backend, 'console_font', None)
        self.files.write('/etc/vconsole.conf', vconsole_conf(layouts, font))
        packages = software_packages(self.plan.config.get('software', 'rich'), btrfs=self.plan.btrfs,
                                     graphics=self.plan.graphics_packages)
        if self.plan.config['mode'] == 'alongside':
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
        preserved_loaders = (alongside.efi_loaders(self.files.path('/efi'))
                             if self.plan.config['mode'] == 'alongside' else {})
        if self.plan.config['mode'] == 'alongside':
            reviewed = next(p['_efi_loaders'] for p in self.plan.disk['partitions'] if p['esp'])
            require(preserved_loaders == reviewed, Code.BOOT_VERIFY,
                    'STOP: EFI loaders differ from the reviewed Windows installation.')
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
                        Code.BOOT_VERIFY, 'emaki-config did not install the snapshot boot hook.')
        for kernel in ('linux', 'linux-lts'):
            require(self.files.path(f'/etc/mkinitcpio.d/{kernel}.preset').is_file(),
                    Code.BOOT_VERIFY, f'Missing {kernel} mkinitcpio preset.')
            self.files.write(f'/etc/mkinitcpio.d/{kernel}.preset', mkinitcpio_package_preset(kernel))
        self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
        for kernel in ('linux', 'linux-lts'):
            for name in (f'vmlinuz-{kernel}', f'initramfs-{kernel}.img'):
                p = self.files.path('/boot/' + name)
                require(p.is_file() and p.stat().st_size > 0, Code.BOOT_VERIFY, f'Missing /boot/{name}.')
        self.files.write('/etc/default/grub', grub_defaults(
            self.plan.config['mode'] == 'alongside', root.luks_uuid,
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
            self.log('WARNING: GRUB NVRAM registration failed; retrying vendor files without NVRAM.')
            try:
                self.runner.chroot([*command, '--no-nvram'], self.target)
            except InstallError:
                vendor_ok = False
                self.log('WARNING: vendor GRUB retry failed; attempting the removable loader.')
        removable_ok = False
        if not fallback.exists() or (own_fallback and self.plan.config['mode'] != 'alongside'):
            try:
                self.runner.chroot([*command, '--removable', '--no-nvram'], self.target)
                removable_ok = True
            except InstallError:
                self.log('WARNING: removable GRUB installation failed.')
        else:
            self.log('WARNING: preserving existing fallback EFI boot file; removable GRUB copy skipped.')
        require(nvram_ok or removable_ok, Code.BOOT_VERIFY,
                'GRUB NVRAM registration failed and no removable loader could be installed.')
        if not nvram_ok and removable_ok:
            self.notice('The firmware could not save a startup entry (NVRAM/efibootmgr). '
                        'Emaki installed the standard fallback loader (EFI/BOOT/BOOTX64.EFI). '
                        'If needed, choose this disk in your firmware boot menu.')
        if self.plan.encrypted:
            self.unlock_screen([name for name, written in (('/efi/EFI/Emaki/grubx64.efi', vendor_ok),
                                                          ('/efi/EFI/BOOT/BOOTX64.EFI', removable_ok)) if written])
        self.grub_config()
        if self.plan.config['mode'] == 'alongside':
            after = alongside.efi_loaders(self.files.path('/efi'))
            require(all(after.get(name) == digest for name, digest in preserved_loaders.items()),
                    Code.BOOT_VERIFY, 'STOP: a pre-existing Microsoft or fallback EFI loader changed.')
        text = self.runner.run(['genfstab', '-U', '-f', str(self.target), str(self.target)])
        fstab = normalize_fstab(text, self.plan)
        if self.plan.swap_bytes:
            fstab += '/swap/swapfile\tnone\tswap\tdefaults,pri=10\t0 0\n'
        self.files.write('/etc/fstab', fstab)

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
        require(loaders, Code.BOOT_VERIFY, 'No GRUB loader was written.')
        for name in loaders:
            require(grub_image_carries(self.files.path(name).read_bytes(), prefix), Code.BOOT_VERIFY,
                    f'GRUB loader {name} does not carry the expected prefix; the unlock screen was not built.')
        load_cfg = self.files.read(platform + '/load.cfg')
        early = grub_early_config(load_cfg, self.plan.root.luks_uuid)
        require(GRUB_VISIBLE_FONT.is_file(), Code.BOOT_VERIFY, 'The GRUB unlock-screen font is missing from this ISO.')
        font = GRUB_VISIBLE_FONT.read_bytes()
        require(font.startswith(b'FILE\x00\x00\x00\x04PFF2'), Code.BOOT_VERIFY, 'The GRUB unlock-screen font is not a PFF2 font.')
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
                'grub-mkimage did not produce a GRUB image with the expected prefix.')
        for name in loaders:
            self.files.write(name, image.read_bytes(), 0o600)

    def create_swap(self):
        self.files.mkdir('/swap', 0o700).chmod(0o700)
        path = self.files.path('/swap/swapfile')
        require(not path.exists(), Code.MANUAL_LAYOUT,
                'A swapfile already exists at /swap/swapfile; move it before installing.')
        size = self.plan.swap_bytes
        require(os.statvfs(path.parent).f_bavail * os.statvfs(path.parent).f_frsize >= size,
                Code.ROOT_TOO_SMALL, 'Not enough free space for the RAM-sized swapfile.')
        if self.plan.btrfs:
            self.runner.run(['chattr', '+C', str(path.parent)])
            self.runner.run(['btrfs', 'filesystem', 'mkswapfile', '--size', str(size), str(path)])
            output = self.runner.run(['btrfs', 'inspect-internal', 'map-swapfile', '-r', str(path)])
            require(output.strip().isdigit(), Code.BOOT_VERIFY, 'Cannot determine btrfs resume offset.')
            offset = int(output.strip())
        else:
            self.files.write('/swap/swapfile', b'', 0o600)
            self.runner.run(['fallocate', '-l', str(size), str(path)])
            self.runner.run(['mkswap', str(path)])
            # filefrag reports physical blocks in explicitly requested page units.
            output = self.runner.run(['filefrag', '-v', '-b4096', str(path)])
            match = re.search(r'^\s*0:\s+0\.\.\s*\d+:\s+(\d+)\.\.', output, re.M)
            require(match is not None, Code.BOOT_VERIFY, 'Cannot determine ext4 resume offset.')
            offset = int(match[1])
        require(offset > 0 and path.stat().st_size == size, Code.BOOT_VERIFY,
                'Swapfile size or resume offset is invalid.')
        path.chmod(0o600)
        return offset

    def grub_config(self):
        if self.plan.btrfs:
            config = '/etc/default/grub-btrfs/config'
            self.files.write(config, grub_btrfs_package_config(self.files.read(config)))
        title_output = self.runner.chroot(
            ['/usr/share/libalpm/scripts/emaki-grub-title'], self.target, check=False)
        if title_output.strip():
            self.log('WARNING: GRUB menu title adjustment reported a problem; installation continues.')
        self.runner.chroot(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], self.target)
        verify_grub(self.files.read('/boot/grub/grub.cfg'))
        if self.plan.config['mode'] == 'alongside':
            # 30_os-prober invokes os-prober inside this chroot. Do not invent
            # a Windows entry when discovery failed.
            require(re.search(r'^menuentry [^\n]*Windows Boot Manager',
                              self.files.read('/boot/grub/grub.cfg'), re.M), Code.BOOT_VERIFY,
                    'Windows Boot Manager was not discovered by os-prober; inspect GRUB before rebooting.')

    def account(self):
        user = self.plan.config['user']
        # A kept /home may already hold this login's directory; remember whose it was.
        home = self.files.path('/home/' + user['login'])
        if home.exists():
            info = home.stat()
            self.kept_home = (str(info.st_uid), str(info.st_gid))
        self.runner.chroot(['useradd', '-m', '-d', '/home/' + user['login'], '-G', 'wheel',
                            '-s', '/bin/bash', '-c', user['name'], user['login']], self.target)
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
        self.files.write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
        self.files.write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
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
            self.log('Wi-Fi country left unset: timezone ' + c['timezone'] + ' has no country.')
        if self.wifi_profile is not None:
            try:
                install_wifi(getattr(self, 'wifi_uuid', None) or c['wifi_uuid'], self.wifi_profile, self.files)
            except (InstallError, OSError):
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
                self.log('WARNING: no connected DRM output identified; scale override omitted.')
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
                    'Test mode requires /etc/emaki-test/authorized_keys.')
            ssh = home + '/.ssh'
            self.files.mkdir(ssh, 0o700).chmod(0o700)
            owned[ssh + '/authorized_keys'] = keys.read_bytes()
            self.files.write(ssh + '/authorized_keys', owned[ssh + '/authorized_keys'], 0o600)
            written += [ssh, ssh + '/authorized_keys']
        account = next((row.split(':') for row in self.files.read('/etc/passwd').splitlines()
                        if row.split(':')[0] == user['login']), None)
        require(account is not None and len(account) == 7 and account[2].isdigit() and account[3].isdigit(),
                Code.BAD_CONFIG, 'Cannot determine the new account UID/GID.')
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
                done.append(f'files of user {old_uid} now belong to {user["login"]} ({uid})')
            elif not regular_id(old_uid):
                done.append(f'files of user {old_uid} keep that owner (not a regular account)')
            if old_gid != gid and regular_id(old_gid):
                self.runner.chroot(['chown', '-R', '-h', '--from=:' + old_gid, ':' + gid, home], self.target)
                done.append(f'files of group {old_gid} now have group {gid}')
            elif not regular_id(old_gid):
                done.append(f'files of group {old_gid} keep that group (not a regular group)')
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
            self.log('WARNING: the login screen shows its plain background until the first login (wallpaper copy: '
                     + (', '.join(filter(None, state.groups())) if state else 'no answer') + ').')
        self.repositories()
        self.runner.chroot(['systemctl', 'preset-all'], self.target)
        units = ['greetd.service', 'NetworkManager.service', 'bluetooth.service', 'fstrim.timer']
        if self.plan.btrfs:
            units += ['grub-btrfsd.service', 'snapper-timeline.timer', 'snapper-cleanup.timer']
        else:
            # A metapackage preset must not start snapshot services on ext4.
            for unit in ('grub-btrfsd.service', 'snapper-timeline.timer', 'snapper-cleanup.timer'):
                self.runner.chroot(['systemctl', 'disable', unit], self.target, check=False)
            self.log('ext4 recovery: use linux-lts, cached packages, or the live ISO; snapshots are unavailable.')
        if self.test_mode:
            units.append('sshd.service')
        enable_units(self.runner, self.target, units)

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
                'Target pacman.conf is missing standard Arch repositories.')
        self.files.write('/etc/pacman.conf', text)
        if self.plan.config.get('repo_server'):
            self.files.write('/etc/pacman.d/emaki-mirrorlist', 'Server = ' + self.plan.config['repo_server'] + '\n')
        mirrors = Path('/etc/pacman.d/mirrorlist')
        require(mirrors.is_file(), Code.OFFLINE_REPO, 'The live Arch mirrorlist is missing.')
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
                        Code.COMMAND_FAILED, 'Mirror ranking returned no usable HTTPS list.')
                self.files.write('/etc/pacman.d/mirrorlist', text)
        except (InstallError, OSError, UnicodeError) as error:
            self.log('WARNING: Arch mirror ranking failed; the USB mirror list is retained: ' + str(error))

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
                    'Existing snapper root config points at a different subvolume.')
        mountpoint.chmod(0o750)
        self.files.write('/etc/snapper/configs/root', snapper_config(config.read_text()), 0o640)
        # The first snapshot must not carry the marker: a rollback to it would bring the
        # marker back. The live root keeps it until finish(), also while snapper runs, so a
        # power loss here still leaves an installation that says it is unfinished. The
        # snapshot is made writable, loses the marker, then becomes read-only as snapper
        # makes it by default: snapper keeps no read-only flag of its own (info.xml) and
        # reads the subvolume's (snapper 0.13.2, Snapshot.cc:318, Btrfs.cc:457-472).
        output = self.runner.chroot(['snapper', '--no-dbus', '-c', 'root', 'create', '--read-write',
                                     '--print-number', '-d', f'Fresh Emaki {__version__} install'], self.target)
        # The number is the last line it prints (client/snapper/cmd-create.cc).
        number = (output.strip().splitlines() or [''])[-1].strip()
        require(number.isdigit(), Code.COMMAND_FAILED, 'snapper did not print the new snapshot number.')
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
                self.log('WARNING: online update failed; the offline installation is retained: ' + exc.message)
                self.release_pacman_lock()
                return
            # Excluding a repository cannot establish a fully upgraded system.
            if self.update_options:
                self.log('WARNING: the full update is deferred until every repository is reachable.')
                return
            # From here on the target changes: finish() rebuilds the boot images.
            self.update_attempted = True
            try:
                output = self.runner.chroot([*UPDATE_PACMAN, '-Su', '--noconfirm'], self.target)
                if re.search(r'^error: command failed to execute correctly\s*$', output, re.M):
                    raise InstallError(Code.COMMAND_FAILED, 'Package setup did not finish [pacman].', output=output)
            except InstallError as exc:
                self.warnings.append(PARTIAL_UPDATE + ' ' + update_error_details(exc.output or exc.message))
                self.log(f'WARNING: {PARTIAL_UPDATE} ({exc.message})')
                return
            self.upgraded = True
        finally:
            if self.update_options:
                try:
                    Path(self.update_options[1]).unlink(missing_ok=True)
                except OSError as error:
                    self.log(f'WARNING: could not remove {self.update_options[1]}: {error.strerror}')

    def download(self, args, *, allow_emaki_fallback=True):
        """A limited download; when only [emaki] is unreachable, it is retried once without it.

        The target's /etc/pacman.conf keeps [emaki]: the installed system tries it again
        on its own first update.
        """
        if self.skip_update.is_set():
            raise InstallError(Code.CANCELLED, 'Update download stopped.')
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
                    raise InstallError(Code.COMMAND_FAILED, f'Cannot write {path}: {error.strerror}.') from error
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
            raise InstallError(Code.CANCELLED, 'Update download stopped.')

    def refresh_keyring(self):
        """Packages newer than the image can be signed by keys the image does not know yet."""
        try:
            # Downloaded with the same limit, installed without one, like the update itself.
            self.download(['-Syw', '--needed', '--noconfirm', 'archlinux-keyring'])
            self.runner.chroot([*UPDATE_PACMAN, '-S', '--needed', '--noconfirm', 'archlinux-keyring',
                                *self.update_options], self.target)
        except InstallError as exc:
            self.log('WARNING: the Arch keyring was not refreshed; the update is still attempted: ' + exc.message)
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
            self.log('WARNING: the Emaki keyring was not refreshed; the update is still attempted: ' + exc.message)
            self.release_pacman_lock()

    def release_pacman_lock(self):
        """A stopped pacman leaves its lock behind; remove it only when no pacman runs in the target."""
        lock = self.files.path('/var/lib/pacman/db.lck')
        if not lock.exists():
            return
        if any(process_name(pid) == 'pacman' for pid, _ in processes_under(self.target)):
            self.log('WARNING: pacman is still running in the target; its lock is kept.')
            return
        lock.unlink()
        self.log('Removed the stale pacman lock; no pacman runs in the target.')

    def record_package_lists(self):
        """A cancelled or failed refresh may already have replaced some databases."""
        if any(path.is_file() and path.stat().st_size > 0
               for path in self.files.path('/var/lib/pacman/sync').glob('*.db')):
            self.synced = True

    def update_notice(self):
        """Explain the full update needed before installing apps, without changing packages."""
        if not self.upgraded and not any(warning.startswith(PARTIAL_UPDATE) for warning in self.warnings):
            self.warnings.append(UPGRADE_FIRST)

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
                    Code.BOOT_VERIFY, f'Target {name} has unsafe directory permissions.')
        if self.update_attempted:
            # Optional update failure is a warning. A subsequently broken boot
            # configuration is independently fatal at the final verification.
            self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
            self.grub_config()
        self.update_notice()
        self.files.path(MARKER).unlink(missing_ok=True)
        self.log('Installation complete; syncing and unmounting target filesystems.')
        destination = '/var/log/emaki-install/install-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.log'
        # LOG is flushed by the broker on each line. It never contains stdin.
        self.files.write(destination, LOG.read_bytes(), 0o600)
