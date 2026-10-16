# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual partition plans bind protection evidence and inspect shared Btrfs roots."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from emaki_installer.errors import InstallError
from emaki_installer.inventory import Inventory, other_system, _root_hint
from emaki_installer.planner import make_plan
from support import inventory, manual


class PartitionProtectionTests(unittest.TestCase):
    def test_foreign_btrfs_root_is_protected_beside_completed_or_incomplete_emaki(self):
        for unfinished in (False, True):
            with self.subTest(unfinished=unfinished), tempfile.TemporaryDirectory() as temp:
                probe = Path(temp) / 'probe'
                (probe / '@/etc').mkdir(parents=True)
                (probe / 'other/etc').mkdir(parents=True)
                (probe / '@/etc/os-release').write_text('ID=arch\nVARIANT_ID=emaki\n')
                if unfinished:
                    (probe / '@/.emaki-install-incomplete').write_text('unfinished')
                (probe / 'other/etc/os-release').write_text('ID=other-linux\n')
                machine = inventory(partitions=True)
                disk = machine['disks'][0]
                part = disk['partitions'][1]

                def command(argv):
                    if argv[:3] == ['btrfs', 'filesystem', 'show']:
                        return 'Total devices 1 FS bytes used 65536\n'
                    if argv[:3] == ['btrfs', 'subvolume', 'list']:
                        return 'ID 256 gen 1 top level 5 path @\nID 257 gen 1 top level 5 path other\n'
                    if argv[0] == 'umount':
                        for child in probe.iterdir():
                            shutil.rmtree(child)
                    return ''

                runner = Mock()
                runner.run.side_effect = command
                with patch('emaki_installer.inventory.tempfile.mkdtemp', return_value=str(probe)):
                    Inventory(runner)._inspect(disk, part)
                self.assertEqual(other_system(part), 'Linux')
                with self.assertRaisesRegex(InstallError, 'holds Linux'):
                    make_plan(manual(), machine)

    def test_incomplete_btrfs_inspection_never_authorizes_emaki_reuse(self):
        for truncated in (False, True):
            with self.subTest(truncated=truncated), tempfile.TemporaryDirectory() as temp:
                probe = Path(temp) / 'probe'
                probe.mkdir()
                (probe / 'root0').mkdir()
                machine = inventory(partitions=True)
                disk = machine['disks'][0]
                part = disk['partitions'][1]

                def command(argv):
                    if argv[:3] == ['btrfs', 'filesystem', 'show']:
                        return 'Total devices 1 FS bytes used 65536\n'
                    if argv[:3] == ['btrfs', 'subvolume', 'list']:
                        return ''.join(f'ID {256 + n} gen 1 top level 5 path root{n}\n'
                                       for n in range(257 if truncated else 1))
                    if argv[0] == 'umount':
                        (probe / 'root0').rmdir()
                    return ''

                runner = Mock()
                runner.run.side_effect = command
                hints = {'return_value': 'emaki'} if truncated else {'side_effect': ['emaki', OSError('unreadable')]}
                with patch('emaki_installer.inventory.tempfile.mkdtemp', return_value=str(probe)), \
                        patch('emaki_installer.inventory._root_hint', **hints):
                    Inventory(runner)._inspect(disk, part)
                self.assertEqual(other_system(part), 'files Emaki cannot check')
                with self.assertRaisesRegex(InstallError, 'files Emaki cannot check'):
                    make_plan(manual(), machine)

    def test_nested_roots_snapshots_and_unsafe_paths(self):
        for name, foreign, expected in (
                ('systems/linux', True, 'Linux'),
                ('@snapshots/1/snapshot', False, None),
                ('../outside', False, 'files Emaki cannot check'),
                ('/outside', False, 'files Emaki cannot check'),
                ('link/child', False, 'files Emaki cannot check'),
                ('missing/child', False, 'files Emaki cannot check')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                probe = Path(temp) / 'probe'
                (probe / '@/etc').mkdir(parents=True)
                (probe / '@/etc/os-release').write_text('ID=arch\nVARIANT_ID=emaki\n')
                if name.startswith(('systems/', '@snapshots/')):
                    (probe / name / 'etc').mkdir(parents=True)
                    (probe / name / 'etc/os-release').write_text(
                        'ID=other-linux\n' if foreign else 'ID=arch\nVARIANT_ID=emaki\n')
                if name == 'link/child':
                    (probe / 'link').symlink_to(Path(temp), target_is_directory=True)
                machine = inventory(partitions=True)
                disk = machine['disks'][0]
                part = disk['partitions'][1]

                def command(argv):
                    if argv[:3] == ['btrfs', 'filesystem', 'show']:
                        return 'Total devices 1 FS bytes used 65536\n'
                    if argv[:3] == ['btrfs', 'subvolume', 'list']:
                        return f'ID 256 gen 1 top level 5 path @\nID 257 gen 1 top level 256 path {name}\n'
                    if argv[0] == 'umount':
                        for child in probe.iterdir():
                            if child.is_symlink():
                                child.unlink()
                            else:
                                shutil.rmtree(child)
                    return ''

                runner = Mock()
                runner.run.side_effect = command
                with patch('emaki_installer.inventory.tempfile.mkdtemp', return_value=str(probe)):
                    Inventory(runner)._inspect(disk, part)
                self.assertEqual(other_system(part), expected)
                if expected:
                    with self.assertRaises(InstallError):
                        make_plan(manual(), machine)
                else:
                    self.assertTrue(make_plan(manual(), machine).root.format)

    def test_root_identity_read_error_is_not_empty_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'etc').mkdir()
            (root / 'etc/os-release').write_text('ID=other-linux\n')
            with patch.object(Path, 'open', side_effect=OSError('read failed')):
                with self.assertRaises(OSError):
                    _root_hint(root)

    def test_root_identity_metadata_error_is_not_empty_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'etc').mkdir()
            (root / 'etc/os-release').write_text('ID=other-linux\n')
            for method in ('stat', 'lstat'):
                with self.subTest(method=method), patch.object(Path, method, side_effect=OSError('metadata failed')):
                    if method == 'lstat':
                        with self.assertRaises(OSError):
                            _root_hint(root)
                    else:
                        # Ownership uses no following stat call: its lstat evidence still identifies Linux.
                        self.assertEqual(_root_hint(root), 'linux')
