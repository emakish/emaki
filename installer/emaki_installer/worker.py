"""Ordered installation phases. All mutations start only after confirmation."""
from datetime import datetime, timezone
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import unquote, urlsplit

from .arch_backend import Backend, offline_config
from .constants import LOG, MARKER, PACKAGES, PHASES, TARGET, TEST_PACKAGES, WORK
from .errors import Code, InstallError, require
from .planner import make_plan
from .render import (console_keymap, grub_btrfs_config, grub_defaults, mkinitcpio_config, mkinitcpio_preset, niri_config,
                     normalize_fstab, snapper_config, verify_grub)
from .runtime import Runner, TargetFiles, cleanup


def enable_units(runner, target, units):
    for unit in units:
        # list-unit-files succeeds with no matches; systemctl enable does not.
        result = runner.chroot(['systemctl', 'list-unit-files', '--no-legend', unit], target)
        if not result.strip():
            runner.log(f'WARNING: optional unit {unit} is missing; skipped.')
            continue
        runner.chroot(['systemctl', 'enable', unit], target)


def preflight_repo(runner, test_mode=False):
    """Resolve the complete transaction without touching the target or live DB."""
    try:
        conf = offline_config()
        with tempfile.TemporaryDirectory(prefix='emaki-repo-preflight-') as temporary:
            work = Path(temporary)
            for name in ('db', 'cache'):
                (work / name).mkdir()
            options = ['--config', str(conf), '--dbpath', str(work / 'db'),
                       '--cachedir', str(work / 'cache'), '--logfile', str(work / 'pacman.log')]
            runner.run(['pacman', '-Sy', '--noconfirm', *options])
            packages = PACKAGES + ['mkinitcpio'] + (TEST_PACKAGES if test_mode else [])
            output = runner.run(['pacman', '-Sp', '--print-format', '%l', *options, *packages])
            urls = [line.strip() for line in output.splitlines() if line.startswith('file://')]
            require(urls, Code.OFFLINE_REPO_INCOMPLETE, 'Offline package transaction is empty.')
            for url in urls:
                package = Path(unquote(urlsplit(url).path))
                require(package.is_file() and package.stat().st_size > 0
                        and Path(str(package) + '.sig').is_file(), Code.OFFLINE_REPO_INCOMPLETE,
                        f'Offline package or signature is missing: {package.name}.')
    except (InstallError, OSError) as exc:
        raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                           'Offline repository preflight failed; disk untouched: ' + str(exc)) from exc


