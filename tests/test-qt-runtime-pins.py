#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt minor-series dependencies and update resolution in scratch roots."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HELPERS = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))
ACTIVE = ROOT / 'packaging/quickshell-emaki/PKGBUILD'
# packaging/activate-qt612.py moves the active recipe to the staged Qt 6.12 build.
RECIPES = ((ACTIVE, '6.12.0' if '0003-qt-6.12-moc-includes.patch' in ACTIVE.read_text() else '6.11.2'),
           (ROOT / 'packaging/quickshell-emaki/qt-6.12/PKGBUILD', '6.12.0'))


def staged_dependencies(recipe, version=None, qml=None, provides=False, after_build=''):
    version = version or dict(RECIPES)[recipe]
    qml = qml or version
    with tempfile.TemporaryDirectory(prefix='emaki-qt-build-') as directory:
        work = Path(directory)
        (work / 'quickshell').mkdir()
        (work / 'quickshell/LICENSE').write_text('fixture\n')
        return subprocess.run(['bash', '-eu', '-c', '''
            source "$1"
            cd "$2"
            pkgdir="$2/stage"
            core_version=$3 qml_version=$4 output_field=$5
            cmake() { mkdir -p build; }
            pkg-config() {
                if [[ $2 == Qt6Core ]]; then printf '%s\\n' "$core_version"; else printf '%s\\n' "$qml_version"; fi
            }
            build
            eval "$6"
            cd "$2"
            package
            [[ $(<"$pkgdir/usr/share/quickshell-emaki/qt-build-version") == "$core_version" ]]
            declare -n output="$output_field"
            printf '%s\\n' "${output[@]}"
        ''', 'qt-test', str(recipe), str(work), version, qml, 'provides' if provides else 'depends', after_build], text=True, capture_output=True)


