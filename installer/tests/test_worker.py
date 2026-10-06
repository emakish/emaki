import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from emaki_installer.constants import MARKER, PACKAGES, PHASES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import GRUB_EARLY_MODULES, grub_early_config, grub_unlock_memdisk
from emaki_installer.runtime import Redactor, Runner, TargetFiles, cleanup
from emaki_installer import worker as worker_module
from emaki_installer.worker import REFRESH_TIMEOUT, UPDATE_TIMEOUT, Worker
from support import FakeInventory, RecordingRunner, alongside_on, config, inventory

SNAPSHOT_HOOK = '/usr/lib/initcpio/{}/emaki-snapshot-fstab'
# pacman-download-*.txt: log lines cut from the VM walk's run E stream
# (~/VMs/night-installer-walk/runE/install.ndjson, 2026-10-05), checked for the walk password.
FIXTURES = Path(__file__).parent / 'fixtures'

# Recorded through a pipe with LC_ALL=C from pacman 7.1.0.r9.g54d9411-2 (the test ISO's
# version): `pacman -Syuw --noconfirm` with reachable [core]/[extra] and [emaki] at
# pkgs.emaki.sh, which does not resolve yet; then with every repository unreachable.
EMAKI_DOWN = '''\
:: Synchronizing package databases...
 core downloading...
 extra downloading...
 emaki downloading...
error: failed retrieving file 'emaki.db' from pkgs.emaki.sh : Could not resolve host: pkgs.emaki.sh
warning: fatal error from pkgs.emaki.sh, skipping for the remainder of this transaction
error: failed to synchronize all databases (invalid url for server)
'''
ALL_DOWN = '''\
:: Synchronizing package databases...
 core downloading...
 extra downloading...
 emaki downloading...
error: failed retrieving file 'core.db' from mirror.emaki-test.invalid : Could not resolve host: mirror.emaki-test.invalid
warning: fatal error from mirror.emaki-test.invalid, skipping for the remainder of this transaction
error: failed retrieving file 'extra.db' from mirror.emaki-test.invalid : Could not resolve host: mirror.emaki-test.invalid
error: failed retrieving file 'emaki.db' from pkgs.emaki.sh : Could not resolve host: pkgs.emaki.sh
warning: fatal error from pkgs.emaki.sh, skipping for the remainder of this transaction
error: failed to synchronize all databases (invalid url for server)
'''
TARGET_PACMAN_CONF = '''\
[options]
HoldPkg     = pacman glibc
SigLevel    = Required DatabaseOptional

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

[emaki]
Include = /etc/pacman.d/emaki-mirrorlist
'''


PARTIAL_UPDATE = ('The online update stopped part-way; some packages may be newer than others. '
                  'Run `sudo pacman -Syu` after the first login.')
NO_PACKAGE_LISTS = 'Run `sudo pacman -Syu` once you are online.'
UPGRADE_FIRST = 'Run `sudo pacman -Syu` before installing software.'


def pacman_failure(output):
    # Runner keeps a failed command's output through the redactor, which drops line breaks.
    return InstallError(Code.COMMAND_FAILED, 'arch-chroot exited with status 1.',
                        output=Redactor().text(output), returncode=1)


def finishable(root):
    """A marked target that finish() accepts: Arch's directory modes, the files grub_config() reads."""
    files = TargetFiles(root)
    files.write('/etc/default/grub-btrfs/config', 'GRUB_BTRFS_SUBMENUNAME="Arch Linux snapshots"\n')
    files.write('/boot/grub/grub.cfg', 'linux /@/boot/vmlinuz-linux root=x\nlinux /@/boot/vmlinuz-linux-lts root=x\n')
    files.write(MARKER, 'Emaki installation in progress\n', 0o600)
    root.chmod(0o755)
    for name in ('/etc', '/usr', '/var', '/boot'):
        files.mkdir(name).chmod(0o755)


