#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve update dependency boundaries with pacman in disposable scratch roots."""
import os
from pathlib import Path
import re
import runpy
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HELPERS = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))
metadata = HELPERS['metadata']
stub_repository = HELPERS['stub_repository']


def recipe(name):
    info = metadata(ROOT / 'packaging' / name / 'PKGBUILD')
    return {'name': name, 'version': f'{info["pkgver"][0]}-{info["pkgrel"][0]}',
            'depends': info['depends']}


@unittest.skipUnless(all(map(shutil.which, ('pacman', 'repo-add', 'bsdtar')))
                     and (os.geteuid() == 0 or shutil.which('fakeroot')),
                     'needs pacman, repo-add, bsdtar and fakeroot')
class UpdatePinTests(unittest.TestCase):
    pacman = HELPERS['PortalResolutionTests'].pacman
    installed = HELPERS['PortalResolutionTests'].installed

    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='emaki-pins-')))
        self.system = self.work / 'system'
        self.serial = 0

    def repository(self, stubs):
        self.serial += 1
        return [('testing', stub_repository(self.work / str(self.serial), 'testing', stubs))]

    def install(self, stubs, package):
        code, output = self.pacman(self.system, self.repository(stubs), '-Sy', package)
        self.assertEqual(code, 0, output)

    def niri_stubs(self, version='26.04-1', broken_soname=None):
        fork = recipe('niri-emaki')
        # Represent every real fork dependency. Virtual sonames carry the ABI recorded
        # in its recipe; all other packages are empty dependency providers.
        providers = []
        for dep in fork['depends']:
            name = re.split(r'[<>=]', dep)[0]
            if name == 'niri':
                continue
            if '.so' in name:
                provides = dep if dep != broken_soname else dep.split('=')[0] + '=999-64'
                providers.append({'name': 'abi-' + str(len(providers)), 'version': '1-1',
                                  'provides': [provides]})
            else:
                providers.append({'name': name, 'version': '1-1'})
        return [fork, {'name': 'niri', 'version': version}, *providers]

    def test_new_stock_niri_upgrades_without_a_fork_rebuild(self):
        old = self.niri_stubs()
        self.install(old, 'niri-emaki')
        code, output = self.pacman(self.system, self.repository(self.niri_stubs('99.01-1')), '-Syyu')
        self.assertEqual(code, 0, output)
        installed = self.installed(self.system)
        self.assertEqual(installed['niri'], '99.01-1')
        self.assertEqual(installed['niri-emaki'], old[0]['version'])

    def test_old_exact_pins_migrate_in_one_full_upgrade(self):
        # Reconstruct the old exact-pin relationship, without claiming these stubs
        # reproduce the complete historical package closure or payload.
        for number, release in enumerate(('0.1.0-1', '0.1.1-1', '0.1.2-1', '0.2.0-1')):
            with self.subTest(release=release):
                self.system = self.work / f'legacy-system-{number}'
                current = self.niri_stubs('99.01-1')
                current.extend(stub for stub in self.marker_stubs() if stub['name'] != 'niri-emaki')
                old = []
                for stub in current:
                    stub = dict(stub)
                    if stub['name'] == 'niri':
                        stub['version'] = '26.04-1'
                    elif stub['name'] == 'niri-emaki':
                        stub['version'] = '26.04-8'
                        stub['depends'] = ['niri=26.04' if dep.startswith('niri>=') else dep
                                           for dep in stub['depends']]
                    elif stub['name'].startswith('emaki'):
                        stub['version'] = release
                    old.append(stub)
                marker = next(stub for stub in old if stub['name'] == 'emaki')
                names = {re.split(r'[<>=]', dep)[0] for dep in marker['depends']}
                marker['depends'] = [f'{stub["name"]}={stub["version"]}' for stub in old
                                     if stub['name'] in names]
                self.install(old, 'emaki')
                code, output = self.pacman(self.system, self.repository(current), '-Syyu')
                self.assertEqual(code, 0, output)
                installed = self.installed(self.system)
                self.assertEqual(installed['niri'], '99.01-1')
                self.assertEqual(installed['emaki'], recipe('emaki')['version'])
                self.assertEqual(installed['niri-emaki'], recipe('niri-emaki')['version'])

    def test_older_stock_niri_is_rejected(self):
        code, output = self.pacman(self.system, self.repository(self.niri_stubs('25.01-1')),
                                   '-Sy', 'niri-emaki')
        self.assertNotEqual(code, 0, output)
        self.assertIn('niri>=26.04', output)

    def test_each_soname_remains_a_hard_upgrade_boundary(self):
        sonames = [dep for dep in recipe('niri-emaki')['depends'] if '.so=' in dep]
        self.assertTrue(sonames)
        for number, soname in enumerate(sonames):
            with self.subTest(soname=soname):
                self.system = self.work / f'abi-system-{number}'
                self.install(self.niri_stubs(), 'niri-emaki')
                newer = self.niri_stubs('99.01-1', broken_soname=soname)
                for stub in newer:
                    if stub['name'].startswith('abi-'):
                        stub['version'] = '2-1'
                code, output = self.pacman(self.system, self.repository(newer), '-Syyu')
                self.assertNotEqual(code, 0, output)
                self.assertIn(soname, output)
                self.assertEqual(self.installed(self.system)['niri'], '26.04-1')

    def marker_stubs(self):
        marker = recipe('emaki')
        stubs = [marker]
        for dependency in marker['depends']:
            name, version = re.split(r'>=|=', dependency)
            stubs.append({'name': name, 'version': version})
        return stubs

    def test_trust_and_routing_can_upgrade_independently(self):
        for number, name in enumerate(('emaki-keyring', 'emaki-mirrorlist')):
            with self.subTest(package=name):
                self.system = self.work / f'marker-system-{number}'
                old = self.marker_stubs()
                self.install(old, 'emaki')
                newer = [dict(stub, version='99.0-1') if stub['name'] == name else stub
                         for stub in old]
                code, output = self.pacman(self.system, self.repository(newer), '-Syyu')
                self.assertEqual(code, 0, output)
                installed = self.installed(self.system)
                self.assertEqual(installed[name], '99.0-1')
                self.assertEqual(installed['emaki'], old[0]['version'])

    def test_trust_and_routing_minimum_versions_are_enforced(self):
        for number, name in enumerate(('emaki-keyring', 'emaki-mirrorlist')):
            with self.subTest(package=name):
                self.system = self.work / f'minimum-system-{number}'
                stubs = [dict(stub, version='0.1.0-1') if stub['name'] == name else stub
                         for stub in self.marker_stubs()]
                code, output = self.pacman(self.system, self.repository(stubs), '-Sy', 'emaki')
                self.assertNotEqual(code, 0, output)
                self.assertIn(name + '>=0.4.1-1', output)


if __name__ == '__main__':
    unittest.main()
