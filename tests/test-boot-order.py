#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Firmware priority recovery preserves foreign loaders and ambiguous state."""
import importlib.machinery
import importlib.util
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import io
import json
import re
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot, errors
sys.modules['emaki_boot.boot'] = boot
from emaki_installer import secureboot
sys.modules['emaki_boot.secureboot'] = secureboot
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

    def status(self, marker, fstab_text):
        """Status without the record: only the fstab is read, never firmware or DMI data."""
        fstab = self.pending.parent / 'fstab'
        fstab.write_text(fstab_text)
        real_read = module.Path.read_text
        def read_text(path, *args, **kwargs):
            self.assertEqual(path, fstab)
            return real_read(path, *args, **kwargs)
        output = io.StringIO()
        with patch.object(module, 'MARKER', marker), patch.object(module, 'FSTAB', fstab), \
                patch.object(module, 'recover') as recover, \
                patch.object(module.Path, 'read_text', read_text), \
                patch.object(sys, 'argv', ['emaki-boot-order', '--json']), redirect_stdout(output):
            code = module.main()
        recover.assert_not_called()
        return code, json.loads(output.getvalue())

    def test_non_alongside_status_is_success_without_firmware_access(self):
        fstab = ('UUID=1111 / btrfs rw,noatime,subvol=/@ 0 0\n'
                 'UUID=2222 /efi vfat rw,relatime,fmask=0077,dmask=0077,errors=remount-ro 0 2\n')
        self.assertEqual(self.status(self.pending.parent / 'absent', fstab),
                         (0, {'state': 'not_alongside', 'changed': False}))

    def test_missing_installation_record_with_surviving_evidence_refuses(self):
        marker = self.pending.parent / 'alongside-boot.json'
        stop = marker.with_name('shared-esp-stop.json')
        # The installer's alongside row: rw and relatime removed, ro and noatime added, no fsck.
        installed = 'UUID=2222\t/efi\tvfat\tfmask=0022,dmask=0022,codepage=437,ro,noatime\t0\t0\n'
        for evidence in ('readonly_fstab', 'stop_file', 'stop_symlink', 'marker_symlink'):
            with self.subTest(evidence=evidence):
                marker.unlink(missing_ok=True)
                stop.unlink(missing_ok=True)
                if evidence == 'stop_file':
                    stop.write_text('{}\n')
                elif evidence == 'stop_symlink':
                    stop.symlink_to('missing-stop')
                elif evidence == 'marker_symlink':
                    marker.symlink_to('missing-marker')
                code, answer = self.status(marker, installed if evidence == 'readonly_fstab' else '')
                self.assertEqual(code, 1)
                self.assertEqual(answer['state'], 'refused')
                if evidence != 'marker_symlink':
                    # A symlinked record is already refused by the no-follow open.
                    self.assertEqual(answer, {'state': 'refused', 'changed': False,
                                              'reason': 'missing_installation_record'})

    def test_missing_record_ignores_unrelated_fstab_rows(self):
        for row in ('UUID=1234 /efi vfat defaults,rw 0 2',
                    'UUID=1234 /efi vfat rw,errors=remount-ro 0 2',
                    'UUID=1234 /boot vfat defaults,ro 0 2',
                    '# UUID=1234 /efi vfat defaults,ro 0 2',
                    'UUID=1234 /efi/extra vfat ro 0 2'):
            with self.subTest(row=row):
                self.assertEqual(self.status(self.pending.parent / 'absent', row + '\n'),
                                 (0, {'state': 'not_alongside', 'changed': False}))

    def test_changed_firmware_uses_current_windows_inventory_for_menu(self):
        menu = (f'# Windows firmware target: {UUID} /EFI/Microsoft/Boot/bootmgfw.efi\n'
                '  if bootnext 0001; then\n')
        fresh = inventory('0004,0002,0003').replace('Boot0001*', 'Boot0004*')
        for current, expected in ((fresh, 'refresh_pending'),
                                  (inventory('0001,0001,0002'), 'windows_unsafe')):
            with self.subTest(expected=expected):
                self.pending.unlink(missing_ok=True)
                replies = iter((inventory(), current))
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return next(replies)
                result = module.recover(UUID, 'Dell', run, True, menu=menu)
                self.assertEqual(result, {'state': 'refused', 'changed': False,
                                         'reason': 'firmware_changed', 'menu': expected})
                self.assertEqual(self.pending.exists(), expected == 'refresh_pending')

    def test_recovery_refusals_still_check_menu(self):
        for reason, listing in (
            ('ambiguous_boot_entries', inventory('0001,0003')),
            ('unverified_emaki_entry', inventory().replace('grubx64.efi', 'unknown.efi')),
            ('invalid_boot_order', inventory('0001,0001,0002')),
            ('firmware_changed', inventory()),
        ):
            with self.subTest(reason=reason):
                self.pending.unlink(missing_ok=True)
                calls = []
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    calls.append(args)
                    return inventory('0003,0001,0002') if len(calls) > 1 else listing
                result = module.recover(UUID, 'Dell', run, True, menu='')
                menu = 'windows_unsafe' if reason == 'invalid_boot_order' else 'refresh_pending'
                self.assertEqual(result, {'state': 'refused', 'changed': False,
                                         'reason': reason, 'menu': menu})
                if menu == 'refresh_pending':
                    self.assertEqual(json.loads(self.pending.read_text()), {'boots': 0})
                else:
                    self.assertFalse(self.pending.exists())

    def test_p4_case_h_renumbered_windows_without_ordered_emaki_queues_menu(self):
        partuuid = '33333333-3333-4333-8333-333333333333'
        listing = (ROOT / 'tests/fixtures/firmware/mac-booted.txt').read_text()
        listing = listing.replace('Boot0002* Windows', 'Boot0004* Windows').replace(
            'BootOrder: 0003,0002,0001,0000,0080', 'BootOrder: 0004,0001,0000,0080')
        menu = (f'# Windows firmware target: {partuuid} /EFI/Microsoft/Boot/bootmgfw.efi\n'
                '  if bootnext 0002; then\n')
        def run(args):
            self.assertEqual(args, ['efibootmgr', '-v'])
            return listing
        result = module.recover(partuuid, 'Dell', run, True, menu=menu)
        self.assertEqual(result, {'state': 'refused', 'changed': False,
                                 'reason': 'ambiguous_boot_entries', 'menu': 'refresh_pending'})
        self.assertEqual(json.loads(self.pending.read_text()), {'boots': 0})

    def test_refusal_from_recovery_exits_nonzero_with_menu(self):
        result = {'state': 'refused', 'changed': False, 'reason': 'invalid_boot_order',
                  'menu': 'refresh_available'}
        output = io.StringIO()
        with patch.object(module, 'read_marker', return_value=(UUID, UUID)), \
                patch.object(module.Path, 'read_text', return_value='Dell'), \
                patch.object(module, 'recover', return_value=result), \
                patch.object(sys, 'argv', ['emaki-boot-order', '--json']), redirect_stdout(output):
            self.assertEqual(module.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), result)

    def test_service_explains_only_confirmed_priority_recovery(self):
        for listing, repair, expected in (
                (inventory(), True, 'recovered'),
                (inventory('0002,0001,0003'), True, 'ready'),
                (inventory(), False, 'recovery_available')):
            output = io.StringIO()
            current = listing
            def run(args):
                nonlocal current
                if args[:2] == ['efibootmgr', '--bootorder']:
                    current = inventory(args[2])
                return current
            recover = module.recover
            def recover_fixture(*args, **kwargs):
                kwargs['menu'] = None
                return recover(*args, run=run, **kwargs)
            with self.subTest(state=expected), \
                    patch.object(module, 'read_marker', return_value=(UUID, UUID)), \
                    patch.object(module.Path, 'read_text', return_value='Dell'), \
                    patch.object(module.os, 'geteuid', return_value=0), \
                    patch.object(module, 'recover', side_effect=recover_fixture), \
                    patch.object(sys, 'argv', ['emaki-boot-order', '--json'] +
                                 (['--repair'] if repair else [])), redirect_stdout(output):
                self.assertEqual(module.main(), 0)
            answer = json.loads(output.getvalue())
            self.assertEqual(answer['state'], expected)
            if expected == 'recovered':
                self.assertEqual(answer['note'], "Emaki's firmware startup priority was restored.")
            else:
                self.assertNotIn('note', answer)

    def test_missing_own_esp_entry_waits_for_boot_complete_with_clean_service_exit(self):
        own = '87654321-1234-4321-abcd-123456789abc'
        missing = '\n'.join(line for line in inventory('0001,0003').splitlines()
                            if not line.startswith('Boot0002')) + '\n'
        recover = module.recover
        for repair in (False, True):
            output = io.StringIO()
            calls = []
            def run(args):
                calls.append(args)
                return missing
            def recover_missing(*args, **kwargs):
                kwargs['menu'] = None
                return recover(*args, run=run, **kwargs)
            with self.subTest(repair=repair), \
                    patch.object(module, 'read_marker', return_value=(own, UUID)), \
                    patch.object(module.Path, 'read_text', return_value='Dell'), \
                    patch.object(module.os, 'geteuid', return_value=0), \
                    patch.object(module, 'recover', side_effect=recover_missing), \
                    patch.object(sys, 'argv', ['emaki-boot-order', '--json'] +
                                 (['--repair'] if repair else [])), redirect_stdout(output):
                self.assertEqual(module.main(), 0)
            self.assertEqual(json.loads(output.getvalue()), {
                'state': 'recovery_pending', 'changed': False, 'reason': 'missing_emaki_entry',
                'note': 'Emaki firmware entry is missing; emaki-boot-complete will attempt recreation.'})
            self.assertEqual(calls, [['efibootmgr', '-v']])

    def test_deferred_entry_recovery_does_not_hide_invalid_or_unowned_state(self):
        own = '87654321-1234-4321-abcd-123456789abc'
        missing = '\n'.join(line for line in inventory('0001,0003').splitlines()
                            if not line.startswith('Boot0002')) + '\n'
        for listing, normal, windows in (
            (missing, False, UUID), (missing, True, own),
            (missing.replace('0001,0003', '0001,0002,0003'), True, UUID),
            (missing.replace('0001,0003', '0001,0001,0003'), True, UUID),
            (inventory('0001,0003').replace(UUID, own), True, UUID),
            (inventory().replace('grubx64.efi', 'unknown.efi'), True, UUID),
        ):
            with self.subTest(listing=listing, normal=normal, windows=windows), \
                    patch.object(module, 'normal_boot', return_value=normal):
                answer = module.recover(own, 'Dell', lambda args: listing, True, windows=windows)
                self.assertEqual(answer['state'], 'refused')
                self.assertFalse(answer['changed'])

    def test_inaccessible_or_invalid_installation_record_still_refuses(self):
        for error in (PermissionError('denied'), ValueError('invalid_installation_record')):
            output = io.StringIO()
            with self.subTest(error=error), patch.object(module, 'read_marker', side_effect=error), \
                    patch.object(sys, 'argv', ['emaki-boot-order', '--json']), redirect_stdout(output):
                self.assertEqual(module.main(), 1)
            self.assertEqual(json.loads(output.getvalue()),
                             {'state': 'refused', 'changed': False, 'reason': str(error)})

    def test_missing_firmware_vendor_is_not_non_alongside(self):
        output = io.StringIO()
        with patch.object(module, 'read_marker', return_value=(UUID, UUID)), \
                patch.object(module.Path, 'read_text', side_effect=FileNotFoundError('vendor missing')), \
                patch.object(sys, 'argv', ['emaki-boot-order', '--json']), redirect_stdout(output):
            self.assertEqual(module.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['state'], 'refused')

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
            with self.subTest(data=data):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                self.assertEqual(module.recover(UUID, 'Dell Inc.', run, repair=True),
                                 {'state': 'refused', 'changed': False, 'reason': 'unverified_emaki_entry'})

    def test_secure_boot_shim_entry_is_this_installation_loader(self):
        # A machine that signs its boot files has its firmware entry on shim (both listing shapes).
        for listing in (inventory().replace('grubx64.efi', 'shimx64.efi'),
                        inventory18().replace('grubx64.efi', 'shimx64.efi'),
                        inventory().replace('grubx64.efi', 'SHIMX64.EFI'),
                        # Boot slots (0.9): "Emaki" names the active slot's shim.
                        inventory().replace('grubx64.efi', 'a\\shimx64.efi'),
                        inventory18().replace('grubx64.efi', 'b\\shimx64.efi')):
            with self.subTest(listing=listing):
                order = re.search(r'^BootOrder: (\S+)$', listing, re.M)[1].split(',')
                self.assertEqual(module.proposed_order(listing, UUID),
                                 (order, ['0002'] + [n for n in order if n != '0002']))
        # Only shim and GRUB in EFI/Emaki: MokManager or another file is still not verified.
        for name in ('mmx64.efi', 'shimx64.efi.bak', 'shim.efi', 'a\\mmx64.efi', 'a\\grubx64.efi',
                     'c\\shimx64.efi', 'a\\b\\shimx64.efi'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unverified_emaki_entry'):
                module.proposed_order(inventory().replace('grubx64.efi', name), UUID)

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

    def test_own_esp_next_to_windows_verifies_each_entry_on_its_own_esp(self):
        # Since 0.9 Emaki has its own ESP next to Windows: "Emaki" names that partition,
        # Windows Boot Manager stays on Windows' ESP (the record's esp_partuuid).
        own = 'aaaaaaaa-1111-4222-8333-444444444444'
        def two_esps(order='0001,0004,0002,0003', loader='a\\shimx64.efi'):
            # Windows Boot Manager (0001) on UUID, "Emaki" (0002) on partition 5, Emaki's own ESP.
            lines = inventory(order).splitlines()
            # A stale entry of an older shared-ESP installation stays where it is.
            stale = lines[3].replace('Boot0002', 'Boot0004')
            lines[3] = lines[3].replace('HD(1,', 'HD(5,').replace(
                f'{UUID},0x800,0x32000', f'{own},0x9000800,0x200000').replace('grubx64.efi', loader)
            return '\n'.join(lines + [stale]) + '\n'
        menu = (f'# Windows firmware target: {UUID} /EFI/Microsoft/Boot/bootmgfw.efi\n'
                '  if bootnext 0001; then\n')
        for loader in ('a\\shimx64.efi', 'b\\shimx64.efi', 'grubx64.efi'):
            with self.subTest(loader=loader):
                calls = []
                current = two_esps(loader=loader)
                def run(args):
                    nonlocal current
                    calls.append(args)
                    if '--bootorder' in args:
                        current = two_esps(args[-1], loader)
                        return ''
                    return current
                self.pending.unlink(missing_ok=True)
                self.assertEqual(module.recover(own, 'Dell Inc.', run, True, menu=menu, windows=UUID),
                                 {'state': 'recovered', 'changed': True, 'menu': 'ready'})
                self.assertEqual([x for x in calls if '--bootorder' in x],
                                 [['efibootmgr', '--bootorder', '0002,0001,0004,0003']])
                self.assertFalse(self.pending.exists())
        # Status only: Emaki already first on its own ESP, the menu names Windows' ESP.
        listing = two_esps('0002,0001,0004,0003')
        self.assertEqual(module.recover(own, 'LENOVO', lambda args: listing, menu=menu, windows=UUID),
                         {'state': 'ready', 'changed': False, 'menu': 'ready'})
        # A menu aimed at Emaki's ESP is not the Windows restart: a refresh is offered.
        wrong = menu.replace(UUID, own)
        self.assertEqual(module.recover(own, 'LENOVO', lambda args: listing, menu=wrong, windows=UUID),
                         {'state': 'ready', 'changed': False, 'menu': 'refresh_available'})

    def test_installation_record_names_emaki_and_windows_esps(self):
        own = 'aaaaaaaa-1111-4222-8333-444444444444'
        record = self.pending.parent / 'alongside-boot.json'
        real_fstat = module.os.fstat

        def fstat(fd):
            # The installer writes the record as root; this test runs unprivileged.
            values = list(real_fstat(fd))
            values[4] = 0
            return module.os.stat_result(values)

        def status(value):
            record.write_text(json.dumps(value) + '\n')
            record.chmod(0o644)
            output = io.StringIO()
            with patch.object(module, 'MARKER', record), \
                    patch.object(module.os, 'fstat', side_effect=fstat), \
                    patch.object(module.Path, 'read_text', return_value='Dell Inc.'), \
                    patch.object(module, 'recover', return_value={'state': 'ready', 'changed': False}) as recover, \
                    patch.object(sys, 'argv', ['emaki-boot-order', '--json']), redirect_stdout(output):
                code = module.main()
            return code, json.loads(output.getvalue()), recover

        new = {'version': 1, 'esp_partuuid': UUID, 'emaki_esp_partuuid': own,
               'windows_esp_uuid': 'ABCD-1234'}
        code, _, recover = status(new)
        self.assertEqual(code, 0)
        recover.assert_called_once_with(own, 'Dell Inc.', repair=False,
                                        menu=Path('/boot/grub/grub.cfg'), windows=UUID)
        # The older shared layout: one ESP for both entries.
        code, _, recover = status({'version': 1, 'esp_partuuid': UUID})
        self.assertEqual(code, 0)
        recover.assert_called_once_with(UUID, 'Dell Inc.', repair=False,
                                        menu=Path('/boot/grub/grub.cfg'), windows=UUID)
        for damaged in (dict(new, emaki_esp_partuuid='x'), dict(new, emaki_esp_partuuid=None),
                        dict(new, emaki_esp_partuuid=UUID.upper())):
            with self.subTest(damaged=damaged):
                code, answer, recover = status(damaged)
                self.assertEqual((code, answer), (1, {'state': 'refused', 'changed': False,
                                                      'reason': 'invalid_esp_identity'}))
                recover.assert_not_called()

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
            with self.subTest(data=data):
                def run(args):
                    self.assertEqual(args, ['efibootmgr', '-v'])
                    return data
                result = module.recover(UUID, 'Dell Inc.', run, True)
                self.assertEqual(result['state'], 'refused')
                self.assertFalse(result['changed'])

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
        self.assertEqual(module.recover(UUID, 'Dell', run, True),
                         {'state': 'refused', 'changed': False, 'reason': 'firmware_changed'})

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
                    result = module.recover(partuuid, 'Dell', run, True, menu='')
                    self.assertEqual(result['state'], 'refused')
                    self.assertEqual(result['reason'], 'ambiguous_boot_entries')
                    self.assertIn('menu', result)
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

    def test_missing_windows_entry_refreshes_legacy_chainload_only_once(self):
        listing = inventory().replace('Boot0001* Windows', 'Boot0001  Windows')
        legacy = ("### BEGIN /etc/grub.d/30_os-prober ###\n"
                  "menuentry 'Windows Boot Manager' {\n"
                  "  search --no-floppy --fs-uuid --set=root ABCD-1234\n"
                  "  chainloader /EFI/Microsoft/Boot/bootmgfw.efi\n"
                  "}\n### END /etc/grub.d/30_os-prober ###\n")
        self.assertEqual(module.refresh_windows_menu(listing, UUID, legacy, False),
                         'refresh_available')
        self.assertFalse(self.pending.exists())
        self.assertEqual(module.refresh_windows_menu(listing, UUID, legacy, True),
                         'refresh_pending')
        guarded = boot.windows_bootnext_menu(legacy, UUID, 'ABCD-1234', listing)
        self.pending.unlink()
        for repair in (False, True):
            self.assertEqual(module.refresh_windows_menu(listing, UUID, guarded, repair), 'ready')
            self.assertFalse(self.pending.exists())

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


class Documentation(unittest.TestCase):
    def test_the_installed_map_names_the_status_without_a_record(self):
        # The installed system map is rendered from MANIFEST.md.
        page = (ROOT / 'docs/system-map.md').read_text()
        block = page.split('component: alongside-test-boot-safety\n', 1)[1].split('```', 1)[0]
        change = ' '.join(next(line for line in block.splitlines() if line.startswith('change:')).split())
        self.assertIn('A refused priority recovery still checks and queues the menu.', change)
        self.assertIn('not_alongside', change)
        self.assertIn('missing_installation_record', change)

    def test_retry_marker_text_separates_boot_retries_from_explicit_attempts(self):
        text = ' '.join((ROOT / 'docs/iso.md').read_text().split())
        self.assertIn('On a boot retry, a kept loader or a permanent refusal retains the marker with '
                      'an exhausted budget; an explicit hook or manual attempt that ends kept or '
                      'refused clears it.', text)
        self.assertNotIn('Success clears pending retry; a kept loader', text)


if __name__ == '__main__':
    unittest.main()