class WorkerLifecycleTests(unittest.TestCase):
    def execute(self, fs='btrfs', fail=None, cancel=None, changed=False, *, test_mode=False,
                backend=None, log=None, cleanup_effect=None, live=None, disk_password=None,
                resolved=(Path('/repo/a.pkg.tar.zst'), Path('/repo/b.pkg.tar.zst')),
                online=False, update=None, real=(), runner_fail=None, target=None, verify=False,
                hook_error=None, fresh_disk=None):
        inv = FakeInventory(inventory())
        inv.data['network']['online'] = online
        settings = config(fs=fs)
        settings['online_update'] = online if update is None else update
        if disk_password is not None:
            settings.update(encryption='separate', disk_password=disk_password)
        plan = make_plan(settings, inv.probe())
        if changed:
            inv.data['disks'][0]['_identity']['diskseq'] += 1
        if fresh_disk:
            inv.data['disks'][0].update(fresh_disk)
        events, phases, runners = [], [], []

        def runner_factory(*args):
            runner = RecordingRunner(*args)
            runner.fail = runner_fail
            runners.append(runner)
            return runner

        class PhaseWorker(Worker):
            pass

        def phase_fn(phase):
            def fn(self):
                phases.append(phase)
                if phase == 'prepare_disk':
                    self.disk_changed = True
                if phase == cancel:
                    self.cancelled.set()
                    phases.append(phase + '-completed')
                if phase == fail:
                    raise InstallError(Code.COMMAND_FAILED, 'injected failure')
            return fn

        for phase in PHASES:
            if phase not in real:
                setattr(PhaseWorker, phase, phase_fn(phase))
        self.factory = Mock(return_value=backend)
        self.plan = plan
        self.worker = worker = PhaseWorker(
            None, inv, Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
            log or (lambda line: None), test_mode=test_mode, backend_factory=self.factory,
            runner_factory=runner_factory, **({} if target is None else {'target': target}))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('emaki_installer.worker.preflight_repo',
                                      **({} if resolved is None else {'return_value': list(resolved)})))
            if not verify:
                stack.enter_context(patch('emaki_installer.worker.verify_packages'))
            # The fixture archives are names only; test_f1 reads real ones.
            self.hook_check = stack.enter_context(patch('emaki_installer.worker.verify_snapshot_hook',
                                                        side_effect=hook_error))
            clean =stack.enter_context(patch('emaki_installer.worker.cleanup', side_effect=cleanup_effect))
            if live is not None:
                # Never read the real /etc of the machine running the tests.
                stack.enter_context(patch(
                    'emaki_installer.worker.Path',
                    side_effect=lambda p: live / str(p).lstrip('/') if str(p).startswith('/etc/') else Path(p)))
            worker.run(plan, threading.Event())
            cleanups = list(clean.call_args_list)
        self.assertEqual(plan.config['user']['password'], '')
        return events, phases, cleanups, runners

    def test_success_all_phases_then_cleanup_then_done(self):
        events, phases, cleanups, _ = self.execute()
        self.assertEqual(phases, [p for p in PHASES if p != 'update'])
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-2]['total_pct'], 100)
        self.assertEqual(len(cleanups), 2)
        self.assertNotIn('lazy', cleanups[-1].kwargs)

    def test_ext4_snapshot_is_null_and_skipped(self):
        events, phases, _, _ = self.execute('ext4')
        self.assertNotIn('snapshot', phases)
        snapshots = [e for e in events if e.get('phase') == 'snapshot']
        self.assertTrue(all(e['phase_pct'] is None for e in snapshots))
        self.assertEqual(events[-1]['type'], 'done')

    def test_failure_always_cleans_and_never_done(self):
        for failed in ('prepare_disk', 'copy_packages', 'bootloader', 'account', 'settings', 'snapshot', 'finish'):
            events, phases, cleanups, _ = self.execute(fail=failed)
            self.assertEqual(events[-1]['type'], 'error')
            self.assertEqual(events[-1]['phase'], failed)
            self.assertIn('log_path', events[-1])
            self.assertEqual(phases[-1], failed)
            # The stubbed prepare_disk marks the disk as changed before failing.
            self.assertIs(events[-1]['retryable'], False)
            self.assertNotIn('lazy', cleanups[-1].kwargs)

    def test_cancellation_is_at_next_boundary(self):
        events, phases, _, _ = self.execute(cancel='copy_packages')
        self.assertIn('copy_packages-completed', phases)
        self.assertNotIn('bootloader', phases)
        self.assertEqual(events[-2], {'type': 'cancel_ack', 'disk_changed': True})
        self.assertEqual(events[-1]['code'], 'cancelled')
        self.assertIs(events[-1]['retryable'], False)

    def test_the_packaged_snapshot_hook_is_read_before_the_disk_only_for_btrfs(self):
        resolved = [Path('/repo/a.pkg.tar.zst'), Path('/repo/emaki-config-0.1.2-1-x86_64.pkg.tar.zst')]
        for fs, called in (('btrfs', True), ('ext4', False)):
            with self.subTest(fs=fs):
                events, phases, _, runners = self.execute(fs, resolved=resolved)
                self.assertEqual(events[-1]['type'], 'done')
                if called:
                    self.hook_check.assert_called_once_with(runners[0], resolved)
                else:
                    self.hook_check.assert_not_called()

    def test_a_refused_snapshot_hook_leaves_the_disk_untouched_and_can_be_retried(self):
        message = 'Offline repository preflight failed; disk untouched: emaki-config has no snapshot boot hook.'
        events, phases, cleanups, _ = self.execute(
            hook_error=InstallError(Code.OFFLINE_REPO_INCOMPLETE, message))
        self.hook_check.assert_called_once()
        self.assertEqual(phases, [])
        self.assertEqual(cleanups, [])
        self.factory.assert_not_called()
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['code'], 'offline_repo_incomplete')
        self.assertIs(events[-1]['retryable'], True)

    def test_hotplugged_disk_rejected_before_prepare(self):
        events, phases, _, _ = self.execute(changed=True)
        self.assertEqual(phases, [])
        self.assertEqual(events[-1]['code'], 'disk_changed')
        self.assertIs(events[-1]['retryable'], True)

    def test_phase_start_is_indeterminate_except_for_the_counted_copy(self):
        events, _, _, _ = self.execute()
        states = [e for e in events if e['type'] == 'state']
        first = {phase: next(e for e in states if e['phase'] == phase) for phase in PHASES}
        last = {phase: [e for e in states if e['phase'] == phase][-1] for phase in PHASES}
        for phase in ('prepare_disk', 'bootloader', 'account', 'settings', 'snapshot', 'finish'):
            self.assertIs(first[phase]['indeterminate'], True, phase)
            self.assertEqual(first[phase]['phase_pct'], 0, phase)
        self.assertIs(first['copy_packages']['indeterminate'], False)
        # Skipped in the harness: online_update is off.
        self.assertIsNone(first['update']['phase_pct'])
        self.assertIs(first['update']['indeterminate'], False)
        for phase in PHASES:
            self.assertIs(last[phase]['indeterminate'], False, phase)
            self.assertEqual(last[phase]['phase_pct'], None if phase == 'update' else 100, phase)

    @staticmethod
    def failing_final_cleanup(runner, roots, *, lazy=False, before_unmount=None):
        if not lazy:
            raise InstallError(Code.CLEANUP_FAILED, 'umount exited with status 32.')

    def test_luks_close_is_attempted_after_a_failed_unmount(self):
        backend = SimpleNamespace(devices=Mock())
        logs = []
        events, _, cleanups, _ = self.execute(backend=backend, log=logs.append,
                                              cleanup_effect=self.failing_final_cleanup)
        self.assertEqual(len(cleanups), 2)
        backend.devices.close.assert_called_once_with()
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['code'], 'cleanup_failed')
        self.assertIs(events[-1]['retryable'], False)
        self.assertIn('Cleanup failed: umount exited with status 32.', logs)

    def test_luks_close_failure_is_a_logged_cleanup_failure(self):
        backend = SimpleNamespace(devices=Mock())
        backend.devices.close.side_effect = InstallError(Code.COMMAND_FAILED, 'cryptsetup exited with status 5.')
        logs = []
        events, _, _, _ = self.execute(backend=backend, log=logs.append)
        backend.devices.close.assert_called_once_with()
        self.assertEqual(events[-1]['code'], 'cleanup_failed')
        self.assertNotIn('done', [e['type'] for e in events])
        self.assertIn('Cleanup failed: cryptsetup exited with status 5.', logs)

    def test_full_log_cannot_skip_password_wipe_or_the_terminal_event(self):
        def full_log(line):
            raise OSError(28, 'No space left on device')

        backend = SimpleNamespace(devices=Mock())
        events, _, cleanups, _ = self.execute(backend=backend, log=full_log, disk_password='disk-secret-for-tests',
                                              cleanup_effect=self.failing_final_cleanup)
        self.assertEqual(self.plan.config['user']['password'], '')
        self.assertEqual(self.plan.config['disk_password'], '')
        self.assertEqual(len(cleanups), 2)
        backend.devices.close.assert_called_once_with()
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['code'], 'cleanup_failed')

    def test_copy_without_a_package_total_is_indeterminate(self):
        # preflight_repo patched with a bare Mock: no usable package count.
        events, _, _, _ = self.execute(resolved=None)
        first = next(e for e in events if e['type'] == 'state' and e['phase'] == 'copy_packages')
        self.assertIs(first['indeterminate'], True)
        self.assertIsNone(self.worker.package_total)
        self.worker.phase = 'copy_packages'
        self.worker.phase_pct = 0
        events.clear()
        self.worker.progress(50)
        self.worker.progress(None, package='filesystem')
        self.assertEqual(events, [])
        self.assertEqual(self.worker.phase_pct, 0)

    def test_package_progress_follows_installed_packages_not_hooks(self):
        lines = (Path(__file__).parent / 'fixtures/pacstrap-progress.txt').read_text().splitlines()
        total = sum(line.startswith('installing ') for line in lines)
        self.assertEqual(total, 40)
        events = []
        worker = Worker(None, None, Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
                        lambda line: None)
        worker.phase, worker.package_total = 'copy_packages', total
        runner = Runner(lambda line: None, Redactor(), worker.progress)
        clock = iter(range(1, 10_000))
        seen, installed = [], 0
        with patch('emaki_installer.worker.time.monotonic', side_effect=lambda: next(clock)):
            for line in lines:
                runner._line(line)
                installed += line.startswith('installing ')
                seen.append(worker.phase_pct)
                self.assertEqual(worker.phase_pct, min(99, 100 * installed / total), line)
                if line.startswith('(16/16)'):
                    # The first transaction's hooks are done; 28 of 40 packages are still to come.
                    self.assertEqual(worker.phase_pct, 30)
                    self.assertLess(worker.phase_pct, 50)
        self.assertEqual(seen, sorted(seen))
        emitted = [e['phase_pct'] for e in events]
        self.assertEqual(emitted, [min(99, 100 * n / total) for n in range(1, total + 1)])
        self.assertTrue(all(e['type'] == 'progress' and e['indeterminate'] is False for e in events))

    def test_signature_check_reports_its_count(self):
        # The VM walk measured 18-95 s of `pacman-key --verify`, one call per package, under a
        # window that said only "0 % · Prepare the disk · Working…".
        packages = [Path(f'/repo/p{n}.pkg.tar.zst') for n in range(5)]
        clock = iter([0.0, 0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1])
        with patch('emaki_installer.worker.time.monotonic', side_effect=lambda: next(clock, 2.0)):
            events, _, _, runners = self.execute(resolved=packages, verify=True)
        verified = [c[-1] for c, _ in runners[0].commands if c[:2] == ['pacman-key', '--verify']]
        self.assertEqual(verified, [str(p) for p in packages])
        first_state = next(i for i, e in enumerate(events) if e['type'] == 'state')
        before = events[:first_state]
        self.assertTrue(before and all(e['type'] == 'progress' for e in before), before)
        counts = [(e['activity']['name'], e['activity']['done'], e['activity']['total']) for e in before]
        # The first and the last count always, the rest at most four times a second.
        self.assertEqual(counts[0], ('signatures', 0, 5))
        self.assertEqual(counts[-1], ('signatures', 5, 5))
        self.assertLess(len(counts), 6, counts)
        self.assertEqual([done for _, done, _ in counts], sorted(done for _, done, _ in counts))
        for event in before:
            self.assertEqual((event['phase'], event['total_pct'], event['indeterminate']), ('prepare_disk', 0, True))
        # The phase's own events carry no activity: the window drops the count with them.
        self.assertNotIn('activity', events[first_state])

    def test_timed_out_update_download_installs_nothing_and_the_install_completes(self):
        def timeout(argv):
            if '-Syuw' in argv:
                raise InstallError(Code.COMMAND_FAILED, 'arch-chroot timed out.')
            return False

        logs = []
        with tempfile.TemporaryDirectory() as temp:
            events, phases, _, runners = self.execute(online=True, real=('update',), runner_fail=timeout,
                                                      target=Path(temp), log=logs.append)
        commands = [c for runner in runners for c, _ in runner.commands]
        self.assertTrue(any('-Syuw' in c for c in commands))
        self.assertFalse(any('-Su' in c for c in commands))
        self.assertIs(self.worker.update_attempted, False)
        self.assertIn('WARNING: online update failed; the offline installation is retained: arch-chroot timed out.', logs)
        update = [e for e in events if e['type'] == 'state' and e['phase'] == 'update']
        self.assertEqual(update[-1]['phase_pct'], 100)
        self.assertEqual(phases[-1], 'finish')
        self.assertEqual(events[-1]['type'], 'done')

    def test_partial_update_reaches_the_done_event_after_the_images_are_rebuilt(self):
        logs = []
        with tempfile.TemporaryDirectory() as temp, patch('emaki_installer.worker.LOG') as log:
            log.read_bytes.return_value = b'log\n'
            target = Path(temp)
            finishable(target)
            events, _, _, runners = self.execute(online=True, real=('update', 'finish'), target=target,
                                                 runner_fail=lambda argv: '-Su' in argv, log=logs.append)
            marker = (target / MARKER.lstrip('/')).exists()
        commands = [c[2:] for runner in runners for c, _ in runner.commands]
        rebuilt = commands[next(i for i, c in enumerate(commands) if '-Su' in c) + 1:]
        self.assertIn(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], rebuilt)
        self.assertIn(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], rebuilt)
        self.assertFalse(marker)
        finish = [e for e in events if e['type'] == 'state' and e['phase'] == 'finish']
        self.assertEqual(finish[-1]['phase_pct'], 100)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [PARTIAL_UPDATE])
        self.assertFalse(any('offline installation is retained' in line for line in logs), logs)

    def test_a_lock_kept_for_a_live_pacman_is_removed_once_it_is_gone(self):
        # The timed-out download's pacman outlived the Runner's SIGKILL, so its lock was kept;
        # it is gone by the final cleanup. The installed system's pacman refuses to run while
        # /var/lib/pacman/db.lck exists.
        logs, alive = [], []
        with tempfile.TemporaryDirectory() as temp, patch('emaki_installer.worker.LOG') as log:
            log.read_bytes.return_value = b'log\n'
            target = Path(temp)
            finishable(target)
            lock = target / 'var/lib/pacman/db.lck'

            def timeout(argv):
                if '-Syuw' in argv:
                    lock.parent.mkdir(parents=True, exist_ok=True)
                    lock.write_text('')
                    alive.append(4242)
                    raise InstallError(Code.COMMAND_FAILED, 'arch-chroot timed out.')
                return False

            def processes(root):
                running = [(pid, target) for pid in alive]
                alive.clear()  # It ends right after this look.
                return running

            with patch('emaki_installer.worker.processes_under', side_effect=processes), \
                    patch('emaki_installer.worker.process_name', return_value='pacman'):
                events, _, _, _ = self.execute(online=True, real=('update', 'finish'), runner_fail=timeout,
                                               target=target, log=logs.append, cleanup_effect=cleanup)
            kept = lock.exists()
        self.assertIn('WARNING: pacman is still running in the target; its lock is kept.', logs)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertFalse(kept)

    def test_done_without_a_partial_update_has_no_warnings(self):
        events, _, _, _ = self.execute()
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [])

    def install_to_the_end(self, *, online_at_end, **kwargs):
        """The real update and finish phases on a finishable target; the rest is stubbed."""
        logs = []
        with tempfile.TemporaryDirectory() as temp, patch('emaki_installer.worker.LOG') as log, \
                patch('emaki_installer.worker.default_route', return_value=online_at_end) as route:
            log.read_bytes.return_value = b'log\n'
            target = Path(temp)
            finishable(target)
            # The final cleanup is stubbed: a lock left here was left by the phases themselves.
            events, _, _, runners = self.execute(real=('update', 'finish'), target=target, log=logs.append, **kwargs)
            lock = (target / 'var/lib/pacman/db.lck').exists()
        commands = [(c[2:], kwargs) for runner in runners for c, kwargs in runner.commands
                    if c[:1] == ['arch-chroot'] and 'pacman' in c]
        return events, commands, logs, lock, route

    def test_without_the_update_an_online_install_still_gets_package_lists(self):
        # B4/C1 of the VM walk: "Update Emaki at the end" off left no sync databases, and the
        # installed system's first `pacman -S` failed until `pacman -Sy`. Lists newer than the
        # USB's packages make that first `pacman -S` a partial upgrade: the done page says to
        # upgrade first. Also with the update on but no network while it would have run.
        for online, update in ((True, False), (False, True)):
            with self.subTest(online_at_start=online, update=update):
                events, commands, _, _, route = self.install_to_the_end(
                    online=online, update=update, online_at_end=True)
                self.assertEqual([c for c, _ in commands], [['pacman', '-Sy']])
                self.assertEqual(commands[0][1]['timeout'], REFRESH_TIMEOUT)
                route.assert_called_once_with()
                self.assertEqual(events[-1]['type'], 'done')
                self.assertEqual(events[-1]['warnings'], [UPGRADE_FIRST])
                self.assertIs(self.worker.upgraded, False)
                update = [e for e in events if e['type'] == 'state' and e['phase'] == 'update']
                self.assertIsNone(update[-1]['phase_pct'], 'the update itself stays skipped')

    def test_lists_synced_by_the_keyring_alone_still_say_to_upgrade(self):
        # The small archlinux-keyring download synced the lists; the update's own download then
        # timed out, so nothing was upgraded. No second refresh: the lists are there.
        def timeout(argv):
            if '-Syuw' in argv:
                raise InstallError(Code.COMMAND_FAILED, 'arch-chroot timed out.')
            return False

        events, commands, logs, _, route = self.install_to_the_end(
            online=True, online_at_end=True, runner_fail=timeout)
        self.assertEqual([c[c.index('pacman') + 1] for c, _ in commands], ['-Syw', '-S', '-Syuw'])
        self.assertIn('WARNING: online update failed; the offline installation is retained: arch-chroot timed out.', logs)
        route.assert_not_called()
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [UPGRADE_FIRST])
        self.assertIs(self.worker.synced, True)
        self.assertIs(self.worker.upgraded, False)

    def test_offline_at_the_end_the_done_page_says_what_to_run(self):
        for online in (False, True):
            with self.subTest(online_at_start=online):
                events, commands, _, _, _ = self.install_to_the_end(online=online, update=False, online_at_end=False)
                self.assertEqual(commands, [])
                self.assertEqual(events[-1]['type'], 'done')
                self.assertEqual(events[-1]['warnings'], [NO_PACKAGE_LISTS])

    def test_a_failed_refresh_only_warns_and_leaves_no_lock(self):
        def refused(argv):
            if argv[2:] == ['pacman', '-Sy']:
                # A stopped pacman keeps its lock: the installed pacman would refuse to run.
                lock = Path(argv[1]) / 'var/lib/pacman/db.lck'
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_text('')
                raise InstallError(Code.COMMAND_FAILED, 'arch-chroot timed out.')
            return False

        events, commands, logs, lock, _ = self.install_to_the_end(
            online=True, update=False, online_at_end=True, runner_fail=refused)
        self.assertEqual([c for c, _ in commands], [['pacman', '-Sy']])
        self.assertIn('WARNING: the package lists were not downloaded: arch-chroot timed out.', logs)
        self.assertFalse(lock)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [NO_PACKAGE_LISTS])

    def test_an_update_that_downloaded_needs_no_second_refresh(self):
        # The happy path: the full upgrade completed, so the done page has nothing to add.
        events, commands, _, _, route = self.install_to_the_end(online=True, online_at_end=True)
        self.assertNotIn(['pacman', '-Sy'], [c for c, _ in commands])
        self.assertTrue(any('-Syuw' in c for c, _ in commands), commands)
        self.assertTrue(any('-Su' in c for c, _ in commands), commands)
        route.assert_not_called()
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [])
        self.assertIs(self.worker.upgraded, True)

    def test_a_failed_update_download_is_not_retried_at_the_end(self):
        # The limited download already waited for this network; the person is told instead.
        events, commands, _, _, _ = self.install_to_the_end(
            online=True, online_at_end=True, runner_fail=lambda argv: '-Syuw' in argv or '-Syw' in argv)
        self.assertNotIn(['pacman', '-Sy'], [c for c, _ in commands])
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['warnings'], [NO_PACKAGE_LISTS])

    def test_test_mode_without_keys_fails_before_any_disk_work(self):
        with tempfile.TemporaryDirectory() as temp:
            live = Path(temp)
            events, phases, cleanups, _ = self.execute(test_mode=True, live=live)
            self.assertEqual(events[-1]['type'], 'error')
            self.assertEqual(events[-1]['code'], 'bad_config')
            self.assertEqual(events[-1]['message'], 'Test mode requires /etc/emaki-test/authorized_keys.')
            self.assertIs(events[-1]['retryable'], True)
            self.assertEqual(phases, [])
            self.assertEqual(cleanups, [])
            self.factory.assert_not_called()
            self.assertFalse(self.worker.disk_changed)
            key = live / 'etc/emaki-test/authorized_keys'
            key.parent.mkdir(parents=True)
            key.write_text('ssh-ed25519 TEST-ONLY\n')
            events, phases, _, _ = self.execute(test_mode=True, live=live)
            self.assertEqual(events[-1]['type'], 'done')
            self.factory.assert_called_once()