class QtDependencyTests(unittest.TestCase):
    def test_packaged_dependencies_keep_minor_ranges(self):
        for recipe, minor in RECIPES:
            with self.subTest(recipe=recipe):
                result = staged_dependencies(recipe)
                self.assertEqual(result.returncode, 0, result.stderr)
                dependencies = result.stdout.splitlines()
                self.assertEqual(dependencies, HELPERS['metadata'](recipe)['depends'])
                upper = '6.' + str(int(minor.split('.')[1]) + 1)
                for component in ('qt6-base', 'qt6-declarative'):
                    self.assertIn(component + '>=' + minor, dependencies)
                    self.assertIn(component + '<' + upper, dependencies)
                self.assertFalse(any(dep.startswith('qt6-wayland') for dep in dependencies))
                # The Qt 6.12 build also bounds SVG to its minor series (DECISIONS 2026-10-07).
                if minor == '6.11.2':
                    self.assertIn('qt6-svg', dependencies)
                else:
                    self.assertIn('qt6-svg>=' + minor, dependencies)
                    self.assertIn('qt6-svg<' + upper, dependencies)
                self.assertIn('libpipewire-0.3.so', dependencies)

    def test_build_metadata_tracks_validated_qt_release(self):
        for recipe, version in RECIPES:
            with self.subTest(recipe=recipe):
                result = staged_dependencies(recipe, provides=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('emaki-quickshell-qt-build=' + version, result.stdout.splitlines())
                for core, qml in ((version, '6.99.0'), ('6.11.3', '6.11.3'), ('6.11', '6.11')):
                    result = staged_dependencies(recipe, core, qml)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Qt build versions must agree and match both dependency lower bounds.', result.stderr)

    def test_package_requires_original_build_capture(self):
        for mutation in ('rm build/emaki-qt-build-version',
                         "printf '6.99.0\\n' > build/emaki-qt-build-version",
                         'pkg-config() { return 1; }',
                         'core_version=6.99.0 qml_version=6.99.0'):
            with self.subTest(mutation=mutation):
                result = staged_dependencies(RECIPES[0][0], after_build=mutation)
                self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which('makepkg') and os.geteuid() != 0,
                         'makepkg --printsrcinfo requires a non-root build user')
    def test_srcinfo_lists_runtime_dependencies(self):
        for recipe, _ in RECIPES:
            with self.subTest(recipe=recipe):
                result = subprocess.run(['makepkg', '--printsrcinfo', '-p', str(recipe)],
                                        cwd=recipe.parent, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                dependencies = [line.strip().split(' = ', 1)[1] for line in result.stdout.splitlines()
                                if line.strip().startswith('depends = ')]
                self.assertEqual(dependencies, HELPERS['metadata'](recipe)['depends'])
                self.assertNotIn('', dependencies)

    def test_watcher_preserves_build_fences_without_running_package(self):
        watch = runpy.run_path(str(ROOT / 'packaging/arch-watch'))
        for recipe, minor in RECIPES:
            with self.subTest(recipe=recipe):
                fences, names = watch['recipe_metadata'](recipe, 'x86_64')
                values = {f.value for f in fences}
                self.assertEqual(names, {'quickshell-emaki'})
                for component in ('qt6-base', 'qt6-declarative'):
                    self.assertIn(component + '>=' + minor, values)
                self.assertIn('libpipewire-0.3.so', values)
        with tempfile.TemporaryDirectory(prefix='emaki-qt-discovery-') as directory:
            recipe = Path(directory) / 'PKGBUILD'
            recipe.write_text("""pkgname=fixture
pkgver=1
pkgrel=1
arch=(x86_64)
depends=('build-lib>=1')
package() {
    depends=('runtime-lib=2')
    touch PACKAGE_MUST_NOT_RUN
}
""")
            fences, _ = watch['recipe_metadata'](recipe, 'x86_64')
            self.assertEqual({f.value for f in fences}, {'build-lib>=1', 'runtime-lib=2'})
            self.assertFalse((recipe.parent / 'PACKAGE_MUST_NOT_RUN').exists())


@unittest.skipUnless(all(map(shutil.which, ('pacman', 'repo-add', 'bsdtar')))
                     and (os.geteuid() == 0 or shutil.which('fakeroot')),
                     'needs pacman, repo-add, bsdtar and fakeroot')
class QtResolutionTests(unittest.TestCase):
    pacman = HELPERS['PortalResolutionTests'].pacman
    installed = HELPERS['PortalResolutionTests'].installed

    def test_rebuild_rejects_older_qt_patch(self):
        recipe, version = RECIPES[0]
        dependencies = [value for value in HELPERS['metadata'](recipe)['depends']
                        if value.startswith(('qt6-base', 'qt6-declarative'))]
        for changed in ('qt6-base', 'qt6-declarative'):
            with self.subTest(component=changed), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                packages = [{'name': 'quickshell-emaki', 'version': '0.3.1-4', 'depends': dependencies}]
                packages.extend({'name': name, 'version': ('6.11.1' if name == changed else version) + '-1'}
                                for name in ('qt6-base', 'qt6-declarative'))
                repository = HELPERS['stub_repository'](work / 'repo', 'testing', packages)
                code, output = self.pacman(work / 'system', [('testing', repository)], '-Sy', 'quickshell-emaki')
                self.assertNotEqual(code, 0, output)
                self.assertIn(changed + '>=' + version, output)

    def test_patch_updates_pass_and_minor_updates_are_refused(self):
        for recipe, minor in RECIPES:
            for changed in ('qt6-base', 'qt6-declarative'):
                with self.subTest(recipe=recipe, component=changed):
                    with tempfile.TemporaryDirectory(prefix='emaki-qt-solver-') as directory:
                        work = Path(directory)
                        result = staged_dependencies(recipe)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        dependencies = [dep for dep in result.stdout.splitlines()
                                        if dep.startswith(('qt6-base', 'qt6-declarative'))]
                        original = [{'name': 'quickshell-emaki', 'version': '0.3.1-3',
                                     'depends': dependencies}]
                        original.extend({'name': name, 'version': minor + '-1'}
                                        for name in ('qt6-base', 'qt6-declarative'))

                        def repo(name, stubs):
                            return [('testing', HELPERS['stub_repository'](work / name, 'testing', stubs))]

                        system = work / 'system'
                        code, output = self.pacman(system, repo('original', original), '-Sy', 'quickshell-emaki')
                        self.assertEqual(code, 0, output)
                        repacked = [dict(stub, version=stub['version'][:-1] + '2')
                                    if stub['name'] == changed else stub for stub in original]
                        code, output = self.pacman(system, repo('repacked', repacked), '-Syyu')
                        self.assertEqual(code, 0, output)
                        newer = [dict(stub, version=minor.rsplit('.', 1)[0] + '.9-1')
                                 if stub['name'] == changed else stub for stub in original]
                        code, output = self.pacman(system, repo('newer', newer), '-Syyu')
                        self.assertEqual(code, 0, output)
                        self.assertEqual(self.installed(system)[changed], minor.rsplit('.', 1)[0] + '.9-1')
                        next_minor = '6.' + str(int(minor.split('.')[1]) + 1)
                        incompatible = [dict(stub, version=next_minor + '.0-1')
                                        if stub['name'] == changed else stub for stub in newer]
                        code, output = self.pacman(system, repo('minor', incompatible), '-Syyu')
                        self.assertNotEqual(code, 0, output)
                        self.assertIn(changed + '<' + next_minor, output)
                        self.assertEqual(self.installed(system)[changed], minor.rsplit('.', 1)[0] + '.9-1')


if __name__ == '__main__':
    unittest.main()
