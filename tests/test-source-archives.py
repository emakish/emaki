#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline fixtures for prepared sources, shared git objects and locked Cargo vendoring."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_archive', ROOT / 'packaging/source_archive.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def git_commit(path):
    run('git', '-C', str(path), 'init', '-q')
    run('git', '-C', str(path), 'add', '.')
    run('git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture')
    return run('git', '-C', str(path), 'rev-parse', 'HEAD')


class Archives(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='emaki-sources-test-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        shutil.copy2(ROOT / 'LICENSE', self.repo / 'LICENSE')
        self.src = self.base / 'prepared'
        self.src.mkdir()
        self.out = self.base / 'out'
        self.out.mkdir()

    def recipe(self, name):
        path = self.repo / 'packaging' / name
        path.mkdir(parents=True)
        (path / 'PKGBUILD').write_text(f'pkgname={name}\npkgver=1.0\npkgrel=1\n')
        (path / 'fix.patch').write_text('fixture patch\n')
        if name in ('niri-emaki', 'emaki-config'):
            (self.src / 'THIRD-PARTY-NOTICES').write_text('Locked Rust notices\n')
        if name == 'niri-emaki':
            (self.repo / 'packaging/rust_notices.py').write_text('# notice generator fixture\n')
            (path / 'rust_notices.py').write_text('# checksummed notice generator fixture\n')
        self.commit = git_commit(self.repo)
        return path

    def unpack(self, archive):
        destination = self.base / 'unpacked'
        destination.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(destination, filter='data')
        return destination / archive.name.removesuffix('.sources.tar.gz')

    def test_font_archive_requires_and_preserves_all_faces_and_notices(self):
        recipe = self.recipe('emaki-font-iosevka')
        with self.assertRaisesRegex(ValueError, 'missing prepared source'):
            source.archive(recipe, self.src, self.out, self.commit, '3.5.1-1')
        names = [f'IosevkaNerdFontMono-{style}.ttf'
                 for style in ('Regular', 'Bold', 'Italic', 'BoldItalic')] + ['LICENSE.md', 'README.md']
        for name in names:
            (self.src / name).write_text('upstream ' + name)
        archive = source.archive(recipe, self.src, self.out, self.commit, '3.5.1-1')
        root = self.unpack(archive)
        for name in names:
            self.assertEqual((root / 'src' / name).read_bytes(), (self.src / name).read_bytes())

    def test_optional_turn_setup_source_archive_keeps_prepared_payload(self):
        recipe = self.recipe('emaki-sudo-key')
        with self.assertRaisesRegex(ValueError, 'missing prepared source'):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        project = self.src / 'emaki-sudo-key'
        for relative in ('scripts/emaki-sudo-turn', 'sudo/turn-reporting.md'):
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        shutil.copytree(ROOT / 'sudo/integrations', project / 'sudo/integrations')
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        root = self.unpack(archive)
        for path in project.rglob('*'):
            if path.is_file():
                self.assertEqual((root / 'src/emaki-sudo-key' / path.relative_to(project)).read_bytes(),
                                 path.read_bytes())
        self.assertEqual((root / 'SOURCE-COMMIT').read_text().strip(), self.commit)

    def test_quickshell_archive_has_prepared_patch_and_recipe(self):
        recipe = self.recipe('quickshell-emaki')
        project = self.src / 'quickshell'
        project.mkdir()
        (project / 'capture.cpp').write_text('patched capture\n')
        (self.src / 'fix.patch').symlink_to(recipe / 'fix.patch')
        (project / 'alias.cpp').symlink_to('capture.cpp')
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        shutil.rmtree(self.src)
        root = self.unpack(archive)
        self.assertEqual((root / 'src/quickshell/capture.cpp').read_text(), 'patched capture\n')
        self.assertEqual((root / 'src/fix.patch').read_text(), 'fixture patch\n')
        self.assertEqual((root / 'src/quickshell/alias.cpp').read_text(), 'patched capture\n')
        self.assertEqual((root / 'SOURCE-COMMIT').read_text().strip(), self.commit)
        self.assertIn('makepkg --noextract', (root / 'REBUILD.txt').read_text())
        metadata = json.loads((self.out / 'SOURCES.json').read_text())
        self.assertEqual(metadata['commit'], self.commit)
        self.assertEqual(metadata['packages']['quickshell-emaki']['sha256'],
                         hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_portal_shared_git_sources_survive_deleting_original_stores(self):
        recipe = self.recipe('xdg-desktop-portal-gnome-emaki')
        expected = {}
        for name in ('xdg-desktop-portal-gnome', 'libgxdp'):
            origin = self.base / ('origin-' + name)
            origin.mkdir()
            (origin / 'source.c').write_text(name + '\n')
            expected[name] = git_commit(origin)
            run('git', 'clone', '-q', '--shared', str(origin), str(self.src / name))
            (self.src / name / 'source.c').write_text(name + ' prepared\n')
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        shutil.rmtree(self.src)
        for origin in self.base.glob('origin-*'):
            shutil.rmtree(origin)
        root = self.unpack(archive)
        for name, commit in expected.items():
            checkout = root / 'src' / name
            self.assertEqual(run('git', '-C', str(checkout), 'rev-parse', 'HEAD'), commit)
            run('git', '-C', str(checkout), 'fsck', '--full')
            self.assertEqual((checkout / 'source.c').read_text(), name + ' prepared\n')
            self.assertFalse((checkout / '.git/objects/info/alternates').exists())

    @unittest.skipUnless(shutil.which('cargo') and shutil.which('rustc'), 'Cargo and Rust required')
    def test_niri_vendors_locked_git_crate_and_rebuilds_without_cache(self):
        recipe = self.recipe('niri-emaki')
        dependency = self.base / 'dependency'
        (dependency / 'src').mkdir(parents=True)
        (dependency / 'Cargo.toml').write_text('[package]\nname="fixture-dep"\nversion="0.1.0"\nedition="2021"\n')
        (dependency / 'src/lib.rs').write_text('pub fn value() -> u8 { 7 }\n')
        revision = git_commit(dependency)
        project = self.src / 'niri-1.0'
        (project / 'src').mkdir(parents=True)
        (project / 'Cargo.toml').write_text(
            '[package]\nname="fixture-niri"\nversion="0.1.0"\nedition="2021"\n'
            '[dependencies]\nfixture-dep = { git = "' + dependency.as_uri() + '", rev = "' + revision + '" }\n')
        (project / 'src/main.rs').write_text('fn main() { assert_eq!(fixture_dep::value(), 7); }\n')
        run('cargo', 'generate-lockfile', cwd=project,
            env=dict(os.environ, CARGO_HOME=str(self.src / 'cargo-home')))
        source.vendor(self.src)
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        shutil.rmtree(self.src)
        shutil.rmtree(dependency)
        root = self.unpack(archive)
        self.assertFalse((root / 'src/cargo-home').exists())
        self.assertEqual((root / 'src/THIRD-PARTY-NOTICES').read_text(), 'Locked Rust notices\n')
        self.assertTrue((root / 'rust_notices.py').is_file())
        self.assertTrue((root / 'src/niri-1.0/vendor/fixture-dep/.cargo-checksum.json').exists() or
                        list((root / 'src/niri-1.0/vendor').glob('*/.cargo-checksum.json')))
        run('cargo', 'run', '--frozen', cwd=root / 'src/niri-1.0',
            env=dict(os.environ, CARGO_HOME=str(self.base / 'empty-cargo'), CARGO_NET_OFFLINE='true'))

    @unittest.skipUnless(shutil.which('cargo') and shutil.which('rustc'), 'Cargo and Rust required')
    def test_prepare_core_fills_selected_cache_for_offline_build(self):
        dependency = self.base / 'dependency'
        (dependency / 'src').mkdir(parents=True)
        (dependency / 'Cargo.toml').write_text(
            '[package]\nname="fixture-dep"\nversion="0.1.0"\nedition="2021"\n')
        (dependency / 'src/lib.rs').write_text('pub fn value() -> u8 { 7 }\n')
        revision = git_commit(dependency)
        project = self.base / 'project'
        (project / 'src').mkdir(parents=True)
        (project / 'crates').mkdir()
        (project / 'Cargo.toml').write_text(
            '[workspace]\n[package]\nname="fixture-core"\nversion="0.1.0"\nedition="2021"\n'
            '[dependencies]\nfixture-dep = { git = "' + dependency.as_uri()
            + '", rev = "' + revision + '" }\n')
        (project / 'src/main.rs').write_text('fn main() { assert_eq!(fixture_dep::value(), 7); }\n')
        shutil.copyfile(ROOT / 'Makefile', project / 'Makefile')
        seed = self.base / 'seed-cache'
        # The only fetch is a local file URL, including when the enclosing build is offline.
        preparation_env = dict(os.environ, CARGO_NET_OFFLINE='false')
        run('cargo', 'generate-lockfile', cwd=project,
            env=dict(preparation_env, CARGO_HOME=str(seed)))
        lock = (project / 'Cargo.lock').read_bytes()
        shutil.rmtree(seed)
        cache = self.base / 'selected-cache'
        target = self.base / 'target'
        env = dict(os.environ, CARGO_HOME=str(cache), CARGO_TARGET_DIR=str(target))
        missing = subprocess.run(['cargo', 'build', '--locked', '--offline'], cwd=project,
                                 env=env, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        run('make', 'prepare-core', f'CARGO_HOME={cache}', f'CARGO_TARGET_DIR={target}',
            cwd=project, env=preparation_env)
        shutil.rmtree(dependency)
        run('cargo', 'run', '--locked', '--offline', cwd=project,
            env=dict(env, CARGO_NET_OFFLINE='true'))
        self.assertEqual((project / 'Cargo.lock').read_bytes(), lock)
        self.assertFalse((project / '.cache/cargo-home').exists())

    def test_niri_archive_keeps_checksummed_recipe_helper(self):
        recipe = self.recipe('niri-emaki')
        (self.src / 'niri-1.0/vendor').mkdir(parents=True)
        (self.src / 'niri-1.0/.cargo').mkdir()
        (self.src / 'niri-1.0/.cargo/config.toml').write_text('[net]\noffline = true\n')
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        root = self.unpack(archive)
        self.assertEqual((root / 'rust_notices.py').read_bytes(),
                         (recipe / 'rust_notices.py').read_bytes())

    def test_snapguard_release_checkout_survives_deleting_original_store(self):
        recipe = self.recipe('snapguard')
        origin = self.base / 'origin-snapguard'
        origin.mkdir()
        (origin / 'snapshot-guard.py').write_text('guard\n')
        commit = git_commit(origin)
        run('git', 'clone', '-q', '--shared', str(origin), str(self.src / 'snapguard'))
        archive = source.archive(recipe, self.src, self.out, self.commit, '0.3.0-1')
        self.assertEqual(json.loads((self.out / 'SOURCES.json').read_text())['packages']['snapguard']['archive'],
                         'snapguard-0.3.0-1.sources.tar.gz')
        shutil.rmtree(self.src)
        shutil.rmtree(origin)
        checkout = self.unpack(archive) / 'src/snapguard'
        self.assertEqual(run('git', '-C', str(checkout), 'rev-parse', 'HEAD'), commit)
        run('git', '-C', str(checkout), 'fsck', '--full')
        self.assertEqual((checkout / 'snapshot-guard.py').read_text(), 'guard\n')

    def test_snapguard_without_its_release_checkout_is_rejected(self):
        recipe = self.recipe('snapguard')
        (self.src / 'snapguard').mkdir()
        (self.src / 'snapguard/snapshot-guard.py').write_text('guard\n')
        with self.assertRaisesRegex(ValueError, 'missing prepared source: snapguard/.git'):
            source.archive(recipe, self.src, self.out, self.commit, '0.3.0-1')

    def shim_sources(self):
        """Lay out src/ as makepkg leaves it: downloads linked from SRCDEST, the RPM unpacked."""
        store = self.base / 'srcdest'
        store.mkdir()
        for name, data in (('shim-x64-16.1-7.x86_64.rpm', b'fedora rpm\n'), ('shim-16.1-COPYRIGHT', b'bsd text\n')):
            (store / name).write_bytes(data)
            (self.src / name).symlink_to(store / name)
        (self.src / 'shim-16.1.tar.bz2').write_bytes(b'upstream source fixture')
        for name in ('shim-16.1/COPYRIGHT', 'shim-16.1/gnu-efi/README.efilib',
                     'shim-16.1/Cryptlib/OpenSSL/LICENSE', 'notices/SHIM-NOTICES',
                     'notices/GNU-EFI-NOTICES', 'notices/CRYPTLIB-NOTICES', 'notices/OPENSSL-NOTICES'):
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('upstream terms fixture\n')
        efi = self.src / 'rpm/usr/lib/efi/shim/16.1-7/EFI/fedora'
        efi.mkdir(parents=True)
        for name in ('shimx64.efi', 'mmx64.efi'):
            (efi / name).write_bytes(name.encode())
        return store, efi

    def test_shim_archive_keeps_the_pinned_rpm_after_its_download_store_is_gone(self):
        recipe = self.recipe('emaki-shim-signed')
        store, _ = self.shim_sources()
        archive = source.archive(recipe, self.src, self.out, self.commit, '16.1+fedora+7-1')
        self.assertEqual(json.loads((self.out / 'SOURCES.json').read_text())['packages']['emaki-shim-signed']['archive'],
                         'emaki-shim-signed-16.1+fedora+7-1.sources.tar.gz')
        shutil.rmtree(self.src)
        shutil.rmtree(store)
        root = self.unpack(archive) / 'src'
        rpm = root / 'shim-x64-16.1-7.x86_64.rpm'
        self.assertFalse(rpm.is_symlink())
        self.assertEqual(rpm.read_bytes(), b'fedora rpm\n')
        self.assertEqual((root / 'shim-16.1-COPYRIGHT').read_bytes(), b'bsd text\n')
        self.assertEqual((root / 'rpm/usr/lib/efi/shim/16.1-7/EFI/fedora/shimx64.efi').read_bytes(), b'shimx64.efi')

    def test_shim_without_its_unpacked_loaders_is_rejected(self):
        recipe = self.recipe('emaki-shim-signed')
        _, efi = self.shim_sources()
        (efi / 'mmx64.efi').unlink()
        with self.assertRaisesRegex(ValueError, 'missing prepared source: rpm/usr/lib/efi/shim/16.1-7/EFI/fedora/mmx64.efi'):
            source.archive(recipe, self.src, self.out, self.commit, '16.1+fedora+7-1')

    def test_shim_without_its_rpm_is_rejected(self):
        recipe = self.recipe('emaki-shim-signed')
        self.shim_sources()
        (self.src / 'shim-x64-16.1-7.x86_64.rpm').unlink()
        with self.assertRaisesRegex(ValueError, 'missing prepared source: shim-x64-16.1-7.x86_64.rpm'):
            source.archive(recipe, self.src, self.out, self.commit, '16.1+fedora+7-1')

    def test_missing_second_portal_source_is_rejected(self):
        recipe = self.recipe('xdg-desktop-portal-gnome-emaki')
        (self.src / 'xdg-desktop-portal-gnome').mkdir()
        with self.assertRaisesRegex(ValueError, 'libgxdp'):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')

    def test_changed_recipe_is_rejected(self):
        recipe = self.recipe('quickshell-emaki')
        (self.src / 'quickshell').mkdir()
        (recipe / 'fix.patch').write_text('different patch\n')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')

    def test_mixed_commit_metadata_is_rejected(self):
        recipe = self.recipe('quickshell-emaki')
        (self.src / 'quickshell').mkdir()
        (self.out / 'SOURCES.json').write_text(json.dumps({'format': 1, 'commit': '0' * 40}))
        with self.assertRaisesRegex(ValueError, 'another build'):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')

    def test_recipe_without_committed_gpl_text_is_rejected(self):
        recipe = self.recipe('emaki')
        (self.repo / 'LICENSE').unlink()
        self.commit = git_commit(self.repo)
        with self.assertRaises(subprocess.CalledProcessError):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        self.assertFalse(list(self.out.glob('*.sources.tar.gz')))

    def test_empty_metapackage_is_self_contained(self):
        recipe = self.recipe('emaki')
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        shutil.rmtree(self.repo)
        root = self.unpack(archive)
        self.assertTrue((root / 'src').is_dir())
        self.assertEqual((root / 'PKGBUILD').read_text(), 'pkgname=emaki\npkgver=1.0\npkgrel=1\n')
        self.assertNotIn('/tree/', (root / 'REBUILD.txt').read_text())
        self.assertEqual((root / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())

    def checkout_fixture(self):
        if not (ROOT / 'scripts/make-public.sh').is_file():
            self.skipTest('private export policy is not shipped in the public tree')
        (self.repo / 'scripts/public').mkdir(parents=True)
        (self.repo / 'art/wallpaper').mkdir(parents=True)
        (self.repo / 'art/wallpaper/README.md').write_text('private background notes')
        (self.repo / 'scripts/public/wallpaper-README.md').write_text('public background description')
        shutil.copy2(ROOT / 'scripts/make-public.sh', self.repo / 'scripts/make-public.sh')
        (self.repo / 'scripts/public/allow.txt').write_text('')
        (self.repo / 'Makefile').write_text('check:\n\tpython3 tests/test-make-public.py\n\techo checked\n')
        return self.base / 'public-inputs'

    def test_checkout_uses_public_exclusions_before_archiving(self):
        target = self.checkout_fixture()
        policy = (self.repo / 'scripts/make-public.sh').read_text()
        notes = source.shlex.split(source.re.search(r'^NOTES=\((.*?)\)', policy,
                                                    source.re.M | source.re.S)[1])
        private = self.repo / 'nested' / notes[0].swapcase()
        private.parent.mkdir()
        private.write_text('private notes')
        commit = git_commit(self.repo)
        source.checkout(self.repo, target, commit)
        self.assertFalse((target / private.relative_to(self.repo)).exists())
        self.assertFalse((target / 'scripts/public').exists())
        self.assertNotIn('test-make-public.py', (target / 'Makefile').read_text())
        self.assertEqual((target / 'art/wallpaper/README.md').read_text(), 'public background description')
        self.assertFalse((target / '.git').exists())
        public_commit = git_commit(target)
        with self.assertRaisesRegex(ValueError, 'cannot read committed public source exclusion policy'):
            source.checkout(target, self.base / 'second-export', public_commit)
        self.assertFalse((self.base / 'second-export').exists())

    def test_checkout_refuses_missing_or_incomplete_policy(self):
        for policy in (None, 'EXCLUDE=()\nNOTES=()\n',
                       'EXCLUDE=(private)\nNOTES=(private)\n'):
            with self.subTest(policy=policy):
                path = self.repo / 'scripts/make-public.sh'
                path.parent.mkdir(exist_ok=True)
                if policy is not None:
                    path.write_text(policy)
                (self.repo / 'input').write_text('public input')
                commit = git_commit(self.repo)
                with self.assertRaisesRegex(ValueError, 'public source'):
                    source.checkout(self.repo, self.base / 'refused', commit)
                self.assertFalse((self.base / 'refused').exists())

    def test_checkout_guard_checks_ignored_files(self):
        target = self.checkout_fixture()
        identity = '@'.join(('private-person', 'private.invalid'))
        (self.repo / 'identity.txt').write_text(identity)
        commit = git_commit(self.repo)
        shutil.copy2(ROOT / 'LICENSE', self.repo / 'LICENSE')
        (self.repo / '.gitignore').write_text('identity.txt\n')
        commit = git_commit(self.repo)
        with self.assertRaisesRegex(ValueError, 'identity.txt'):
            source.checkout(self.repo, target, commit)

    def test_checkout_refuses_missing_allow_list(self):
        target = self.checkout_fixture()
        (self.repo / 'scripts/public/allow.txt').unlink()
        commit = git_commit(self.repo)
        with self.assertRaisesRegex(ValueError, 'public source policy failed'):
            source.checkout(self.repo, target, commit)

    def test_checkout_refuses_archive_substitution(self):
        target = self.checkout_fixture()
        (self.repo / '.gitattributes').write_text('input export-subst\n')
        (self.repo / 'input').write_text('$' + 'Format:%an$')
        commit = git_commit(self.repo)
        with self.assertRaisesRegex(ValueError, 'attributes enable export-subst'):
            source.checkout(self.repo, target, commit)
        self.assertFalse(target.exists())

    @unittest.skipUnless(shutil.which('cargo') and shutil.which('rustc'), 'Cargo and Rust required')
    def test_config_recipe_loads_and_builds_without_checkout_or_download_cache(self):
        recipe = self.recipe('emaki-config')
        shutil.copy2(ROOT / 'packaging/emaki-config/PKGBUILD', recipe / 'PKGBUILD')
        self.commit = git_commit(self.repo)
        project = self.src / 'emaki'
        (project / 'src').mkdir(parents=True)
        (project / 'Cargo.toml').write_text('[package]\nname="fixture-config"\nversion="0.1.0"\nedition="2021"\n')
        (project / 'src/main.rs').write_text('fn main() {}\n')
        (project / 'Makefile').write_text('build:\n\tcargo build --frozen\n')
        (self.src / 'emaki-source-commit').write_text(self.commit + '\n')
        run('cargo', 'generate-lockfile', cwd=project)
        source.vendor(self.src)
        archive = source.archive(recipe, self.src, self.out, self.commit, '1.0-1')
        shutil.rmtree(self.repo)
        shutil.rmtree(self.src)
        root = self.unpack(archive)
        run('bash', '-c', 'startdir=$PWD; srcdir=$PWD/src; source PKGBUILD; build', cwd=root,
            env=dict(os.environ, CARGO_NET_OFFLINE='true', EMAKI_SOURCE_COMMIT=self.commit))
        self.assertTrue((root / 'src/emaki/.cache/target/debug/fixture-config').is_file())
        refusal = subprocess.run(['bash', '-c', 'startdir=$PWD; source PKGBUILD'], cwd=root,
                                 env=dict(os.environ, EMAKI_SOURCE_COMMIT='0' * 40))
        self.assertNotEqual(refusal.returncode, 0)

    def test_external_directory_symlink_is_rejected(self):
        recipe = self.recipe('quickshell-emaki')
        (self.src / 'quickshell').mkdir()
        external = self.base / 'external'
        external.mkdir()
        (self.src / 'quickshell/external').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'external directory'):
            source.archive(recipe, self.src, self.out, self.commit, '1.0-1')

class BuildWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = ('makepkg', 'pacman', 'vercmp', 'fakeroot', 'zstd', 'bsdtar')
        missing = [name for name in required if not shutil.which(name)]
        if missing or os.geteuid() == 0:
            raise unittest.SkipTest('requires a normal build user and Arch build tools: ' + ', '.join(missing))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='emaki-build-sources-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / 'tree'
        recipe = self.repo / 'packaging/quickshell-emaki'
        recipe.mkdir(parents=True)
        shutil.copy2(ROOT / 'LICENSE', self.repo / 'LICENSE')
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        for filename in ('build.sh', 'source_archive.py', 'release_inputs.py'):
            shutil.copy2(ROOT / 'packaging' / filename, self.repo / 'packaging' / filename)
        constants = self.repo / 'installer/emaki_installer/constants.py'
        constants.parent.mkdir(parents=True)
        constants.write_text('ALONGSIDE = False\n')
        (recipe / 'input.txt').write_text('original\n')
        (recipe / 'PKGBUILD').write_text('''pkgname=quickshell-emaki
pkgver=1.0
pkgrel=1
pkgdesc='Source archive fixture'
arch=(any)
license=(GPL-3.0-or-later)
source=(input.txt)
sha256sums=(SKIP)
prepare() {
  mkdir quickshell
  sed s/original/patched/ input.txt > quickshell/content.txt
  printf 'prepare\\n' >> "$BUILD_EVENTS"
}
build() {
  test "$(cat quickshell/content.txt)" = patched
  test -s "$PKGDEST/SOURCES.json"
  printf 'build\\n' >> "$BUILD_EVENTS"
  cp quickshell/content.txt quickshell/build-output
}
package() {
  install -Dm644 quickshell/build-output "$pkgdir/usr/share/source-fixture/content.txt"
}
''')
        self.commit = git_commit(self.repo)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name, body in {'systemd-detect-virt': 'echo qemu', 'gpg': 'exit 0',
                           'pacman': '[[ $1 == -T ]]'}.items():
            path = self.bin / name
            path.write_text('#!/bin/bash\n' + body + '\n')
            path.chmod(0o755)
        self.out = self.base / 'out'
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TMPDIR=str(self.base), BUILD_EVENTS=str(self.base / 'events'))

    def build(self, name='quickshell-emaki'):
        result = subprocess.run(['bash', self.repo / 'packaging/build.sh', '--out', self.out,
                                 '--only', name], env=self.env, capture_output=True, text=True)
        log = self.out / ('logs/' + name + '.log')
        if log.exists():
            result.stderr += log.read_text()
        return result

    def test_prepared_archive_precedes_build_and_matches_binary(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.base / 'events').read_text(), 'prepare\nbuild\n')
        record = json.loads((self.out / 'SOURCES.json').read_text())
        self.assertEqual(record['commit'], self.commit)
        archive = self.out / record['packages']['quickshell-emaki']['archive']
        with tarfile.open(archive) as tar:
            paths = tar.getnames()
            self.assertIn('quickshell-emaki-1.0-1/src/quickshell/content.txt', paths)
            self.assertNotIn('quickshell-emaki-1.0-1/src/quickshell/build-output', paths)
        lines = (self.out / 'BUILDINFO').read_text().splitlines()
        self.assertEqual(lines[0], 'commit ' + self.commit)
        hashed = {}
        for line in lines[1:]:
            digest, filename = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.out / filename).read_bytes()).hexdigest())
            hashed[filename] = digest
        self.assertIn('SOURCES.json', hashed)
        self.assertIn(archive.name, hashed)
        package = next(self.out.glob('*.pkg.tar.zst'))
        self.assertIn(package.name, hashed)
        self.assertEqual(run('bsdtar', '-xOf', str(package), 'usr/share/source-fixture/content.txt'), 'patched')

    def test_local_and_empty_packages_rebuild_without_original_recipe(self):
        for name, local in (('emaki-mirrorlist', True), ('emaki', False)):
            with self.subTest(name=name):
                recipe = self.repo / 'packaging' / name
                recipe.mkdir()
                if local:
                    (recipe / 'input.txt').write_text('local input\n')
                (recipe / 'PKGBUILD').write_text(
                    'pkgname=' + name + '\npkgver=1.0\npkgrel=1\narch=(any)\n'
                    + ('source=(input.txt)\nsha256sums=(SKIP)\n' if local else 'source=()\n')
                    + 'package() {\n'
                    + ('install -Dm644 "$srcdir/input.txt" "$pkgdir/usr/share/fixture/input.txt"\n'
                       if local else 'mkdir -p "$pkgdir/usr/share/fixture"\n') + '}\n')
                git_commit(self.repo)
                self.out = self.base / ('out-' + name)
                result = self.build(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                archive = next(self.out.glob('*.sources.tar.gz'))
                unpacked = self.base / ('unpacked-' + name)
                with tarfile.open(archive) as contents:
                    contents.extractall(unpacked, filter='data')
                shutil.rmtree(recipe)
                root = next(unpacked.iterdir())
                if local:
                    self.assertFalse((root / 'src/input.txt').is_symlink())
                run('makepkg', '--noextract', '--nodeps', '--noconfirm', cwd=root,
                    env=dict(os.environ, BUILDDIR=str(root), PKGDEST=str(root), SRCDEST=str(root)))
                self.assertTrue(list(root.glob('*.pkg.tar.zst')))
                git_commit(self.repo)

    def test_installer_package_rebuilds_from_archive(self):
        recipe = self.repo / 'packaging/emaki-installer'
        recipe.mkdir()
        shutil.copy2(ROOT / 'packaging/emaki-installer/PKGBUILD', recipe / 'PKGBUILD')
        commit = git_commit(self.repo)
        prepared = self.base / 'installer-prepared'
        project = prepared / 'emaki-installer'
        shutil.copytree(ROOT / 'installer', project / 'installer', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'i18n/installer', project / 'i18n/installer')
        shutil.copy2(ROOT / 'i18n/catalogues.py', project / 'i18n/catalogues.py')
        shutil.copy2(ROOT / 'LICENSE', project / 'LICENSE')
        archive = source.archive(recipe, prepared, self.out, commit, '0.2.0-1')
        unpacked = self.base / 'installer-unpacked'
        with tarfile.open(archive) as contents:
            contents.extractall(unpacked, filter='data')
        shutil.rmtree(self.repo)
        shutil.rmtree(prepared)
        root = next(unpacked.iterdir())
        run('makepkg', '--noextract', '--nodeps', '--nocheck', '--noconfirm', cwd=root,
            env=dict(os.environ, BUILDDIR=str(root), PKGDEST=str(root), SRCDEST=str(root)))
        self.assertTrue(list(root.glob('emaki-installer-*.pkg.tar.zst')))

    def test_dirty_tree_stops_before_preparing(self):
        (self.repo / 'packaging/quickshell-emaki/input.txt').write_text('changed\n')
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('uncommitted changes', result.stderr)
        self.assertFalse((self.base / 'events').exists())

    def test_prepare_failure_stops_before_archiving_and_build(self):
        recipe = self.repo / 'packaging/quickshell-emaki/PKGBUILD'
        recipe.write_text(recipe.read_text().replace('  mkdir quickshell', '  return 1\n  mkdir quickshell'))
        git_commit(self.repo)
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.out / 'SOURCES.json').exists())
        self.assertFalse((self.base / 'events').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
