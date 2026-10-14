#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The From Emaki shelf: one data file, catalog rows, NVIDIA only with an NVIDIA display device."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine

SHIPPED = Path(engine.__file__).with_name('emaki-shelf.json')
KEY = dict(package='emaki-sudo-key', title='Sudo key', summary='A key.')
NVIDIA = dict(package='emaki-nvidia', title='NVIDIA support', summary='Settings for NVIDIA.', when='nvidia-gpu')
GOOD = dict(version=1, items=[KEY, NVIDIA])


def package(name, repository='emaki'):
    return dict(repository=repository, package=name, version='0.8.0-1', summary='Packaged summary',
                licence='GPL', downloadSize=10, installedSize=20)


def sysfs(root, *devices):
    """A fake sysfs tree: one PCI device directory per (vendor, class)."""
    for number, (vendor, pci_class) in enumerate(devices):
        device = Path(root) / 'bus' / 'pci' / 'devices' / f'0000:0{number}:00.0'
        device.mkdir(parents=True)
        (device / 'vendor').write_text(vendor + '\n')
        (device / 'class').write_text(pci_class + '\n')
        (device / 'device').write_text('0x2200\n')
    return Path(root)


class ShelfFileTests(unittest.TestCase):
    def write(self, value):
        directory = tempfile.TemporaryDirectory(prefix='programs-shelf-')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'emaki-shelf.json'
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def test_a_valid_file_gives_its_items_in_order(self):
        self.assertEqual(engine.shelf_items([self.write(GOOD)]), [KEY, NVIDIA])

    def test_a_missing_or_invalid_file_gives_an_empty_shelf(self):
        broken = [
            '{', '[]', dict(GOOD, version=2), dict(GOOD, extra=1), dict(version=1),
            dict(GOOD, items=[dict(KEY, extra=1)]),
            dict(GOOD, items=[dict(KEY, package='--root')]),
            dict(GOOD, items=[dict(KEY, package='Emaki Key')]),
            dict(GOOD, items=[dict(KEY, title='x' * 65)]),
            dict(GOOD, items=[dict(KEY, summary='x' * 161)]),
            dict(GOOD, items=[dict(KEY, summary='')]),
            dict(GOOD, items=[dict(KEY, title='Sudo‮key')]),
            dict(GOOD, items=[dict(KEY, when=7)]),
            dict(GOOD, items=[dict(KEY, when='../x')]),
            dict(GOOD, items=[KEY, KEY]),
            dict(GOOD, items=[dict(KEY, package='p%d' % number) for number in range(9)]),
        ]
        for value in broken:
            with self.subTest(value=str(value)[:70]):
                # An invalid first file stops the search: the installed copy is not a fallback for it.
                self.assertEqual(engine.shelf_items([self.write(value), SHIPPED]), [])
        missing = Path(tempfile.gettempdir()) / 'no-such-dir-for-emaki-shelf' / 'emaki-shelf.json'
        self.assertEqual(engine.shelf_items([missing]), [])
        self.assertEqual(engine.shelf_items([missing, self.write(GOOD)]), [KEY, NVIDIA])

    def test_the_shipped_file_validates_and_every_item_is_built_here(self):
        items = engine.shelf_items([SHIPPED])
        self.assertEqual([item['package'] for item in items], ['emaki-sudo-key', 'snapguard', 'emaki-nvidia'])
        self.assertEqual(items[2]['when'], 'nvidia-gpu')
        self.assertNotIn(('[PLACE' + 'HOLDER]'), SHIPPED.read_text())
        packaging = Path(engine.__file__).resolve().parents[1] / 'packaging'
        order = (packaging / 'build.sh').read_text()
        for item in items:
            # Each shelf package has a recipe and is built with the Emaki repository.
            self.assertTrue((packaging / item['package'] / 'PKGBUILD').is_file(), item['package'])
            self.assertRegex(order, r'(?s)\nORDER=\([^)]*\s' + item['package'] + r'\s', item['package'])
            # A person who installed a shelf package may remove it again.
            self.assertTrue(engine.removal.assess(item['package'], 0, item['package'] + '\n', '')['ok'], item['package'])
        # The window names no shelf package; the data file does.
        ui = Path(engine.__file__).with_name('ui')
        for source in sorted(ui.glob('*.qml')) + sorted(ui.glob('*.js')):
            for item in items:
                self.assertNotIn('"' + item['package'] + '"', source.read_text(), source.name)


