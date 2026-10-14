#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Firmware priority recovery preserves foreign loaders and ambiguous state."""
import importlib.machinery
import importlib.util
from contextlib import ExitStack, nullcontext, redirect_stderr
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot, errors
sys.modules['emaki_boot.boot'] = boot
sys.modules['emaki_boot.errors'] = errors
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
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pending = Path(temporary.name) / 'pending'
        self.normal_boot = module.normal_boot
        for mock in (patch.object(module, 'PENDING', self.pending),
                     patch.object(module, 'normal_boot', return_value=True)):
            mock.start()
            self.addCleanup(mock.stop)

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
            inventory18().replace('grubx64.efi', 'grubx64.efi57494e44'),
            inventory18().replace('\\Emaki\\grubx64.efi', '\\Microsoft\\Boot\\bootmgfw.efi'),
            inventory18().replace('/\\EFI\\Emaki\\grubx64.efi', '/File(\\EFI\\Emaki\\grubx64.efi'),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'unverified_emaki_entry'):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                module.recover(UUID, 'Dell Inc.', run, repair=True)

    def test_stale_same_loader_entry_does_not_block_current_installation(self):
        stale = inventory().splitlines()[3].replace('Boot0002', 'Boot0004').replace(
            UUID, '87654321-1234-4321-abcd-123456789abc')
        for active in ('*', ' '):
            listing = inventory('0001,0004,0002,0003') + stale.replace('0004*', '0004' + active) + '\n'
            self.assertEqual(module.proposed_order(listing, UUID),
                             (['0001', '0004', '0002', '0003'], ['0002', '0001', '0004', '0003']))
        with self.assertRaisesRegex(ValueError, 'ambiguous_boot_entries'):
            module.proposed_order(inventory().replace(UUID, '87654321-1234-4321-abcd-123456789abc'), UUID)

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

    def test_apple_only_reads_firmware(self):
        def read(args):
            self.assertEqual(args, ['efibootmgr', '-v'])
            return inventory()
        self.assertEqual(module.recover(UUID, 'Apple Inc.', read, True)['state'], 'manual_recovery')

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
            inventory().replace('BootOrder: 0001,0002,0003', 'BootOrder: 0001,0002,0002'),
            inventory().replace('BootOrder: 0001,0002,0003', 'BootOrder: 0001,0002,0004'),
            inventory() + inventory().splitlines()[3] + '\n',
            inventory().replace('/File(\\EFI\\Emaki\\grubx64.efi)', '/File(\\EFI\\Emaki\\grubx64.efi)garbage'),
            inventory().replace('\tHD', ' HD'),
            # Hardware nodes may precede the partition, never follow or replace it.
            inventory().replace('\tHD(', '\tPciRoot(0x0)/HD(1,GPT,' + UUID + ',0x800,0x32000)/HD('),
            inventory().replace('\tHD(', '\tPciRoot(0x0)/File(\\x)/HD('),
            inventory().replace('/File(\\EFI\\Emaki\\grubx64.efi)',
                                '/USB(0x1,0x0)/File(\\EFI\\Emaki\\grubx64.efi)'),
            # Only the earliest verified Emaki entry in BootOrder may move; none in order refuses.
            inventory('0001,0003'),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                module.recover(UUID, 'Dell Inc.', run, True)

    def test_firmware_expanded_path_and_duplicate_entries(self):
        expanded = inventory().replace(
            f'Boot0002* Emaki\tHD(', 'Boot0002* Emaki\tPciRoot(0x0)/Pci(0x1d,0x0)/Pci(0x0,0x0)/'
            'NVMe(0x1,00-00-00-00-00-00-00-00)/HD(')
        self.assertEqual(module.proposed_order(expanded, UUID)[1], ['0002', '0001', '0003'])
        emaki = inventory().splitlines()[3]
        duplicate = inventory('0001,0003,0002').replace(
            'Boot0003* USB\tPciRoot(0x0)/USB(0x1,0x0)', emaki.replace('Boot0002', 'Boot0003'))
        self.assertEqual(module.proposed_order(duplicate, UUID),
                         (['0001', '0003', '0002'], ['0003', '0001', '0002']))
        # An already-first duplicate means nothing is written.
        first = duplicate.replace('BootOrder: 0001,0003,0002', 'BootOrder: 0002,0001,0003')
        old, new = module.proposed_order(first, UUID)
        self.assertEqual(old, new)

    def test_ovmf_capture_from_vm_install(self):
        # efibootmgr 18 output captured verbatim in the alongside VM run (OVMF, BootOrder
        # after the installer's grub-install, then with Windows Boot Manager moved first).
        fixture = ROOT / 'tests/fixtures/efibootmgr-18-ovmf.txt'
        text = fixture.read_text()
        partuuid = (ROOT / 'tests/fixtures/efibootmgr-18-ovmf.partuuid').read_text().strip()
        old, new = module.proposed_order(text, partuuid)
        self.assertNotEqual(old, new)
        self.assertEqual(sorted(old), sorted(new))
        self.assertIn('Emaki', [line.split('\t')[0].split(' ', 1)[1].strip()
                                for line in text.splitlines() if line.startswith('Boot' + new[0])])
        calls = []
        current = text

        def run(args):
            nonlocal current
            calls.append(args)
            if '--bootorder' in args:
                current = current.replace('BootOrder: ' + ','.join(old), 'BootOrder: ' + args[-1])
                return ''
            return current
        self.assertEqual(module.recover(partuuid, 'QEMU', run, repair=True),
                         {'state': 'recovered', 'changed': True})
        self.assertEqual(calls[2], ['efibootmgr', '--bootorder', ','.join(new)])

    def test_change_between_read_and_write_refuses(self):
        answers = iter((inventory(), inventory('0003,0001,0002')))
        def run(args):
            self.assertEqual(args, ['efibootmgr', '-v'])
            return next(answers)
        with self.assertRaisesRegex(ValueError, 'firmware_changed'):
            module.recover(UUID, 'Dell', run, True)

    def test_sanitized_firmware_listings(self):
        partuuid = '33333333-3333-4333-8333-333333333333'
        for name in ('mac-before', 'mac-created', 'mac-booted'):
            with self.subTest(name=name):
                listing = (ROOT / 'tests/fixtures/firmware' / (name + '.txt')).read_text()
                self.assertIn('Boot0082* \t', listing)
                self.assertIn('BootFFFF* \t', listing)
                self.assertIn('dp:', listing)
                if name == 'mac-booted':
                    old, new = module.proposed_order(listing, partuuid)
                    self.assertEqual(old, ['0003', '0002', '0001', '0000', '0080'])
                    self.assertEqual(new, old)
                else:
                    with self.assertRaisesRegex(ValueError, 'ambiguous_boot_entries'):
                        module.proposed_order(listing, partuuid)
                calls = []
                def run(args):
                    calls.append(args)
                    return listing if args == ['efibootmgr', '-v'] else ''
                result = module.recover(partuuid, 'Apple Inc.', run, True, menu='')
                self.assertEqual(result['menu'], 'refresh_pending')
                self.assertEqual(calls, [['efibootmgr', '-v']])
                if name != 'mac-booted':
                    calls.clear()
                    with self.assertRaisesRegex(ValueError, 'ambiguous_boot_entries'):
                        module.recover(partuuid, 'Dell', run, True, menu='')
                    self.assertEqual(calls, [['efibootmgr', '-v']])

    def test_inactive_entry_activated_without_creation(self):
        for order in ('0001,0002,0003', '0002,0001,0003'):
            with self.subTest(order=order):
                current = inventory(order).replace('Boot0002*', 'Boot0002 ')
                calls = []
                def run(args):
                    nonlocal current
                    calls.append(args)
                    if args == ['efibootmgr', '-b', '0002', '-a']:
                        current = current.replace('Boot0002 ', 'Boot0002*')
                    elif '--bootorder' in args:
                        current = current.replace('BootOrder: ' + order, 'BootOrder: ' + args[-1])
                    return current
                self.assertEqual(module.recover(UUID, 'Dell', run, True),
                                 {'state': 'recovered', 'changed': True})
                self.assertIn(['efibootmgr', '-b', '0002', '-a'], calls)
                self.assertEqual(any('--bootorder' in call for call in calls), order.startswith('0001'))
                self.assertFalse(any('--create' in call for call in calls))

    def test_active_duplicate_preferred_to_inactive(self):
        inactive = inventory().splitlines()[3].replace('0002*', '0004 ')
        listing = inventory('0004,0001,0002,0003') + inactive + '\n'
        self.assertEqual(module.recovery_plan(listing, UUID),
                         (['0004', '0001', '0002', '0003'], ['0002', '0004', '0001', '0003'], False))

    def test_activation_failure_does_not_reorder(self):
        listing = inventory().replace('Boot0002*', 'Boot0002 ')
        calls = []
        def run(args):
            calls.append(args)
            return listing
        self.assertEqual(module.recover(UUID, 'Dell', run, True),
                         {'state': 'recovery_uncertain', 'changed': None})
        self.assertFalse(any('--bootorder' in call for call in calls))

    def test_renumbered_windows_refreshes_on_pc_and_apple(self):
        menu = f'# Windows firmware target: {UUID} /EFI/Microsoft/Boot/bootmgfw.efi\n  if bootnext 0004; then\n'
        for vendor in ('Dell', 'Apple Inc.'):
            calls = []
            def run(args):
                calls.append(args)
                return inventory('0002,0001,0003') if args == ['efibootmgr', '-v'] else ''
            result = module.recover(UUID, vendor, run, True, menu=menu)
            self.assertEqual(result['menu'], 'refresh_pending')
            self.assertEqual(calls, [['efibootmgr', '-v']])

    def test_unchanged_windows_and_status_do_not_refresh(self):
        menu = f'# Windows firmware target: {UUID} /EFI/Microsoft/Boot/bootmgfw.efi\n  if bootnext 0001; then\n'
        calls = []
        def run(args):
            calls.append(args)
            return inventory('0002,0001,0003')
        self.assertEqual(module.recover(UUID, 'Apple', run, True, menu=menu)['menu'], 'ready')
        self.assertEqual(module.recover(UUID, 'Apple', run, menu='')['menu'], 'refresh_available')
        self.assertEqual(calls, [['efibootmgr', '-v'], ['efibootmgr', '-v']])

    def test_windows_disappearance_or_appearance_queues_refresh(self):
        menu = f'# Windows firmware target: {UUID} /EFI/Microsoft/Boot/bootmgfw.efi\n  if bootnext 0001; then\n'
        foreign = inventory().replace('Boot0001* Windows', 'Boot0001  Windows')
        self.assertEqual(module.refresh_windows_menu(foreign, UUID, menu, True), 'refresh_pending')
        self.assertEqual(module.refresh_windows_menu(inventory(), UUID,
                         'chainloader /EFI/Microsoft/Boot/bootmgfw.efi', True), 'refresh_pending')
        self.assertEqual(json.loads(self.pending.read_text()), {'boots': 0})

    def test_missing_canonical_menu_queues_even_without_windows_entry(self):
        listing = inventory().replace('Boot0001* Windows', 'Boot0001  Windows')
        self.assertEqual(module.refresh_windows_menu(listing, UUID, '', True), 'refresh_pending')
        self.assertEqual(json.loads(self.pending.read_text()), {'boots': 0})

    def test_menu_failure_cannot_gate_windows_first_recovery(self):
        for failure in ('queue', 'windows', 'read'):
            with self.subTest(failure=failure):
                current = inventory()
                calls = []
                if failure == 'windows':
                    current += current.splitlines()[2].replace('Boot0001', 'Boot0004') + '\n'
                def run(args):
                    nonlocal current
                    calls.append(args)
                    if '--bootorder' in args:
                        current = current.replace('BootOrder: 0001,0002,0003',
                                                  'BootOrder: ' + args[-1])
                    return current
                def fail_queue():
                    self.assertIn(['efibootmgr', '--bootorder', '0002,0001,0003'], calls)
                    raise OSError('cannot queue refresh')
                menu = self.pending.parent if failure == 'read' else ''
                with patch.object(module, 'queue_refresh', side_effect=fail_queue):
                    result = module.recover(UUID, 'Dell', run, True, menu=menu)
                self.assertEqual(result, {'state': 'recovered', 'changed': True,
                    'menu': 'windows_unsafe' if failure == 'windows' else 'refresh_failed'})
                self.assertEqual(calls, [['efibootmgr', '-v'], ['efibootmgr', '-v'],
                                        ['efibootmgr', '--bootorder', '0002,0001,0003'],
                                        ['efibootmgr', '-v']])

    def test_existing_retry_budget_is_never_reset(self):
        for boots in range(4):
            record = json.dumps({'boots': boots}) + '\n'
            self.pending.write_text(record)
            inode = self.pending.stat().st_ino
            for _ in range(5):
                result = module.recover(UUID, 'Apple', lambda args: inventory(), True, menu='')
                self.assertEqual(result['menu'], 'refresh_exhausted' if boots == 3 else 'refresh_pending')
                self.assertEqual(self.pending.read_text(), record)
                self.assertEqual(self.pending.stat().st_ino, inode)

    def test_invalid_pending_record_reports_failure_without_reset(self):
        for record in ('', '{}', '{"boots": -1}', '{"boots": true}', '{"boots": 4}'):
            self.pending.write_text(record)
            result = module.recover(UUID, 'Apple', lambda args: inventory(), True, menu='')
            self.assertEqual(result['menu'], 'refresh_failed')
            self.assertEqual(self.pending.read_text(), record)

    def test_lasting_refresh_failure_stops_across_recovery_boots(self):
        from emaki_boot import refresh
        with ExitStack() as stack:
            stack.enter_context(patch.object(refresh, 'PENDING', str(self.pending)))
            stack.enter_context(patch.object(refresh, 'LOG', io.StringIO()))
            stack.enter_context(patch.object(refresh, 'remove_previous_boot_lock'))
            stack.enter_context(patch.object(refresh, 'current_boot_id', return_value='boot'))
            stack.enter_context(patch.object(refresh, 'pacman_lock', side_effect=nullcontext))
            work = stack.enter_context(patch.object(refresh, 'discover',
                side_effect=OSError('persistent refresh failure')))
            stack.enter_context(redirect_stderr(io.StringIO()))
            for boot in range(1, 7):
                current = inventory()
                def run(args):
                    nonlocal current
                    if '--bootorder' in args:
                        current = inventory(args[-1])
                    return current
                result = module.recover(UUID, 'Dell', run, True, menu='')
                self.assertEqual(result['state'], 'recovered')
                self.assertEqual(result['menu'], 'refresh_pending' if boot <= 3 else 'refresh_exhausted')
                self.assertEqual(refresh.refresh_attempt(True), 0)
                self.assertEqual(work.call_count, min(boot, 3))
                self.assertEqual(json.loads(self.pending.read_text()), {'boots': min(boot, 3)})

    def test_snapshot_menu_step_is_skipped_after_recovery(self):
        current = inventory()
        def run(args):
            nonlocal current
            if '--bootorder' in args:
                current = inventory(args[-1])
            return current
        with patch.object(module, 'normal_boot', return_value=False):
            # A directory fails if read as a menu; snapshot boots must not inspect it.
            result = module.recover(UUID, 'Dell', run, True, menu=self.pending.parent)
        self.assertEqual(result, {'state': 'recovered', 'changed': True, 'menu': 'skipped_snapshot'})
        self.assertFalse(self.pending.exists())

    def test_normal_boot_requires_generation_and_non_overlay_root(self):
        generation = 'emaki.generation=' + 'a' * 32
        for cmdline, filesystem, expected in (
            ('quiet', 'btrfs', False),
            ('other.' + generation, 'btrfs', False),
            ('emaki.generation=', 'btrfs', False),
            ('quiet ' + generation + ' rw', 'btrfs', True),
            (generation, 'ext4', True),
            (generation, 'overlay', False),
        ):
            with self.subTest(cmdline=cmdline, filesystem=filesystem):
                mounts = f'1 0 0:1 /@ / rw - {filesystem} /dev/root rw\n'
                with patch.object(module.Path, 'read_text', side_effect=[cmdline, mounts]):
                    self.assertEqual(self.normal_boot(), expected)

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
