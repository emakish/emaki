#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unprivileged regression checks for target-only offline repository staging."""
import base64
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('repo_files', HERE / 'repo-files.py')
repo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo)
spec = importlib.util.spec_from_file_location('nvidia_seeds', HERE / 'nvidia-seeds.py')
nvidia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvidia)
sys.path.insert(0, str(HERE.parent / 'installer'))
from emaki_installer import worker


class WorkerPackagesTests(unittest.TestCase):
    def test_every_worker_transaction_resolves_from_target_seeds(self):
        seeds = set((HERE / 'target-packages.txt').read_text().split())

        def resolve(packages):
            self.assertTrue(packages)
            self.assertFalse(set(packages) - seeds, f'Missing target seeds: {set(packages) - seeds}')

        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'fixture.pkg.tar.zst'
            archive.write_bytes(b'package')
            Path(str(archive) + '.sig').write_bytes(b'signature')
            for software in ('minimal', 'rich'):
                for mode in ('erase', 'manual', 'alongside'):
                    for test_mode in (False, True):
                        with self.subTest(software=software, mode=mode, test_mode=test_mode):
                            requested = []

                            def run(argv):
                                if '-Sp' in argv:
                                    # The remaining arguments after --logfile's value are seeds.
                                    packages = argv[argv.index('--logfile') + 2:]
                                    resolve(packages)
                                    requested.extend(packages)
                                    return archive.as_uri() + '\n'
                                return ''

                            with patch.object(worker, 'offline_config', return_value=Path(temporary) / 'pacman.conf'):
                                worker.preflight_repo(Mock(run=run), test_mode, software,
                                                      alongside_mode=mode == 'alongside')
                            install = Mock()
                            install.test_mode = test_mode
                            install.kept_home = None
                            install.wifi_profile = None
                            install.files = MagicMock()
                            install.files.read.return_value = 'user:x:1000:1000::/home/user:/bin/bash\n'
                            install.runner.chroot.return_value = ''
                            install.plan.config = dict(software=software, mode=mode, layouts=['us'],
                                                       user={'login': 'user'}, hostname='emaki', timezone='UTC')
                            install.plan.graphics_packages = ()
                            worker.Worker.copy_packages(install)
                            # Keep settings' target files and optional live test keys in memory.
                            with patch.object(worker, 'Path') as paths:
                                paths.return_value.stat.return_value.st_size = 1
                                paths.return_value.read_bytes.return_value = b'test key\n'
                                worker.Worker.settings(install)
                            installed = []
                            for call in install.backend.instance.pacman.strap.call_args_list:
                                resolve(call.args[0])
                                installed.extend(call.args[0])
                            self.assertEqual(set(installed), set(requested))

            # The optional online refresh also explicitly installs a package.
            install = Mock()
            install.update_options = []
            worker.Worker.refresh_keyring(install)
            for call in install.runner.chroot.call_args_list:
                command = call.args[0]
                resolve(command[command.index('--noconfirm') + 1:])


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.db = self.root / 'db'
        (self.db / 'sync').mkdir(parents=True)
        self.stage = self.root / 'stage'
        self.manifest = self.root / 'manifest'
        self.name = 'target-1-1-any.pkg.tar.zst'
        self.manifest.write_text(self.name + '\n')
        (self.cache / self.name).write_bytes(b'package')
        self.signature = self.cache / (self.name + '.sig')
        self.signature.write_bytes(b'signature')
        (self.cache / 'live-only-1-1-any.pkg.tar.zst').write_bytes(b'live')
        self.before = self.snapshot()

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.cache.iterdir()}

    def run_stage(self):
        repo.stage(self.cache, self.manifest, self.db, self.stage)

    def test_only_target_copied_and_verified_without_mutating_cache(self):
        with patch.object(repo.subprocess, 'run') as verify:
            self.run_stage()
        self.assertEqual(set(p.name for p in self.stage.iterdir()), {self.name, self.name + '.sig'})
        verify.assert_called_once_with(['pacman-key', '--verify', str(self.stage / self.signature.name),
                                       str(self.stage / self.name)], check=True)
        self.assertNotEqual((self.stage / self.name).stat().st_ino, (self.cache / self.name).stat().st_ino)
        self.assertEqual(self.before, self.snapshot())

    def test_embedded_signature_written_only_to_copy(self):
        self.signature.unlink()
        desc = f'%FILENAME%\n{self.name}\n\n%PGPSIG%\n{base64.b64encode(b"signed").decode()}\n\n'.encode()
        with tarfile.open(self.db / 'sync/core.db', 'w:gz') as archive:
            item = tarfile.TarInfo('target/desc')
            item.size = len(desc)
            archive.addfile(item, io.BytesIO(desc))
        before = self.snapshot()
        with patch.object(repo.subprocess, 'run'):
            self.run_stage()
        self.assertEqual((self.stage / self.signature.name).read_bytes(), b'signed')
        self.assertEqual(before, self.snapshot())

    def test_invalid_or_missing_packages_rejected(self):
        for manifest in ('', '../escape.pkg.tar.zst\n', 'missing-1-1-any.pkg.tar.zst\n',
                         self.name + '\n' + self.name + '\n'):
            with self.subTest(manifest=manifest):
                self.manifest.write_text(manifest)
                with self.assertRaises(ValueError):
                    self.run_stage()
                self.assertFalse(self.stage.exists())
        self.assertEqual(self.before, self.snapshot())

    def test_missing_signature_rejected(self):
        self.signature.unlink()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.run_stage()
        self.assertFalse(self.stage.exists())
        self.assertEqual(before, self.snapshot())

    def test_invalid_signature_or_package_rejected_by_verifier(self):
        with patch.object(repo.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pacman-key')):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_stage()
        self.assertFalse(self.stage.exists())
        self.assertEqual(self.before, self.snapshot())

    def test_existing_stage_and_symlink_input_rejected(self):
        self.stage.mkdir()
        marker = self.stage / 'keep'
        marker.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            self.run_stage()
        self.assertEqual(marker.read_bytes(), b'keep')
        marker.unlink()
        self.stage.rmdir()
        self.signature.unlink()
        self.signature.symlink_to(self.cache / self.name)
        with self.assertRaises(ValueError):
            self.run_stage()
        self.assertFalse(self.stage.exists())

    def test_build_keeps_union_but_ships_and_resolves_only_target(self):
        script = (HERE / 'build.sh').read_text()
        self.assertIn('>"$work/closure.txt"', script)
        self.assertIn('>"$work/target-closure.txt"', script)
        self.assertIn('"$work/profile" "$work/build-repo" "$version"', script)
        self.assertIn('cp -a -- "$work/target-repo/." "$work/mk/iso/emaki/repo/"', script)
        self.assertIn('cp -- "$work/target-closure.txt" "$work/mk/iso/emaki/repo/closure.txt"', script)
        config = script.split('cat >"$work/target.conf" <<CONF\n', 1)[1].split('\nCONF', 1)[0]
        self.assertIn('SigLevel = Required DatabaseOptional TrustedOnly', config)
        self.assertEqual([line for line in config.splitlines() if line.startswith('Server')],
                         ['Server = file://$work/target-repo'])
        self.assertIn('mkdir -- "$work/resolved-db" "$work/verify-cache"', script)
        self.assertIn('--cachedir "$work/verify-cache" --dbpath "$work/resolved-db"', script)
        self.assertIn('--config "$work/target.conf" "${target_packages[@]}"', script)


class NvidiaSeedsTests(unittest.TestCase):
    def test_seed_union_is_complete_but_base_transaction_is_unchanged(self):
        seeds = (HERE / 'target-packages.txt').read_text()
        self.assertTrue(nvidia.CONDITIONAL <= set(seeds.split()))
        self.assertFalse(nvidia.CONDITIONAL & set(nvidia.base_packages(seeds)))
        self.assertIn('linux', nvidia.base_packages(seeds))
        self.assertIn('linux-lts', nvidia.base_packages(seeds))
        live = set((HERE / 'profile/packages.x86_64').read_text().split())
        self.assertFalse(live & nvidia.CONDITIONAL)

    def test_missing_prebuilt_for_either_kernel_uses_dkms_for_both(self):
        for missing in ('nvidia-open', 'nvidia-open-lts'):
            families = nvidia.selected_families(nvidia.CONDITIONAL - {missing}, nvidia.LOCAL_REQUIRED)
            self.assertNotIn('open-prebuilt', families)
            self.assertTrue(set(nvidia.HEADERS) <= set(families['open-dkms']))

    def test_prebuilt_omits_dkms_and_its_build_dependencies(self):
        for available in (nvidia.CONDITIONAL, set(nvidia.FAMILIES['open-prebuilt'])):
            families = nvidia.selected_families(available, nvidia.LOCAL_REQUIRED)
            self.assertEqual(families, {'open-prebuilt': nvidia.FAMILIES['open-prebuilt']})

    def test_only_policy_package_must_come_from_input_repository(self):
        self.assertEqual(nvidia.LOCAL_REQUIRED, {'emaki-nvidia'})
        self.assertEqual(set(nvidia.FAMILIES), {'open-prebuilt', 'open-dkms'})
        for missing in nvidia.LOCAL_REQUIRED:
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'Signed input repository'):
                nvidia.selected_families(nvidia.CONDITIONAL, nvidia.LOCAL_REQUIRED - {missing})
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            nvidia.selected_families(nvidia.CONDITIONAL - {'nvidia-open', 'nvidia-open-dkms'},
                                     nvidia.LOCAL_REQUIRED)

    def fixture(self, root):
        seeds = root / 'target-packages.txt'
        seeds.write_text('base\nlinux\nlinux-lts\n' + '\n'.join(sorted(nvidia.CONDITIONAL)) + '\n')
        db, cache = root / 'db', root / 'cache'
        db.mkdir()
        cache.mkdir()
        closure, target = root / 'closure', root / 'target-closure'
        closure.write_text('live-1-1-any.pkg.tar.zst\nbase-1-1-any.pkg.tar.zst\n')
        target.write_text('base-1-1-any.pkg.tar.zst\n')
        return seeds, db, cache, closure, target

    def test_checked_prebuilt_download_updates_both_closures(self):
        calls = []
        def command(argv, capture=False):
            calls.append(argv)
            if '-Slq' in argv:
                return '\n'.join(nvidia.LOCAL_REQUIRED if argv[-1] == 'emaki-offline' else nvidia.CONDITIONAL)
            if '-Sp' in argv:
                # Return filenames of the explicit transaction to exercise deduplication.
                requested = argv[argv.index('--config') + 2:]
                return '\n'.join(name.rsplit('/', 1)[-1] + '-1-1-any.pkg.tar.zst' for name in requested)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seeds, db, cache, closure, target = self.fixture(root)
            with patch.object(nvidia, 'run', side_effect=command):
                nvidia.resolve(root / 'download.conf', db, cache, seeds, closure, target)
            downloads = [call for call in calls if '-Sw' in call]
            self.assertEqual(len(downloads), 1)
            for call in downloads:
                self.assertIn('base', call)
                self.assertIn('linux', call)
                self.assertIn('linux-lts', call)
                self.assertIn('emaki-offline/emaki-nvidia', call)
                self.assertIn('nvidia-utils', call)
                self.assertNotEqual('nvidia-open' in call, 'nvidia-open-dkms' in call)
                self.assertEqual([name for name in call if name.startswith('emaki-offline/')],
                                 ['emaki-offline/emaki-nvidia'])
            target_names = target.read_text().splitlines()
            self.assertEqual(target_names, sorted(set(target_names)))
            self.assertTrue(set(target_names) < set(closure.read_text().splitlines()))
            self.assertNotIn('live-1-1-any.pkg.tar.zst', target_names)
            self.assertIn('nvidia-open-1-1-any.pkg.tar.zst', target_names)
            self.assertIn('nvidia-open-lts-1-1-any.pkg.tar.zst', target_names)
            self.assertNotIn('nvidia-open-dkms-1-1-any.pkg.tar.zst', target_names)
            for name in nvidia.HEADERS:
                self.assertNotIn(name + '-1-1-any.pkg.tar.zst', target_names)

    def test_verification_uses_fresh_database_and_cache_for_selected_family(self):
        seen = []
        def command(argv, capture=False):
            if '-Slq' in argv:
                return '\n'.join(nvidia.CONDITIONAL)
            self.assertIn('-Syw', argv)
            db = Path(argv[argv.index('--dbpath') + 1])
            cache = Path(argv[argv.index('--cachedir') + 1])
            self.assertEqual(list(db.iterdir()), [])
            self.assertEqual(list(cache.iterdir()), [])
            seen.append((db, cache))
            (db / 'marker').write_text('resolved')
            (cache / 'marker').write_text('downloaded')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seeds, db, cache, closure, target = self.fixture(root)
            before = closure.read_bytes(), target.read_bytes()
            with patch.object(nvidia, 'run', side_effect=command):
                nvidia.resolve(root / 'target.conf', db, cache, seeds, verify=True)
            self.assertEqual(len(set(seen)), 1)
            self.assertTrue(all(not path.exists() for pair in seen for path in pair))
            self.assertEqual(before, (closure.read_bytes(), target.read_bytes()))

    def test_failed_family_does_not_publish_partial_closure(self):
        def command(argv, capture=False):
            if '-Slq' in argv:
                return '\n'.join(nvidia.CONDITIONAL)
            raise subprocess.CalledProcessError(1, argv)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seeds, db, cache, closure, target = self.fixture(root)
            before = closure.read_bytes(), target.read_bytes()
            with patch.object(nvidia, 'run', side_effect=command), self.assertRaises(subprocess.CalledProcessError):
                nvidia.resolve(root / 'download.conf', db, cache, seeds, closure, target)
            self.assertEqual(before, (closure.read_bytes(), target.read_bytes()))


if __name__ == '__main__':
    unittest.main()
