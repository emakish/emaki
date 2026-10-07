"""Regressions from the first installed-disk VM acceptance."""
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer.arch_backend import load_archinstall
from emaki_installer.errors import Code, InstallError
from emaki_installer.render import grub_btrfs_config
from emaki_installer.runtime import cleanup, processes_under, stop_target_processes
from support import RecordingRunner
import test_worker


class PartedWrapper:
    """pyparted forwards attributes; they never enter the wrapper's __dict__."""
    def __init__(self):
        self.__dict__['wrapped'] = SimpleNamespace(
            getAllDevices=Mock(), getDevice=Mock(), IOException=RuntimeError)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def __setattr__(self, name, value):
        setattr(self.wrapped, name, value)


class WrapperTests(unittest.TestCase):
    def test_load_suppresses_discovery_and_restores_wrapper_and_aliases(self):
        parted = PartedWrapper()
        originals = parted.getAllDevices, parted.getDevice
        self.assertNotIn('getAllDevices', parted.__dict__)
        with self.assertRaises(AttributeError):
            del parted.getAllDevices
        # Reproduce the old context-manager exit failure, then reset the fake.
        with self.assertRaises(AttributeError):
            with patch.object(parted, 'getAllDevices', return_value=[]):
                pass
        parted.getAllDevices = originals[0]
        handler = SimpleNamespace()

        def importing(name):
            if name == 'archinstall.lib.disk.device_handler':
                return handler
            self.assertEqual(parted.getAllDevices(), [])
            with self.assertRaisesRegex(RuntimeError, 'Emaki owns device discovery'):
                parted.getDevice('/dev/never-probed')
            handler.getAllDevices, handler.getDevice = parted.getAllDevices, parted.getDevice
            return SimpleNamespace()

        with patch.dict('sys.modules', parted=parted), \
                patch('emaki_installer.arch_backend.dist_version', return_value='4.5'), \
                patch('emaki_installer.arch_backend.importlib.import_module', side_effect=importing):
            self.assertEqual(load_archinstall().version, '4.5')
        self.assertIs(parted.getAllDevices, originals[0])
        self.assertIs(parted.getDevice, originals[1])
        self.assertIs(handler.getAllDevices, originals[0])
        self.assertIs(handler.getDevice, originals[1])
        for original in originals:
            original.assert_not_called()

    def test_wrapper_restored_on_import_failure(self):
        parted = PartedWrapper()
        originals = parted.getAllDevices, parted.getDevice
        with patch.dict('sys.modules', parted=parted), \
                patch('emaki_installer.arch_backend.importlib.import_module', side_effect=ImportError):
            with self.assertRaises(InstallError):
                load_archinstall()
        self.assertEqual((parted.getAllDevices, parted.getDevice), originals)


