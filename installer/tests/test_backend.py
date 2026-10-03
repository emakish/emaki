from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.arch_backend import DeviceOperations, OfflinePacman, offline_config
from emaki_installer.constants import OFFLINE_REPO
from emaki_installer.errors import InstallError
from emaki_installer.planner import make_plan
from support import RecordingRunner, config, inventory, manual


class BackendTests(unittest.TestCase):
    def test_signed_offline_config_only(self):
        valid = f'[options]\nArchitecture = auto\n[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\nServer = {OFFLINE_REPO}\n'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pacman.conf'
            path.write_text(valid)
            self.assertEqual(offline_config(path), path)
            for bad in (valid + '[core]\nServer = https://example.org\n',
                        valid.replace('Required', 'Never'),
                        valid.replace(OFFLINE_REPO, 'https://example.org'),
                        valid + 'Include = /etc/pacman.conf\n',
                        valid + 'XferCommand = malicious\n'):
                path.write_text(bad)
                with self.assertRaises(InstallError):
                    offline_config(path)

    def test_pacstrap_explicit_config_and_keyring_no_shell(self):
        runner = RecordingRunner()
        with patch('emaki_installer.arch_backend.offline_config', return_value=Path('/offline.conf')):
            OfflinePacman(runner, Path('/target')).strap(['linux-lts', 'linux', 'linux'])
        argv = runner.commands[0][0]
        self.assertEqual(argv[:5], ['pacstrap', '-C', '/offline.conf', '-K', '/target'])
        self.assertEqual(argv.count('linux'), 1)
        self.assertIn('linux-lts', argv)

    def test_manual_partition_never_rewrites_table(self):
        runner = RecordingRunner()
        plan = make_plan(manual(), inventory(partitions=True))
        DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=False))
        self.assertEqual(runner.commands, [])

    def test_format_only_invokes_requested_filesystem(self):
        runner = RecordingRunner()
        ops = DeviceOperations(make_plan(config(), inventory()), runner)
        ops.format(SimpleNamespace(value='fat32'), Path('/dev/fake1'))
        self.assertEqual(runner.commands[0][0], ['mkfs.fat', '-F', '32', '/dev/fake1'])

    def test_erase_sfdisk_half_open_geometry_and_tree_verification(self):
        import json
        plan = make_plan(config(), inventory())
        runner = RecordingRunner()
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE', '/dev/vda')
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': [
            {'path': f'/dev/vda{p.number}', 'partn': p.number, 'start': p.start // 512, 'size': p.size}
            for p in plan.partitions]}]})
        archparts = [SimpleNamespace(dev_path=None) for _ in plan.partitions]
        DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=True, partitions=archparts))
        command, kwargs = runner.commands[0]
        self.assertEqual(command[0], 'sfdisk')
        self.assertIn('start=2048, size=2097152, type=U', kwargs['input'])
        self.assertEqual(archparts[0].dev_path, Path('/dev/vda1'))
        self.assertEqual(plan.partitions[1].path, '/dev/vda2')

    def test_post_partition_mismatch_fails_before_format(self):
        plan = make_plan(config(), inventory())
        runner = RecordingRunner()
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE', '/dev/vda')
        runner.responses[query] = '{"blockdevices":[{"pttype":"gpt","children":[]}]}'
        with self.assertRaises(InstallError):
            DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=True, partitions=[1, 2]))
        self.assertFalse(any(command[0].startswith('mkfs') for command, _ in runner.commands))


if __name__ == '__main__':
    unittest.main()
