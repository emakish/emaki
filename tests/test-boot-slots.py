#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure Boot loaders in A/B slots, switched by firmware entries (boot refresh side).

A machine with its signing key keeps EFI/Emaki/a and EFI/Emaki/b, each a complete set of
shim, MokManager, the signed GRUB and the certificate, with a manifest written last. An update
writes only the slot no BootOrder entry starts and offers it once by BootNext; a completed
boot through it switches BootOrder. These tests cut the power at every write an update and a
switch make (every ESP write and every firmware variable write, plus random points with torn
writes and torn FAT renames) and check that BootOrder always starts a complete slot.
"""
from contextlib import ExitStack, contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('secureboot_fixture', ROOT / 'tests/test-boot-refresh-secureboot.py')
sbt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbt)
refresh, secureboot, base = sbt.refresh, sbt.secureboot, sbt.base
ESP_PARTUUID = sbt.ESP_PARTUUID
WINDOWS = '\\EFI\\Microsoft\\Boot\\bootmgfw.efi'


class StartupWording(unittest.TestCase):
    def test_recovery_messages_name_startup_copies(self):
        for message in (refresh.SLOT_NO_ENTRY, refresh.SLOT_NOT_PROVEN,
                        refresh.REPAIR_NOT_SIGNING, refresh.REPAIR_NO_GENERATION,
                        refresh.REPAIR_KEPT, refresh.REPAIR_WRITTEN, refresh.REPAIR_DONE):
            with self.subTest(message=message):
                self.assertNotIn('boot slot', message.lower())
                self.assertIn('startup cop', message.lower())

    def test_software_change_wait_is_plain(self):
        # A running package change is a plain wait; an unverified lock keeps the lock advice.
        for advice, expected in (
                (refresh.LOCK_LIVE,
                 'The boot loader update will wait for other software changes to finish.'),
                ('unverified lock advice', 'unverified lock advice')):
            with self.subTest(advice=advice), \
                    patch.object(refresh, 'current_boot_id', return_value='test'), \
                    patch.object(refresh, 'remove_previous_boot_lock'), \
                    patch.object(refresh, 'lock_message', return_value=advice), \
                    patch.object(refresh.os, 'open', side_effect=FileExistsError), \
                    patch.object(refresh, 'LOG', io.StringIO()) as log:
                with self.assertRaises(refresh.Transient) as caught:
                    with refresh.pacman_lock():
                        self.fail('The occupied lock must refuse the update')
                self.assertIn('The package database is locked', log.getvalue())
                self.assertEqual(str(caught.exception), expected)

    def test_unknown_copy_retains_its_identity(self):
        with self.assertRaisesRegex(secureboot.SecureBootError, "Unknown startup copy 'wrong'"):
            secureboot.slot_dir(Path('/unused'), 'wrong')
        with self.assertRaisesRegex(secureboot.SecureBootError, "Unknown startup copy 'wrong'"):
            secureboot.slot_entry('wrong')


class Stop(BaseException):
    """A power cut: nothing after it runs."""


class Firmware:
    """NVRAM as efibootmgr shows and changes it. Each variable write is atomic (UEFI
    SetVariable): a cut happens before or after one, never inside."""

    def __init__(self, f):
        self.f = f
        self.entries = {'0000': ('Windows Boot Manager', WINDOWS)}
        self.order, self.next, self.current = ['0000'], None, None
        self.cut = lambda: None
        self.writes = 0
        # Firmware faults: a full variable store (EFI_OUT_OF_RESOURCES, ENOSPC to efibootmgr),
        # a boot order lock, and writes the firmware accepts but does not keep.
        self.full, self.locked, self.dropped = False, False, set()
        # Entries someone made inactive (efibootmgr -A) and entries with optional data.
        self.inactive, self.data = set(), {}

    def listing(self):
        lines = []
        if self.current:
            lines.append('BootCurrent: ' + self.current)
        if self.next:
            lines.append('BootNext: ' + self.next)
        lines += ['Timeout: 1 seconds', 'BootOrder: ' + ','.join(self.order)]
        for number, (label, loader) in sorted(self.entries.items()):
            flag = ' ' if number in self.inactive else '*'
            data = self.data.get(number, b'')
            # efibootmgr 18: optional data appended to a bare path, and its data line.
            lines.append(f'Boot{number}{flag} {label}\tHD(1,GPT,{ESP_PARTUUID},0x800,0x32000)/{loader}{data.hex()}')
            if data:
                lines.append('    data: ' + ' '.join(f'{byte:02x}' for byte in data))
        return '\n'.join(lines) + '\n'

    def started(self):
        """The entry firmware starts from BootOrder: the first active one that exists."""
        return next((n for n in self.order if n in self.entries and n not in self.inactive), None)

    def write(self, change):
        self.writes += 1
        self.cut()
        change()

    def run(self, args):
        if args == ['efibootmgr', '-v']:
            return self.listing()
        if self.full and '--create-only' in args:
            raise refresh.Transient('efibootmgr failed: efibootmgr: Could not prepare Boot variable: '
                                    'No space left on device')
        if self.locked and '--bootorder' in args:
            raise refresh.Transient('efibootmgr failed: efibootmgr: Could not set BootOrder: '
                                    'Permission denied')
        if any(option in self.dropped for option in args):
            self.writes += 1
            return ''
        if '--create-only' in args:
            assert not set(args) & {'-@', '--append-binary-args', '-u', '--unicode'}, args
            label, loader = args[args.index('--label') + 1], args[args.index('--loader') + 1]
            number = next(f'{n:04X}' for n in range(1, 0xffff) if f'{n:04X}' not in self.entries)
            self.write(lambda: self.entries.__setitem__(number, (label, loader)))
        elif '--delete-bootnum' in args:
            number = args[args.index('--bootnum') + 1]
            assert number in self.entries, args

            def delete():
                del self.entries[number]
                self.inactive.discard(number)
                self.data.pop(number, None)
                # efibootmgr removes a deleted entry from BootOrder too (a second variable).
            self.write(delete)
            if number in self.order:
                self.write(lambda: self.order.remove(number))
        elif '--bootorder' in args:
            order = args[args.index('--bootorder') + 1].split(',')
            assert all(number in self.entries for number in order), args
            self.write(lambda: setattr(self, 'order', order))
        elif '--bootnext' in args:
            number = args[args.index('--bootnext') + 1]
            assert number in self.entries, args
            self.write(lambda: setattr(self, 'next', number))
        elif '--delete-bootnext' in args:
            self.write(lambda: setattr(self, 'next', None))
        else:
            raise AssertionError('unexpected efibootmgr call: ' + repr(args))
        return ''

    def boot(self):
        """Firmware starts: BootNext once, else the first BootOrder entry that exists."""
        number = self.next if self.next in self.entries else self.started()
        self.next = None
        self.current = number
        label, loader = self.entries[number]
        base.RefreshOrchestrationChecks  # the fixture's module stays loaded
        sbt.reboot(self.f)
        data = self.data.get(number, b'')
        sbt.booted_from(self.f, loader, number=int(number, 16),
                        option=sbt.load_option(loader, optional=data) if data else None)
        return secureboot.entry_slot(loader)


def signed_pe(label):
    """A PE image whose certificate table holds one WIN_CERTIFICATE (PKCS_SIGNED_DATA)."""
    return sbt.pe_image(label)


def package(f, shim=b'S', manager=b'M'):
    sbt.machine(f, removable=False, shim=signed_pe(shim), manager=signed_pe(manager))


def installed(f, fw):
    """The installer's layout: slot a, one 'Emaki' entry first in BootOrder, EFI/BOOT copies."""
    package(f)
    grub = f.originals['/efi/EFI/Emaki/grubx64.efi'] + sbt.SIGNATURE
    payload = secureboot.slot_payload(grub, f.mapped(str(secureboot.SHIM_DIR)), f.mapped(str(secureboot.DER)))
    secureboot.write_slot(f.mapped('/efi'), 'a', payload)
    for name, data in ((secureboot.REMOVABLE_SHIM, payload[secureboot.SHIM]),
                       (secureboot.MOK_MANAGER, payload[secureboot.MOK_MANAGER]),
                       (secureboot.GRUB, grub)):
        f.put(f.mapped('/efi/EFI/BOOT') / name, data)
    fw.entries['0001'] = ('Emaki', secureboot.slot_entry('a'))
    fw.order = ['0001', '0000']
    fw.boot()
    return payload


@contextmanager
def machine(test):
    with ExitStack() as stack:
        f = stack.enter_context(test.fixture(False))
        fw = Firmware(f)
        stack.enter_context(sbt.signing_tools(f))
        signing_run = refresh.run

        def run(arguments):
            args = list(map(str, arguments))
            if args[0] == 'efibootmgr':
                f.commands.append(args)
                return fw.run(args)
            if args[:2] == ['sbverify', '--list']:
                if not secureboot.pe_certificates_present(Path(args[2]).read_bytes()):
                    raise refresh.Transient('sbverify: No signature table present')
                return 'signature 1\nimage signature issuers:\n'
            return signing_run(arguments)

        stack.enter_context(patch.object(refresh, 'run', side_effect=run))
        stack.enter_context(patch.object(refresh, 'esp_location', return_value=('/dev/vda', '1')))
        stack.enter_context(patch.object(refresh, 'esp_partuuid', return_value=ESP_PARTUUID))
        # The ESP's FAT logical sector size is read from its device; the fixture ESP is a directory.
        stack.enter_context(patch.object(refresh, 'esp_sector_size', return_value=512, create=True))
        which = refresh.shutil.which
        stack.enter_context(patch.object(refresh.shutil, 'which',
                                         side_effect=lambda name: '/usr/bin/sbverify' if name == 'sbverify' else which(name)))
        yield f, fw


def esp_files(f, slot):
    directory = secureboot.slot_dir(f.mapped('/efi'), slot)
    return {path.name: path.read_bytes() for path in directory.iterdir()} if directory.is_dir() else {}


def boot_and_complete(test, f, fw):
    """Power on, start through the firmware, and run mark-good once the start completed."""
    slot = fw.boot()
    record = secureboot.read_slot(f.mapped('/efi'), slot) if slot else None
    tag = record['generation'] if record else None
    if tag and (f.mapped('/boot/emaki') / tag).is_dir():
        test.complete_boot(f, f.mapped('/boot/emaki') / tag)
    else:
        sbt.boot_without_generation(f)
    return slot