class CleanupTests(unittest.TestCase):
    def test_busy_mount_retries_then_succeeds_before_parent(self):
        runner = RecordingRunner()
        mounts = [Path('/target/home'), Path('/target')]
        runner.fail = lambda c: c[0] == 'umount' and len([x for x, _ in runner.commands if x[0] == 'umount']) < 4
        with patch('emaki_installer.runtime.mounts_under', return_value=mounts), \
                patch('emaki_installer.runtime.stop_target_processes'), \
                patch('emaki_installer.runtime.time.sleep') as sleep:
            cleanup(runner, [Path('/target')])
        self.assertEqual([c for c, _ in runner.commands if c[0] == 'umount'],
                         [['umount', '--', '/target/home']] * 4 + [['umount', '--', '/target']])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0, 2.0])
        self.assertFalse(any(c[0] == 'fuser' for c, _ in runner.commands))

    def test_exhausted_retry_logs_diagnostics_and_remains_fatal(self):
        logs = []
        runner = RecordingRunner(logs.append)
        runner.fail = lambda c: c[0] == 'umount'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('emaki_installer.runtime.mounts_under', return_value=[root]), \
                    patch('emaki_installer.runtime.stop_target_processes') as stop, \
                    patch('emaki_installer.runtime.processes_under', return_value=[(123, root)]), \
                    patch('emaki_installer.runtime.time.sleep') as sleep:
                with self.assertRaises(InstallError) as raised:
                    cleanup(runner, [root])
                stop.assert_called_once_with(runner, root)
            self.assertEqual(raised.exception.code, Code.CLEANUP_FAILED)
            self.assertEqual(len([c for c, _ in runner.commands if c[0] == 'umount']), 10)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0] + [2.0] * 7)
            self.assertIn((['fuser', '-vm', '--', str(root)], {'check': False}), runner.commands)
            self.assertTrue(any(f'123 root={root}' in line for line in logs))

    def test_lazy_startup_does_not_signal_or_retry(self):
        runner = RecordingRunner()
        with patch('emaki_installer.runtime.mounts_under', return_value=[Path('/target')]), \
                patch('emaki_installer.runtime.stop_target_processes') as stop:
            cleanup(runner, [Path('/target')], lazy=True)
        stop.assert_not_called()
        self.assertEqual(runner.commands, [(['umount', '-l', '--', '/target'], {})])

    def test_process_scan_uses_root_not_cwd_and_excludes_prefix_siblings(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target, proc = base / 'target', base / 'proc'
            for directory in (target / 'nested', base / 'target-other', proc):
                directory.mkdir(parents=True)
            for pid, root in ((10, target), (11, target / 'nested'), (12, base / 'target-other'),
                              (13, Path('/')), (14, base / 'missing')):
                entry = proc / str(pid)
                entry.mkdir()
                (entry / 'root').symlink_to(root)
                (entry / 'cwd').symlink_to(target)
            self.assertEqual(dict(processes_under(target, proc)), {10: target, 11: target / 'nested'})
            with self.assertRaises(InstallError):
                processes_under(Path('/'), proc)

    def test_signals_recheck_root_and_use_pidfd(self):
        root = Path('/target')
        with patch('emaki_installer.runtime.processes_under', side_effect=[[(123, root)], [], []]), \
                patch('emaki_installer.runtime.os.pidfd_open', return_value=9), \
                patch('emaki_installer.runtime.os.close') as close, \
                patch('emaki_installer.runtime.signal.pidfd_send_signal') as send:
            stop_target_processes(RecordingRunner(), root)
        send.assert_not_called()
        close.assert_called_once_with(9)
        with patch('emaki_installer.runtime.processes_under', return_value=[(123, root)]), \
                patch('emaki_installer.runtime.os.pidfd_open', return_value=9), \
                patch('emaki_installer.runtime.os.close'), \
                patch('emaki_installer.runtime.time.sleep'), \
                patch('emaki_installer.runtime.signal.pidfd_send_signal') as send:
            stop_target_processes(RecordingRunner(), root)
        self.assertEqual([c.args for c in send.call_args_list], [(9, signal.SIGTERM), (9, signal.SIGKILL)])


class ConfigTests(unittest.TestCase):
    def test_snapshot_title_render_preserves_options_and_is_idempotent(self):
        for existing in ('', 'GRUB_BTRFS_SUBMENUNAME="Arch Linux snapshots"\n',
                         '#GRUB_BTRFS_SUBMENUNAME="Arch Linux snapshots"\n',
                         ' export GRUB_BTRFS_SUBMENUNAME="Old"\n'):
            text = grub_btrfs_config(existing + 'GRUB_BTRFS_LIMIT="50"\n', force=True)
            self.assertIn('. /usr/share/emaki/boot/grub-btrfs.conf; fi\n', text)
            self.assertIn('GRUB_BTRFS_LIMIT="50"', text)
            self.assertEqual(grub_btrfs_config(text), text)


class WorkerFixTests(unittest.TestCase):
    setUp = test_worker.BootAndSnapshotTests.setUp
    tearDown = test_worker.BootAndSnapshotTests.tearDown
    boot_files = test_worker.BootAndSnapshotTests.boot_files

    def test_release_settings_do_not_install_test_packages_or_enable_ssh(self):
        self.worker.backend = SimpleNamespace(instance=SimpleNamespace(pacman=Mock()))
        self.worker.repositories = Mock()
        self.worker.files.write('/etc/passwd', 'vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n')
        self.worker.settings()
        self.worker.backend.instance.pacman.strap.assert_not_called()
        self.assertFalse(any('sshd.service' in c for c, _ in self.worker.runner.commands))
        self.assertFalse(self.worker.files.path('/home/vmuser/.ssh').exists())

    def test_settings_set_wifi_country_from_timezone_only_when_known(self):
        self.worker.backend = SimpleNamespace(instance=SimpleNamespace(pacman=Mock()))
        self.worker.repositories = Mock()
        self.worker.files.write('/etc/passwd', 'vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n')
        self.worker.files.write('/usr/share/zoneinfo/zone.tab',
                                'US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n')
        self.worker.settings()
        self.assertFalse(self.worker.files.path('/etc/conf.d/wireless-regdom').exists())
        self.worker.plan.config['timezone'] = 'America/New_York'
        self.worker.settings()
        self.assertIn('\nWIRELESS_REGDOM="US"\n', self.worker.files.read('/etc/conf.d/wireless-regdom'))

    def test_snapshot_title_written_before_every_grub_generation(self):
        self.boot_files()
        original = self.worker.runner.run

        def run(argv, **kwargs):
            if 'grub-mkconfig' in argv:
                self.assertIn('. /usr/share/emaki/boot/grub-btrfs.conf',
                              self.worker.files.read('/etc/default/grub-btrfs/config'))
            return original(argv, **kwargs)

        self.worker.runner.run = run
        self.worker.bootloader()
        self.worker.grub_config()

    def test_keyring_helpers_stopped_even_when_pacman_key_fails(self):
        for failed in (None, '--init', '--populate'):
            self.worker.runner = RecordingRunner()
            self.worker.runner.fail = lambda c: failed is not None and failed in c
            if failed:
                with self.assertRaises(InstallError):
                    self.worker.populate_keyring()
            else:
                self.worker.populate_keyring()
            last, kwargs = self.worker.runner.commands[-1]
            self.assertEqual(last, ['arch-chroot', str(self.root), 'gpgconf', '--homedir',
                                    '/etc/pacman.d/gnupg', '--kill', 'all'])
            self.assertFalse(kwargs['check'])