class Worker:
    def __init__(self, api, inventory, redactor, emit, log, *, test_mode=False,
                 target=TARGET, backend_factory=Backend, runner_factory=Runner):
        self.api, self.inventory, self.redactor = api, inventory, redactor
        self.emit, self.log, self.test_mode = emit, log, test_mode
        self.target, self.files = target, TargetFiles(target)
        self.backend_factory, self.runner_factory = backend_factory, runner_factory
        self.phase = 'prepare_disk'
        self.completed = 0
        self.phase_pct = 0
        self.last_progress = 0
        self.disk_changed = False
        self.update_attempted = False
        self.cleanup_required = False

    def progress(self, pct):
        if self.phase != 'copy_packages':
            return
        # Multiple pacstrap transactions must never move the displayed bar back.
        self.phase_pct = max(self.phase_pct, min(99, pct))
        now = time.monotonic()
        if now - self.last_progress >= 0.25:
            self.emit('progress', phase=self.phase, phase_pct=self.phase_pct,
                      total_pct=self.completed + PHASES[self.phase] * self.phase_pct / 100,
                      indeterminate=False)
            self.last_progress = now

    def run(self, plan, cancelled):
        started = time.monotonic()
        self.plan, self.cancelled = plan, cancelled
        self.runner = self.runner_factory(self.log, self.redactor, self.progress)
        failure, success = None, False
        try:
            # This is intentionally repeated after token validation: a USB can
            # disappear or GParted can finish between plan and actual execution.
            current = self.inventory.probe()
            fresh = make_plan(plan.config, current)
            require(fresh.fingerprint == plan.fingerprint, Code.DISK_CHANGED,
                    'Disk identity or partition layout changed after the review.')
            self.online = current['network']['online']
            preflight_repo(self.runner, self.test_mode)
            self.cleanup_required = True
            cleanup(self.runner, [self.target, WORK / 'btrfs-top'], lazy=True)
            self.backend = self.backend_factory(self.api, plan, self.runner, self.target)
            for phase in PHASES:
                self.phase, self.phase_pct = phase, 0
                if cancelled.is_set():
                    raise InstallError(Code.CANCELLED, 'Installation cancelled at a phase boundary; disk changes are not undone.')
                skipped = ((phase == 'snapshot' and not plan.btrfs) or
                           (phase == 'update' and not (plan.config['online_update'] and self.online)))
                self.emit('state', phase=phase, phase_pct=None if skipped else 0,
                          total_pct=self.completed, indeterminate=False)
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
                if self.cleanup_required:
                    cleanup(self.runner, [self.target, WORK / 'btrfs-top'])
            except Exception as exc:
                success = False
                self.log('Cleanup failed: ' + self.redactor.text(exc))
                failure = InstallError(Code.CLEANUP_FAILED, 'Could not unmount all target filesystems; inspect the log before retrying.')
            # Drop the password as soon as the account operation/job has finished.
            plan.config['user']['password'] = ''
        if failure:
            if failure.code == Code.CANCELLED:
                self.emit('cancel_ack', disk_changed=self.disk_changed)
            self.emit('error', code=failure.code.value, phase=self.phase,
                      message=self.redactor.text(failure.message), retryable=failure.retryable,
                      log_path=str(LOG))
        elif success:
            self.emit('done', log_path=str(LOG), seconds=round(time.monotonic() - started, 2))

    def prepare_disk(self):
        # Mark conservatively before calling the first destructive operation.
        self.disk_changed = True
        self.backend.prepare()
        self.files.write(MARKER, 'Emaki 0.1.0 installation in progress\n', 0o600)

    def copy_packages(self):
        keymap = console_keymap(self.plan.config['layouts'][0])
        locale = self.api.locale.LocaleConfiguration(keymap, 'en_US', 'UTF-8')
        self.backend.minimal(locale)
        packages = PACKAGES + ['mkinitcpio']
        self.backend.instance.pacman.strap(packages)
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
        self.files.write('/etc/mkinitcpio.conf', mkinitcpio_config(self.plan.btrfs))
        for kernel in ('linux', 'linux-lts'):
            require(self.files.path(f'/etc/mkinitcpio.d/{kernel}.preset').is_file(),
                    Code.BOOT_VERIFY, f'Missing {kernel} mkinitcpio preset.')
            self.files.write(f'/etc/mkinitcpio.d/{kernel}.preset', mkinitcpio_preset(kernel))
        self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
        for kernel in ('linux', 'linux-lts'):
            for name in (f'vmlinuz-{kernel}', f'initramfs-{kernel}.img'):
                p = self.files.path('/boot/' + name)
                require(p.is_file() and p.stat().st_size > 0, Code.BOOT_VERIFY, f'Missing /boot/{name}.')
        self.files.write('/etc/default/grub', grub_defaults(self.plan.config['mode'] == 'alongside'))
        # Recognize a previous Emaki copy before updating the vendor binary.
        # FAT is case insensitive; Path works on the actual mounted filesystem.
        fallback = self.files.path('/efi/EFI/BOOT/BOOTX64.EFI')
        vendor = self.files.path('/efi/EFI/Emaki/grubx64.efi')
        own_fallback = (fallback.is_file() and vendor.is_file()
                        and fallback.read_bytes() == vendor.read_bytes())
        command = ['grub-install', '--target=x86_64-efi', '--efi-directory=/efi',
                   '--boot-directory=/boot', '--bootloader-id=Emaki']
        nvram_ok = True
        try:
            self.runner.chroot(command, self.target)
        except InstallError:
            nvram_ok = False
            self.log('WARNING: GRUB NVRAM registration failed; retrying vendor files without NVRAM.')
            try:
                self.runner.chroot([*command, '--no-nvram'], self.target)
            except InstallError:
                self.log('WARNING: vendor GRUB retry failed; attempting the removable loader.')
        removable_ok = False
        if not fallback.exists() or own_fallback:
            try:
                self.runner.chroot([*command, '--removable'], self.target)
                removable_ok = True
            except InstallError:
                self.log('WARNING: removable GRUB installation failed.')
        else:
            self.log('WARNING: preserving existing fallback EFI boot file; removable GRUB copy skipped.')
        require(nvram_ok or removable_ok, Code.BOOT_VERIFY,
                'GRUB NVRAM registration failed and no removable loader could be installed.')
        self.grub_config()
        text = self.runner.run(['genfstab', '-U', '-f', str(self.target), str(self.target)])
        self.files.write('/etc/fstab', normalize_fstab(text, self.plan))

    def grub_config(self):
        config = '/etc/default/grub-btrfs/config'
        self.files.write(config, grub_btrfs_config(self.files.read(config)))
        self.runner.chroot(['/usr/share/libalpm/scripts/emaki-grub-title'], self.target)
        self.runner.chroot(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], self.target)
        verify_grub(self.files.read('/boot/grub/grub.cfg'))

    def account(self):
        user = self.plan.config['user']
        self.runner.chroot(['useradd', '-m', '-d', '/home/' + user['login'], '-G', 'wheel',
                            '-s', '/bin/bash', '-c', user['name'], user['login']], self.target)
        self.runner.chroot(['chpasswd'], self.target, input=user['login'] + ':' + user['password'] + '\n', secret=True)
        user['password'] = ''
        self.runner.chroot(['passwd', '-l', 'root'], self.target)
        self.files.write('/etc/sudoers.d/10-wheel', '%wheel ALL=(ALL:ALL) ALL\n', 0o440)
        self.runner.chroot(['visudo', '-cf', '/etc/sudoers.d/10-wheel'], self.target)

    def settings(self):
        c, user = self.plan.config, self.plan.config['user']
        if self.test_mode:
            self.backend.instance.pacman.strap(TEST_PACKAGES)
            self.populate_keyring()
        self.files.write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
        self.files.write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
        self.files.write('/etc/vconsole.conf', 'KEYMAP=' + console_keymap(c['layouts'][0]) + '\n')
        self.files.write('/etc/hostname', c['hostname'] + '\n')
        self.files.write('/etc/systemd/zram-generator.conf', '[zram0]\nzram-size = min(ram / 2, 8192)\n')
        # A target-relative absolute symlink is correct after boot/chroot.
        parent = self.files.path('/etc')
        localtime = parent / 'localtime'
        localtime.unlink(missing_ok=True)
        localtime.symlink_to('/usr/share/zoneinfo/' + c['timezone'])
        self.runner.chroot(['locale-gen'], self.target)
        self.runner.chroot(['hwclock', '--systohc'], self.target)
        outputs = []
        if c.get('scale_guess') is not None and c['scale_guess'] != 1:
            for status in Path('/sys/class/drm').glob('card*-*/status'):
                if status.read_text().strip() == 'connected':
                    outputs.append(status.parent.name.split('-', 1)[1])
            if not outputs:
                self.log('WARNING: no connected DRM output identified; scale override omitted.')
        self.files.write('/home/' + user['login'] + '/.config/niri/config.kdl',
                         niri_config(c['layouts'], c.get('scale_guess'), sorted(set(outputs))))
        if self.test_mode:
            keys = Path('/etc/emaki-test/authorized_keys')
            require(keys.is_file() and keys.stat().st_size > 0, Code.BAD_CONFIG,
                    'Test mode requires /etc/emaki-test/authorized_keys.')
            ssh = '/home/' + user['login'] + '/.ssh'
            self.files.mkdir(ssh, 0o700).chmod(0o700)
            self.files.write(ssh + '/authorized_keys', keys.read_bytes(), 0o600)
        account = next((row.split(':') for row in self.files.read('/etc/passwd').splitlines()
                        if row.split(':')[0] == user['login']), None)
        require(account is not None and len(account) == 7 and account[2].isdigit() and account[3].isdigit(),
                Code.BAD_CONFIG, 'Cannot determine the new account UID/GID.')
        self.runner.chroot(['chown', '-R', account[2] + ':' + account[3], '/home/' + user['login']], self.target)
        self.runner.run(['emaki-greeter-provision', '--root', str(self.target), '--user', user['login']])
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
                                 root.path, str(mountpoint)])
        else:
            require(re.search(r'^SUBVOLUME="/"$', config.read_text(), re.M), Code.MANUAL_LAYOUT,
                    'Existing snapper root config points at a different subvolume.')
        mountpoint.chmod(0o750)
        self.files.write('/etc/snapper/configs/root', snapper_config(config.read_text()), 0o640)
        self.runner.chroot(['snapper', '--no-dbus', '-c', 'root', 'create',
                            '-d', 'Fresh Emaki 0.1.0 install'], self.target)
        # grub-btrfsd is enabled for the first boot, not running in this chroot.
        self.grub_config()

    def update(self):
        self.update_attempted = True
        try:
            self.runner.chroot(['pacman', '-Syu', '--noconfirm'], self.target)
        except InstallError as exc:
            self.log('WARNING: online update failed; the offline installation is retained: ' + exc.message)

    def finish(self):
        # Catch a restrictive umask leaking into the target (0700 /etc locks everyone out).
        # Arch's own root directory is 0555 (host and pacstrap targets alike), so both
        # world-readable modes are accepted.
        for name in ('/', '/etc', '/usr', '/var', '/boot'):
            path = self.files.path(name)
            require(path.is_dir() and stat.S_IMODE(path.stat().st_mode) in (0o755, 0o555),
                    Code.BOOT_VERIFY, f'Target {name} must be a directory with mode 0755 or 0555.')
        if self.update_attempted:
            # Optional update failure is a warning. A subsequently broken boot
            # configuration is independently fatal at the final verification.
            self.runner.chroot(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], self.target)
            self.grub_config()
        self.files.path(MARKER).unlink(missing_ok=True)
        self.log('Installation complete; syncing and unmounting target filesystems.')
        destination = '/var/log/emaki-install/install-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.log'
        # LOG is flushed by the broker on each line. It never contains stdin.
        self.files.write(destination, LOG.read_bytes(), 0o600)