class Cuts:
    """Count every write and stop at one of them, optionally leaving it torn."""

    def __init__(self, fw, at=None, rng=None):
        self.fw, self.at, self.rng, self.count, self.stopped = fw, at, rng, 0, False

    def point(self):
        if self.stopped:
            raise Stop()
        self.count += 1
        if self.at is not None and self.count == self.at:
            self.stopped = True
            return True
        return False

    @contextmanager
    def active(self):
        durable, replace, sync = secureboot._durable, os.replace, secureboot._sync_directory
        refresh_sync, refresh_write = refresh.sync_directory, refresh.write

        def cut_durable(path, data):
            if self.point():
                if self.rng and self.rng.random() < 0.7:
                    with open(path, 'wb') as handle:
                        handle.write(data[:self.rng.randrange(len(data) + 1)])
                raise Stop()
            durable(path, data)

        def cut_replace(source, target):
            if self.point():
                mode = self.rng.choice(('before', 'after', 'torn')) if self.rng else 'before'
                if mode == 'torn' and '/efi/' not in str(target):
                    mode = 'before'  # a btrfs rename is atomic; only FAT tears one
                if mode == 'after':
                    replace(source, target)
                elif mode == 'torn':
                    # FAT: a power cut inside a rename can leave the target empty.
                    Path(target).write_bytes(b'')
                raise Stop()
            replace(source, target)

        def cut_sync(path):
            if self.point():
                raise Stop()
            sync(path)

        def cut_refresh_sync(path):
            if self.point():
                raise Stop()
            refresh_sync(path)

        def cut_write(path, data, mode=0o600):
            if self.point():
                raise Stop()
            refresh_write(path, data, mode)

        def firmware_cut():
            if self.point():
                if self.rng and self.rng.random() < 0.5:
                    return  # the variable write itself completed; the cut came after it
                raise Stop()

        self.fw.cut = firmware_cut
        with patch.object(secureboot, '_durable', side_effect=cut_durable), \
                patch.object(secureboot.os, 'replace', side_effect=cut_replace), \
                patch.object(secureboot, '_sync_directory', side_effect=cut_sync), \
                patch.object(refresh, 'sync_directory', side_effect=cut_refresh_sync), \
                patch.object(refresh, 'write', side_effect=cut_write):
            try:
                yield self
            except Stop:
                pass
            finally:
                self.fw.cut = lambda: None
        if self.stopped and self.fw.cut is not None:
            pass