class NvidiaTests(unittest.TestCase):
    def tree(self, *devices):
        directory = tempfile.TemporaryDirectory(prefix='programs-sysfs-')
        self.addCleanup(directory.cleanup)
        return sysfs(directory.name, *devices)

    def test_only_an_nvidia_display_controller_counts(self):
        self.assertTrue(engine.nvidia_gpu(self.tree(('0x10de', '0x030000'))))
        self.assertTrue(engine.nvidia_gpu(self.tree(('0x8086', '0x030000'), ('0x10de', '0x030200'))))
        # The HDMI audio function of an NVIDIA card is not a graphics device.
        self.assertFalse(engine.nvidia_gpu(self.tree(('0x10de', '0x040300'))))
        self.assertFalse(engine.nvidia_gpu(self.tree(('0x8086', '0x030000'), ('0x1002', '0x038000'))))
        self.assertFalse(engine.nvidia_gpu(self.tree(('0x10de', 'garbage'))))
        self.assertFalse(engine.nvidia_gpu(self.tree()))
        self.assertFalse(engine.nvidia_gpu(Path(tempfile.gettempdir()) / 'no-such-sysfs-for-emaki'))


class ShelfRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-shelf-engine-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.legacy = SimpleNamespace(pacman=Mock(return_value=SimpleNamespace(stdout='')),
                                      preview=Mock(), remove=Mock())
        self.engine = engine.Engine(self.legacy, self.root / 'cache')
        self.inventory = self.enterContext(patch.object(engine.arch, 'inventory', return_value=(
            dict({'emaki/emaki-sudo-key': package('emaki-sudo-key')}, **{
                'emaki/' + name: package(name) for name in engine.arch.graphics_policy().package_selection(
                    ({'vendor': 0x10de, 'device': 0x2200},))}),
            {'emaki-sudo-key': '0.7.0-1'})))
        self.enterContext(patch.object(engine.appstream, 'components', return_value=([], [])))
        self.enterContext(patch.object(engine.flatpak, 'discover', return_value=([], [])))
        self.enterContext(patch.object(engine.receipts, 'DIRECTORY', self.root / 'installs'))
        self.shelf_file = self.root / 'emaki-shelf.json'
        self.shelf_file.write_text(json.dumps(GOOD))
        self.enterContext(patch.object(engine, 'storefront_paths',
                                       side_effect=lambda name=engine.STOREFRONT: [
                                           self.shelf_file if name == engine.SHELF else SHIPPED.with_name(name)]))

    def gpu(self, *devices):
        target = self.root / ('sysfs-%d' % len(list(self.root.glob('sysfs-*'))))
        return patch.object(engine, 'SYSFS', sysfs(target, *devices))

    def test_rows_come_from_the_catalog_in_file_order_with_the_shelf_texts(self):
        with self.gpu(('0x10de', '0x030000')):
            rows = self.engine.catalog()['shelf']
        self.assertEqual([row['key'] for row in rows], ['package:emaki/emaki-sudo-key', 'package:emaki/emaki-nvidia'])
        key, nvidia = rows
        self.assertEqual((key['name'], key['summary'], key['shelf']), ('Sudo key', 'A key.', True))
        self.assertEqual(nvidia['name'], 'NVIDIA support')
        # Installed state, actions and the install target are the catalog row's.
        self.assertEqual((key['installed'], key['installedVersion'], key['actions']), (True, '0.7.0-1', ['remove']))
        self.assertEqual((nvidia['installed'], nvidia['actions'], nvidia['target']), (False, ['install'], 'emaki/emaki-nvidia'))
        self.assertTrue(key['packageOnly'])
        # The shelf's texts do not leak into the catalog's own row.
        self.assertEqual(self.engine.details('package:emaki/emaki-sudo-key')['program']['name'], 'emaki-sudo-key')

    def test_installed_policy_with_missing_driver_packages_can_still_install(self):
        packages, installed = self.inventory.return_value
        installed['emaki-nvidia'] = '0.8.0-1'
        with self.gpu(('0x10de', '0x030000')):
            row = self.engine.catalog()['shelf'][-1]
        self.assertFalse(row['installed'])
        self.assertEqual(row['actions'], ['install'])

    def test_complete_nvidia_stack_is_installed(self):
        packages, installed = self.inventory.return_value
        installed.update({row['package']: row['version'] for row in packages.values()})
        with self.gpu(('0x10de', '0x030000')):
            row = self.engine.catalog()['shelf'][-1]
        self.assertTrue(row['installed'])
        self.assertEqual(row['actions'], [])

    def test_nvidia_receipt_never_offers_undo_in_shelf_details_or_plan(self):
        packages, installed = self.inventory.return_value
        added = {row['package']: row['version'] for row in packages.values()}
        installed.update(added)
        receipt = engine.receipts.record('arch', '', 'NVIDIA support', 'emaki-nvidia', {}, added)
        with self.gpu(('0x10de', '0x030000')):
            shelf = self.engine.catalog()['shelf'][-1]
        key = shelf['key']
        for row in (shelf, self.engine.details(key)['program']):
            self.assertNotIn('undo', row['actions'])
            self.assertNotIn('undo', row)
            self.assertIn('restore point', row['description'])
            self.assertIn('open Updates', row['description'])
        self.assertEqual(self.engine.plan('undo', key)['code'], 'nothing-to-undo')
        for upgraded in ({}, {'nvidia-utils': ['1', '2']}):
            with self.subTest(upgraded=upgraded):
                self.assertEqual(engine.receipts.undo_plan(dict(receipt, upgraded=upgraded), self.legacy.pacman),
                                 dict(ok=False, code='restore-only'))

    def test_old_nvidia_or_missing_stack_is_hidden(self):
        with self.gpu(('0x10de', '0x030000')):
            for device in (engine.SYSFS / 'bus/pci/devices').iterdir():
                (device / 'device').write_text('0x1000')
            self.assertEqual([row['package'] for row in self.engine.catalog()['shelf']], ['emaki-sudo-key'])
        del self.inventory.return_value[0]['emaki/nvidia-utils']
        with self.gpu(('0x10de', '0x030000')):
            self.assertEqual([row['package'] for row in self.engine.catalog(refresh=True)['shelf']], ['emaki-sudo-key'])

    def test_nvidia_support_is_hidden_without_an_nvidia_display_device(self):
        for devices in [(), (('0x8086', '0x030000'),), (('0x8086', '0x030000'), ('0x10de', '0x040300'))]:
            with self.subTest(devices=devices), self.gpu(*devices):
                self.assertEqual([row['package'] for row in self.engine.catalog()['shelf']], ['emaki-sudo-key'])

    def test_an_unknown_condition_hides_its_item(self):
        self.shelf_file.write_text(json.dumps(dict(GOOD, items=[dict(KEY, when='quantum-gpu'), NVIDIA])))
        with self.gpu(('0x10de', '0x030000')):
            self.assertEqual([row['package'] for row in self.engine.catalog()['shelf']], ['emaki-nvidia'])

    def test_a_package_no_repository_offers_is_left_out(self):
        self.inventory.return_value = ({key: value for key, value in self.inventory.return_value[0].items()
                                        if value['package'] != 'emaki-sudo-key'}, {})
        self.shelf_file.write_text(json.dumps(dict(GOOD, items=[dict(KEY, package='snapguard'), NVIDIA, KEY])))
        with self.gpu(('0x10de', '0x030000')):
            self.assertEqual([row['package'] for row in self.engine.catalog()['shelf']], ['emaki-nvidia'])

    def test_shipped_snapguard_appears_installs_and_removes_through_the_store_path(self):
        self.shelf_file.write_text(SHIPPED.read_text())
        packages, installed = self.inventory.return_value
        packages['emaki/snapguard'] = package('snapguard')
        shipped = {item['package']: item for item in engine.shelf_items([SHIPPED])}['snapguard']
        with self.gpu():
            rows = self.engine.catalog()['shelf']
        self.assertEqual([row['package'] for row in rows], ['emaki-sudo-key', 'snapguard'])
        guard = rows[1]
        self.assertEqual((guard['name'], guard['summary'], guard['shelf'], guard['actions'], guard['target']),
                         (shipped['title'], shipped['summary'], True, ['install'], 'emaki/snapguard'))
        native = dict(ok=True, code='ready', action='install', kind='arch', target='emaki/snapguard',
                      packages=[], token='c' * 64)
        with patch.object(engine.arch, 'install_plan', return_value=native) as install_plan:
            plan = self.engine.plan('install', guard['key'])
        install_plan.assert_called_once_with('emaki/snapguard', cache=self.engine.cache, refresh=True)
        self.assertEqual((plan['ok'], plan['action'], plan['key']), (True, 'install', 'package:emaki/snapguard'))
        installed['snapguard'] = '0.3.0-1'
        with self.gpu():
            guard = self.engine.catalog(refresh=True)['shelf'][1]
        self.assertEqual((guard['installed'], guard['installedVersion'], guard['actions']), (True, '0.3.0-1', ['remove']))
        # Removal is the removal backend's whole preview; snapguard is not a protected component.
        self.legacy.preview.return_value = engine.removal.assess('snapguard', 0, 'snapguard\n', '')
        plan = self.engine.plan('remove', guard['key'])
        self.legacy.preview.assert_called_once_with('snapguard')
        self.assertEqual((plan['ok'], plan['transaction']['packages']), (True, ['snapguard']))

    def test_the_first_configured_repository_wins(self):
        self.inventory.return_value = ({'emaki-testing/emaki-sudo-key': package('emaki-sudo-key', 'emaki-testing'),
                                        'emaki/emaki-sudo-key': package('emaki-sudo-key')}, {})
        with self.gpu():
            self.assertEqual([row['target'] for row in self.engine.catalog()['shelf']], ['emaki-testing/emaki-sudo-key'])

    def test_a_broken_shelf_file_leaves_the_catalog_working(self):
        self.shelf_file.write_text('{')
        with self.gpu(('0x10de', '0x030000')):
            answer = self.engine.catalog(scope='packages')
        self.assertTrue(answer['ok'])
        self.assertEqual(answer['shelf'], [])
        self.assertEqual(answer['total'], len(self.inventory.return_value[0]))

    def test_a_shelf_row_installs_through_the_one_target_repository_path(self):
        native = dict(ok=True, code='ready', action='install', kind='arch', target='emaki/emaki-nvidia',
                      packages=[], token='b' * 64)
        with self.gpu(('0x10de', '0x030000')):
            row = self.engine.catalog()['shelf'][-1]
        with patch.object(engine.arch, 'install_plan', return_value=native) as install_plan:
            plan = self.engine.plan('install', row['key'])
        install_plan.assert_called_once_with('emaki/emaki-nvidia', cache=self.engine.cache, refresh=True)
        self.assertEqual((plan['ok'], plan['kind'], plan['key']), (True, 'arch', 'package:emaki/emaki-nvidia'))


if __name__ == '__main__':
    unittest.main()
