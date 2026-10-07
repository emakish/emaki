# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Firmware and PCI fixtures never inspect or change the host's hardware."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from emaki_installer.hardware import disk_behind_vmd, secure_boot, vmd_present, VMD_MESSAGE
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import mkinitcpio_machine_config
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, config, inventory


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def variable(self, data):
        path = self.root / 'sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_secure_boot_attributes_are_not_the_value(self):
        self.assertIs(secure_boot(self.root), False)
        for state in (0, 1):
            self.variable(bytes([7, 0, 0, 0, state]))
            self.assertIs(secure_boot(self.root), bool(state))
        for data in (b'', b'\x01', bytes([7, 0, 0, 0, 2])):
            self.variable(data)
            self.assertIsNone(secure_boot(self.root))
        self.variable(b'').unlink()
        self.assertIs(secure_boot(self.root), False)

    def test_uefi_without_secure_boot_variable_allows_installation(self):
        (self.root / 'sys/firmware/efi/efivars').mkdir(parents=True)
        state = secure_boot(self.root)
        self.assertIs(state, False)
        data = inventory()
        data['hardware'] = {'secure_boot': state}
        self.assertEqual(make_plan(config(), data).summary, make_plan(config(), inventory()).summary)

    def test_missing_efivars_or_read_errors_refuse_installation(self):
        (self.root / 'sys/firmware/efi').mkdir(parents=True)
        self.assertIsNone(secure_boot(self.root))
        self.variable(bytes([7, 0, 0, 0, 0]))
        for error in (PermissionError('unreadable'), OSError('read failed')):
            with self.subTest(error=error), patch.object(Path, 'read_bytes', side_effect=error):
                state = secure_boot(self.root)
            self.assertIsNone(state)
            data = inventory()
            data['hardware'] = {'secure_boot': state}
            with self.assertRaises(InstallError) as raised:
                make_plan(config(), data)
            self.assertEqual(raised.exception.code, Code.SECURE_BOOT)

    def test_vmd_is_selected_disk_ancestry_not_loaded_module(self):
        sys = self.root / 'sys'
        controller = sys / 'devices/pci0000:00/0000:00:0e.0'
        device = controller / 'pci10000:e0/10000:e0:06.0/nvme/nvme0/nvme0n1'
        device.mkdir(parents=True)
        driver = sys / 'bus/pci/drivers/vmd'
        driver.mkdir(parents=True)
        (controller / 'driver').symlink_to(driver)
        links = sys / 'class/block'
        links.mkdir(parents=True)
        (links / 'nvme0n1').symlink_to(device)
        (sys / 'module/vmd').mkdir(parents=True)
        (links / 'sda').mkdir()
        self.assertTrue(vmd_present(self.root))
        self.assertTrue(disk_behind_vmd('/dev/nvme0n1', self.root))
        self.assertFalse(disk_behind_vmd('/dev/sda', self.root))
        self.assertFalse(disk_behind_vmd('/dev/missing', self.root))
        self.assertIn('MODULES=(vmd)', mkinitcpio_machine_config(True, vmd=True))
        self.assertIn('MODULES=()', mkinitcpio_machine_config(True))

    def test_unbound_vmd_tool_output(self):
        self.assertTrue(vmd_present(self.root, '00:0e.0 RAID bus controller: Intel Corporation Volume Management Device'))
        self.assertFalse(vmd_present(self.root, '00:02.0 VGA compatible controller: Intel Corporation Graphics'))

    def test_enabled_or_unreadable_secure_boot_refuses_plan(self):
        for state in (True, None):
            data = inventory()
            data['hardware'] = {'secure_boot': state}
            with self.assertRaises(InstallError) as raised:
                make_plan(config(), data)
            self.assertEqual(raised.exception.code, Code.SECURE_BOOT)
            self.assertIn('turn Secure Boot off', raised.exception.message)
        data['hardware'] = {'secure_boot': False}
        self.assertEqual(make_plan(config(), data).summary, make_plan(config(), inventory()).summary)

    def test_no_disk_vmd_message(self):
        data = inventory()
        data.update(disks=[], hardware={'disk_notice': VMD_MESSAGE})
        with self.assertRaisesRegex(InstallError, 'AHCI'):
            make_plan(config(), data)

    def test_secure_boot_rechecked_before_worker_side_effects(self):
        live = FakeInventory()
        plan = make_plan(config(), live.probe())
        live.data['hardware'] = {'secure_boot': True}
        events = []
        runner = RecordingRunner()
        worker = Worker(None, live, Redactor(), lambda kind, **kw: events.append((kind, kw)),
                        lambda line: None, target=self.root, runner_factory=lambda *a: runner)
        with patch('emaki_installer.worker.preflight_repo') as packages:
            worker.run(plan, threading.Event())
        packages.assert_not_called()
        self.assertFalse(worker.disk_changed)
        self.assertEqual(runner.commands, [])
        self.assertEqual(events[-1][1]['code'], 'secure_boot')

    def test_vmd_warning_only_for_selected_disk(self):
        data = inventory()
        normal = make_plan(config(), data)
        data['disks'][0]['_vmd'] = True
        special = make_plan(config(), data)
        self.assertEqual(special.summary, normal.summary)
        self.assertEqual(special.warnings[-1]['code'], 'vmd')


if __name__ == '__main__':
    unittest.main()
