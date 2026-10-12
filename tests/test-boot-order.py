#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Firmware priority recovery preserves foreign loaders and ambiguous state."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('boot_order', str(ROOT / 'scripts/emaki-boot-order'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
UUID = '12345678-1234-4321-abcd-123456789abc'


def inventory(order='0001,0002,0003'):
    return (f'BootCurrent: 0002\nBootOrder: {order}\n'
            f'Boot0001* Windows Boot Manager\tHD(1,GPT,{UUID},0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n'
            f'Boot0002* Emaki\tHD(1,GPT,{UUID},0x800,0x32000)/File(\\EFI\\Emaki\\grubx64.efi)\n'
            'Boot0003* USB\tPciRoot(0x0)/USB(0x1,0x0)\n')


def inventory18(order='0000,0002'):
    # Captured efibootmgr 18 / efivar 39 line shapes, with fixture identities.
    return (f'BootCurrent: 0002\nTimeout: 0 seconds\nBootOrder: {order}\n'
            f'Boot0000* Windows Boot Manager\tHD(1,GPT,{UUID},0x800,0x64000)/\\EFI\\Microsoft\\Boot\\bootmgfw.efi57494e44\n'
            '      dp: 04 01 2a 00 / 7f ff 04 00\n'
            '    data: 57 49 4e 44\n'
            f'Boot0002* Emaki\tHD(1,GPT,{UUID},0x800,0x64000)/\\EFI\\Emaki\\grubx64.efi\n'
            '      dp: 04 01 2a 00 / 7f ff 04 00\n')


class Recovery(unittest.TestCase):
    def test_efibootmgr18_recovers_and_preserves_windows_optional_data(self):
        calls = []
        current = inventory18()
        def run(args):
            nonlocal current
            calls.append(args)
            if '--bootorder' in args:
                current = inventory18(args[-1])
                return ''
            return current
        self.assertEqual(module.recover(UUID, 'Dell Inc.', run, repair=True),
                         {'state': 'recovered', 'changed': True})
        self.assertEqual(calls, [['efibootmgr', '-v'], ['efibootmgr', '-v'],
                                ['efibootmgr', '--bootorder', '0002,0000'],
                                ['efibootmgr', '-v']])
        self.assertEqual(current.splitlines()[3:], inventory18().splitlines()[3:])

    def test_efibootmgr18_still_refuses_unverified_emaki_paths(self):
        for data in (
            inventory18().replace(UUID, '87654321-1234-4321-abcd-123456789abc'),
            inventory18().replace('Boot0002*', 'Boot0002 '),
            inventory18().replace('grubx64.efi', 'grubx64.efi57494e44'),
            inventory18().replace('\\Emaki\\grubx64.efi', '\\Microsoft\\Boot\\bootmgfw.efi'),
            inventory18().replace('/\\EFI\\Emaki\\grubx64.efi', '/File(\\EFI\\Emaki\\grubx64.efi'),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'unverified_emaki_entry'):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                module.recover(UUID, 'Dell Inc.', run, repair=True)

    def test_windows_first_preserves_other_entries(self):
        calls = []
        current = inventory()
        def run(args):
            nonlocal current
            calls.append(args)
            if '--bootorder' in args:
                current = inventory(args[-1])
                return ''
            return current
        result = module.recover(UUID, 'Dell Inc.', run, repair=True)
        self.assertEqual(result, {'state': 'recovered', 'changed': True})
        self.assertEqual([x for x in calls if '--bootorder' in x],
                         [['efibootmgr', '--bootorder', '0002,0001,0003']])
        self.assertEqual(len(calls), 4)

    def test_apple_performs_no_firmware_commands(self):
        def forbidden(args):
            self.fail(str(args))
        self.assertEqual(module.recover(UUID, 'Apple Inc.', forbidden, True)['state'], 'manual_recovery')

    def test_already_first_and_status_never_write(self):
        for data in (inventory(), inventory('0002,0001,0003')):
            def run(args):
                self.assertEqual(args, ['efibootmgr', '-v'])
                return data
            self.assertFalse(module.recover(UUID, 'LENOVO', run)['changed'])
        self.assertFalse(module.recover(UUID, 'LENOVO', run, True)['changed'])

    def test_malformed_ambiguous_or_foreign_identity_refuses(self):
        bad = [
            inventory().replace(UUID, '87654321-1234-4321-abcd-123456789abc'),
            inventory().replace('\\Emaki\\grubx64.efi', '\\Microsoft\\Boot\\bootmgfw.efi'),
            inventory().replace('Boot0002*', 'Boot0002 '),
            inventory().replace('BootOrder: 0001,0002,0003', 'BootOrder: 0001,0002,0002'),
            inventory().replace('BootOrder: 0001,0002,0003', 'BootOrder: 0001,0002,0004'),
            inventory() + inventory().splitlines()[3] + '\n',
            inventory().replace('Boot0003* USB\tPciRoot(0x0)/USB(0x1,0x0)',
                                inventory().splitlines()[3].replace('0002', '0003')),
            inventory().replace('/File(\\EFI\\Emaki\\grubx64.efi)', '/File(\\EFI\\Emaki\\grubx64.efi)garbage'),
            inventory().replace('\tHD', ' HD'),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                module.recover(UUID, 'Dell Inc.', run, True)

    def test_change_between_read_and_write_refuses(self):
        answers = iter((inventory(), inventory('0003,0001,0002')))
        def run(args):
            self.assertEqual(args, ['efibootmgr', '-v'])
            return next(answers)
        with self.assertRaisesRegex(ValueError, 'firmware_changed'):
            module.recover(UUID, 'Dell', run, True)

    def test_unknown_vendor_refuses(self):
        with self.assertRaisesRegex(ValueError, 'unknown_firmware_vendor'):
            module.recover(UUID, '', lambda args: self.fail(str(args)), True)

    def test_write_or_verification_failure_is_uncertain(self):
        for fail_write in (True, False):
            def run(args):
                if '--bootorder' in args and fail_write:
                    raise OSError('firmware refused')
                return inventory()
            self.assertEqual(module.recover(UUID, 'Dell', run, True),
                             {'state': 'recovery_uncertain', 'changed': None})


if __name__ == '__main__':
    unittest.main()