class SlotChecks(unittest.TestCase):
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot

    def assert_bootable(self, f, fw, active_files=None):
        """BootOrder starts a complete slot, every slot entry in BootOrder or BootNext names a
        complete slot, and the active slot holds exactly what it held before."""
        parsed = secureboot.firmware_entries(fw.listing())
        active = secureboot.active_slot(parsed, ESP_PARTUUID)
        self.assertIsNotNone(active, fw.listing())
        # What firmware starts: the first active BootOrder entry naming a slot is the one the
        # code calls active (an inactive entry is skipped by firmware, never by the code alone).
        first = next(n for n in fw.order if n in fw.entries and n not in fw.inactive
                     and secureboot.entry_slot(fw.entries[n][1]))
        self.assertEqual(secureboot.entry_slot(fw.entries[first][1]), active, fw.listing())
        reachable = [n for n in fw.order if n in fw.entries and n not in fw.inactive]
        reachable += [fw.next] if fw.next in fw.entries else []
        for number in reachable:
            slot = secureboot.entry_slot(fw.entries[number][1])
            if slot:
                self.assertIsNotNone(secureboot.read_slot(f.mapped('/efi'), slot),
                                     f'{number} starts the incomplete slot {slot}')
        if active_files is not None:
            slot, files = active_files
            if active == slot:
                self.assertEqual(esp_files(f, slot), files)
        return active

    def test_update_writes_the_other_slot_and_offers_it_once(self):
        with machine(self) as (f, fw):
            payload = installed(f, fw)
            before = esp_files(f, 'a')
            refresh.refresh(f.identity)
            self.assertEqual(esp_files(f, 'a'), before)
            record = secureboot.read_slot(f.mapped('/efi'), 'b')
            generation, = f.mapped('/boot/emaki').iterdir()
            self.assertEqual(record['generation'], generation.name)
            image = (generation / 'grub/x86_64-efi/emaki-early.efi').read_bytes()
            files = esp_files(f, 'b')
            self.assertEqual(files[secureboot.GRUB], image)
            self.assertEqual(files[secureboot.SHIM], payload[secureboot.SHIM])
            self.assertEqual(sorted(files), sorted((*secureboot.slot_files_names(), secureboot.SLOT_MANIFEST)))
            # BootOrder still starts slot a; slot b has an entry outside it and BootNext.
            parsed = secureboot.firmware_entries(fw.listing())
            self.assertEqual(secureboot.active_slot(parsed, ESP_PARTUUID), 'a')
            entry, = secureboot.slot_entries(parsed, ESP_PARTUUID)['b']
            # The complete candidate stands right after the known-good entry, for firmware that
            # starts BootNext only for an entry BootOrder lists.
            self.assertEqual(fw.order, ['0001', entry, '0000'])
            self.assertEqual(fw.next, entry)
            self.assertEqual(fw.entries[entry][0], secureboot.PREVIOUS_LABEL)
            # Nothing is renamed over a loader that a firmware entry starts; no trial loader.
            self.assertFalse(list(f.mapped('/efi/EFI/Emaki').glob('loader-*.efi')))
            self.assertFalse(f.mapped('/efi/EFI/Emaki/trial.env').exists())
            self.assertIn('The boot loader update is ready for the next start.', f.output.getvalue())
            # The next start goes through b; a completed boot there switches BootOrder.
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            parsed = secureboot.firmware_entries(fw.listing())
            self.assertEqual(secureboot.active_slot(parsed, ESP_PARTUUID), 'b')
            first, second = [n for n in fw.order if n != '0000']
            self.assertEqual(fw.entries[first], (secureboot.ACTIVE_LABEL, secureboot.slot_entry('b')))
            self.assertEqual(fw.entries[second], (secureboot.PREVIOUS_LABEL, secureboot.slot_entry('a')))
            self.assertEqual(len([n for n in fw.entries if n != '0000']), 2)
            self.assertEqual(fw.order[-1], '0000')
            # The removable path now holds b's loaders.
            self.assertEqual(f.mapped('/efi/EFI/BOOT/grubx64.efi').read_bytes(), image)
            self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), files[secureboot.SHIM])
            state = json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())
            self.assertEqual(state, {'good': generation.name, 'newest': generation.name, 'fallback_sha256': None})
            # The following update writes a, the slot that is now the previous one.
            f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'changed module')
            refresh.refresh(f.identity)
            self.assertEqual(esp_files(f, 'b'), files)
            self.assertNotEqual(secureboot.read_slot(f.mapped('/efi'), 'a')['generation'], generation.name)
            self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')

    def test_previous_start_explains_default_change(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'next module')
            refresh.refresh(f.identity)
            self.assertEqual(boot_and_complete(self, f, fw), 'a')
            previous, = secureboot.slot_entries(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID)['b']
            fw.next = previous
            f.output.seek(0)
            f.output.truncate(0)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')
            self.assertIn('The previous Emaki startup copy completed this boot and is now the default.',
                          f.output.getvalue())

    def test_repaired_previous_entry_survives_ordinary_start(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            fw.entries = {'0000': ('Windows Boot Manager', WINDOWS)}
            fw.order, fw.next = ['0000'], None
            refresh.repair_slots(f.identity)
            parsed = secureboot.firmware_entries(fw.listing())
            previous, = secureboot.slot_entries(parsed, ESP_PARTUUID)['a']
            self.assertNotIn(previous, fw.order)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            self.assertEqual(fw.entries[previous], (secureboot.PREVIOUS_LABEL, secureboot.slot_entry('a')))
            self.assertNotIn(previous, fw.order)

    def test_mark_good_switches_only_after_a_boot_through_the_new_slot(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            # The boot that wrote the slot, a boot through a, and a boot through EFI/BOOT prove nothing.
            for loader in (None, secureboot.slot_entry('a'), '\\EFI\\BOOT\\BOOTX64.EFI'):
                if loader:
                    sbt.reboot(f)
                    sbt.booted_from(f, loader, number=1 if 'Emaki' in loader else 7)
                self.complete_boot(f, generation)
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'a')
            # A boot through b whose command line names another generation proves nothing either.
            entry = fw.next
            sbt.reboot(f)
            sbt.booted_from(f, secureboot.slot_entry('b'), number=int(entry, 16))
            other = f.mapped('/boot/emaki') / ('0' * 32)
            other.mkdir()
            (other / 'manifest.json').write_text('{}')
            self.complete_boot(f, other)
            self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'a')
            # A missed trial is not retried until the inputs change.
            fw.next = None
            refresh.refresh(f.identity)
            self.assertIn(refresh.TRIAL_FAILED, f.output.getvalue())
            self.assertIsNone(fw.next)

    def test_incomplete_slot_is_rewritten_and_never_started(self):
        for damage in ('manifest', 'grub', 'shim-empty', 'manifest-empty', 'stray'):
            with self.subTest(damage=damage), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                directory = secureboot.slot_dir(f.mapped('/efi'), 'b')
                if damage == 'manifest':
                    (directory / secureboot.SLOT_MANIFEST).unlink()
                elif damage == 'grub':
                    (directory / secureboot.GRUB).write_bytes(b'MZ short')
                elif damage == 'shim-empty':
                    (directory / secureboot.SHIM).write_bytes(b'')
                elif damage == 'manifest-empty':
                    (directory / secureboot.SLOT_MANIFEST).write_bytes(b'')
                else:
                    (directory / secureboot.SLOT_MANIFEST).write_text('{"version": 1, "slot": "b"}')
                self.assertIsNone(secureboot.read_slot(f.mapped('/efi'), 'b'))
                refresh.refresh(f.identity)
                record = secureboot.read_slot(f.mapped('/efi'), 'b')
                self.assertIsNotNone(record)
                self.assert_bootable(f, fw)
                self.assertEqual(boot_and_complete(self, f, fw), 'b')
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')

    def test_a_cut_at_every_write_of_an_update_and_a_switch(self):
        """Every boundary of the update and of the switch, then at least 100 random cuts with
        torn writes and torn renames, some of them repeated before a run completes."""
        with machine(self) as (f, fw):
            installed(f, fw)
            counter = Cuts(fw)
            with counter.active():
                refresh.refresh(f.identity)
            update_writes = counter.count
            boot_and_complete(self, f, fw)
        self.assertGreater(update_writes, 10)
        rng = random.Random(20261015)
        plans = [[point] for point in range(1, update_writes + 1)]
        plans += [[rng.randrange(1, update_writes + 1) for _ in range(rng.choice((1, 1, 2, 3)))]
                  for _ in range(110)]
        for index, points in enumerate(plans):
            with self.subTest(points=points), machine(self) as (f, fw):
                installed(f, fw)
                before = ('a', esp_files(f, 'a'))
                for point in points:
                    cut = Cuts(fw, point, rng if index >= update_writes else None)
                    with cut.active():
                        refresh.refresh(f.identity)
                    self.assert_bootable(f, fw, before)
                    if cut.stopped:
                        boot_and_complete(self, f, fw)
                        self.assert_bootable(f, fw)
                        before = None
                # Uninterrupted runs then finish the update and the switch.
                refresh.refresh(f.identity)
                self.assert_bootable(f, fw)
                boot_and_complete(self, f, fw)
                active = self.assert_bootable(f, fw)
                record = secureboot.read_slot(f.mapped('/efi'), active)
                self.assertIsNotNone(record['generation'])
                self.assertTrue((f.mapped('/boot/emaki') / record['generation'] / 'manifest.json').is_file())

    def test_a_cut_at_every_write_of_the_switch(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            fw.boot()
            generation, = f.mapped('/boot/emaki').iterdir()
            counter = Cuts(fw)
            with counter.active():
                self.complete_boot(f, generation)
            switch_writes = counter.count
        self.assertGreater(switch_writes, 3)
        for point in range(1, switch_writes + 1):
            with self.subTest(point=point), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                fw.boot()
                generation, = f.mapped('/boot/emaki').iterdir()
                with Cuts(fw, point).active():
                    self.complete_boot(f, generation)
                self.assert_bootable(f, fw)
                # Whichever slot starts now is complete, and the next completed boot tidies the entries.
                boot_and_complete(self, f, fw)
                active = self.assert_bootable(f, fw)
                emaki = [n for n in fw.entries if n != '0000']
                self.assertLessEqual(len(emaki), 2, fw.listing())
                first = next(n for n in fw.order if n in emaki)
                self.assertEqual(fw.entries[first], (secureboot.ACTIVE_LABEL, secureboot.slot_entry(active)))

    def test_slot_space_rule_at_its_boundary(self):
        for shared in (False, True):
            with self.subTest(shared=shared), machine(self) as (f, fw):
                installed(f, fw)
                sizes = {name: len(data) for name, data in
                         secureboot.slot_payload(b'G' * 6_000_000, f.mapped(str(secureboot.SHIM_DIR)),
                                                 f.mapped(str(secureboot.DER))).items()}
                cluster = 4096
                real = os.statvfs(f.mapped('/efi'))
                with patch.object(refresh, 'shared_esp', return_value=shared), patch.object(refresh, 'fallback_loader',
                                  return_value=None if shared else Path('/efi/EFI/BOOT/grubx64.efi')):
                    held = sum(refresh.slot_usage(slot, cluster) for slot in secureboot.SLOTS)
                    if not shared:
                        held += sum(secureboot.fat_bytes((f.mapped('/efi/EFI/BOOT') / name).stat().st_size, cluster)
                                    for name in ('BOOTX64.EFI', 'mmx64.efi', 'grubx64.efi'))
                    need = secureboot.slots_need(sizes, cluster, not shared, 0) + (
                        secureboot.SHARED_ESP_RESERVE if shared else 0)
                    for free, admitted in ((need - held, True), (need - held - cluster, False)):
                        fake = SimpleNamespace(f_bavail=free // cluster, f_frsize=cluster, f_blocks=real.f_blocks)
                        with patch.object(refresh.os, 'statvfs', return_value=fake):
                            if admitted:
                                refresh.check_slot_space(sizes)
                            else:
                                with self.assertRaises(refresh.Kept) as refused:
                                    refresh.check_slot_space(sizes)
                                self.assertEqual(str(refused.exception), refresh.SLOT_NO_ROOM.format(
                                    free=(free + held) // refresh.MIB, needed=-(-need // refresh.MIB)))
                                self.assertIn('Then start Emaki again and run: sudo emaki-boot-refresh',
                                              str(refused.exception))

    def test_short_space_refuses_before_any_write(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            before = {str(p): p.read_bytes() for p in f.mapped('/efi').rglob('*') if p.is_file()}
            listing = fw.listing()
            real = os.statvfs(f.mapped('/efi'))
            fake = SimpleNamespace(f_bavail=1, f_frsize=4096, f_blocks=real.f_blocks)
            with patch.object(refresh.os, 'statvfs', side_effect=lambda path: fake if str(path).endswith('/efi')
                              else real.__class__(os.statvfs(path))):
                refresh.refresh(f.identity) if False else None
                with self.assertRaises(refresh.Kept):
                    refresh.check_slot_space({name: 1_000_000 for name in secureboot.slot_files_names()})
            after = {str(p): p.read_bytes() for p in f.mapped('/efi').rglob('*') if p.is_file()}
            self.assertEqual(after, before)
            self.assertEqual(fw.listing(), listing)

    def test_shared_esp_never_writes_the_removable_path(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            with self.shared_esp(f):
                windows_boot = f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes()
                refresh.refresh(f.identity)
                boot_and_complete(self, f, fw)
                self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), windows_boot)
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')

    def test_own_esp_next_to_windows_is_an_emaki_only_esp(self):
        # Since 0.9 Emaki has its own ESP next to Windows (alongside-boot.json names both):
        # slots and EFI/BOOT copies on it, no shared reserve, no write gate, Windows' entry
        # (on Windows' ESP) kept after Emaki's.
        windows_esp = base.WINDOWS_ESP
        record = dict(base.OWN_RECORD, emaki_esp_partuuid=ESP_PARTUUID)
        with machine(self) as (f, fw), \
                base.alongside_record(f.mapped('/var/lib/emaki/alongside-boot.json'), record), \
                patch.object(refresh.shared, 'open_writes',
                             side_effect=AssertionError('the shared write gate opened')):
            payload = installed(f, fw)
            listing = fw.listing
            fw.listing = lambda: '\n'.join(
                line.replace(ESP_PARTUUID, windows_esp) if line.startswith('Boot0000') else line
                for line in listing().split('\n'))
            self.assertIn(windows_esp, fw.listing())
            self.assertFalse(refresh.shared_esp())
            self.assertIsNotNone(refresh.fallback_loader())
            # The space rule of an Emaki-only ESP: the EFI/BOOT copies count, no shared reserve.
            sizes = {name: len(data) for name, data in
                     secureboot.slot_payload(b'G' * 6_000_000, f.mapped(str(secureboot.SHIM_DIR)),
                                             f.mapped(str(secureboot.DER))).items()}
            cluster = 4096
            real = os.statvfs(f.mapped('/efi'))
            held = sum(refresh.slot_usage(slot, cluster) for slot in secureboot.SLOTS)
            held += sum(secureboot.fat_bytes((f.mapped('/efi/EFI/BOOT') / name).stat().st_size, cluster)
                        for name in ('BOOTX64.EFI', 'mmx64.efi', 'grubx64.efi'))
            need = secureboot.slots_need(sizes, cluster, True, 0)
            for free, admitted in ((need - held, True), (need - held - cluster, False)):
                fake = SimpleNamespace(f_bavail=free // cluster, f_frsize=cluster, f_blocks=real.f_blocks)
                with patch.object(refresh.os, 'statvfs', return_value=fake):
                    if admitted:
                        refresh.check_slot_space(sizes)
                    else:
                        with self.assertRaises(refresh.Kept):
                            refresh.check_slot_space(sizes)
            # An update writes slot b; the completed start through it switches and copies EFI/BOOT.
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            self.assertEqual(secureboot.read_slot(f.mapped('/efi'), 'b')['generation'], generation.name)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            files = esp_files(f, 'b')
            image = (generation / 'grub/x86_64-efi/emaki-early.efi').read_bytes()
            self.assertEqual(f.mapped('/efi/EFI/BOOT/grubx64.efi').read_bytes(), image)
            self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), files[secureboot.SHIM])
            self.assertEqual(files[secureboot.SHIM], payload[secureboot.SHIM])
            parsed = secureboot.firmware_entries(fw.listing())
            self.assertEqual(secureboot.active_slot(parsed, ESP_PARTUUID), 'b')
            self.assertEqual(fw.entries['0000'][0], 'Windows Boot Manager')
            self.assertEqual(fw.order[-1], '0000')
            self.assertFalse(f.mapped('/efi/EFI/Microsoft').exists())

    def test_torn_boot_state_never_stops_a_boot(self):
        # boot-state.json is replaced by a FAT rename, which a cut can leave empty; with slots it
        # is only a note, so mark-good, cleanup and the next refresh go on.
        for data in (b'', b'{"good": "', b'[]'):
            with self.subTest(data=data), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                f.mapped('/efi/EFI/Emaki/boot-state.json').write_bytes(data)
                self.assertEqual(boot_and_complete(self, f, fw), 'b')
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')
                f.mapped('/efi/EFI/Emaki/boot-state.json').write_bytes(data)
                f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'changed module')
                refresh.refresh(f.identity)
                self.assertIsNotNone(secureboot.read_slot(f.mapped('/efi'), 'a'))
                self.assert_bootable(f, fw)

    def test_lost_firmware_entries_refuse_and_come_back_after_a_completed_start(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            boot_and_complete(self, f, fw)
            generation, = [p for p in f.mapped('/boot/emaki').iterdir()]
            # A firmware reset drops every Emaki entry; the machine starts through EFI/BOOT.
            fw.entries = {'0000': fw.entries['0000']}
            fw.order, fw.next = ['0000'], None
            f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'changed module')
            with self.assertRaises(refresh.Kept) as kept:
                refresh.refresh(f.identity)
            self.assertEqual(str(kept.exception), refresh.SLOT_NO_ENTRY)
            sbt.reboot(f)
            sbt.booted_from(f, '\\EFI\\BOOT\\BOOTX64.EFI', number=9)
            self.complete_boot(f, generation)
            self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')


class FirmwareOrder(unittest.TestCase):
    """Order drift, the BootNext candidate in BootOrder, full or forgetful NVRAM, MokManager."""
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot
    assert_bootable = SlotChecks.assert_bootable

    def order(self, fw):
        parsed = secureboot.firmware_entries(fw.listing())
        return secureboot.active_slot(parsed, ESP_PARTUUID), fw.order

    def test_windows_moved_first_is_put_back_after_emaki_keeping_every_entry(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            fw.entries['0005'] = ('UEFI PXE', 'MAC(001122334455,0)')
            fw.order = ['0000', '0005', '0001']  # a Windows upgrade put itself first
            fw.next = '0001'  # the person picked Emaki in the firmware menu
            boot_and_complete(self, f, fw)
            self.assertEqual(fw.order, ['0001', '0000', '0005'])
            self.assertIn(refresh.ORDER_RESTORED.format(label='Windows Boot Manager'), f.output.getvalue())
            # The same during an update's switch: the new slot first, Windows and the rest kept.
            refresh.refresh(f.identity)
            fw.order = ['0000'] + [n for n in fw.order if n != '0000']
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            active, order = self.order(fw)
            self.assertEqual(active, 'b')
            self.assertEqual(fw.entries[order[0]], (secureboot.ACTIVE_LABEL, secureboot.slot_entry('b')))
            self.assertEqual(fw.entries[order[1]], (secureboot.PREVIOUS_LABEL, secureboot.slot_entry('a')))
            self.assertEqual(order[2:], ['0000', '0005'])

    def test_a_locked_boot_order_is_left_alone_and_the_person_is_told(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            fw.order, fw.next, fw.locked = ['0000', '0001'], '0001', True
            boot_and_complete(self, f, fw)
            self.assertEqual(fw.order, ['0000', '0001'])
            self.assertIn(refresh.ORDER_LOCKED.format(label='Windows Boot Manager'), f.output.getvalue())

    def test_the_complete_candidate_stands_after_the_known_good_entry_only_for_its_trial(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            seen = []
            original = fw.write

            def record(change):
                original(change)
                # After every variable write: does BootOrder reach slot b, and is b complete?
                reaches = any(secureboot.entry_slot(fw.entries[number][1]) == 'b'
                              for number in fw.order if number in fw.entries)
                seen.append((reaches, secureboot.read_slot(f.mapped('/efi'), 'b') is not None))
            fw.write = record
            refresh.refresh(f.identity)
            candidate = fw.next
            self.assertEqual(fw.order, ['0001', candidate, '0000'])
            # Never in BootOrder before its slot was complete, and in it from that write on.
            self.assertTrue(all(complete for reaches, complete in seen if reaches), seen)
            self.assertEqual([reaches for reaches, _ in seen][-2:], [True, True])
            # The trial is consumed by a start that never completes (MokManager resets the
            # machine): the next start comes through a and does not count as b's trial.
            fw.boot()
            self.assertIsNone(fw.next)
            self.assertEqual(boot_and_complete(self, f, fw), 'a')
            active, order = self.order(fw)
            self.assertEqual(active, 'a')
            self.assertNotIn(candidate, order)  # its trial is over: out of BootOrder, entry kept
            self.assertEqual(fw.entries[candidate][1], secureboot.slot_entry('b'))
            refresh.refresh(f.identity)
            self.assertIn(refresh.TRIAL_FAILED, f.output.getvalue())
            self.assertIsNone(fw.next)

    def test_a_mokmanager_start_never_counts_as_a_successful_trial(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            for loader in ('\\EFI\\Emaki\\b\\mmx64.efi', '\\EFI\\Emaki\\b\\grubx64.efi'):
                sbt.reboot(f)
                sbt.booted_from(f, loader, number=0x20)
                self.complete_boot(f, generation)
                self.assertEqual(self.order(fw)[0], 'a')

    def test_a_full_variable_store_keeps_the_loader_with_advice(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            before = esp_files(f, 'a')
            fw.full = True
            with self.assertRaises(refresh.Kept) as kept:
                refresh.refresh(f.identity)
            self.assertEqual(str(kept.exception), refresh.NVRAM_FULL)
            self.assertEqual((fw.order, fw.next), (['0001', '0000'], None))
            self.assertEqual(esp_files(f, 'a'), before)
            self.assertEqual(boot_and_complete(self, f, fw), 'a')

    def test_every_firmware_write_is_read_back(self):
        for option in ('--create-only', '--bootorder', '--bootnext'):
            with self.subTest(option=option), machine(self) as (f, fw):
                installed(f, fw)
                fw.dropped = {option}
                with self.assertRaises(refresh.Refuse) as refused:
                    refresh.refresh(f.identity)
                self.assertTrue(str(refused.exception).startswith('The firmware did not keep the change to'),
                                refused.exception)
                self.assertEqual(self.assert_bootable(f, fw), 'a')
                self.assertIsNone(fw.next)
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            fw.boot()
            fw.dropped = {'--bootorder'}
            with self.assertRaises(refresh.Refuse):
                refresh.mark_good_slots(f.identity)
            self.assertEqual(self.order(fw)[0], 'a')


def generation_of(f, slot):
    return secureboot.read_slot(f.mapped('/efi'), slot)['generation']


def switched(test, f, fw):
    """The installer's layout, one update, and the switch to b; b's generation directory."""
    installed(f, fw)
    refresh.refresh(f.identity)
    test.assertEqual(boot_and_complete(test, f, fw), 'b')
    test.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')
    return f.mapped('/boot/emaki') / generation_of(f, 'b')


class FixedCut:
    """A random source for Cuts that always picks one way to tear: 'torn' (a FAT rename that
    leaves the target empty), 'before' or 'after' a rename, and half-written files."""

    def __init__(self, mode):
        self.mode = mode

    def choice(self, options):
        return self.mode if self.mode in options else options[0]

    def random(self):
        return 0.0

    def randrange(self, stop):
        return stop // 2


def removable_copies(f):
    removable = f.mapped('/efi/EFI/BOOT')
    return {name: (removable / name).read_bytes() if (removable / name).is_file() else None
            for name in (secureboot.REMOVABLE_SHIM, secureboot.MOK_MANAGER, secureboot.GRUB)}


class ReviewR9S9(unittest.TestCase):
    """The R9S9 review findings of the slot design, each as the review reproduced it."""
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot
    assert_bootable = SlotChecks.assert_bootable

    def active(self, fw):
        return secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID)

    def test_an_inactive_entry_before_the_started_slot_is_never_taken_for_it(self):
        """#1 (P1): an inactive entry of slot a before the active entry of slot b. Firmware
        skips the inactive one and starts b, so b is the active slot: an update rewrites a,
        never b, and after a cut at any write BootOrder still starts the untouched b."""
        def setup(f, fw):
            switched(self, f, fw)
            b_entry, a_entry = [n for n in fw.order if n != '0000']
            fw.order = [a_entry, b_entry, '0000']
            fw.inactive.add(a_entry)
            f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'changed module')
            return esp_files(f, 'b')

        with machine(self) as (f, fw):
            before = setup(f, fw)
            counter = Cuts(fw)
            with counter.active():
                refresh.refresh(f.identity)
            writes = counter.count
            self.assertEqual(esp_files(f, 'b'), before)
            self.assertEqual(self.active(fw), 'b')
            self.assertEqual(secureboot.entry_slot(fw.entries[fw.next][1]), 'a')
            self.assertFalse(fw.inactive & set(fw.entries))  # the inactive entry was withdrawn
        self.assertGreater(writes, 10)
        for point in range(1, writes + 1):
            with self.subTest(point=point), machine(self) as (f, fw):
                before = setup(f, fw)
                with Cuts(fw, point).active():
                    refresh.refresh(f.identity)
                self.assertEqual(esp_files(f, 'b'), before)
                self.assert_bootable(f, fw, ('b', before))
                self.assertEqual(secureboot.entry_slot(fw.entries[fw.started()][1]), 'b')

    def test_the_trial_entry_never_stands_before_the_entry_firmware_starts(self):
        """#1/#7: with an inactive entry of the active slot first in BootOrder, the trial
        entry goes right after the active entry firmware really starts, never before it."""
        with machine(self) as (f, fw):
            installed(f, fw)
            fw.entries['0005'] = (secureboot.ACTIVE_LABEL, secureboot.slot_entry('a'))
            fw.inactive.add('0005')
            fw.order = ['0005', '0001', '0000']
            refresh.refresh(f.identity)
            self.assertEqual(fw.order, ['0005', '0001', fw.next, '0000'])
            self.assertEqual(fw.started(), '0001')

    def test_an_entry_firmware_skips_or_overrides_is_replaced_by_a_clean_one(self):
        """#1/#8: an inactive "Emaki" entry or one with optional data is never kept as the
        active slot's entry: a clean one is created first, then it is deleted."""
        for fault in ('inactive', 'data'):
            with self.subTest(fault=fault), machine(self) as (f, fw):
                installed(f, fw)
                # The installer's entry 0001 became unusable; a clean "Emaki" -> a follows it.
                fw.entries['0002'] = (secureboot.ACTIVE_LABEL, secureboot.slot_entry('a'))
                fw.order = ['0001', '0002', '0000']
                if fault == 'inactive':
                    fw.inactive.add('0001')
                else:
                    fw.data['0001'] = '\\EFI\\Other\\x.efi\0'.encode('utf-16-le')
                boot_and_complete(self, f, fw)
                self.assertNotIn('0001', fw.entries)
                self.assertEqual(fw.order, ['0002', '0000'])
                self.assertEqual(self.active(fw), 'a')
                self.assertEqual(secureboot.entry_slot(fw.entries[fw.started()][1]), 'a')

    def lost_entries(self, case):
        with machine(self) as (f, fw):
            generation = switched(self, f, fw)
            fw.entries = {'0000': fw.entries['0000']}
            fw.order, fw.next = ['0000'], None
            loader, option, release = '\\EFI\\BOOT\\BOOTX64.EFI', None, 'test-kernel'
            if case == 'other-root':
                manifest = generation / 'manifest.json'
                record = json.loads(manifest.read_text())
                record['uuid'] = '00000000-0000-4000-8000-000000000000'
                manifest.write_text(json.dumps(record))
            elif case == 'no-modules':
                release = 'another-kernel'
            elif case == 'other-loader':
                loader = '\\EFI\\Other\\loader.efi'
            elif case == 'fallback-differs':
                f.put(f.mapped('/efi/EFI/BOOT/grubx64.efi'), esp_files(f, 'a')[secureboot.GRUB])
            elif case == 'data':
                option = sbt.load_option(loader, optional='\\EFI\\Other\\x.efi\0'.encode('utf-16-le'))
            elif case == 'removable-media':
                # A firmware's own entry for the disk: a device path and no file.
                option = sbt.raw_option(sbt.disk_node() + sbt.node(0x7f, 0xff, b''), 'UEFI OS')
            elif case in ('ovmf-misc-device', 'misc-device-other-data'):
                # OVMF's own entry for the disk (S9V H-13f): PciRoot(0x0)/Pci(0x2,0x0) and, as data,
                # EDK2's auto-created marker, which EDK2 never passes to the started image.
                pci = sbt.node(2, 1, bytes.fromhex('d041030a00000000')) + sbt.node(1, 1, bytes((0, 2)))
                data = (bytes.fromhex('4eac0881119f594d850ee21a522c59b2') if case == 'ovmf-misc-device'
                        else bytes.fromhex('4eac0881119f594d850ee21a522c59b3'))
                option = sbt.raw_option(pci + sbt.node(0x7f, 0xff, b''), 'UEFI Misc Device', optional=data)
            elif case in ('options.csv', 'OPTIONS.CSV', 'fbx64.efi'):
                # A second-stage override next to the fallback shim (R9S10 #2): shim may start another file.
                f.put(f.mapped('/efi/EFI/BOOT') / case, '\\EFI\\Other\\x.efi,\0'.encode('utf-16-le'))
            sbt.reboot(f)
            efivars = f.mapped('/sys/firmware/efi/efivars')
            if case == 'no-current':
                (efivars / f'BootCurrent-{secureboot.GLOBAL_GUID}').unlink()
            else:
                sbt.booted_from(f, loader, number=9, option=option)
            f.put(f.mapped('/proc/cmdline'), f'root=UUID={f.identity["uuid"]} emaki.generation={generation.name}\n')
            f.mapped('/usr/lib/modules/test-kernel').mkdir(parents=True, exist_ok=True)
            refused = None
            with patch.object(refresh.os, 'uname', return_value=SimpleNamespace(release=release)), \
                    patch.object(refresh, 'LOG', io.StringIO()) as log:
                try:
                    refresh.mark_good(f.identity)
                except refresh.Refuse as error:
                    refused = str(error)
                    if case == 'no-modules':
                        self.assertEqual(refused,
                                         'Files needed by the running system are missing; '
                                         'the startup copy was not switched.')
                        self.assertIn('The running kernel has no installed modules', log.getvalue())
            return fw, refused, f.output.getvalue()

    def test_lost_entries_come_back_only_with_the_whole_proof(self):
        """#2 (P1): recreating lost entries needs the switch's proof (this root, the running
        kernel's modules, the generation's image) and proof that this ESP's EFI/BOOT ran and
        holds that slot's loaders; otherwise nothing is recreated and emaki-boot-repair is named."""
        for case in ('proven', 'removable-media', 'ovmf-misc-device'):
            with self.subTest(case=case):
                fw, refused, _ = self.lost_entries(case)
                self.assertIsNone(refused)
                self.assertEqual(self.active(fw), 'b')
                self.assertEqual(fw.entries[fw.order[0]], (secureboot.ACTIVE_LABEL, secureboot.slot_entry('b')))
                self.assertEqual(fw.order[1:], ['0000'])
        for case in ('other-root', 'no-modules', 'other-loader', 'no-current', 'fallback-differs', 'data',
                     'options.csv', 'OPTIONS.CSV', 'fbx64.efi', 'misc-device-other-data'):
            with self.subTest(case=case):
                fw, refused, output = self.lost_entries(case)
                self.assertEqual((set(fw.entries), fw.order), ({'0000'}, ['0000']), refused)
                if refused is None:
                    self.assertIn(refresh.SLOT_NOT_PROVEN, output)
                    self.assertIn('sudo emaki-boot-repair', refresh.SLOT_NOT_PROVEN)

    def test_a_cut_right_after_bootnext_still_runs_the_completion_service(self):
        """#3 (P2): a fresh Secure Boot install has no boot-state.json; a cut right after the
        first update's BootNext leaves none either, and the trial must still be confirmed."""
        unit = (ROOT / 'systemd/emaki-boot-complete.service').read_text()
        triggers = [line.split('|', 1)[1] for line in unit.splitlines() if line.startswith('ConditionPathExists=|')]
        with machine(self) as (f, fw):
            installed(f, fw)
            f.mapped('/efi/EFI/Emaki/boot-state.json').unlink(missing_ok=True)
            original = fw.write

            def write(change):
                original(change)
                if fw.next is not None:
                    raise Stop()  # the power goes right after BootNext was written
            fw.write = write
            with self.assertRaises(Stop):
                refresh.refresh(f.identity)
            fw.write = original
            self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-state.json').exists())
            self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())
            self.assertTrue(any(f.mapped(path).exists() for path in triggers), triggers)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            self.assertEqual(self.active(fw), 'b')

    def test_a_torn_fallback_copy_is_rewritten_at_the_next_completed_boot(self):
        """#6 (P2): a cut while the switch replaces EFI/BOOT can leave BOOTX64.EFI empty or half
        written; the next completed boot through the active slot rewrites all three copies."""
        def setup(f, fw):
            installed(f, fw)
            # A shim package update: the switch replaces every fallback copy, the shim last.
            f.put(f.mapped(str(secureboot.SHIM_DIR / secureboot.SHIM)), signed_pe(b'S2'))
            refresh.refresh(f.identity)
            fw.boot()
            return f.mapped('/boot/emaki') / generation_of(f, 'b')

        with machine(self) as (f, fw):
            generation = setup(f, fw)
            counter = Cuts(fw)
            with counter.active():
                self.complete_boot(f, generation)
            writes = counter.count
        torn = 0
        for point in range(1, writes + 1):
            for mode in ('torn', 'before', 'after'):
                with self.subTest(point=point, mode=mode), machine(self) as (f, fw):
                    generation = setup(f, fw)
                    with Cuts(fw, point, FixedCut(mode)).active():
                        self.complete_boot(f, generation)
                    shim = removable_copies(f)[secureboot.REMOVABLE_SHIM]
                    torn += shim not in (esp_files(f, 'a')[secureboot.SHIM], esp_files(f, 'b')[secureboot.SHIM])
                    boot_and_complete(self, f, fw)
                    active = self.assert_bootable(f, fw)
                    files = esp_files(f, active)
                    self.assertEqual(removable_copies(f), {secureboot.REMOVABLE_SHIM: files[secureboot.SHIM],
                                                           secureboot.MOK_MANAGER: files[secureboot.MOK_MANAGER],
                                                           secureboot.GRUB: files[secureboot.GRUB]})
                    self.assertFalse([p.name for p in f.mapped('/efi/EFI/BOOT').iterdir() if '.emaki-' in p.name])
                    self.assertFalse(f.mapped('/efi/EFI/Emaki/fallback-update').exists())
                    self.assertNotIn(refresh.FOREIGN, f.output.getvalue())
        self.assertGreater(torn, 0)  # the sweep really tore the fallback shim

    def test_a_load_override_or_another_image_never_proves_a_slot(self):
        """#8 (P2): optional data on the started entry, a stray options.csv in the slot (shim
        reads it), or a slot GRUB that is not the generation's image never switch BootOrder."""
        for fault in ('optional-data', 'options.csv', 'OPTIONS.CSV', 'other-image'):
            with self.subTest(fault=fault), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                candidate = fw.next
                tag = generation_of(f, 'b')
                directory = secureboot.slot_dir(f.mapped('/efi'), 'b')
                if fault == 'optional-data':
                    fw.data[candidate] = '\\EFI\\Other\\x.efi\0'.encode('utf-16-le')
                elif fault == 'other-image':
                    files = {name: (directory / name).read_bytes() for name in secureboot.slot_files_names()}
                    files[secureboot.GRUB] = sbt.pe_image(b'other GRUB') + sbt.SIGNATURE
                    secureboot.write_slot(f.mapped('/efi'), 'b', files, tag)
                else:
                    (directory / fault).write_text('shimx64.efi,Emaki,\\EFI\\Other\\x.efi,Other\n')
                self.assertEqual(fw.boot(), 'b')
                f.put(f.mapped('/proc/cmdline'), f'root=UUID={f.identity["uuid"]} emaki.generation={tag}\n')
                f.mapped('/usr/lib/modules/test-kernel').mkdir(parents=True, exist_ok=True)
                with patch.object(refresh.os, 'uname', return_value=SimpleNamespace(release='test-kernel')), \
                        patch.object(refresh, 'LOG', io.StringIO()) as log:
                    try:
                        refresh.mark_good(f.identity)
                    except refresh.Refuse as error:
                        self.assertEqual(fault, 'other-image', error)
                        self.assertEqual(str(error),
                                         'The startup files do not match; the startup copy was not switched.')
                        self.assertIn(f'The GRUB in boot slot b is not the image of boot generation {tag}',
                                      log.getvalue())
                self.assertEqual(self.active(fw), 'a')
                self.assertEqual(fw.entries[fw.order[0]], (secureboot.ACTIVE_LABEL, secureboot.slot_entry('a')))


class FsckRecords(unittest.TestCase):
    """S9V F-F1 / R9S9 #12: fsck.fat's FSCKnnnn.REC files after a cut, at the next start."""
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot

    def test_records_of_emaki_files_go_and_every_other_record_stays(self):
        for shared in (False, True):
            with self.subTest(shared=shared), machine(self) as (f, fw), ExitStack() as stack:
                installed(f, fw)
                cluster = os.statvfs(f.mapped('/efi')).f_frsize
                shim = esp_files(f, 'a')[secureboot.SHIM]
                unknown = bytes(range(256)) * (2 * cluster // 256)
                if shared:
                    # Never read on a shared ESP: even copies of Emaki's own files stay.
                    records = {'FSCK0000.REC': shim + bytes(-len(shim) % cluster), 'FSCK0002.REC': unknown}
                    for name, data in records.items():
                        (f.mapped('/efi') / name).write_bytes(data)
                        (f.mapped('/efi') / name).chmod(0o444)
                    # The ESP root is Windows' (read-only here); Emaki writes only inside EFI/Emaki.
                    f.mapped('/efi').chmod(0o555)
                    stack.callback(f.mapped('/efi').chmod, 0o755)
                    stack.enter_context(patch.object(refresh, 'shared_esp', return_value=True))
                    stack.enter_context(patch.object(refresh.shared, 'open_writes'))
                refresh.refresh(f.identity)
                if not shared:
                    generation = f.mapped('/boot/emaki') / generation_of(f, 'b')
                    image = (generation / 'grub/x86_64-efi/emaki-early.efi').read_bytes()
                    # The image of an update whose slot a later update rewrote: only /boot has it.
                    stopped = f.mapped('/boot/emaki') / ('c' * 32)
                    f.put(stopped / 'grub/x86_64-efi/emaki-early.efi', image[::-1])
                    f.put(stopped / 'manifest.json', json.dumps({'efi_sha256': refresh.digest(image[::-1])}))
                    # An image whose manifest names another hash, and a generation without one.
                    for name, other in (('d' * 32, {'efi_sha256': '0' * 64}), ('e' * 32, None)):
                        f.put(f.mapped('/boot/emaki') / name / 'grub/x86_64-efi/emaki-early.efi', image[1:] + b'X')
                        if other is not None:
                            f.put(f.mapped('/boot/emaki') / name / 'manifest.json', json.dumps(other))
                    # R9S10 #1: an incomplete slot a whose GRUB a cut left as two bytes.
                    (f.mapped('/efi/EFI/Emaki/a/slot.json')).unlink()
                    f.put(f.mapped('/efi/EFI/Emaki/a/grubx64.efi'), b'MZ')
                    records = {
                        # The whole GRUB of a slot write a cut stopped, the rest of its page zero.
                        'FSCK0000.REC': image + bytes(-len(image) % cluster),
                        # The first cluster of a shim whose write was cut (any letter case).
                        'fsck0001.rec': shim[:cluster],
                        # Something else: maybe the only copy of another file. It stays.
                        'FSCK0002.REC': unknown,
                        # A chain of zeros proves nothing.
                        'FSCK0003.REC': bytes(cluster),
                        'FSCK0004.REC': image[::-1] + bytes(-len(image) % cluster),
                        # Unrelated bytes that start like an EFI image: never a copy of b'MZ'.
                        'FSCK0005.REC': (b'MZ' + bytes(range(256)) * (cluster // 256 + 1))[:cluster],
                        # Images no manifest vouches for.
                        'FSCK0006.REC': image[1:] + b'X' + bytes(-len(image) % cluster)}
                    for name, data in records.items():
                        (f.mapped('/efi') / name).write_bytes(data)
                boot_and_complete(self, f, fw)
                left = {p.name: p.read_bytes() for p in f.mapped('/efi').iterdir() if p.name.upper().startswith('FSCK')}
                output = f.output.getvalue()
                if shared:
                    self.assertEqual(left, records)
                    self.assertIn('Kept FSCK0000.REC (%d bytes): the startup partition is shared with Windows'
                                  % len(records['FSCK0000.REC']), output)
                else:
                    self.assertEqual(left, {name: records[name] for name in
                                            ('FSCK0002.REC', 'FSCK0003.REC', 'FSCK0005.REC', 'FSCK0006.REC')})
                    self.assertIn('Removed FSCK0000.REC (%d bytes): a lost copy of EFI/Emaki/b/grubx64.efi'
                                  % len(records['FSCK0000.REC']), output)
                    self.assertIn('Removed fsck0001.rec', output)
                    self.assertIn('Removed FSCK0004.REC (%d bytes): a lost copy of the emaki-early.efi of %s'
                                  % (len(records['FSCK0004.REC']), 'c' * 32), output)
                    self.assertIn('Kept FSCK0002.REC (%d bytes): its content is not a known Emaki boot file.'
                                  % len(unknown), output)
                self.assertEqual(self.active(fw), 'b')

    active = ReviewR9S9.active

    def test_the_esps_logical_sector_size_comes_from_its_boot_sector(self):
        # R9S11: Linux writes a FAT file in blocks of its logical sector (fs/fat/inode.c).
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            device = Path(directory) / 'esp-device'
            for value, expected in ((512, 512), (4096, 4096), (2048, 2048), (0, 512), (3000, 512)):
                with self.subTest(value=value):
                    device.write_bytes(bytes(11) + value.to_bytes(2, 'little') + bytes(499))
                    with patch.object(refresh, 'mount_info', return_value={'source': str(device)}):
                        self.assertEqual(refresh.esp_sector_size(), expected)
            device.write_bytes(bytes(100))
            with patch.object(refresh, 'mount_info', return_value={'source': str(device)}):
                self.assertEqual(refresh.esp_sector_size(), 512)
            with patch.object(refresh, 'mount_info', return_value={'source': str(Path(directory) / 'gone')}):
                self.assertEqual(refresh.esp_sector_size(), 512)
            with patch.object(refresh, 'mount_info', side_effect=refresh.Refuse('no /efi')):
                self.assertEqual(refresh.esp_sector_size(), 512)

    def test_a_record_with_older_sectors_after_the_file_is_removed(self):
        # R9S11's construction through the refresh: an Emaki-only ESP with 512-byte sectors.
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            cluster = os.statvfs(f.mapped('/efi')).f_frsize
            image = esp_files(f, 'b')[secureboot.GRUB]
            sector_end = -(-len(image) // 512) * 512
            record = image + bytes(sector_end - len(image))
            record += b'\xa5' * (-len(record) % cluster)
            self.assertGreater(len(record), sector_end)
            (f.mapped('/efi') / 'FSCK0000.REC').write_bytes(record)
            with patch.object(refresh, 'esp_sector_size', return_value=512):
                boot_and_complete(self, f, fw)
            self.assertFalse((f.mapped('/efi') / 'FSCK0000.REC').exists())
            self.assertIn('Removed FSCK0000.REC', f.output.getvalue())


class RealSignedSlot(unittest.TestCase):
    """Real grub-mkimage, sbsign and sbverify on the refresh's build path, published in a slot
    (R9S9 #10). Set EMAKI_SBSIGNTOOLS (and EMAKI_GRUB_TOOLS when GRUB lives elsewhere) to
    extracted package roots; skipped when the tools are missing."""
    fixture = sbt.Checks.fixture
    complete_boot = sbt.Checks.complete_boot

    def setUp(self):
        sbsign_root = sbt.tool_root('EMAKI_SBSIGNTOOLS')
        grub_root = sbt.tool_root('EMAKI_GRUB_TOOLS') or sbsign_root or Path('/')
        self.tools = {}
        for name, root in (('sbsign', sbsign_root), ('sbverify', sbsign_root), ('grub-mkimage', grub_root),
                           ('openssl', None)):
            path = root / 'usr/bin' / name if root else None
            found = str(path) if path and path.is_file() else sbt.shutil.which(name)
            if not found:
                self.skipTest('Missing real executable: ' + name)
            self.tools[name] = found
        self.modules = grub_root / 'usr/lib/grub/x86_64-efi'
        self.sbat = grub_root / 'usr/share/grub/sbat.csv'
        if not (self.modules / 'moddep.lst').is_file() or not self.sbat.is_file():
            self.skipTest('Missing the real GRUB x86_64-efi modules or sbat.csv')
        self.env = dict(os.environ)
        if sbsign_root:
            self.env['LD_LIBRARY_PATH'] = str(sbsign_root / 'usr/lib')

    def execute(self, args, input=None):
        args = list(map(str, args))
        if args[0] == 'sh':
            args[4] = self.tools['grub-mkimage']
        elif args[0] in self.tools:
            args[0] = self.tools[args[0]]
        result = sbt.subprocess.run(args, input=input, text=True, capture_output=True, env=self.env)
        if result.returncode:
            raise refresh.Transient(f'{Path(args[0]).name} failed: {result.stderr.strip()[-1800:]}')
        return result.stdout.strip()

    def test_real_build_signs_and_publishes_a_verified_slot(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            # The real key, made as the installer makes it, and the real modules and SBAT data.
            state = f.mapped(str(secureboot.STATE))
            for name in (secureboot.KEY.name, secureboot.CERT.name, secureboot.DER.name):
                (state / name).unlink(missing_ok=True)
            secureboot.generate_key(self.execute, state)
            modules = f.mapped('/usr/lib/grub/x86_64-efi')
            sbt.shutil.rmtree(modules)
            sbt.shutil.copytree(self.modules, modules)
            f.put(f.mapped(str(secureboot.SBAT_SOURCE)), self.sbat.read_bytes())
            machine_run = refresh.run

            def run(arguments):
                args = list(map(str, arguments))
                # Real signing and real signature checks with the machine key; the fixture's
                # shim stand-ins carry no real PKCS#7, so sbverify --list stays the fixture's.
                if args[0] in ('sh', 'sbsign') or args[:2] == ['sbverify', '--cert']:
                    f.commands.append(args)
                    return self.execute(args)
                return machine_run(arguments)

            with patch.object(refresh, 'run', side_effect=run):
                refresh.refresh(f.identity)
                generation = f.mapped('/boot/emaki') / generation_of(f, 'b')
                platform = generation / 'grub/x86_64-efi'
                image = (platform / 'emaki-early.efi').read_bytes()
                early = (platform / 'emaki-early.cfg').read_bytes()
                memdisk = (platform / 'emaki-early.tar').read_bytes()
                refresh.verify_image(image, '(memdisk)/boot/grub', early, memdisk)
                slot_grub = secureboot.slot_dir(f.mapped('/efi'), 'b') / secureboot.GRUB
                self.assertIn('Signature verification OK',
                              self.execute(['sbverify', '--cert', state / secureboot.CERT.name, slot_grub]))
                self.assertEqual(slot_grub.read_bytes(), image)
                sbat = (platform / refresh.SBAT_DATA).read_text()
                self.assertIn('grub.emaki,1,Emaki,grub,', sbat)
                self.assertNotIn('\ngrub,4,', sbat)
                record = secureboot.read_slot(f.mapped('/efi'), 'b')
                self.assertEqual(json.loads((generation / 'manifest.json').read_text())['efi_sha256'],
                                 record['files'][secureboot.GRUB]['sha256'])
                self.assertEqual(esp_files(f, 'b')[secureboot.ESP_CERT], (state / secureboot.DER.name).read_bytes())
                self.assertEqual(secureboot.entry_slot(fw.entries[fw.next][1]), 'b')
                # The trial through b completes and switches BootOrder with the real image's proof.
                self.assertEqual(boot_and_complete(self, f, fw), 'b')
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'b')


class SlotBuild(unittest.TestCase):
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot

    def test_signed_image_with_menu_modules_faces_and_sbat_data(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                generation, = f.mapped('/boot/emaki').iterdir()
                platform = generation / 'grub/x86_64-efi'
                builds = [c for c in f.commands if c[0] == 'sh']
                self.assertEqual(len(builds), 2)
                for build in builds:
                    self.assertEqual(build[5], '--sbat=' + str(platform / refresh.SBAT_DATA))
                    modules = build[build.index('--output=' + str(platform / 'emaki-early.efi')) + 1:]
                    self.assertEqual(tuple(modules), secureboot.signed_image_modules(refresh.IMAGE_MODULES))
                self.assertIn('\ngrub,6,Free Software Foundation,', (platform / refresh.SBAT_DATA).read_text())
                image = (platform / 'emaki-early.efi').read_bytes()
                self.assertTrue(image.endswith(sbt.SIGNATURE))
                self.assertEqual(esp_files(f, 'b')[secureboot.GRUB], image)
                manifest = json.loads((generation / 'manifest.json').read_text())
                self.assertEqual(manifest['efi_sha256'], refresh.digest(image))
                # The slot's GRUB was verified with the machine key before its manifest.
                verify = [c for c in f.commands if c[:2] == ['sbverify', '--cert']]
                self.assertTrue(any(c[-1].endswith('/EFI/Emaki/b/grubx64.efi') for c in verify), verify)

    def test_missing_shim_package_refuses_before_any_write(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            f.mapped(str(secureboot.SHIM_DIR / secureboot.MOK_MANAGER)).unlink()
            before, listing = sbt.esp_tree(f), fw.listing()
            with self.assertRaisesRegex(refresh.Refuse, 'reinstall emaki-shim-signed'):
                refresh.refresh(f.identity)
            self.assertEqual(sbt.esp_tree(f), before)
            self.assertEqual(fw.listing(), listing)

    def test_unsigned_shim_or_grub_is_never_completed(self):
        for damage in ('shim', 'grub'):
            with self.subTest(damage=damage), machine(self) as (f, fw):
                installed(f, fw)
                if damage == 'shim':
                    f.put(f.mapped(str(secureboot.SHIM_DIR / secureboot.SHIM)), sbt.pe_image(b'S', signed=False))
                    stack = patch.object(refresh, 'run', side_effect=lambda args, fake=refresh.run: fake(args))
                else:
                    stack = patch.object(refresh, 'run', side_effect=lambda args, fake=refresh.run: (
                        (_ for _ in ()).throw(refresh.Transient('sbverify failed'))
                        if list(map(str, args[:2])) == ['sbverify', '--cert'] else fake(args)))
                with stack, self.assertRaises(refresh.Refuse):
                    refresh.refresh(f.identity)
                self.assertIsNone(secureboot.read_slot(f.mapped('/efi'), 'b'))
                self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'a')
                self.assertFalse(secureboot.slot_entries(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID)['b'])
                self.assertIsNone(fw.next)


class IntentWithoutKey(unittest.TestCase):
    """Machines without the key keep the in-place transaction; its record never stops recovery."""
    fixture = sbt.Checks.fixture
    complete_boot = sbt.Checks.complete_boot

    def stopped(self, f, condition):
        with sbt.stop_when(condition):
            refresh.refresh(f.identity)
        self.assertTrue(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def assert_clean(self, f):
        self.assertFalse([name for name in sbt.esp_tree(f) if '.emaki-' in name])
        self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def test_unreadable_intent_before_any_rename_is_undone(self):
        for data in (b'', b'{"tag": "0123', b'[]\n'):
            with self.subTest(data=data), self.fixture(False) as f:
                intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                self.stopped(f, intent.exists)
                intent.write_bytes(data)
                sbt.boot_without_generation(f)
                self.assertIn(refresh.TORN_UNDONE, f.output.getvalue())
                self.assert_clean(f)
                self.assertEqual(f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes(),
                                 f.originals['/efi/EFI/Emaki/grubx64.efi'])
                refresh.refresh(f.identity)
                self.assert_clean(f)

    def test_wrongly_shaped_intent_entries_never_crash_recovery(self):
        # R9S8 P2 #5: a valid tag with an entry lacking its hashes raised KeyError at every start.
        shapes = ([{'target': '/efi/EFI/Emaki/trial.env'}],
                  [{'target': '/efi/EFI/Emaki/trial.env', 'old': None, 'new': 'x'}],
                  [{'target': 'relative', 'old': None, 'new': None}],
                  [{'target': '/efi/EFI/Emaki/trial.env', 'old': None, 'new': None, 'new_data': 'zz'}])
        for entries in shapes:
            with self.subTest(entries=entries), self.fixture(False) as f:
                intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                self.stopped(f, intent.exists)
                intent.write_text(json.dumps({'tag': 'a' * 32, 'entries': entries}))
                self.assertIsNone(refresh.intent_record(intent.read_bytes()))
                sbt.boot_without_generation(f)
                self.assert_clean(f)
                sbt.boot_without_generation(f)
                self.assert_clean(f)


class SlotHelpers(unittest.TestCase):
    def test_manifest_shapes_never_raise(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory:
            esp = Path(directory)
            files = {name: name.encode() * 10 for name in secureboot.slot_files_names()}
            record = secureboot.write_slot(esp, 'a', files, 'f' * 32)
            self.assertEqual(record['generation'], 'f' * 32)
            manifest = secureboot.slot_dir(esp, 'a') / secureboot.SLOT_MANIFEST
            good = manifest.read_bytes()
            for bad in (b'', b'{', b'[]', b'null', b'"x"', b'{"version": 1}', b'\xff\xfe',
                        good.replace(b'"slot": "a"', b'"slot": "b"'),
                        good.replace(b'"version": 1', b'"version": true'),
                        good.replace(b'"size": 110', b'"size": "110"', 1),
                        good.replace(b'"size": 110', b'"size": 111', 1),
                        b'[' * 100000):
                manifest.write_bytes(bad)
                self.assertIsNone(secureboot.read_slot(esp, 'a'), bad[:40])
            manifest.write_bytes(good)
            self.assertIsNotNone(secureboot.read_slot(esp, 'a'))
            self.assertIsNone(secureboot.read_slot(esp, 'b'))

    def test_certificate_table_entries(self):
        image = sbt.pe_image(b'S')
        self.assertTrue(secureboot.pe_certificates_present(image))
        self.assertTrue(refresh.signed_pe_image(image))
        pe = 64
        optional = pe + 24
        start, size = struct.unpack_from('<II', image, optional + 112 + 32)
        zeroed = image[:start] + bytes(size) + image[start + size:]
        self.assertFalse(secureboot.pe_certificates_present(zeroed))
        self.assertFalse(refresh.signed_pe_image(zeroed))
        one_byte = bytearray(image[:start + 1])
        struct.pack_into('<II', one_byte, optional + 112 + 32, start, 1)
        self.assertFalse(refresh.signed_pe_image(bytes(one_byte)))
        wrong_type = bytearray(image)
        struct.pack_into('<H', wrong_type, start + 6, 1)
        self.assertFalse(refresh.signed_pe_image(bytes(wrong_type)))
        self.assertFalse(refresh.signed_pe_image(sbt.pe_image(b'S', signed=False)))

    def test_firmware_listing_parser(self):
        listing = ('BootCurrent: 0002\nBootNext: 0003\nTimeout: 0 seconds\nBootOrder: 0002,0000\n'
                   f'Boot0000* Windows Boot Manager\tHD(1,GPT,{ESP_PARTUUID},0x800,0x32000)/File({WINDOWS})\n'
                   f'Boot0002* Emaki\tPciRoot(0x0)/Pci(0x1,0x0)/HD(1,GPT,{ESP_PARTUUID},0x800,0x32000)/'
                   '\\EFI\\Emaki\\a\\shimx64.efi\n'
                   f'Boot0003  Emaki (previous)\tHD(1,GPT,{ESP_PARTUUID},0x800,0x32000)/'
                   'File(\\EFI\\EMAKI\\B\\SHIMX64.EFI)\n')
        parsed = secureboot.firmware_entries(listing)
        self.assertEqual(parsed['order'], ['0002', '0000'])
        self.assertEqual((parsed['next'], parsed['current']), ('0003', '0002'))
        self.assertEqual(secureboot.slot_entries(parsed, ESP_PARTUUID), {'a': ['0002'], 'b': ['0003']})
        self.assertEqual(secureboot.active_slot(parsed, ESP_PARTUUID), 'a')
        self.assertEqual(secureboot.slot_entries(parsed, '00000000-0000-0000-0000-000000000000'),
                         {'a': [], 'b': []})
        for broken in ('BootOrder: 0001,0001\n', 'BootOrder: 0001\nBootOrder: 0002\n', 'BootNext: xyz\n',
                       'Boot0001* A\tx\nBoot0001* B\ty\n'):
            self.assertIsNone(secureboot.firmware_entries(broken), broken)

    def test_space_rule_counts_every_destination(self):
        sizes = {secureboot.SHIM: 1_036_008, secureboot.MOK_MANAGER: 874_360,
                 secureboot.GRUB: 6_000_000, secureboot.ESP_CERT: 4096}
        cluster = 4096
        one = secureboot.slot_bytes(sizes.values(), cluster)
        self.assertGreaterEqual(one, sum(secureboot.fat_bytes(size, cluster) for size in sizes.values()))
        plain = secureboot.slots_need(sizes, cluster)
        self.assertGreaterEqual(plain, 2 * one)
        removable = secureboot.slots_need(sizes, cluster, removable=True)
        self.assertGreaterEqual(removable - plain, sum(secureboot.fat_bytes(sizes[name], cluster)
                                                       for name in (secureboot.SHIM, secureboot.MOK_MANAGER,
                                                                    secureboot.GRUB)))
        staged = secureboot.slots_need(sizes, cluster, stage=sizes[secureboot.GRUB])
        self.assertGreaterEqual(staged - plain, secureboot.fat_bytes(sizes[secureboot.GRUB], cluster))


class RepairRootFiles(unittest.TestCase):
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot

    def test_optional_menu_parser_conditions(self):
        path = '($root)/@/boot/grub/custom.cfg'
        pattern = r'/@/boot/([\w.+/-]+)(?=[\s\'";]|$)'
        optional = (
            'if false; then echo no; elif [ -e "{path}" ]; then source "{path}"; fi',
            'if false; then echo no; elif [ ! -f "{path}" ]; then echo no; else . "{path}"; fi',
            'if [ -e "{path}" ]; then . "{path}"; fi',
        )
        required = (
            'if [ -e "{path}" ]; true; then source "{path}"; fi',
            'if [ ! -e "{path}" ]; false; then echo no; else source "{path}"; fi',
            'if false; then echo no; elif [ -f "{path}" ]; true; then source "{path}"; fi',
            'if [ -e "{path}" ]; "then"; source "{path}"; fi',
            'if [ -e "{path}" ]; then echo no; "else"; source "{path}"; fi',
            '"if" [ -e "{path}" ]; then source "{path}"; fi',
            r'\if [ -e "{path}" ]; then source "{path}"; fi',
            'if [ -e "{path}" ]; then "fi"; source "{path}"; fi',
            'if [ -e "{path}" ]; then echo no; elif true; then source "{path}"; fi',
            'if [ -e "{path}" ]; then . "{path}"; fi; . "{path}"',
            'if [ ! -e "{path}" ]; then . "{path}"; fi',
        )
        for fragment in optional + required:
            with self.subTest(fragment=fragment):
                result = refresh.repair_required_references(fragment.format(path=path), pattern)
                self.assertEqual(result, set() if fragment in optional else {'grub/custom.cfg'})

    def test_rebuild_checks_writes_before_withdrawing_entries(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            before = fw.listing()
            with patch.object(secureboot, 'read_slot', return_value=None), \
                    patch.object(refresh, 'repair_generations', return_value=[None]), \
                    patch.object(refresh, 'repair_image', return_value=b'image'), \
                    patch.object(refresh, 'slot_payload', return_value={}), \
                    patch.object(refresh, 'check_slot_space'), \
                    patch.object(refresh, 'esp_writable', side_effect=refresh.shared.Refused(refresh.shared.BAD_RECORD)):
                with self.assertRaisesRegex(refresh.shared.Refused, 'without the installation record'):
                    refresh.repair_slots(f.identity)
            self.assertEqual(fw.listing(), before)
            self.assertIn('Firmware entries can be repaired', refresh.shared.BAD_RECORD)

    def test_kept_message_is_flushed_before_later_refusal(self):
        with machine(self) as (f, fw):
            data = io.BytesIO()
            output = io.TextIOWrapper(data, encoding='utf-8')
            with patch.object(refresh, 'LOG', io.StringIO()), \
                    patch.object(refresh, 'discover', side_effect=refresh.Kept('The loader was kept.')), \
                    patch.object(refresh.sys, 'stdout', output):
                self.assertEqual(refresh.refresh_attempt(False), 0)
                data.write(b'The later safety check refused.\n')
                output.flush()
                self.assertEqual(data.getvalue(), b'The loader was kept.\nThe later safety check refused.\n')

    def test_missing_guarded_menu_is_repaired(self):
        for name in ('grub-btrfs.cfg', 'custom.cfg'):
            for test in ('-e', '-f'):
                for negated in (False, True):
                    with self.subTest(name=name, test=test, negated=negated), machine(self) as (f, fw):
                        installed(f, fw)
                        refresh.refresh(f.identity)
                        self.assertEqual(boot_and_complete(self, f, fw), 'b')
                        target = f'($root){f.identity["fsroot"]}/boot/grub/{name}'
                        if negated:
                            fragment = (f'if [ ! {test} "{target}" ]; then\n  echo ""\nelse\n'
                                        f"  submenu 'Snapshots' {{\n    configfile \"{target}\"\n  }}\nfi\n")
                        else:
                            fragment = f'if [ {test} {target} ]; then source "{target}"; fi\n'
                        menu = f.mapped('/boot/grub/grub.cfg')
                        menu.write_text(menu.read_text() + fragment)
                        f.mapped('/boot/grub/' + name).unlink(missing_ok=True)
                        before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
                        fw.entries = {'0000': ('Windows Boot Manager', WINDOWS)}
                        fw.order, fw.next = ['0000'], None
                        f.commands.clear()
                        refresh.repair_slots(f.identity)
                        self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
                        self.assertEqual(boot_and_complete(self, f, fw), 'b')
                        self.assertIn(refresh.REPAIR_DONE.format(slot='b'), f.output.getvalue())
                        self.assertFalse(any(args[0] in ('sbsign', 'grub-mkimage') for args in f.commands))

    def test_guard_does_not_hide_required_menu_references(self):
        for fragment in (
                'if [ -e "{path}" ]; true; then source "{path}"; fi\n',
                'if false; then echo no; elif [ -e "{path}" ]; true; then source "{path}"; fi\n',
                '"if" [ -e "{path}" ]; then source "{path}"; fi\n',
                'if [ -e "{path}" ]; then source "{path}"; fi; source "{path}"\n',
                'if [ -e "{path}" ]; then echo present; else source "{path}"; fi\n',
                'if [ ! -f "{path}" ]; then source "{path}"; fi\n',
                'if [ -e "{path}.other" ]; then source "{path}"; fi\n',
                'if [ -e "{path}" ]; then if true; then echo present; fi; fi; source "{path}"\n'):
            with self.subTest(fragment=fragment), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                target = f'($root){f.identity["fsroot"]}/boot/grub/grub-btrfs.cfg'
                menu = f.mapped('/boot/grub/grub.cfg')
                menu.write_text(menu.read_text() + fragment.format(path=target))
                f.mapped('/boot/grub/grub-btrfs.cfg').unlink(missing_ok=True)
                before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
                firmware_before = fw.listing()
                with self.assertRaises(refresh.Refuse):
                    refresh.repair_slots(f.identity)
                self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
                self.assertEqual(fw.listing(), firmware_before)

    def test_older_root_restore_then_usb_repair(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            menu = f.mapped('/boot/grub/grub.cfg')
            before = menu.read_bytes()
            refresh.refresh(f.identity)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            generation, = f.mapped('/boot/emaki').iterdir()
            refresh.shutil.rmtree(generation)
            menu.write_bytes(before)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            slots_before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
            fw.entries = {'0000': ('Windows Boot Manager', WINDOWS)}
            fw.order, fw.next = ['0000'], None
            refresh.repair_slots(f.identity)
            self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, slots_before)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            self.assertIn(refresh.REPAIR_DONE.format(slot='b'), f.output.getvalue())

    def test_canonical_menu_does_not_need_unused_generation_metadata(self):
        for missing in ('manifest.json', 'grub/grub.cfg'):
            with self.subTest(missing=missing), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                self.assertEqual(boot_and_complete(self, f, fw), 'b')
                generation, = f.mapped('/boot/emaki').iterdir()
                (generation / missing).unlink()
                before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
                f.commands.clear()
                refresh.repair_slots(f.identity)
                self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
                self.assertIn(refresh.REPAIR_KEPT.format(slot='b'), f.output.getvalue())
                self.assertFalse(any(args[0] in ('sbsign', 'grub-mkimage') for args in f.commands))

    def test_canonical_menu_requires_files_rewritten_into_a_generation(self):
        for name in ('grub/fonts/menu.pf2', 'grub/themes/emaki/theme.txt',
                     'grub/grub-btrfs.cfg'):
            with self.subTest(name=name), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                generation, = f.mapped('/boot/emaki').iterdir()
                path = generation / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'menu resource')
                logical = Path('/boot/emaki') / generation.name
                reference = refresh.rewrite_menu(
                    f'source ($root){f.identity["fsroot"]}/boot/{name}\n', logical,
                    f.identity['fsroot'])
                menu = f.mapped('/boot/grub/grub.cfg')
                menu.write_text(menu.read_text() + reference)
                path.unlink()
                before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
                firmware_before = fw.listing()
                with self.assertRaises(refresh.Refuse):
                    refresh.repair_slots(f.identity)
                self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
                self.assertEqual(fw.listing(), firmware_before)

    def test_unusable_installer_slot_does_not_hide_valid_generation_slot(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            f.mapped('/boot/grub/grub.cfg').unlink()
            before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
            f.commands.clear()
            refresh.repair_slots(f.identity)
            self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
            self.assertEqual(boot_and_complete(self, f, fw), 'b')
            self.assertFalse(any(secureboot.entry_slot(loader) == 'a'
                                 for label, loader in fw.entries.values()))
            self.assertFalse(any(args[0] in ('sbsign', 'grub-mkimage') for args in f.commands))

    def test_unusable_slots_are_rebuilt_from_an_available_generation(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            f.mapped('/boot/grub/grub.cfg').unlink()
            payload = {name: (secureboot.slot_dir(f.mapped('/efi'), 'b') / name).read_bytes()
                       for name in (secureboot.SHIM, secureboot.MOK_MANAGER,
                                    secureboot.GRUB, secureboot.ESP_CERT)}
            secureboot.write_slot(f.mapped('/efi'), 'b', payload, '0' * 32)
            refresh.repair_slots(f.identity)
            self.assertEqual(secureboot.read_slot(f.mapped('/efi'), 'a')['generation'], generation.name)
            self.assertEqual(boot_and_complete(self, f, fw), 'a')
            self.assertFalse(any(secureboot.entry_slot(loader) == 'b'
                                 for label, loader in fw.entries.values()))
            self.assertIn(refresh.REPAIR_WRITTEN.format(slot='a'), f.output.getvalue())

    def test_complete_slot_with_missing_root_files_is_refused_without_changes(self):
        for missing in ('generation', 'manifest.json', 'grub/grub.cfg',
                        '/boot/vmlinuz-linux', '/boot/initramfs-linux.img'):
            with self.subTest(missing=missing), machine(self) as (f, fw):
                installed(f, fw)
                refresh.refresh(f.identity)
                self.assertEqual(boot_and_complete(self, f, fw), 'b')
                generation, = f.mapped('/boot/emaki').iterdir()
                if not missing.startswith('/'):
                    f.mapped('/boot/grub/grub.cfg').unlink()
                if missing == 'generation':
                    refresh.shutil.rmtree(generation)
                else:
                    target = f.mapped(missing) if missing.startswith('/') else generation / missing
                    target.unlink()
                before = {slot: esp_files(f, slot) for slot in secureboot.SLOTS}
                firmware_before = fw.listing()
                f.commands.clear()
                f.output.seek(0)
                f.output.truncate()
                with self.assertRaises(refresh.Refuse):
                    refresh.repair_slots(f.identity)
                self.assertEqual({slot: esp_files(f, slot) for slot in secureboot.SLOTS}, before)
                self.assertEqual(fw.listing(), firmware_before)
                self.assertNotIn(refresh.REPAIR_DONE.format(slot='b'), f.output.getvalue())
                self.assertFalse(any(args[0] in ('sbsign', 'grub-mkimage') for args in f.commands))

    def test_complete_installer_slot_requires_its_root_menu(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            f.mapped('/boot/grub/grub.cfg').unlink()
            with self.assertRaises(refresh.Refuse):
                refresh.repair_slots(f.identity)

    def test_complete_slot_requires_generation_of_this_root(self):
        with machine(self) as (f, fw):
            installed(f, fw)
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            path = generation / 'manifest.json'
            manifest = json.loads(path.read_text())
            manifest['uuid'] = '00000000-0000-0000-0000-000000000000'
            path.write_text(json.dumps(manifest))
            f.mapped('/boot/grub/grub.cfg').unlink()
            with self.assertRaises(refresh.Refuse):
                refresh.repair_slots(f.identity)


if __name__ == '__main__':
    unittest.main()
