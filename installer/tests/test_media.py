# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.arch_backend import OfflinePacman, offline_config
from emaki_installer.errors import InstallError
from emaki_installer.media import PackageSource, discover_repo, mounted_roots, offline_source, package_source


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

    def test_standard_media_validates_then_isolates_config_and_never_downloads(self):
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
                self.assertNotEqual(source.config, config)
                self.assertIn(f'HookDir = {source.hookdir}\n', source.config.read_text())
                self.assertEqual(list(source.hookdir.iterdir()), [])
                self.assertIsNone(source.archives)
                private = source.config
            self.assertTrue(config.is_file())
            self.assertNotIn('HookDir', config.read_text())
            self.assertFalse(private.exists())
            self.assertEqual(runner.commands, [])
            self.assertEqual(list((root / 'work').iterdir()), [])

    def test_corrupt_standard_media_never_falls_back_online(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner()
            (root / 'run/archiso/bootmnt/emaki/repo').mkdir(parents=True)
            with self.assertRaises(InstallError):
                with package_source(runner, ['base'], online=True, root=root,
                                    work_parent=root / 'work'):
                    self.fail('corrupt media accepted')
            self.assertEqual(runner.commands, [])

    def test_hook_directory_must_stay_private_and_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'run/archiso/copytoram/emaki/repo').mkdir(parents=True)
            runner = Runner()
            with package_source(runner, ['base'], online=False, root=root,
                                work_parent=root / 'work') as source:
                stray = source.hookdir / '90-mkinitcpio-install.hook'
                stray.write_text('# Unexpected hook\n')
                with self.assertRaises(InstallError):
                    OfflinePacman(runner, root / 'target', source).strap(['base'])
                stray.unlink()
                source.hookdir.rmdir()
                with self.assertRaises(InstallError):
                    source.validate()
                empty = root / 'other-hooks'
                empty.mkdir()
                source.hookdir.symlink_to(empty, target_is_directory=True)
                with self.assertRaises(InstallError):
                    source.validate()
            self.assertEqual(runner.commands, [])

    def test_hook_directory_cannot_contain_whitespace(self):
        for whitespace in (' ', '\t', '\n', '\r', '\v', '\f'):
            with self.subTest(whitespace=repr(whitespace)), tempfile.TemporaryDirectory() as temporary:
                root, runner = Path(temporary), Runner()
                hooks = root / f'private{whitespace}hooks'
                hooks.mkdir()
                config = root / 'offline.conf'
                config.write_text('[options]\nArchitecture = auto\n'
                                  f'HookDir = {hooks}\n'
                                  '[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\n'
                                  f'Server = {root.as_uri()}\n')
                source = PackageSource(config, root, hookdir=hooks)
                with self.assertRaises(InstallError):
                    source.validate()
                with self.assertRaises(InstallError):
                    offline_config(config, repo=root.as_uri(), hookdir=hooks)
                with self.assertRaises(InstallError):
                    OfflinePacman(runner, root / 'target', source).strap(['linux'])
                self.assertEqual(runner.commands, [])

    def test_pacstrap_without_prepared_source_also_isolates_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, runner = Path(temporary), Runner()
            config = root / 'shipped.conf'
            # The package check archives installer/ without the ISO profile.
            shipped = ('[options]\nArchitecture = auto\n'
                       'SigLevel = Required DatabaseOptional TrustedOnly\n'
                       'LocalFileSigLevel = Required TrustedOnly\n\n'
                       '[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\n'
                       'Server = file:///run/archiso/bootmnt/emaki/repo\n')
            iso_config = (Path(__file__).resolve().parents[2] / 'iso/profile/airootfs/etc/'
                          'emaki-installer/pacman-offline.conf')
            if iso_config.exists():
                self.assertEqual(shipped, iso_config.read_text())
            config.write_text(shipped)
            with offline_source(config, work_parent=root / 'work') as source:
                # Select a fixture shipped config without changing the fallback behavior.
                from contextlib import nullcontext
                with patch('emaki_installer.media.offline_source', return_value=nullcontext(source)):
                    OfflinePacman(runner, root / 'target').strap(['linux'])
                self.assertEqual(runner.commands[0][2], str(source.config))
                self.assertIn(f'HookDir = {source.hookdir}\n', source.config.read_text())
            self.assertEqual(config.read_text(), shipped)

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
                online = source.config.parent / 'online.conf'
                self.assertIn(f'HookDir = {source.hookdir}\n', online.read_text())
                self.assertIn(f'HookDir = {source.hookdir}\n', source.config.read_text())
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