class EncryptedWriteBoundaryTests(unittest.TestCase):
    """Exercise the worker with real inventory parsing and simulated probe commands."""

    def run_probe_case(self, mode, *, confirmed=False, change_at=None, change='encrypted', kind='crypto_LUKS'):
        from emaki_installer.inventory import Inventory
        from emaki_installer.constants import GIB, MIB
        from support import manual

        payload = json.loads((FIXTURES / 'lsblk-blank.json').read_text())
        disk = payload['blockdevices'][0]
        path = '/dev/vda' if mode == 'erase' else '/dev/vda2'
        if mode == 'manual':
            disk.update(pttype='gpt', children=[
                {'name': f'/dev/vda{n}', 'path': f'/dev/vda{n}', 'type': 'part',
                 'partn': n, 'start': start // 512, 'size': size, 'fstype': fs,
                 'uuid': f'plain-{n}', 'partuuid': f'part-{n}', 'mountpoints': [None],
                 'ro': False, 'parttype': ('c12a7328-f81f-11d2-ba4b-00a0c93ec93b' if n == 1 else '')}
                for n, start, size, fs in ((1, MIB, GIB, 'vfat'),
                                           (2, GIB + MIB, 38 * GIB, 'ext4'))])
        state = {'encrypted': confirmed, 'uuid': 'encrypted-before', 'held': False}
        commands, events, order = [], [], []

        def probe_command(argv):
            commands.append(argv)
            if argv[0] == 'lsblk':
                order.append('probe')
                return json.dumps(payload)
            if argv[0] == 'blkid':
                device = argv[-1]
                if device == path and state['encrypted']:
                    return f"TYPE={kind}\nUUID={state['uuid']}\n"
                if mode == 'manual' and device != '/dev/vda':
                    n = int(device[-1])
                    return f"TYPE={'vfat' if n == 1 else 'ext4'}\nUUID=plain-{n}\n"
                raise InstallError(Code.COMMAND_FAILED, 'No signature.', returncode=2)
            return ''

        def mutate():
            if change == 'uuid':
                state['uuid'] = 'encrypted-after'
            elif change == 'held':
                state['held'] = True
            elif change == 'layout':
                disk['disk-seq'] += 1
            else:
                state['encrypted'] = True

        inv = Inventory(SimpleNamespace(run=probe_command))
        backend = Mock()
        backend.prepare.side_effect = lambda: order.append('write')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(inv, '_boot_sources', return_value=({'/dev/sr0'}, True)))
            stack.enter_context(patch.object(inv, '_stable_ids', return_value={}))
            stack.enter_context(patch.object(inv, '_holders', side_effect=lambda nodes: {path: state['held']}))
            # Filesystem inspection is separate from signature and holder detection.
            stack.enter_context(patch.object(inv, '_inspect'))
            stack.enter_context(patch('emaki_installer.inventory._online', return_value=False))
            stack.enter_context(patch('emaki_installer.inventory.firmware', return_value=(True, 64)))
            stack.enter_context(patch('emaki_installer.inventory.memory_size', return_value=4 * GIB))
            settings = config(fs='ext4') if mode == 'erase' else manual(fs='ext4')
            if mode == 'manual':
                settings['mounts'][0]['format'] = True
            if confirmed:
                settings['confirmed_encrypted'] = [{'path': path, 'type': kind,
                                                    'uuid': state['uuid']}]
            plan = make_plan(settings, inv.probe())
            if change_at == 'start':
                mutate()
            stack.enter_context(patch('emaki_installer.worker.preflight_repo', return_value=[]))

            def verify(*args, **kwargs):
                order.append('signatures')
                if change_at == 'signatures':
                    mutate()

            stack.enter_context(patch('emaki_installer.worker.verify_packages', side_effect=verify))
            stack.enter_context(patch('emaki_installer.worker.cleanup'))
            target = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            worker = Worker(None, inv, Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
                            lambda line: None, backend_factory=Mock(return_value=backend),
                            runner_factory=RecordingRunner, target=target)
            for phase in PHASES:
                if phase != 'prepare_disk':
                    stack.enter_context(patch.object(worker, phase))
            worker.run(plan, threading.Event())
        self.assertTrue(any(cmd[:2] == ['blkid', '--probe'] and cmd[-1] == path for cmd in commands))
        return events[-1], backend, order, worker

    def test_worker_probe_refuses_unconfirmed_and_accepts_confirmed_encryption(self):
        for mode in ('erase', 'manual'):
            with self.subTest(mode=mode, confirmed=False):
                event, backend, _, worker = self.run_probe_case(mode, change_at='start')
                self.assertEqual(event['code'], 'encrypted_confirmation')
                self.assertIn('ERASE', event['message'])
                self.assertTrue(event['retryable'])
                backend.prepare.assert_not_called()
                self.assertFalse(worker.disk_changed)
            with self.subTest(mode=mode, confirmed=True):
                event, backend, order, worker = self.run_probe_case(mode, confirmed=True)
                self.assertEqual(event['type'], 'done')
                backend.prepare.assert_called_once_with()
                self.assertTrue(worker.disk_changed)
                self.assertEqual(order[-3:], ['signatures', 'probe', 'write'])

    def test_changes_during_signature_verification_refuse_before_first_write(self):
        for mode in ('erase', 'manual'):
            for change, confirmed, code in (('encrypted', False, 'encrypted_confirmation'),
                                             ('uuid', True, 'encrypted_confirmation'),
                                             ('held', True, 'disk_busy'),
                                             ('layout', True, 'disk_changed')):
                with self.subTest(mode=mode, change=change):
                    event, backend, order, worker = self.run_probe_case(
                        mode, confirmed=confirmed, change_at='signatures', change=change)
                    self.assertEqual(event['code'], code)
                    self.assertTrue(event['retryable'])
                    self.assertEqual(order[-2:], ['signatures', 'probe'])
                    backend.prepare.assert_not_called()
                    self.assertFalse(worker.disk_changed)

    def test_apfs_confirmation_is_rechecked_before_disk_writes(self):
        for mode in ('erase', 'manual'):
            for confirmed, change_at, change in ((False, 'signatures', 'encrypted'),
                                                  (True, 'signatures', 'uuid'),
                                                  (True, None, None)):
                with self.subTest(mode=mode, confirmed=confirmed, change=change):
                    event, backend, order, worker = self.run_probe_case(
                        mode, confirmed=confirmed, change_at=change_at, change=change, kind='apfs')
                    if change_at:
                        self.assertEqual(event['code'], 'encrypted_confirmation')
                        self.assertIn('macOS disk (APFS)', event['message'])
                        self.assertEqual(order[-2:], ['signatures', 'probe'])
                        backend.prepare.assert_not_called()
                        self.assertFalse(worker.disk_changed)
                    else:
                        self.assertEqual(event['type'], 'done')
                        self.assertEqual(order[-3:], ['signatures', 'probe', 'write'])
                        backend.prepare.assert_called_once_with()
                        self.assertTrue(worker.disk_changed)


class BootAndSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=self.root)
        self.worker.plan = make_plan(config(), inventory())
        for p in self.worker.plan.partitions:
            p.path = '/dev/test' + str(p.number)
        self.worker.runner = RecordingRunner()

    def tearDown(self):
        self.temp.cleanup()

    def boot_files(self):
        f = self.worker.files
        f.write('/etc/default/grub-btrfs/config', '# Package defaults\nGRUB_BTRFS_SUBMENUNAME="Arch Linux snapshots"\n')
        # emaki-config's snapshot boot hook, strapped by copy_packages.
        for directory in ('hooks', 'install'):
            f.write(SNAPSHOT_HOOK.format(directory), directory)
        for kernel in ('linux', 'linux-lts'):
            f.write('/etc/mkinitcpio.d/' + kernel + '.preset', 'preset')
            f.write('/boot/vmlinuz-' + kernel, 'kernel')
            f.write('/boot/initramfs-' + kernel + '.img', 'initramfs')
        f.write('/boot/grub/grub.cfg', 'linux /@/boot/vmlinuz-linux root=x\nlinux /@/boot/vmlinuz-linux-lts root=x\n')
        fstab = []
        for part, mp, sv in self.worker.plan.mounts:
            opts = part.options + (['subvol=/' + sv] if sv else [])
            fstab.append(f'UUID=uuid-{part.number} {mp} {part.fs} {",".join(opts)} 0 0')
        self.worker.runner.responses[('genfstab', '-U', '-f', str(self.root), str(self.root))] = '\n'.join(fstab)

    def test_grub_both_vendor_and_removable_on_empty_esp(self):
        self.boot_files()
        self.worker.bootloader()
        grub = [c for c, _ in self.worker.runner.commands if 'grub-install' in c]
        self.assertEqual(len(grub), 2)
        self.assertNotIn('--removable', grub[0])
        self.assertIn('--removable', grub[1])
        for c in grub:
            self.assertIn('--efi-directory=/efi', c)
            self.assertIn('--boot-directory=/boot', c)
            self.assertIn('--bootloader-id=Emaki', c)

    def test_snapshot_hook_comes_from_emaki_config_not_the_installer(self):
        # The image build reads the packaged hook; nothing shadows it in /etc/initcpio,
        # which mkinitcpio searches first.
        self.assertIn('emaki-config', PACKAGES)
        self.assertLess(list(PHASES).index('copy_packages'), list(PHASES).index('bootloader'))
        for fs in ('btrfs', 'ext4'):
            with self.subTest(fs=fs), tempfile.TemporaryDirectory() as temp:
                self.root = self.worker.target = Path(temp)
                self.worker.files = TargetFiles(self.root)
                self.worker.plan = make_plan(config(fs=fs), inventory())
                self.boot_files()
                built = []

                def inspect(command):
                    # The runner sees the whole chroot argv; `built` proves this check ran.
                    if 'mkinitcpio' in command:
                        built.append(command)
                        self.assertFalse(self.worker.files.path('/etc/initcpio').exists())
                        for directory in ('hooks', 'install'):
                            self.assertEqual(self.worker.files.read(SNAPSHOT_HOOK.format(directory)), directory)
                    return False

                self.worker.runner.fail = inspect
                self.worker.bootloader()
                self.assertEqual(len(built), 1)

    def test_btrfs_image_is_not_built_without_the_packaged_snapshot_hook(self):
        for directory in ('hooks', 'install'):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as temp:
                self.root = self.worker.target = Path(temp)
                self.worker.files = TargetFiles(self.root)
                self.worker.plan = make_plan(config(fs='btrfs'), inventory())
                self.worker.runner = RecordingRunner()
                self.boot_files()
                self.worker.files.path(SNAPSHOT_HOOK.format(directory)).unlink()
                with self.assertRaises(InstallError) as caught:
                    self.worker.bootloader()
                self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
                self.assertFalse(any('mkinitcpio' in c for c, _ in self.worker.runner.commands))
                self.assertFalse(self.worker.files.path('/etc/initcpio').exists())

    def test_existing_foreign_fallback_preserved_without_microsoft(self):
        self.boot_files()
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'foreign')
        self.worker.bootloader()
        grub = [c for c, _ in self.worker.runner.commands if 'grub-install' in c]
        self.assertEqual(len(grub), 1)
        self.assertEqual((self.root / 'efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), b'foreign')

    @alongside_on()
    def test_alongside_grub_preserves_loaders_and_uses_chroot_os_prober(self):
        self.boot_files()
        self.worker.plan.config['mode'] = 'alongside'
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'foreign')
        self.worker.files.write('/efi/EFI/Microsoft/Boot/bootmgfw.efi', b'microsoft')
        from emaki_installer.inventory import efi_loaders
        self.worker.plan.disk['partitions'] = [{'esp': True, '_efi_loaders': efi_loaders(self.root / 'efi')}]
        self.worker.files.write('/boot/grub/grub.cfg', self.worker.files.read('/boot/grub/grub.cfg') +
                                "menuentry 'Windows Boot Manager (on /dev/vda1)' {\n}\n")
        self.worker.bootloader()
        commands = [c for c, _ in self.worker.runner.commands]
        self.assertIn(['arch-chroot', str(self.root), 'grub-mkconfig', '-o', '/boot/grub/grub.cfg'], commands)
        self.assertIn('GRUB_DISABLE_OS_PROBER=false', self.worker.files.read('/etc/default/grub'))
        self.assertIn('GRUB_DEFAULT=0', self.worker.files.read('/etc/default/grub'))
        self.assertFalse(any('--removable' in c for c in commands))
        self.assertEqual(self.worker.files.read('/efi/EFI/Microsoft/Boot/bootmgfw.efi'), 'microsoft')
        self.worker.files.write('/efi/EFI/Microsoft/Boot/bootmgfw.efi', b'changed')
        self.worker.runner.commands.clear()
        with self.assertRaisesRegex(InstallError, 'differ from the reviewed'):
            self.worker.bootloader()
        self.assertEqual(self.worker.runner.commands, [])

    @alongside_on()
    def test_alongside_missing_windows_menu_is_fatal(self):
        self.boot_files()
        self.worker.plan.config['mode'] = 'alongside'
        with self.assertRaisesRegex(InstallError, 'not discovered by os-prober'):
            self.worker.grub_config()

    def test_known_emaki_fallback_can_be_refreshed(self):
        self.boot_files()
        self.worker.files.write('/efi/EFI/Emaki/grubx64.efi', b'emaki-loader')
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'emaki-loader')
        self.worker.bootloader()
        self.assertTrue(any('--removable' in c for c, _ in self.worker.runner.commands))

    def test_microsoft_directory_alone_does_not_prevent_fallback_write(self):
        self.boot_files()
        self.worker.files.mkdir('/efi/EFI/Microsoft')
        self.worker.bootloader()
        self.assertTrue(any('--removable' in c for c, _ in self.worker.runner.commands))

    def test_nvram_failure_retries_then_installs_removable(self):
        self.boot_files()
        self.worker.runner.fail = lambda c: 'grub-install' in c and '--no-nvram' not in c and '--removable' not in c
        self.worker.bootloader()
        grub = [c for c, _ in self.worker.runner.commands if 'grub-install' in c]
        self.assertEqual(len(grub), 3)
        self.assertIn('--no-nvram', grub[1])
        self.assertIn('--removable', grub[2])

    def test_nvram_failure_with_foreign_fallback_fails_loudly(self):
        self.boot_files()
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'foreign')
        self.worker.runner.fail = lambda c: 'grub-install' in c and '--no-nvram' not in c
        with self.assertRaises(InstallError) as raised:
            self.worker.bootloader()
        self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
        self.assertFalse(any('--removable' in c for c, _ in self.worker.runner.commands))
        self.assertEqual(self.worker.files.read('/efi/EFI/BOOT/BOOTX64.EFI'), 'foreign')

    def test_removable_failure_warns_when_nvram_worked(self):
        self.boot_files()
        self.worker.log = Mock()
        self.worker.runner.fail = lambda c: '--removable' in c
        self.worker.bootloader()
        self.assertTrue(any('WARNING' in call.args[0] for call in self.worker.log.call_args_list))

    def test_both_boot_paths_failing_is_fatal(self):
        self.boot_files()
        self.worker.runner.fail = lambda c: 'grub-install' in c
        with self.assertRaises(InstallError) as raised:
            self.worker.bootloader()
        self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)

    def test_snapper_unmount_create_delete_remount_order(self):
        f = self.worker.files
        f.mkdir('/.snapshots')
        runner = self.worker.runner
        original = runner.run

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            if 'create-config' in argv:
                f.mkdir('/.snapshots')
                f.write('/etc/snapper/configs/root', 'SUBVOLUME="/"\nNUMBER_LIMIT="50"\nTIMELINE_CREATE="no"\n')
            if 'delete' in argv:
                (self.root / '.snapshots').rmdir()
            if 'snapper' in argv and 'create' in argv:
                (self.root / '.snapshots/1/snapshot').mkdir(parents=True)
                return '1\n'
            return result

        runner.run = run
        self.worker.grub_config = Mock()
        self.worker.snapshot()
        cmds = [c for c, _ in runner.commands]
        unmount = next(i for i, c in enumerate(cmds) if c[0] == 'umount')
        create = next(i for i, c in enumerate(cmds) if 'create-config' in c)
        delete = next(i for i, c in enumerate(cmds) if 'delete' in c)
        remount = next(i for i, c in enumerate(cmds) if c[0] == 'mount')
        self.assertLess(unmount, create)
        self.assertLess(create, delete)
        self.assertLess(delete, remount)
        self.assertIn('subvol=@snapshots', cmds[remount][4])
        self.assertEqual((self.root / '.snapshots').stat().st_mode & 0o777, 0o750)

    def test_fresh_install_snapshot_does_not_contain_the_incomplete_marker(self):
        f = self.worker.files
        f.mkdir('/.snapshots')
        f.write('/etc/snapper/configs/root', 'SUBVOLUME="/"\nNUMBER_LIMIT="50"\nTIMELINE_CREATE="no"\n')
        f.write(MARKER, 'Emaki installation in progress\n', 0o600)
        marker = self.root / MARKER.lstrip('/')
        snapshot = self.root / '.snapshots/1/snapshot'
        runner = self.worker.runner
        original, seen = runner.run, []

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            if 'snapper' in argv and 'create' in argv:
                # The live root, marker included, is what snapper copies.
                seen.append(marker.exists())
                snapshot.mkdir(parents=True, exist_ok=True)
                if marker.exists():
                    (snapshot / MARKER.lstrip('/')).write_bytes(marker.read_bytes())
                return '1\n'
            if argv[:3] == ['btrfs', 'property', 'set']:
                seen.append((snapshot / MARKER.lstrip('/')).exists())
            return result

        runner.run = run
        self.worker.grub_config = Mock()
        self.worker.snapshot()
        # The marker never leaves the live root; the snapshot loses it before it turns read-only.
        self.assertEqual(seen, [True, False])
        create = next(c for c, _ in runner.commands if 'snapper' in c and 'create' in c)
        self.assertIn('--read-write', create)
        self.assertIn('--print-number', create)
        self.assertEqual(runner.commands[-1][0], ['btrfs', 'property', 'set', '-ts', str(snapshot), 'ro', 'true'])
        self.assertFalse((snapshot / MARKER.lstrip('/')).exists())
        self.assertEqual(marker.read_text(), 'Emaki installation in progress\n')
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        # A failing snapshot still leaves the installation marked incomplete.
        runner.fail = lambda argv: 'snapper' in argv and 'create' in argv
        with self.assertRaises(InstallError):
            self.worker.snapshot()
        self.assertTrue(marker.exists())

    def test_snapper_failure_remounts_but_never_deletes_unknown_data(self):
        self.worker.files.mkdir('/.snapshots')
        self.worker.files.write('/.snapshots/keep', 'data')
        with self.assertRaises(OSError):
            self.worker.snapshot()
        commands = [c for c, _ in self.worker.runner.commands]
        self.assertEqual(commands[-1][0], 'mount')
        self.assertFalse(any('delete' in c for c in commands))
        self.assertEqual((self.root / '.snapshots/keep').read_text(), 'data')

    def pacman_commands(self):
        """The update's pacman calls: snap-pac skipped, downloads with line-buffered output.

        Through a pipe pacman 7.1 prints ' <file> downloading...' without flushing; without
        stdbuf the lines arrive in one burst at the end (VM walk run E, and on the host).
        """
        commands = [(c[2:], kwargs) for c, kwargs in self.worker.runner.commands
                    if c[:2] == ['arch-chroot', str(self.root)] and 'pacman' in c]
        result = []
        for command, kwargs in commands:
            self.assertEqual(command[:2], ['env', 'SNAP_PAC_SKIP=y'], command)
            rest = command[2:]
            if any(arg[:1] == '-' and arg[1:2] != '-' and 'w' in arg for arg in rest):
                self.assertEqual(rest[:2], ['stdbuf', '-oL'], command)
                self.assertTrue(callable(kwargs.get('watch')), command)
                rest = rest[2:]
            else:
                self.assertNotIn('watch', kwargs, command)
            self.assertEqual(rest[0], 'pacman', command)
            result.append((rest, kwargs))
        return result

    def test_update_downloads_show_their_count(self):
        # Run E: 7 minutes of "93 % · Check for updates · Working…", 444 s of them in the
        # package lists. The keyring step gets run E's own lines (lists, nothing to do), the
        # download gets a transaction recorded in the same run (pacstrap: 144 packages).
        lists = (FIXTURES / 'pacman-download-update.txt').read_text().splitlines()
        transaction = (FIXTURES / 'pacman-download-pacstrap.txt').read_text().splitlines()
        events = []
        self.worker.emit = lambda kind, **fields: events.append({'type': kind, **fields})
        self.worker.phase, self.worker.completed = 'update', 93
        original = self.worker.runner.run

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            lines = lists if '-Syw' in argv else transaction if '-Syuw' in argv else []
            for line in lines:
                kwargs['watch'](line)
            return result

        self.worker.runner.run = run
        self.worker.update()
        self.assertEqual([c[1] for c, _ in self.pacman_commands()], ['-Syw', '-S', '-Syuw', '-Su'])
        self.assertTrue(all(e['type'] == 'progress' and e['phase'] == 'update' and e['total_pct'] == 93
                            and e['indeterminate'] for e in events), events)
        counts = [(e['activity']['done'], e['activity']['total']) if e['activity'] else 'end' for e in events]
        self.assertEqual(counts, [(None, None), 'end',
                                  (None, None), (0, 144), *[(n, 144) for n in range(1, 145)], 'end'])
        self.assertTrue(all(e['activity']['name'] == 'downloads' for e in events if e['activity']))

    def test_the_download_count_reads_pacman_as_recorded(self):
        def counts(name):
            reports = []
            watch = worker_module.DownloadCount(lambda done, total: reports.append((done, total)))
            for line in (FIXTURES / name).read_text().splitlines():
                watch.line(line)
            return reports

        # The package lists come first, without a count; the lists' own lines are not packages.
        self.assertEqual(counts('pacman-download-update.txt'), [(None, None)])
        self.assertEqual(counts('pacman-download-pacstrap.txt'),
                         [(None, None), (0, 144), *[(n, 144) for n in range(1, 145)]])

    def test_update_transactions_take_no_snap_pac_snapshots(self):
        # Once the snapshot phase has made the root config, snap-pac 3.0.1 snapshots / before
        # and after every pacman transaction, and the live root carries the marker until
        # finish(): such a snapshot would bring the marker back on a rollback.
        self.worker.files.write(MARKER, 'Emaki installation in progress\n', 0o600)
        marker = self.root / MARKER.lstrip('/')
        transactions, snapshots = [], []
        original = self.worker.runner.run

        def run(argv, **kwargs):
            command, environment = argv[2:], {}
            if command[:1] == ['env']:
                command = command[1:]
                while command and '=' in command[0]:
                    key, value = command.pop(0).split('=', 1)
                    environment[key] = value
            # pacman runs hooks with its own environment (libalpm util.c, execv), but never
            # for a download-only transaction (trans.c returns before the hooks).
            if command[:1] == ['pacman'] and not any(
                    arg[:1] == '-' and arg[1:2] != '-' and 'w' in arg for arg in command[1:]):
                transactions.append(command[1])
                # snap-pac's check_skip().
                if environment.get('SNAP_PAC_SKIP', 'n').lower() not in ('y', 'yes', 'true', '1'):
                    snapshots.append((command[1], marker.exists()))
            return original(argv, **kwargs)

        self.worker.runner.run = run
        self.worker.update()
        self.assertEqual(transactions, ['-S', '-Su'])
        self.assertEqual(snapshots, [])
        self.assertTrue(marker.exists())

    def test_online_update_failure_only_warns(self):
        logs = []
        self.worker.log = logs.append
        self.worker.runner.fail = lambda command: 'pacman' in command
        self.worker.update()
        self.assertFalse(self.worker.update_attempted)
        self.assertTrue(any(line.startswith('WARNING: online update failed') for line in logs))
        # Nothing was installed: the person is not told to finish an update.
        self.assertEqual(self.worker.warnings, [])

    def test_online_update_downloads_with_a_limit_then_installs_without_one(self):
        self.worker.update()
        commands = self.pacman_commands()[-2:]
        self.assertEqual([c for c, _ in commands], [['pacman', '-Syuw', '--noconfirm'], ['pacman', '-Su', '--noconfirm']])
        self.assertGreater(commands[0][1]['timeout'], 0)
        self.assertIsNone(commands[1][1].get('timeout'))
        self.assertTrue(self.worker.update_attempted)

    def test_keyring_is_refreshed_before_the_update_download(self):
        self.worker.update()
        commands = self.pacman_commands()
        self.assertEqual([c for c, _ in commands][:2], [['pacman', '-Syw', '--needed', '--noconfirm', 'archlinux-keyring'],
                                                        ['pacman', '-S', '--needed', '--noconfirm', 'archlinux-keyring']])
        self.assertEqual(commands[0][1]['timeout'], commands[2][1]['timeout'])
        self.assertIsNone(commands[1][1].get('timeout'))

    def test_failing_keyring_refresh_still_leads_to_the_download(self):
        for failing in ('-Syw', '-S'):
            with self.subTest(failing=failing):
                logs = []
                self.worker.log = logs.append
                self.worker.runner = RecordingRunner()
                self.worker.runner.fail = lambda command: failing in command
                self.worker.update()
                self.assertEqual([c[1] for c, _ in self.pacman_commands()][-2:], ['-Syuw', '-Su'])
                self.assertTrue(any(line.startswith('WARNING: the Arch keyring was not refreshed') for line in logs))
                self.assertTrue(self.worker.update_attempted)

    def update_with_emaki_down(self, failing, output=EMAKI_DOWN):
        """Fail the limited downloads that still include [emaki] with a recorded pacman output."""
        run_dir = self.root / 'run'
        run_dir.mkdir()
        self.worker.files.write('/etc/pacman.conf', TARGET_PACMAN_CONF)
        logs, seen = [], []
        self.worker.log = logs.append
        original = self.worker.runner.run

        def run(argv, **kwargs):
            if '--config' in argv:
                seen.append(Path(argv[argv.index('--config') + 1]).read_text())
            result = original(argv, **kwargs)
            if any(flag in argv for flag in failing) and '--config' not in argv:
                raise pacman_failure(output)
            return result

        self.worker.runner.run = run
        with patch('emaki_installer.worker.WORK', run_dir):
            self.worker.update()
        return logs, seen, run_dir

    def test_unreachable_emaki_repository_retries_once_without_it(self):
        logs, seen, run_dir = self.update_with_emaki_down(('-Syw', '-Syuw'))
        commands = [c for c, _ in self.pacman_commands()]
        config = ['--config', str(run_dir / 'update-pacman.conf')]
        self.assertEqual(commands, [
            ['pacman', '-Syw', '--needed', '--noconfirm', 'archlinux-keyring'],
            ['pacman', '-Syw', '--needed', '--noconfirm', 'archlinux-keyring', *config],
            ['pacman', '-S', '--needed', '--noconfirm', 'archlinux-keyring', *config],
            ['pacman', '-Syuw', '--noconfirm', *config],
            ['pacman', '-Su', '--noconfirm', *config]])
        retried = [kwargs for c, kwargs in self.pacman_commands() if c[1] in ('-Syw', '-Syuw')]
        self.assertTrue(all(kwargs['timeout'] > 0 for kwargs in retried))
        self.assertTrue(seen)
        for text in seen:
            self.assertNotIn('[emaki]', text)
            self.assertNotIn('emaki-mirrorlist', text)
            self.assertIn('[core]\nInclude = /etc/pacman.d/mirrorlist', text)
            self.assertIn('[extra]\nInclude = /etc/pacman.d/mirrorlist', text)
            self.assertIn('SigLevel    = Required DatabaseOptional', text)
        self.assertEqual(self.worker.files.read('/etc/pacman.conf'), TARGET_PACMAN_CONF)
        self.assertFalse((run_dir / 'update-pacman.conf').exists())
        self.assertTrue(self.worker.update_attempted)
        self.assertIn("Emaki's own repository was unreachable; Arch packages were updated, "
                      'Emaki packages stay at the USB version.', logs)

    def test_emaki_repository_lost_after_the_keyring_step_is_retried_at_the_download(self):
        logs, seen, run_dir = self.update_with_emaki_down(('-Syuw',))
        commands = [c[1:] for c, _ in self.pacman_commands()]
        config = ['--config', str(run_dir / 'update-pacman.conf')]
        self.assertEqual(commands[-3:], [['-Syuw', '--noconfirm'], ['-Syuw', '--noconfirm', *config],
                                         ['-Su', '--noconfirm', *config]])
        self.assertEqual(len(seen), 2)
        self.assertIn("Emaki's own repository was unreachable; Arch packages were updated, "
                      'Emaki packages stay at the USB version.', logs)

    def test_no_retry_when_more_than_the_emaki_repository_is_unreachable(self):
        logs, seen, _ = self.update_with_emaki_down(('-Syw', '-Syuw'), ALL_DOWN)
        commands = [c for c, _ in self.pacman_commands()]
        self.assertEqual([c[1] for c in commands], ['-Syw', '-Syuw'])
        self.assertFalse(any('--config' in c for c in commands))
        self.assertEqual(seen, [])
        self.assertFalse(self.worker.update_attempted)
        self.assertFalse(any(line.startswith("Emaki's own repository") for line in logs))

    def test_unwritable_retry_config_only_warns(self):
        self.worker.files.write('/etc/pacman.conf', TARGET_PACMAN_CONF)
        logs = []
        self.worker.log = logs.append
        self.worker.runner.run = Mock(side_effect=pacman_failure(EMAKI_DOWN))
        with patch('emaki_installer.worker.WORK', self.root / 'missing'):
            self.worker.update()
        self.assertFalse(self.worker.update_attempted)
        self.assertTrue(any(line.startswith('WARNING: online update failed') and 'Cannot write' in line for line in logs))

    def test_no_retry_after_a_timeout(self):
        # The real Runner and a real timeout. The download has already printed that only
        # [emaki] failed, then hangs; a retry without [emaki] would wait the whole limit again.
        run_dir = self.root / 'run'
        run_dir.mkdir()
        self.worker.files.write('/etc/pacman.conf', TARGET_PACMAN_CONF)
        logs, calls = [], []

        class ShellChroot(Runner):
            def chroot(self, argv, target, **kwargs):
                calls.append(argv)
                script = 'printf "%s" "$1"; exec sleep 30' if '-Syuw' in argv else 'exit 0'
                return self.run(['sh', '-c', script, 'sh', EMAKI_DOWN], **kwargs)

        self.worker.log = logs.append
        self.worker.runner = ShellChroot(logs.append, Redactor())
        started = time.monotonic()
        with patch('emaki_installer.worker.UPDATE_TIMEOUT', 0.5), patch('emaki_installer.worker.WORK', run_dir), \
                patch('emaki_installer.runtime.KILL_GRACE', 0.5):
            self.worker.update()
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("error: failed retrieving file 'emaki.db' from pkgs.emaki.sh : "
                      'Could not resolve host: pkgs.emaki.sh', logs)
        self.assertEqual([argv[argv.index('pacman') + 1] for argv in calls], ['-Syw', '-S', '-Syuw'])
        self.assertFalse(any('--config' in argv for argv in calls))
        self.assertIs(self.worker.update_attempted, False)
        self.assertIn('WARNING: online update failed; the offline installation is retained: sh timed out.', logs)

    def test_failed_install_after_a_download_still_rebuilds_the_images(self):
        finishable(self.root)
        self.worker.runner.fail = lambda command: '-Su' in command
        self.worker.update()
        self.assertTrue(self.worker.update_attempted)
        self.worker.runner.fail = None
        with patch('emaki_installer.worker.LOG') as log:
            log.read_bytes.return_value = b'log\n'
            self.worker.finish()
        commands = [c[2:] for c, _ in self.worker.runner.commands]
        rebuilt = commands[next(i for i, c in enumerate(commands) if '-Su' in c) + 1:]
        self.assertIn(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], rebuilt)
        self.assertIn(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], rebuilt)
        self.assertFalse((self.root / MARKER.lstrip('/')).exists())

    def test_a_partial_update_says_so_and_never_claims_the_offline_installation(self):
        logs = []
        self.worker.log = logs.append
        self.worker.runner.fail = lambda command: '-Su' in command
        self.worker.update()
        self.assertFalse(any('offline installation is retained' in line for line in logs), logs)
        self.assertIn('WARNING: ' + PARTIAL_UPDATE + ' (injected command failure)', logs)
        self.assertEqual(self.worker.warnings, [PARTIAL_UPDATE])

    def test_stale_pacman_lock_is_removed_only_without_a_pacman_in_the_target(self):
        lock = self.root / 'var/lib/pacman/db.lck'
        self.worker.runner.fail = lambda command: '-Syuw' in command
        for running, kept in (([], False), ([(4242, self.root)], True)):
            with self.subTest(running=running):
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_text('')
                with patch('emaki_installer.worker.processes_under', return_value=running), \
                        patch('emaki_installer.worker.process_name', return_value='pacman'):
                    self.worker.update()
                self.assertEqual(lock.exists(), kept)
                self.assertFalse(any('-Su' in c for c, _ in self.pacman_commands()))

    def test_account_password_uses_private_stdin_and_root_is_locked(self):
        self.worker.account()
        calls = self.worker.runner.commands
        password_call = next((command, kwargs) for command, kwargs in calls if 'chpasswd' in command)
        self.assertNotIn('a-secret-for-tests', ' '.join(password_call[0]))
        self.assertEqual(password_call[1]['input'], 'vmuser:a-secret-for-tests\n')
        self.assertTrue(password_call[1]['secret'])
        self.assertEqual(self.worker.plan.config['user']['password'], '')
        self.assertTrue(any(command[-3:] == ['passwd', '-l', 'root'] for command, _ in calls))

    def test_test_mode_keys_permissions_and_preset_before_enable(self):
        self.worker.test_mode = True
        self.worker.backend = SimpleNamespace(instance=SimpleNamespace(pacman=Mock()))
        self.worker.files.write('/etc/passwd', 'vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n')
        self.worker.files.write('/etc/pacman.conf', '[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n[extra]\nInclude = /etc/pacman.d/mirrorlist\n')
        live = self.root / 'test-live'
        key = live / 'etc/emaki-test/authorized_keys'
        key.parent.mkdir(parents=True)
        key.write_text('ssh-ed25519 TEST-ONLY\n')
        mirrors = live / 'etc/pacman.d/mirrorlist'
        mirrors.parent.mkdir(parents=True)
        mirrors.write_text('Server = https://example.invalid/$repo/os/$arch\n')
        original = self.worker.runner.run

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            return argv[-1] + ' disabled\n' if 'list-unit-files' in argv else result

        self.worker.runner.run = run
        with patch('emaki_installer.worker.Path', side_effect=lambda p: live / str(p).lstrip('/') if str(p).startswith('/etc/') else Path(p)):
            self.worker.settings()
        ssh = self.root / 'home/vmuser/.ssh'
        self.assertEqual(ssh.stat().st_mode & 0o777, 0o700)
        self.assertEqual((ssh / 'authorized_keys').stat().st_mode & 0o777, 0o600)
        self.assertEqual((ssh / 'authorized_keys').read_bytes(), key.read_bytes())
        self.worker.backend.instance.pacman.strap.assert_called_once_with(['openssh', 'grim'])
        commands = [c for c, _ in self.worker.runner.commands]
        preset = next(i for i, c in enumerate(commands) if 'preset-all' in c)
        enables = [i for i, c in enumerate(commands) if 'enable' in c]
        self.assertTrue(enables and min(enables) > preset)
        self.assertTrue(any(c[-2:] == ['enable', 'sshd.service'] for c in commands))
        self.assertTrue(any('1001:987' in c for c in commands))
        self.assertFalse(any('chown' in c and '-R' in c for c in commands))
        owned = {path for c in commands if '1001:987' in c for path in c[c.index('1001:987') + 1:]}
        self.assertLessEqual({'/home/vmuser/.ssh', '/home/vmuser/.ssh/authorized_keys'}, owned)

    def run_account_and_settings(self, passwd='vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n'):
        self.worker.files.write('/etc/passwd', passwd)
        self.worker.files.write('/etc/pacman.conf', '[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n[extra]\nInclude = /etc/pacman.d/mirrorlist\n')
        live = self.root / 'test-live'
        mirrors = live / 'etc/pacman.d/mirrorlist'
        mirrors.parent.mkdir(parents=True)
        mirrors.write_text('Server = https://example.invalid/$repo/os/$arch\n')
        recorded = self.worker.runner.run

        def run(argv, **kwargs):
            # The wallpaper publisher's answer when a test gives none.
            result = recorded(argv, **kwargs)
            return result or ('{"state":"published","images":1}\n' if self.PUBLISHER in argv else '')

        self.worker.runner.run = run
        with patch('emaki_installer.worker.Path', side_effect=lambda p: live / str(p).lstrip('/') if str(p).startswith('/etc/') else Path(p)):
            self.worker.account()
            self.worker.settings()
        return [c[2:] for c, _ in self.worker.runner.commands if c[:2] == ['arch-chroot', str(self.root)] and c[2] == 'chown']

    def test_new_home_gets_no_recursive_chown(self):
        chowns = self.run_account_and_settings()
        self.assertTrue(chowns)
        self.assertFalse(any('-R' in c for c in chowns))
        owned = {path for c in chowns if '1001:987' in c for path in c[c.index('1001:987') + 1:]}
        self.assertEqual(owned, {'/home/vmuser/.config', '/home/vmuser/.config/niri',
                                 '/home/vmuser/.config/niri/config.kdl'})

    PUBLISHER = '/usr/share/emaki/shell/helpers/publish-wallpaper.py'

    def test_first_greeter_gets_the_default_wallpaper(self):
        # The session's publisher runs once in the target as the new account, with the default
        # wpaperd config: the very first login screen shows the default picture (EYES-48).
        logs = []
        self.worker.log = logs.append
        self.run_account_and_settings()
        self.assertFalse([line for line in logs if 'wallpaper' in line], logs)
        calls = self.worker.runner.commands
        commands = [c for c, _ in calls]
        provision = next(i for i, c in enumerate(commands) if c[0] == 'emaki-greeter-provision')
        publish = [i for i, c in enumerate(commands) if self.PUBLISHER in c]
        self.assertEqual(len(publish), 1, commands)
        command, kwargs = calls[publish[0]]
        self.assertGreater(publish[0], provision)
        self.assertEqual(command[:2], ['arch-chroot', str(self.root)])
        self.assertEqual(command[2:command.index('--')], ['setpriv', '--reuid', '1001', '--regid', '987',
                                                          '--clear-groups', '--no-new-privs'])
        self.assertEqual(command[command.index('--') + 1:], ['env', '-i', 'XDG_CONFIG_HOME=/etc/skel/.config',
                                                             '/usr/bin/python3', '-B', '-s', self.PUBLISHER])
        # The target root itself may live anywhere (TMPDIR); nothing inside the chroot names /home.
        self.assertFalse(any('/home' in part for part in command[2:]), command)
        # A missing copy leaves the flat login background; it never stops the installation.
        self.assertIs(kwargs.get('check'), False)

    def test_a_refused_wallpaper_copy_is_logged_and_the_installation_goes_on(self):
        logs = []
        self.worker.log = logs.append
        original = self.worker.runner.run

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            return '{"state":"refused","reason":"image_limit"}\n' if self.PUBLISHER in argv else result

        self.worker.runner.run = run
        self.run_account_and_settings()
        self.assertIn('WARNING: the login screen shows its plain background until the first login '
                      '(wallpaper copy: refused, image_limit).', logs)
        self.assertTrue(any('preset-all' in c for c, _ in self.worker.runner.commands))

    def keyboard_install(self, layouts):
        settings = config()
        settings['layouts'] = layouts
        self.worker.plan = make_plan(settings, inventory())
        files, written = self.worker.files, []
        self.worker.api = SimpleNamespace(locale=SimpleNamespace(
            LocaleConfiguration=lambda keymap, language, encoding: SimpleNamespace(kb_layout=keymap)))
        # Like the real backend: the minimal install writes the console map archinstall hands it.
        self.worker.backend = Mock()
        self.worker.backend.minimal.side_effect = lambda locale: files.write(
            '/etc/vconsole.conf', 'KEYMAP=' + locale.kb_layout + '\n')
        self.worker.copy_packages()
        written.append(files.path('/etc/vconsole.conf').read_bytes())
        self.assertFalse(any('mkinitcpio' in c for c, _ in self.worker.runner.commands))
        files.path('/etc/vconsole.conf').unlink()
        self.run_account_and_settings()
        written.append(files.path('/etc/vconsole.conf').read_bytes())
        return written

    def test_keyboard_file_is_whole_before_the_image_build_and_identical_in_settings(self):
        for layouts, expected in ((['cz', 'ru'], b'KEYMAP=cz-qwertz\nXKBLAYOUT=cz,ru\n'),
                                  (['ru', 'us'], b'KEYMAP=ru\nXKBLAYOUT=ru,us\n'),
                                  (['dvorak', 'ua'], b'KEYMAP=dvorak\nXKBLAYOUT=us,ua\nXKBVARIANT=dvorak,\n')):
            with self.subTest(layouts=layouts), tempfile.TemporaryDirectory() as temp:
                self.root = self.worker.target = Path(temp)
                self.worker.files = type(self.worker.files)(self.root)
                self.worker.runner = RecordingRunner()
                self.assertEqual(self.keyboard_install(layouts), [expected, expected])

    def test_no_keyboard_settings_are_written_into_the_home(self):
        settings = config()
        settings['layouts'] = ['cz', 'ru']
        self.worker.plan = make_plan(settings, inventory())
        self.run_account_and_settings()
        text = (self.root / 'home/vmuser/.config/niri/config.kdl').read_text()
        self.assertEqual(text, 'include "/usr/share/emaki/niri/default.kdl"\n')
        self.assertFalse((self.root / 'etc/X11').exists())

    UUID = '98cf80d3-e611-4dea-b415-c2c783782a89'
    FONT = Path(__file__).resolve().parents[1] / 'assets/grub/unlock-24.pf2'

    def encrypted_boot_files(self, fs='btrfs', vendor_prefix=None, built_prefix=None):
        """An encrypted plan plus what grub-install leaves behind on a cryptodisk."""
        settings = config(fs=fs)
        settings.update(encryption='separate', disk_password='disk-secret-for-tests')
        self.worker.plan = make_plan(settings, inventory())
        for p in self.worker.plan.partitions:
            p.path = '/dev/test' + str(p.number)
        self.worker.plan.root.luks_uuid = self.UUID
        self.boot_files()
        return self.grub_install_leftovers(vendor_prefix, built_prefix)

    def grub_install_leftovers(self, vendor_prefix=None, built_prefix=None):
        """grub-install's load.cfg and loaders for the current encrypted plan, and fakes of the GRUB tools."""
        f, root = self.worker.files, self.worker.plan.root
        # As grub-install 2.16 writes them on a LUKS2 root: the device-mapper UUID (no dashes)
        # in the prefix, the LUKS UUID as cryptsetup prints it in load.cfg.
        prefix = (f'(cryptouuid/{root.luks_uuid.replace("-", "")})' + ('/@' if root.fs == 'btrfs' else '')
                  + '/boot/grub')
        f.write('/boot/grub/x86_64-efi/load.cfg', f'cryptomount -u {root.luks_uuid}\n')
        vendor = b'MZ' + b'\0' * 30 + (vendor_prefix or prefix).encode() + b'\0vendor image'
        built = b'MZ' + b'\0' * 30 + (built_prefix or prefix).encode() + b'\0early graphics image'

        def side_effects(command):
            # What the real tools leave on disk: grub-install its loaders, grub-mkimage its output.
            if 'grub-install' in command:
                f.write('/efi/EFI/BOOT/BOOTX64.EFI' if '--removable' in command else '/efi/EFI/Emaki/grubx64.efi',
                        vendor)
            if 'grub-mkimage' in command:
                # grub-mkimage's own rule: --memdisk also sets the prefix to (memdisk)/boot/grub,
                # and the later of --memdisk and --prefix wins (util/grub-mkimage.c).
                carried = None
                for arg in command:
                    if arg.startswith('--memdisk='):
                        carried = '(memdisk)/boot/grub'
                    elif arg.startswith('--prefix='):
                        carried = arg.split('=', 1)[1]
                f.write(next(x for x in command if x.startswith('--output=')).split('=', 1)[1],
                        b'MZ' + b'\0' * 30 + (built_prefix or carried).encode() + b'\0early graphics image')
            return False

        self.worker.runner.fail = side_effects
        return prefix, vendor, built

    def run_bootloader(self):
        with patch('emaki_installer.worker.GRUB_VISIBLE_FONT', self.FONT):
            self.worker.bootloader()

    def fresh_target(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = self.worker.target = Path(temp.name)
        self.worker.files = type(self.worker.files)(self.root)
        self.worker.runner = RecordingRunner()

    def test_encrypted_install_rebuilds_grub_to_ask_in_graphics_mode(self):
        for fs in ('btrfs', 'ext4'):
            with self.subTest(fs=fs):
                self.fresh_target()
                prefix, vendor, built = self.encrypted_boot_files(fs)
                self.run_bootloader()
                commands = [c for c, _ in self.worker.runner.commands]
                order = [next(i for i, c in enumerate(commands) if tool in c)
                         for tool in ('grub-install', 'grub-mkimage', 'grub-mkconfig')]
                self.assertEqual(order, sorted(order))
                self.assertEqual(len([c for c in commands if 'grub-install' in c]), 2)
                # --memdisk before --prefix: grub-mkimage resets the prefix on --memdisk.
                self.assertEqual([c for c in commands if 'grub-mkimage' in c], [[
                    'arch-chroot', str(self.root), 'grub-mkimage', '--directory=/usr/lib/grub/x86_64-efi',
                    '--format=x86_64-efi', '--memdisk=/boot/grub/x86_64-efi/emaki-early.tar',
                    '--prefix=' + prefix, '--config=/boot/grub/x86_64-efi/emaki-early.cfg',
                    '--output=/boot/grub/x86_64-efi/emaki-early.efi', *GRUB_EARLY_MODULES]])
                f = self.worker.files
                self.assertEqual(f.read('/boot/grub/x86_64-efi/emaki-early.cfg'),
                                 grub_early_config(f'cryptomount -u {self.UUID}\n', self.UUID))
                # Font and picture go inside the image; GRUB reads nothing from the ESP.
                self.assertEqual(f.path('/boot/grub/x86_64-efi/emaki-early.tar').read_bytes(),
                                 grub_unlock_memdisk(self.FONT.read_bytes(),
                                                     f'cryptomount -u {self.UUID}\n', self.UUID))
                self.assertEqual(sorted(p.name for p in (self.root / 'efi/EFI/Emaki').iterdir()), ['grubx64.efi'])
                # Both loaders grub-install wrote now hold the rebuilt image.
                for loader in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                    self.assertEqual(f.path(loader).read_bytes(), built, loader)

    def test_unlock_screen_needs_no_target_background(self):
        self.encrypted_boot_files()
        self.assertFalse(self.worker.files.path('/usr/share/emaki/grub/background.png').exists())
        self.run_bootloader()
        self.assertTrue(self.worker.files.path('/boot/grub/x86_64-efi/emaki-early.tar').is_file())

    def test_unlock_screen_rejects_missing_or_corrupt_visible_font(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                self.fresh_target()
                _, vendor, _ = self.encrypted_boot_files()
                font = self.root / 'visible.pf2'
                if corrupt:
                    font.write_bytes(b'invalid font')
                with patch('emaki_installer.worker.GRUB_VISIBLE_FONT', font):
                    with self.assertRaises(InstallError) as raised:
                        self.worker.bootloader()
                self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
                self.assertIn('font', raised.exception.message)
                self.assertFalse(any('grub-mkimage' in c for c, _ in self.worker.runner.commands))
                for loader in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                    self.assertEqual(self.worker.files.path(loader).read_bytes(), vendor)

    def test_unencrypted_install_keeps_grub_install_alone(self):
        self.boot_files()
        self.worker.bootloader()
        self.assertFalse(any('grub-mkimage' in c for c, _ in self.worker.runner.commands))
        self.assertFalse((self.root / 'efi/EFI/Emaki').exists())
        self.assertFalse((self.root / 'boot/grub/x86_64-efi').exists())

    def test_unlock_screen_refuses_what_it_does_not_understand_and_leaves_the_loaders(self):
        cases = {'load_cfg': {}, 'vendor_prefix': {'vendor_prefix': '(hd0,gpt2)/@/boot/grub'},
                 'mkimage_output': {'built_prefix': f'(cryptouuid/{self.UUID})/boot/grub'}}
        for change, overrides in cases.items():
            with self.subTest(change=change):
                self.fresh_target()
                prefix, vendor, built = self.encrypted_boot_files(**overrides)
                f = self.worker.files
                if change == 'load_cfg':
                    f.write('/boot/grub/x86_64-efi/load.cfg', 'cryptomount -u 00000000-0000-0000-0000-000000000000\n')
                with self.assertRaises(InstallError) as raised:
                    self.run_bootloader()
                self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
                commands = [c for c, _ in self.worker.runner.commands]
                self.assertFalse(any('grub-mkconfig' in c for c in commands))
                if change != 'mkimage_output':
                    self.assertFalse(any('grub-mkimage' in c for c in commands))
                    self.assertFalse(f.path('/boot/grub/x86_64-efi/emaki-early.tar').exists())
                for loader in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                    self.assertEqual(f.path(loader).read_bytes(), vendor, loader)

    def test_unlock_screen_rewrites_only_the_loaders_grub_install_could_write(self):
        prefix, vendor, built = self.encrypted_boot_files()
        hook = self.worker.runner.fail
        # Both vendor attempts fail (NVRAM, then --no-nvram); the removable loader is the boot path.
        self.worker.runner.fail = lambda c: ('grub-install' in c and '--removable' not in c) or hook(c)
        self.run_bootloader()
        self.assertFalse((self.root / 'efi/EFI/Emaki/grubx64.efi').exists())
        self.assertEqual(self.worker.files.path('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), built)

    def test_unlock_screen_never_touches_a_foreign_fallback_loader(self):
        prefix, vendor, built = self.encrypted_boot_files()
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'foreign')
        self.run_bootloader()
        self.assertEqual(self.worker.files.path('/efi/EFI/Emaki/grubx64.efi').read_bytes(), built)
        self.assertEqual(self.worker.files.path('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), b'foreign')

    def kept_home_owned_by(self, uid, gid):
        """The kept /home/vmuser reports this owner; the tests' own uid does not matter."""
        home = self.root / 'home/vmuser'
        home.mkdir(parents=True)
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if path == home:
                values = list(result[:10])
                values[4:6] = uid, gid
                return type(result)(values)
            return result

        logs = []
        self.worker.log = logs.append
        with patch.object(Path, 'stat', stat):
            chowns = self.run_account_and_settings()
        owned = {path for c in chowns if '1001:987' in c for path in c[c.index('1001:987') + 1:]}
        return [c for c in chowns if '-R' in c], owned, logs

    def test_kept_home_remaps_only_the_previous_owner(self):
        recursive, owned, logs = self.kept_home_owned_by(1000, 1000)
        self.assertEqual(recursive, [
            ['chown', '-R', '-h', '--from=1000', '1001', '/home/vmuser'],
            ['chown', '-R', '-h', '--from=:1000', ':987', '/home/vmuser']])
        self.assertEqual(owned, {'/home/vmuser', '/home/vmuser/.config', '/home/vmuser/.config/niri',
                                 '/home/vmuser/.config/niri/config.kdl'})
        self.assertFalse(any(line.startswith('WARNING') for line in logs))

    def test_kept_home_with_a_system_owner_or_group_names_what_was_remapped(self):
        # The new account is vmuser, 1001:987.
        user, group = 'files of user {} keep that owner (not a regular account)', \
            'files of group {} keep that group (not a regular group)'
        written = 'the directory itself and the files the installer wrote now belong to vmuser.'
        for uid, gid, expected, said in (
                (0, 0, [], [user.format(0), group.format(0)]),
                (65534, 65534, [], [user.format(65534), group.format(65534)]),
                (1000, 0, [['chown', '-R', '-h', '--from=1000', '1001', '/home/vmuser']],
                 ['files of user 1000 now belong to vmuser (1001)', group.format(0)]),
                (0, 1000, [['chown', '-R', '-h', '--from=:1000', ':987', '/home/vmuser']],
                 [user.format(0), 'files of group 1000 now have group 987']),
                # Already the new account's UID: nothing to remap for the owner.
                (1001, 0, [], [group.format(0)])):
            with self.subTest(uid=uid, gid=gid), tempfile.TemporaryDirectory() as temp:
                self.root = self.worker.target = Path(temp)
                self.worker.files = type(self.worker.files)(self.root)
                self.worker.runner = RecordingRunner()
                recursive, owned, logs = self.kept_home_owned_by(uid, gid)
                self.assertEqual(recursive, expected)
                self.assertEqual(owned, {'/home/vmuser', '/home/vmuser/.config', '/home/vmuser/.config/niri',
                                         '/home/vmuser/.config/niri/config.kdl'})
                warnings = [line for line in logs if line.startswith('WARNING')]
                self.assertEqual(warnings, ['; '.join([f'WARNING: the kept /home/vmuser belonged to {uid}:{gid}',
                                                       *said, written])])

    def test_kept_home_with_the_same_ids_is_not_remapped(self):
        home = self.root / 'home/vmuser'
        home.mkdir(parents=True)
        old = home.stat()
        chowns = self.run_account_and_settings(f'vmuser:x:{old.st_uid}:{old.st_gid}:VM User:/home/vmuser:/bin/bash\n')
        self.assertTrue(chowns)
        self.assertFalse(any('-R' in c for c in chowns))


if __name__ == '__main__':
    unittest.main()
