from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from emaki_installer.constants import PHASES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, config, inventory


class WorkerLifecycleTests(unittest.TestCase):
    def execute(self, fs='btrfs', fail=None, cancel=None, changed=False):
        inv = FakeInventory(inventory())
        plan = make_plan(config(fs=fs), inv.probe())
        if changed:
            inv.data['disks'][0]['_identity']['diskseq'] += 1
        events, phases, runners = [], [], []

        def runner_factory(*args):
            runner = RecordingRunner(*args)
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
            setattr(PhaseWorker, phase, phase_fn(phase))
        worker = PhaseWorker(None, inv, Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
                             lambda line: None, backend_factory=lambda *a: None, runner_factory=runner_factory)
        with patch('emaki_installer.worker.preflight_repo'), patch('emaki_installer.worker.cleanup') as clean:
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
            self.assertNotIn('lazy', cleanups[-1].kwargs)

    def test_cancellation_is_at_next_boundary(self):
        events, phases, _, _ = self.execute(cancel='copy_packages')
        self.assertIn('copy_packages-completed', phases)
        self.assertNotIn('bootloader', phases)
        self.assertEqual(events[-2], {'type': 'cancel_ack', 'disk_changed': True})
        self.assertEqual(events[-1]['code'], 'cancelled')

    def test_hotplugged_disk_rejected_before_prepare(self):
        events, phases, _, _ = self.execute(changed=True)
        self.assertEqual(phases, [])
        self.assertEqual(events[-1]['code'], 'disk_changed')


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

    def test_existing_foreign_fallback_preserved_without_microsoft(self):
        self.boot_files()
        self.worker.files.write('/efi/EFI/BOOT/BOOTX64.EFI', b'foreign')
        self.worker.bootloader()
        grub = [c for c, _ in self.worker.runner.commands if 'grub-install' in c]
        self.assertEqual(len(grub), 1)
        self.assertEqual((self.root / 'efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), b'foreign')

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

    def test_snapper_failure_remounts_but_never_deletes_unknown_data(self):
        self.worker.files.mkdir('/.snapshots')
        self.worker.files.write('/.snapshots/keep', 'data')
        with self.assertRaises(OSError):
            self.worker.snapshot()
        commands = [c for c, _ in self.worker.runner.commands]
        self.assertEqual(commands[-1][0], 'mount')
        self.assertFalse(any('delete' in c for c in commands))
        self.assertEqual((self.root / '.snapshots/keep').read_text(), 'data')

    def test_online_update_failure_only_warns(self):
        logs = []
        self.worker.log = logs.append
        self.worker.runner.fail = lambda command: 'pacman' in command
        self.worker.update()
        self.assertTrue(self.worker.update_attempted)
        self.assertTrue(any(line.startswith('WARNING: online update failed') for line in logs))

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


if __name__ == '__main__':
    unittest.main()
