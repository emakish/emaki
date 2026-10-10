#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Delivery mutations run offline, without Git metadata or compiled binaries."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('delivery', ROOT / 'scripts/check-delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class Delivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep the full staging payload under the worktree, not in /tmp.
        cache = ROOT / '.cache/evidence'
        cache.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='delivery-test-', dir=cache)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name) / 'source'
        cls.root.mkdir()
        # Real recipes, no .git and no build artifacts. Copy only their inputs.
        directories = {path.split('/')[0] for path in delivery.RUNTIME_ROOTS}
        directories.update(('docs', 'packaging', 'installer'))
        directories.discard('iso')
        for directory in directories:
            if (ROOT / directory).is_dir():
                shutil.copytree(ROOT / directory, cls.root / directory, symlinks=True,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc',
                                                             'artifacts',
                                                             *(['src', 'pkg'] if directory == 'packaging' else [])))
        shutil.copytree(ROOT / 'iso/profile/airootfs',
                        cls.root / 'iso/profile/airootfs', symlinks=True)
        for name in ('Makefile', 'LICENSE'):
            shutil.copyfile(ROOT / name, cls.root / name)
        cls.exempt = delivery.exceptions(cls.root)
        cls.payloads = {}
        cls.delivered = delivery.installed_sources(cls.root, cls.payloads)

    def test_archive_without_git_stages_runtime_payload(self):
        self.assertFalse((self.root / '.git').exists())
        self.assertFalse(delivery.problems(delivery.candidates(self.root),
                                           self.delivered, self.exempt))
        for name in ('shell/shell.qml', 'niri/default.kdl', 'upkeep/emaki-system-migrate',
                     'systemd/emaki-shell.service', 'installer/ui/InstallerView.qml',
                     'packaging/emaki-nvidia/session.sh', 'installer/emaki_installer/graphics.py'):
            self.assertIn(name, self.delivered)

    def test_each_package_payload_matches_its_inventory(self):
        self.assertFalse(delivery.payload_problems(self.root, self.payloads))
        self.assertIn('/usr/share/emaki-installer/ui/shaders', self.payloads['emaki-installer'])
        self.assertEqual(self.payloads['emaki'], set())
        self.assertEqual(self.payloads['niri-emaki'], {'/usr/bin/niri-emaki'})

    def test_inventory_drift_and_missing_list_fail(self):
        listing = self.root / 'packaging/emaki-apps/expected-files.list'
        original = listing.read_text()
        try:
            listing.write_text(original + '/usr/share/emaki/not-installed\n')
            errors = delivery.payload_problems(self.root, self.payloads)
            self.assertTrue(any('listed path is not installed' in error for error in errors), errors)
            listing.write_text('# Empty inventory\n')
            errors = delivery.payload_problems(self.root, self.payloads)
            self.assertEqual(len(errors), len(self.payloads['emaki-apps']))
            self.assertTrue(all('absent from expected-files.list' in error for error in errors))
            listing.unlink()
            errors = delivery.payload_problems(self.root, self.payloads)
            self.assertEqual(errors, ['emaki-apps: missing expected-files.list'])
        finally:
            listing.write_text(original)

    def test_changed_package_destination_fails_inventory(self):
        recipe = self.root / 'packaging/emaki-apps/PKGBUILD'
        original = recipe.read_text()
        try:
            recipe.write_text(original.replace('$pkgdir/usr/share/applications/emaki-printers.desktop',
                                               '$pkgdir/usr/share/applications/moved-printers.desktop'))
            payloads = {}
            delivery.installed_sources(self.root, payloads)
            errors = delivery.payload_problems(self.root, payloads)
            self.assertEqual(len(errors), 2, errors)
            self.assertTrue(any('moved-printers.desktop' in error for error in errors))
        finally:
            recipe.write_text(original)

    def test_archive_generated_path_modules_are_not_source_candidates(self):
        self.assertFalse((self.root / '.git').exists())
        subprocess.run(['python3', str(self.root / 'scripts/render-paths'),
                        '--source', str(self.root)], check=True)
        generated = ('scripts/emaki_paths.py', 'scripts/paths',
                     'shell/Platform.qml', 'shell/helpers/emaki_paths.py')
        neighbors = ('scripts/emaki_paths_extra.py', 'scripts/paths-extra',
                     'shell/PlatformExtra.qml', 'shell/helpers/emaki_paths_extra.py')
        try:
            for name in neighbors:
                (self.root / name).write_text('# delivery mutation\n')
            files = delivery.candidates(self.root)
            for name in generated:
                self.assertTrue((self.root / name).is_file(), name)
                self.assertNotIn(name, files)
            self.assertIn('scripts/render-paths', files)
            for name in neighbors:
                self.assertIn(name, files)
            errors = delivery.problems(files, self.delivered, self.exempt)
            self.assertEqual(sorted(error.split(':')[0] for error in errors),
                             sorted(neighbors), errors)
        finally:
            for name in neighbors:
                (self.root / name).unlink()

    def test_new_unshipped_file_fails_even_if_recipe_comment_mentions_it(self):
        path = self.root / 'scripts/emaki-unshipped-test'
        path.write_text('# delivery mutation\n')
        makefile = self.root / 'Makefile'
        original = makefile.read_text()
        makefile.write_text(original + '\n# scripts/emaki-unshipped-test\n')
        try:
            errors = delivery.problems(delivery.candidates(self.root),
                                       delivery.installed_sources(self.root), self.exempt)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn('scripts/emaki-unshipped-test: no package installs', errors[0])
        finally:
            path.unlink()
            makefile.write_text(original)

    def test_git_ignored_artifacts_are_not_sources(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        try:
            (self.root / '.gitignore').write_text('installer/ui/tests/artifacts/\n')
            artifact = self.root / 'installer/ui/tests/artifacts/run.log'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('test output\n')
            unignored = self.root / 'scripts/emaki-unshipped-test'
            unignored.write_text('# delivery mutation\n')
            errors = delivery.problems(delivery.candidates(self.root), self.delivered, self.exempt)
            self.assertEqual([e.split(':')[0] for e in errors], ['scripts/emaki-unshipped-test'], errors)
        finally:
            shutil.rmtree(self.root / '.git')
            (self.root / '.gitignore').unlink()
            shutil.rmtree(self.root / 'installer/ui/tests/artifacts')
            (self.root / 'scripts/emaki-unshipped-test').unlink()

    def test_new_unshipped_file_in_each_added_root_fails(self):
        names = (
            'packaging/emaki-apps/unshipped-test',
            'packaging/emaki-desktop/unshipped-test',
            'packaging/emaki-keyring/unshipped-test',
            'packaging/emaki-mirrorlist/unshipped-test',
            'packaging/emaki-installer/unshipped-test',
            'packaging/emaki-nvidia/unshipped-test',
            'installer/unshipped_test.py',
            'iso/profile/airootfs/etc/unshipped-test',
            'crates/emaki-core/src/unshipped_test.rs',
        )
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('# delivery mutation\n')
                try:
                    errors = delivery.problems(delivery.candidates(self.root),
                                               self.delivered, self.exempt)
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn(f'{name}: no package installs', errors[0])
                finally:
                    path.unlink()

    def test_added_roots_include_existing_payload_and_build_inputs(self):
        files = delivery.candidates(self.root)
        for name in ('crates/emaki-core/src/lib.rs',
                     'crates/emaki-cli/Cargo.toml',
                     'iso/profile/airootfs/etc/hostname',
                     'packaging/emaki-apps/PKGBUILD',
                     'packaging/emaki-keyring/emaki-keyring.install'):
            self.assertIn(name, files)
            self.assertIn(name, self.exempt)
        for name in ('packaging/emaki-apps/emaki-printers.desktop',
                     'packaging/emaki-desktop/emaki-lock.pam',
                     'packaging/emaki-keyring/emaki.gpg',
                     'packaging/emaki-mirrorlist/stable.conf'):
            self.assertIn(name, files)
            self.assertIn(name, self.delivered)

    def test_removed_install_rule_fails(self):
        makefile = self.root / 'Makefile'
        original = makefile.read_text()
        makefile.write_text(original.replace('niri/default.kdl niri/theme.kdl niri/shell.kdl',
                                             'niri/default.kdl niri/theme.kdl'))
        try:
            errors = delivery.problems(delivery.candidates(self.root),
                                       delivery.installed_sources(self.root), self.exempt)
            self.assertTrue(any(error.startswith('niri/shell.kdl:') for error in errors), errors)
        finally:
            makefile.write_text(original)

    def test_new_file_in_wholesale_shell_install_is_delivered(self):
        path = self.root / 'shell/DeliveryTest.qml'
        path.write_text('// delivery mutation\n')
        try:
            self.assertIn('shell/DeliveryTest.qml', delivery.installed_sources(self.root))
        finally:
            path.unlink()

    def test_blank_reason_and_directory_exemptions_are_rejected(self):
        manifest = self.root / 'packaging/delivery-dev-only.tsv'
        original = manifest.read_text()
        try:
            for line in ('scripts/new\t\ttests\n', 'scripts/*\tbuild only\tMakefile\n'):
                manifest.write_text(line)
                with self.assertRaises(ValueError):
                    delivery.exceptions(self.root)
        finally:
            manifest.write_text(original)


class BuiltPayloads(unittest.TestCase):
    def test_recipe_rejects_added_and_removed_payload_files(self):
        for name in ('quickshell-emaki', 'quickshell-emaki/qt-6.12',
                     'xdg-desktop-portal-gnome-emaki'):
            with self.subTest(recipe=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / 'source'
                source.mkdir()
                package = root / 'package'
                package.mkdir()
                shell = source / 'quickshell'
                (shell / 'build').mkdir(parents=True)
                (shell / 'build/emaki-qt-build-version').write_text('6.12.0\n')
                (shell / 'LICENSE').write_text('fixture\n')
                expected = ['/usr/share/payload']
                if name.startswith('quickshell-emaki'):
                    expected += ['/usr/share/licenses/quickshell-emaki/LICENSE',
                                 '/usr/share/quickshell-emaki/qt-build-version']
                listing = source / 'expected-files.list'
                listing.write_text('# Fixture inventory\n' + '\n'.join(sorted(expected)) + '\n')
                script = r'''source "$1"
startdir=$2 srcdir=$2 pkgdir=$3
_qt_build_version() { printf '%s\n' '6.12.0'; }
cmake() { mkdir -p "$pkgdir/usr/share"; touch "$pkgdir/usr/share/payload"; }
meson() { cmake; }
cd "$srcdir"
package
'''
                argv = ['bash', '-e', '-c', script, 'test',
                        str(ROOT / 'packaging' / name / 'PKGBUILD'), str(source), str(package)]
                result = subprocess.run(argv, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                (package / 'usr/share/unexpected').touch()
                result = subprocess.run(argv, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('/usr/share/unexpected', result.stdout)
                (package / 'usr/share/unexpected').unlink()
                listing.write_text(listing.read_text() + '/usr/share/missing\n')
                result = subprocess.run(argv, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('/usr/share/missing', result.stdout)


class PrivatePythonBytecode(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='private-python-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith('PYTHON')}

    def stage(self, source, target):
        destination = self.root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
        destination.chmod(0o755)
        return destination

    def assert_no_bytecode(self):
        self.assertEqual(list(self.root.rglob('__pycache__')), [])
        self.assertEqual(list(self.root.rglob('*.pyc')), [])

    def test_cold_boot_entry_point_imports_without_writing_bytecode(self):
        library = self.root / 'usr/lib/emaki/boot'
        for module in ('__init__', 'refresh', 'update_menu', 'update', 'usable'):
            self.stage(f'grub/emaki_boot/{module}.py',
                       f'usr/lib/emaki/boot/emaki_boot/{module}.py')
        for module in ('boot', 'errors', 'grub_screen'):
            self.stage(f'installer/emaki_installer/{module}.py',
                       f'usr/lib/emaki/boot/emaki_boot/{module}.py')
        entry = self.stage('scripts/emaki-boot-refresh', 'usr/bin/emaki-boot-refresh')
        entry.write_text(entry.read_text().replace('/usr/lib/emaki/boot', str(library)))
        result = subprocess.run([str(entry), '--help'], env=self.environment,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--mark-good', result.stdout)
        entry = self.stage('scripts/emaki-update-boot', 'usr/bin/emaki-update-boot')
        entry.write_text(entry.read_text().replace('/usr/lib/emaki/boot', str(library)))
        result = subprocess.run([str(entry), '--help'], env=self.environment,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('accept', result.stdout)
        self.assert_no_bytecode()

    def test_cold_package_hooks_import_without_writing_bytecode(self):
        for module in ('emaki_boot_defaults.py', 'emaki_initramfs.py'):
            self.stage(f'upkeep/{module}', f'usr/share/libalpm/scripts/{module}')
        for name in ('emaki-initramfs-refresh', 'emaki-system-migrate'):
            with self.subTest(name=name):
                entry = self.stage(f'upkeep/{name}', f'usr/share/libalpm/scripts/{name}')
                # An unknown refresh operation exits after imports, before host access.
                # Release imports both modules and operates only on the scratch root.
                argv = (['--invalid-operation'] if name == 'emaki-initramfs-refresh'
                        else [str(self.root / 'target'), '--release'])
                result = subprocess.run([str(entry), *argv], env=self.environment,
                                        text=True, capture_output=True)
                expected = 1 if name == 'emaki-initramfs-refresh' else 0
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected:
                    self.assertIn('Unknown initramfs refresh operation.', result.stderr)
                self.assert_no_bytecode()


if __name__ == '__main__':
    unittest.main()
