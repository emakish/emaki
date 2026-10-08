# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached sysfs fixtures and unchanged non-Nvidia installation contracts."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from emaki_installer import graphics
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.inventory import Inventory
from emaki_installer.worker import Worker, preflight_repo, software_packages
from support import FakeInventory, config, inventory

class ConsoleFontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = SimpleNamespace(graphics_packages=(), config={})

    def connector(self, name, modes, status='connected'):
        path = self.root / 'class/drm' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'status').write_text(status)
        if modes is not None:
            (path / 'modes').write_text(modes)

    def font(self):
        return graphics.console_font(self.plan, self.root)

    def test_native_mode_threshold_and_integer_boundary(self):
        for mode, expected in [('1024x768', 'ter-124b'), ('1280x800', 'ter-124b'),
                               ('1920x1080', 'ter-124b'), ('2560x1600', None),
                               ('1752x1600', 'ter-124b'), ('1760x1600', None)]:
            with self.subTest(mode=mode):
                self.connector('card0-eDP-1', mode + '\n1024x768\n')
                self.assertEqual(self.font(), expected)

    def test_internal_panel_precedes_external_display(self):
        self.connector('card0-HDMI-A-1', '3840x2160\n')
        self.connector('card0-eDP-1', '1280x800\n')
        self.assertEqual(self.font(), 'ter-124b')
        self.connector('card1-LVDS-1', '2560x1600\n')
        self.assertIsNone(self.font())

    def test_external_displays_must_all_be_known_and_below_threshold(self):
        self.connector('card0-DP-1', '1920x1080\n')
        self.connector('card0-HDMI-A-1', '1280x800\n')
        self.assertEqual(self.font(), 'ter-124b')
        self.connector('card0-HDMI-A-1', '3840x2160\n')
        self.assertIsNone(self.font())
        self.connector('card0-HDMI-A-1', '3840x2160\n', 'disconnected')
        self.assertEqual(self.font(), 'ter-124b')

    def test_unknown_geometry_keeps_kernel_default(self):
        self.assertIsNone(self.font())
        for mode in (None, '', 'invalid\n', '0x800\n'):
            with self.subTest(mode=mode):
                self.connector('card0-eDP-1', mode)
                self.assertIsNone(self.font())

    def test_firmware_framebuffer_modes_are_not_panel_geometry(self):
        self.connector('card0-Unknown-1', '1024x768\n')
        self.assertIsNone(self.font())
        self.connector('card0-Unknown-1', '1024x768\n', 'disconnected')
        self.connector('card0-HDMI-A-1', '1024x768\n')
        device = self.root / 'class/drm/card0/device'
        device.mkdir(parents=True)
        driver = device / 'driver'
        for name in ('simple-framebuffer', 'simpledrm', 'efi-framebuffer', 'vesa-framebuffer'):
            with self.subTest(driver=name):
                driver.symlink_to(self.root / 'bus/platform/drivers' / name)
                self.assertIsNone(self.font())
                driver.unlink()
        driver.symlink_to(self.root / 'bus/pci/drivers/i915')
        self.assertEqual(self.font(), graphics.CONSOLE_FONT)

    def test_nvidia_resume_does_not_guess_installed_gop_mode(self):
        self.connector('card0-eDP-1', '1280x800\n')
        self.plan.graphics_packages = ('emaki-nvidia',)
        self.assertEqual(self.font(), 'ter-124b')
        self.plan.config['hibernation'] = True
        self.assertIsNone(self.font())
        self.plan.graphics_packages = ()
        self.assertEqual(self.font(), 'ter-124b')


# Serialized protocol plan fields and ordered packages from the unmodified checkout
# 8afb2e864657f840075ec24daa7f3914aeb41e44, using the deterministic keyboard fixture below.
# Package hashes include the common terminus-font addition from 4daab6f; plan hashes stay fixed.
BASELINE = {
    'btrfs-minimal': ('135423acba4a58851f74b9fd2d52f138f120221d4e16bc7fcdbf8c1658d3d568',
                     'd317feee47e2119c760957ff19c3e2b82147aaf402e926e3808cb820561563df'),
    'btrfs-rich': ('12b4cb778eca3cead0b03d3efcaa6d066172a336e660a9c492ae2120f9138d9b',
                  '48037390883451e5dc6d3a7fcac5267e7c3ca92715698b02ee8b2dc20e8cc938'),
    'ext4-minimal': ('ab33a9debe3b1892c98ba04998d50486f2e229528f5bd8aee776ce8e77ad5e00',
                    'e1cc27f59e7545956bd3bc4504ba10b3afff9ca0d2ec6daba77ad6f517297fe6'),
    'ext4-rich': ('b861d7f51bcddb7f998325c9cf149c9dac7350dcc428a0705e294535e17e0033',
                 'a5a6791ed558c610ef8a7a4134cda446d33c88ade784e94cca2c4ce3cf6a9888'),
}


class GraphicsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def gpu(self, vendor, device, slot='0000:01:00.0', card=None, pci_class=0x030000):
        path = self.root / 'bus/pci/devices' / slot
        path.mkdir(parents=True)
        for name, value in (('vendor', vendor), ('device', device), ('class', pci_class)):
            (path / name).write_text(hex(value))
        # A fixture intentionally has no readable PCI config/resource nodes.
        if card:
            drm = self.root / 'class/drm' / card
            drm.mkdir(parents=True)
            (drm / 'device').symlink_to(path)
        return path

    def test_generations_with_and_without_drm(self):
        for name, device, expected in (('Kepler', 0x1180, 'nouveau'), ('Maxwell', 0x13c2, 'nouveau'),
                                       ('Pascal', 0x1b80, 'nouveau'), ('Volta', 0x1d81, 'nouveau'),
                                       ('Turing', 0x1e04, 'open'), ('Ada', 0x2684, 'open')):
            with self.subTest(name=name):
                path = self.gpu(0x10de, device)
                found = graphics.detect(self.root)
                self.assertEqual(len(found), 1)
                self.assertEqual(graphics.family(found[0]['device']), expected)
                selected = graphics.package_selection(found)
                if expected == 'open':
                    self.assertIn('emaki-nvidia', selected)
                    self.assertTrue(set(graphics.PREBUILT) <= set(selected))
                    self.assertNotIn('dkms', selected)
                else:
                    self.assertEqual(selected, ())
                for child in path.iterdir():
                    child.unlink()
                path.rmdir()

    def test_open_falls_back_for_both_kernels_if_either_prebuilt_absent(self):
        self.gpu(0x10de, 0x2684)
        for available in ((), ('nvidia-open',), ('nvidia-open-lts',)):
            selected = graphics.package_selection(graphics.detect(self.root), available)
            self.assertIn('nvidia-open-dkms', selected)
            self.assertTrue(set(graphics.HEADERS) <= set(selected))
            self.assertFalse(set(graphics.PREBUILT) & set(selected))

    def test_hybrid_and_desktop_do_not_change_install_selection(self):
        self.gpu(0x10de, 0x2684, card='card1')
        expected = graphics.package_selection(graphics.detect(self.root))
        self.gpu(0x8086, 0x9a49, slot='0000:00:02.0', card='card0')
        connector = self.root / 'class/drm/card0-eDP-1'
        connector.mkdir()
        (connector / 'status').write_text('connected\n')
        (connector / 'enabled').write_text('enabled\n')
        self.assertEqual(graphics.package_selection(graphics.detect(self.root)), expected)
        self.assertEqual([gpu['cards'] for gpu in graphics.detect(self.root)], [('card0',), ('card1',)])

    def test_non_display_nvidia_device_does_not_select_driver(self):
        self.gpu(0x10de, 0x2684, pci_class=0x040300)
        self.assertEqual(graphics.detect(self.root), ())

    def test_full_nvidia_inventory_does_not_run_a_pci_probe(self):
        self.gpu(0x10de, 0x2684)
        probe = Inventory(Mock(run=Mock(return_value='{"blockdevices":[]}')), sysfs_root=self.root)
        with patch.object(probe, '_boot_sources', return_value=(set(), True)), \
                patch.object(probe, '_stable_ids', return_value={}), \
                patch.object(probe, '_holders', return_value={}), \
                patch.object(probe, '_optional', side_effect=AssertionError('unexpected device probe')), \
                patch('emaki_installer.inventory._online', return_value=False):
            result = probe.probe()
        self.assertEqual(result['gpu'], 'Graphics controller (PCI 10de:2684)')
        self.assertEqual(result['_graphics']['devices'][0]['device'], 0x2684)

    def test_old_unknown_and_mixed_cards_keep_nouveau(self):
        for device in (0x0fd5, 0x0fe9, 0x0df4, 0x1040, 0x1200, 0x0600, 0x13c2,
                       0x1b80, 0x1d81, 0x1dff, None, -1, 0x10000):
            with self.subTest(device=device):
                self.assertEqual(graphics.family(device), 'nouveau')
                old = dict(vendor=0x10de, device=device)
                self.assertEqual(graphics.package_selection([old]), ())
                self.assertEqual(graphics.package_selection([old, dict(vendor=0x10de, device=0x2684)]), ())
        for device in (0x1e00, 0xffff):
            with self.subTest(device=device):
                self.assertEqual(graphics.family(device), 'open')

    def test_nvidia_plan_names_exact_selected_packages(self):
        inv = inventory()
        inv['_graphics'] = dict(devices=[dict(vendor=0x10de, device=0x2684)], available=list(graphics.PREBUILT))
        plan = make_plan(config(), inv)
        self.assertEqual(plan.graphics_packages, graphics.package_selection(inv['_graphics']['devices']))
        self.assertEqual(plan.summary[-1], 'Graphics driver packages (Nvidia): ' + ', '.join(plan.graphics_packages) + '.')
        self.assertEqual(software_packages(graphics=plan.graphics_packages), software_packages() + list(plan.graphics_packages))

    def test_offline_availability_comes_from_database_names(self):
        database = self.root / 'offline.db'
        with tarfile.open(database, 'w:gz') as archive:
            for name in graphics.PREBUILT:
                value = f'%NAME%\n{name}\n\n'.encode()
                member = tarfile.TarInfo(f'{name}-1-1/desc')
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        self.assertEqual(graphics.available_packages(database), frozenset(graphics.PREBUILT))
        database.write_bytes(b'broken database')
        self.assertEqual(graphics.available_packages(database), frozenset())

    def test_inventory_uses_discovered_offline_repository(self):
        self.gpu(0x10de, 0x2684)
        for mount in ('/run/archiso/bootmnt', '/run/archiso/copytoram', '/media/live'):
            with self.subTest(mount=mount):
                repo = self.root / mount.lstrip('/') / 'emaki/repo'
                repo.mkdir(parents=True)
                mountinfo = self.root / 'proc/self/mountinfo'
                mountinfo.parent.mkdir(parents=True, exist_ok=True)
                mountinfo.write_text(f'1 0 7:0 / {mount} ro - iso9660 /dev/loop0 ro\n')
                database = repo / 'emaki-offline.db'
                for names in (graphics.PREBUILT, graphics.PREBUILT[:1]):
                    with tarfile.open(database, 'w:gz') as archive:
                        for name in names:
                            value = f'%NAME%\n{name}\n\n'.encode()
                            member = tarfile.TarInfo(f'{name}-1-1/desc')
                            member.size = len(value)
                            archive.addfile(member, io.BytesIO(value))
                    inv = graphics.inventory(self.root, root=self.root)
                    self.assertEqual(inv['_graphics']['available'], sorted(names))
                    selected = graphics.planned_packages(inv)
                    self.assertEqual('nvidia-open-dkms' in selected, len(names) != 2)
                    self.assertEqual(set(graphics.HEADERS) <= set(selected), len(names) != 2)
                database.unlink()
                self.assertEqual(graphics.available_packages(root=self.root), frozenset())
                repo.rmdir()

    def test_online_inventory_selects_prebuilt_without_headers(self):
        self.gpu(0x10de, 0x2684)
        inv = graphics.inventory(self.root, root=self.root)
        selected = graphics.planned_packages(inv)
        self.assertTrue(set(graphics.PREBUILT) <= set(selected))
        self.assertFalse(set(('nvidia-open-dkms', 'dkms', *graphics.HEADERS)) & set(selected))

    def test_amd_intel_virtio_match_original_plan_and_packages_byte_for_byte(self):
        for vendor, device in ((0x1002, 0x744c), (0x8086, 0x9a49), (0x1af4, 0x1050)):
            path = self.gpu(vendor, device)
            extra = graphics.inventory(self.root, self.root / 'absent.db')
            self.assertEqual(extra, {})
            for fs in ('btrfs', 'ext4'):
                for software in ('minimal', 'rich'):
                    inv = {**inventory(), **extra}
                    with patch('emaki_installer.planner.xkb_rules', return_value='! layout\n  us English (US)\n  ru Russian\n'):
                        plan = make_plan(dict(config(fs=fs), software=software), inv)
                    value = json.dumps(dict(summary=plan.summary, errors=[], warnings=plan.warnings),
                                       sort_keys=True, separators=(',', ':')).encode()
                    packages = json.dumps(software_packages(software, btrfs=fs == 'btrfs', graphics=plan.graphics_packages),
                                          separators=(',', ':')).encode()
                    self.assertEqual((hashlib.sha256(value).hexdigest(), hashlib.sha256(packages).hexdigest()),
                                     BASELINE[fs + '-' + software])
            for child in path.iterdir():
                child.unlink()
            path.rmdir()

    def test_old_nvidia_and_hybrids_match_original_plan_and_package_hashes(self):
        for device in (0x0fd5, 0x0fe9, 0x0df4, 0x1180, 0x1040, 0x0600, 0x13c2, 0x1b80, 0x1d81, None):
            for companion in (None, dict(vendor=0x8086, device=0x9a49),
                              dict(vendor=0x10de, device=0x2684)):
                devices = [dict(vendor=0x10de, device=device)] + ([companion] if companion else [])
                for fs in ('btrfs', 'ext4'):
                    for software in ('minimal', 'rich'):
                        with self.subTest(device=device, companion=companion, fs=fs, software=software):
                            inv = inventory()
                            inv['_graphics'] = dict(devices=devices, available=list(graphics.PREBUILT))
                            with patch('emaki_installer.planner.xkb_rules', return_value='! layout\n  us English (US)\n  ru Russian\n'):
                                plan = make_plan(dict(config(fs=fs), software=software), inv)
                            self.assertEqual(plan.graphics_packages, ())
                            value = json.dumps(dict(summary=plan.summary, errors=[], warnings=plan.warnings),
                                               sort_keys=True, separators=(',', ':')).encode()
                            packages = json.dumps(software_packages(software, btrfs=fs == 'btrfs',
                                                                   graphics=plan.graphics_packages),
                                                  separators=(',', ':')).encode()
                            self.assertEqual((hashlib.sha256(value).hexdigest(), hashlib.sha256(packages).hexdigest()),
                                             BASELINE[fs + '-' + software])

    def test_preflight_and_copy_use_the_same_graphics_packages(self):
        inv = inventory()
        inv['_graphics'] = dict(devices=[dict(vendor=0x10de, device=0x2684)], available=[])
        plan = make_plan(config(), inv)
        archive = self.root / 'fixture.pkg.tar.zst'
        archive.write_bytes(b'fixture')
        Path(str(archive) + '.sig').write_bytes(b'fixture signature')
        requested = []

        def run(argv):
            if '-Sp' in argv:
                requested.extend(argv[argv.index('--logfile') + 2:])
                return archive.as_uri()
            return ''

        with patch('emaki_installer.worker.offline_config', return_value=self.root / 'pacman.conf'):
            preflight_repo(Mock(run=run), graphics=plan.graphics_packages)
        worker = SimpleNamespace(plan=plan, backend=Mock(), files=Mock(),
                                 api=SimpleNamespace(locale=SimpleNamespace(LocaleConfiguration=Mock())),
                                 populate_keyring=Mock(), runner=Mock(), target=self.root)
        Worker.copy_packages(worker)
        self.assertEqual(worker.backend.instance.pacman.strap.call_args.args[0], requested)
        worker.runner.chroot.assert_called_once_with(
            ['python3', '-I', '/usr/lib/emaki/nvidia/runtime.py', 'check'], self.root)

    def test_failed_module_build_stops_copy_and_completion(self):
        plan = make_plan(config(), inventory())
        plan.graphics_packages = ('emaki-nvidia', 'nvidia-open-dkms')
        worker = SimpleNamespace(plan=plan, backend=Mock(), files=Mock(), runner=Mock(), target=self.root,
                                 api=SimpleNamespace(locale=SimpleNamespace(LocaleConfiguration=Mock())),
                                 populate_keyring=Mock())
        worker.runner.chroot.side_effect = InstallError(Code.BOOT_VERIFY, 'missing module')
        for method in (Worker.copy_packages, Worker.finish):
            with self.assertRaisesRegex(InstallError, 'missing module'):
                method(worker)
        worker.populate_keyring.assert_not_called()
        worker.files.path.assert_not_called()

    def test_changed_graphics_refused_at_final_disk_boundary(self):
        inv = inventory()
        plan = make_plan(config(), inv)
        inv['_graphics'] = dict(devices=[dict(vendor=0x10de, device=0x2684)], available=[])
        worker = SimpleNamespace(plan=plan, inventory=FakeInventory(inv), backend=Mock(), mark_disk_changed=Mock())
        with self.assertRaisesRegex(InstallError, 'Graphics hardware or driver availability changed'):
            Worker.prepare_disk(worker)
        worker.mark_disk_changed.assert_not_called()
        worker.backend.prepare.assert_not_called()
