# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest

from emaki_installer.arch_backend import OfflinePacman
from emaki_installer.errors import InstallError
from emaki_installer.media import discover_repo, mounted_roots, package_source


class Runner:
    def __init__(self, urls='https://mirror.example/base-1-1-x86_64.pkg.tar.zst\n', fail=None):
        self.commands, self.urls, self.fail = [], urls, fail

    def run(self, args):
        self.commands.append(args)
        if self.fail and args[0] == self.fail:
            raise OSError('unavailable')
        if args[:2] == ['pacman', '-Sp']:
            return self.urls
        if args[0] == 'curl':
            Path(args[args.index('--output') + 1]).write_bytes(b'signed package fixture')
        if args[0] == 'repo-add':
            Path(args[1].replace('.db.tar.gz', '.db')).write_bytes(b'database fixture')
        return ''


class MediaTests(unittest.TestCase):
    def test_mountinfo_escaped_path_and_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'proc/self').mkdir(parents=True)
            (root / 'proc/self/mountinfo').write_text('12 1 7:0 / /run/media/Live\\040USB ro - iso9660 /dev/loop0 ro\n')
            repo = root / 'run/media/Live USB/emaki/repo'
            repo.mkdir(parents=True)
            self.assertEqual(mounted_roots(root), ['/run/media/Live USB'])
            self.assertEqual(discover_repo(root), repo)

    def test_standard_repo_wins_even_if_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standard = root / 'run/archiso/bootmnt/emaki/repo'
            copied = root / 'run/archiso/copytoram/emaki/repo'
            standard.mkdir(parents=True)
            copied.mkdir(parents=True)
            self.assertEqual(discover_repo(root), standard)

    def test_standard_media_keeps_existing_config_and_never_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner()
            repo = root / 'run/archiso/bootmnt/emaki/repo'
            repo.mkdir(parents=True)
            config = root / 'etc/emaki-installer/pacman-offline.conf'
            config.parent.mkdir(parents=True)
            config.write_text('[options]\nArchitecture = auto\n'
                              '[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\n'
                              f'Server = {repo.as_uri()}\n')
            with package_source(runner, ['base'], online=True, root=root,
                                work_parent=root / 'work') as source:
                self.assertEqual(source.config, config)
                self.assertIsNone(source.archives)
            self.assertTrue(config.is_file())
            self.assertEqual(runner.commands, [])
            self.assertFalse((root / 'work').exists())

    def test_corrupt_standard_media_never_falls_back_online(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner()
            (root / 'run/archiso/bootmnt/emaki/repo').mkdir(parents=True)
            with self.assertRaises(InstallError):
                with package_source(runner, ['base'], online=True, root=root,
                                    work_parent=root / 'work'):
                    self.fail('corrupt media accepted')
            self.assertEqual(runner.commands, [])

    def test_fixture_root_does_not_follow_outside_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'outside').symlink_to('/tmp', target_is_directory=True)
            self.assertIsNone(discover_repo(root, ['/outside']))

    def test_relocated_media_uses_only_discovered_repo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'run/archiso/copytoram/emaki/repo'
            repo.mkdir(parents=True)
            runner = Runner()
            with package_source(runner, ['base'], online=False, root=root,
                                work_parent=root / 'work') as source:
                self.assertEqual(source.repo, repo)
                self.assertIsNone(source.archives)
                OfflinePacman(runner, root / 'target', source).strap(['base'])
                self.assertEqual(runner.commands[0][:3], ['pacstrap', '-C', str(source.config)])
                self.assertIn(repo.as_uri(), source.config.read_text())
                config = source.config
            self.assertFalse(config.exists())

    def test_missing_offline_media_is_plain_failure_without_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner, notices = Runner(), []
            with self.assertRaisesRegex(InstallError, 'Connect to the internet'):
                with package_source(runner, ['base'], online=False, root=Path(temporary),
                                    notice=notices.append, work_parent=Path(temporary) / 'work'):
                    self.fail('missing media accepted')
            self.assertEqual(runner.commands, [])
            self.assertIn('before your disk is changed', notices[0])

    def test_online_download_is_frozen_and_verified_before_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner()
            with package_source(runner, ['base', 'linux'], online=True, root=root,
                                work_parent=root / 'work') as source:
                self.assertEqual(len(source.archives), 1)
                self.assertTrue(Path(str(source.archives[0]) + '.sig').is_file())
                self.assertTrue(source.validate().is_file())
                tools = [args[0] for args in runner.commands]
                self.assertEqual(tools, ['pacman', 'pacman', 'curl', 'curl', 'pacman-key', 'repo-add'])
                self.assertIn('linux', runner.commands[1])
                self.assertNotIn('https://', source.config.read_text())
                self.assertIn('Required', source.config.read_text())
                repo = source.repo
            self.assertFalse(repo.exists())

    def test_bad_signature_never_builds_or_installs_repo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner(fail='pacman-key')
            with self.assertRaisesRegex(InstallError, 'disk has not been changed'):
                with package_source(runner, ['base'], online=True, root=root, work_parent=root / 'work'):
                    self.fail('bad signature accepted')
            self.assertNotIn('repo-add', [args[0] for args in runner.commands])
            self.assertNotIn('pacstrap', [args[0] for args in runner.commands])

    def test_unsafe_or_empty_download_transaction_fails(self):
        for url in ('http://example/base-1-1-x86_64.pkg.tar.zst',
                    'https://example/%2fetc.pkg.tar.zst', '',
                    'https://example/base-1-1-x86_64.pkg.tar.zst?token=x'):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                root, runner = Path(temporary), Runner(url)
                with self.assertRaises(InstallError):
                    with package_source(runner, ['base'], online=True, root=root,
                                        work_parent=root / 'work'):
                        self.fail('unsafe transaction accepted')
                self.assertNotIn('curl', [args[0] for args in runner.commands])


class BootMarkerTests(unittest.TestCase):
    def test_copytoram_keeps_boot_medium_excluded(self):
        from emaki_installer.inventory import Inventory
        from support import RecordingRunner
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'proc').mkdir()
            (root / 'proc/cmdline').write_text('copytoram=y archisodevice=/dev/sdb1')
            nodes = [{'path': '/dev/sdb1', 'name': '/dev/sdb1', 'label': 'LIVE', 'type': 'part'}]
            probe = Inventory(RecordingRunner(), root=root)
            self.assertEqual(probe._boot_sources(nodes), ({'/dev/sdb1'}, True))
            (root / 'proc/cmdline').write_text('copytoram=y archisolabel=LIVE')
            self.assertEqual(probe._boot_sources(nodes), ({'/dev/sdb1'}, True))
            nodes.append({'path': '/dev/sdc1', 'name': '/dev/sdc1', 'label': 'LIVE'})
            self.assertEqual(probe._boot_sources(nodes), (set(), False))
            (root / 'proc/cmdline').write_text('copytoram=y')
            self.assertEqual(probe._boot_sources(nodes), (set(), False))


if __name__ == '__main__':
    unittest.main()
