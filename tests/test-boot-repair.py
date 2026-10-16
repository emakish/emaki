#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The repair of an installed Emaki that cannot start, from the installation USB.

emaki-boot-repair (installer/emaki_installer/repair.py) finds the installed root and its
startup partition, mounts them with the machine key's subvolume read-only, and runs the
installed system's `emaki-boot-refresh --repair-slots` inside it (refresh.repair_slots). These
tests drive the repair in the slot fixture's simulated NVRAM (tests/test-boot-slots.py) and the
USB side with fake block devices and mounts: no firmware variable is ever written.
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('slots_fixture', ROOT / 'tests/test-boot-slots.py')
slots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slots)
refresh, secureboot, sbt = slots.refresh, slots.secureboot, slots.sbt
ESP_PARTUUID = slots.ESP_PARTUUID
from emaki_installer import repair  # noqa: E402  (the fixture puts installer/ on sys.path)

FIRMWARE_UPDATER = '\\EFI\\fwupd\\fwupdx64.efi'


def lose_entries_and_put_windows_first(fw):
    """A firmware reset or Windows: Emaki's entries are gone, Windows starts first."""
    for number in [number for number in fw.entries if number not in ('0000',)]:
        del fw.entries[number]
    fw.entries['0007'] = ('Linux Firmware Updater', FIRMWARE_UPDATER)
    fw.order, fw.next = ['0000', '0007'], None


def damage(f, slot, how):
    directory = secureboot.slot_dir(f.mapped('/efi'), slot)
    if how == 'grub':
        (directory / secureboot.GRUB).write_bytes(b'MZ short')
    else:
        (directory / secureboot.SLOT_MANIFEST).unlink()
    assert secureboot.read_slot(f.mapped('/efi'), slot) is None


def updated_once(test, f, fw):
    """The installer's slot a, one update into slot b and a completed start through it:
    b is active, a the previous slot, both complete. Returns the generation b holds."""
    slots.installed(f, fw)
    refresh.refresh(f.identity)
    test.assertEqual(slots.boot_and_complete(test, f, fw), 'b')
    generation, = f.mapped('/boot/emaki').iterdir()
    return generation


class InstalledSideChecks(unittest.TestCase):
    fixture = sbt.Checks.fixture
    shared_esp = sbt.Checks.shared_esp
    complete_boot = sbt.Checks.complete_boot

    def emaki_entries(self, fw):
        return {number: entry for number, entry in fw.entries.items() if entry[1].startswith('\\EFI\\Emaki\\')}

    def assert_plain_entries(self, f):
        """Every entry the repair created carries no optional data: --create-only with the
        disk, the partition, the label and the loader, nothing else."""
        created = [args for args in f.commands if args[:2] == ['efibootmgr', '--create-only']]
        self.assertTrue(created)
        for args in created:
            self.assertEqual(args[2::2], ['--disk', '--part', '--label', '--loader'], args)
            self.assertEqual(len(args), 10, args)
        for args in f.commands:
            if args[0] == 'efibootmgr':
                self.assertFalse({'-@', '--append-binary-args', '--unicode', '-u', '--create', '-c'} & set(args), args)

    def assert_key_never_copied(self, f):
        key = f.mapped(str(secureboot.KEY))
        secret = key.read_bytes()
        holders = [path for path in f.root.rglob('*')
                   if path.is_file() and not path.is_symlink() and secret in path.read_bytes()]
        self.assertEqual(holders, [key])

    def test_both_slots_damaged_entries_lost_and_windows_first(self):
        with slots.machine(self) as (f, fw):
            generation = updated_once(self, f, fw)
            damage(f, 'a', 'grub')
            damage(f, 'b', 'manifest')
            lose_entries_and_put_windows_first(fw)
            f.commands.clear()
            refresh.repair_slots(f.identity)
            # Slot a is rebuilt from the installed packages, signed and complete; b stays as it was.
            record = secureboot.read_slot(f.mapped('/efi'), 'a')
            self.assertIsNotNone(record)
            self.assertEqual(record['generation'], generation.name)
            self.assertIsNone(secureboot.read_slot(f.mapped('/efi'), 'b'))
            files = slots.esp_files(f, 'a')
            image = (generation / 'grub/x86_64-efi/emaki-repair.efi').read_bytes()
            self.assertEqual(files[secureboot.GRUB], image)
            self.assertTrue(image.endswith(sbt.SIGNATURE))
            self.assertEqual(files[secureboot.SHIM], f.mapped(str(secureboot.SHIM_DIR / secureboot.SHIM)).read_bytes())
            self.assertEqual(files[secureboot.ESP_CERT], f.mapped(str(secureboot.DER)).read_bytes())
            self.assertTrue(any(args[0] == 'sh' and 'grub-mkimage' in args for args in f.commands))
            self.assertTrue(any(args[0] == 'sbsign' for args in f.commands))
            self.assertTrue(any(args[0] == 'sbverify' and '--cert' in args
                                and args[-1].endswith('EFI/Emaki/a/grubx64.efi') for args in f.commands))
            # "Emaki" starts slot a first; Windows and the other foreign entry keep their order.
            emaki = self.emaki_entries(fw)
            self.assertEqual(list(emaki.values()), [(secureboot.ACTIVE_LABEL, secureboot.slot_entry('a'))])
            first, = emaki
            self.assertEqual(fw.order, [first, '0000', '0007'])
            self.assertEqual(fw.entries['0000'], ('Windows Boot Manager', slots.WINDOWS))
            self.assertIsNone(fw.next)
            self.assert_plain_entries(f)
            self.assertIn(refresh.REPAIR_WRITTEN.format(slot='a'), f.output.getvalue())
            self.assertIn(refresh.REPAIR_DONE.format(slot='a'), f.output.getvalue())
            self.assert_key_never_copied(f)
            # The repaired slot starts, and a completed start keeps it.
            self.assertEqual(slots.boot_and_complete(self, f, fw), 'a')
            self.assertEqual(secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID), 'a')

    def test_a_complete_slot_is_kept_not_rewritten(self):
        with slots.machine(self) as (f, fw):
            generation = updated_once(self, f, fw)
            damage(f, 'a', 'manifest')
            kept = slots.esp_files(f, 'b')
            damaged = slots.esp_files(f, 'a')
            lose_entries_and_put_windows_first(fw)
            f.commands.clear()
            refresh.repair_slots(f.identity)
            self.assertEqual(slots.esp_files(f, 'b'), kept)
            self.assertEqual(slots.esp_files(f, 'a'), damaged)
            self.assertFalse(any(args[0] in ('sh', 'grub-mkimage', 'sbsign') for args in f.commands))
            self.assertFalse((generation / 'grub/x86_64-efi/emaki-repair.efi').exists())
            emaki = self.emaki_entries(fw)
            self.assertEqual(list(emaki.values()), [(secureboot.ACTIVE_LABEL, secureboot.slot_entry('b'))])
            self.assertEqual(fw.order, [*emaki, '0000', '0007'])
            self.assert_plain_entries(f)
            self.assertIn(refresh.REPAIR_KEPT.format(slot='b'), f.output.getvalue())

    def test_both_slots_complete_get_both_entries(self):
        with slots.machine(self) as (f, fw):
            updated_once(self, f, fw)
            before = {slot: slots.esp_files(f, slot) for slot in secureboot.SLOTS}
            lose_entries_and_put_windows_first(fw)
            refresh.repair_slots(f.identity)
            self.assertEqual({slot: slots.esp_files(f, slot) for slot in secureboot.SLOTS}, before)
            emaki = self.emaki_entries(fw)
            labels = {entry: number for number, entry in emaki.items()}
            first = labels[(secureboot.ACTIVE_LABEL, secureboot.slot_entry('b'))]
            labels[(secureboot.PREVIOUS_LABEL, secureboot.slot_entry('a'))]
            self.assertEqual(len(emaki), 2)
            # The previous slot's entry was not in BootOrder: it stays outside it.
            self.assertEqual(fw.order, [first, '0000', '0007'])

    def test_stale_entries_go_and_emaki_moves_first(self):
        with slots.machine(self) as (f, fw):
            updated_once(self, f, fw)
            damage(f, 'a', 'manifest')
            # Windows moved itself first; a's entry is still listed and a duplicate exists.
            fw.entries['0009'] = (secureboot.ACTIVE_LABEL, secureboot.slot_entry('b'))
            fw.order = ['0000'] + [number for number in fw.order if number != '0000']
            refresh.repair_slots(f.identity)
            emaki = self.emaki_entries(fw)
            self.assertEqual(list(emaki.values()), [(secureboot.ACTIVE_LABEL, secureboot.slot_entry('b'))])
            self.assertEqual(fw.order, [*emaki, '0000'])

    def test_a_firmware_that_drops_the_order_fails_clearly(self):
        with slots.machine(self) as (f, fw):
            updated_once(self, f, fw)
            lose_entries_and_put_windows_first(fw)
            run = fw.run
            fw.run = lambda args: '' if '--bootorder' in args else run(args)
            with self.assertRaises(refresh.Refuse) as caught:
                refresh.repair_slots(f.identity)
            self.assertIn('"Emaki" is not first in the startup order', str(caught.exception))
            self.assertTrue(str(caught.exception).startswith('The firmware did not keep the startup entries'))

    def test_a_pending_trial_is_cleared_so_the_next_start_is_the_repaired_slot(self):
        # R9S10 #4: an update offered slot b by BootNext; a repair before its trial selects a.
        with slots.machine(self) as (f, fw):
            slots.installed(f, fw)
            refresh.refresh(f.identity)
            self.assertIsNotNone(fw.next)
            fw.entries['0007'] = ('UEFI Shell', 'FvFile(7c04a583-9e3e-4f1c-ad65-e05268d0b4d1)')
            refresh.repair_slots(f.identity)
            selected = secureboot.active_slot(secureboot.firmware_entries(fw.listing()), ESP_PARTUUID)
            self.assertIsNone(fw.next)
            self.assertEqual(fw.boot(), selected)
            self.assertIn('0007', fw.entries)
        # Another system's one-time start is not Emaki's to clear.
        with slots.machine(self) as (f, fw):
            slots.installed(f, fw)
            fw.next = '0000'
            refresh.repair_slots(f.identity)
            self.assertEqual(fw.next, '0000')

    def test_a_firmware_that_keeps_the_pending_trial_fails_clearly(self):
        with slots.machine(self) as (f, fw):
            updated_once(self, f, fw)
            damage(f, 'a', 'grub')
            fw.next = next(number for number, entry in fw.entries.items() if entry[1] == secureboot.slot_entry('a'))
            run = fw.run
            fw.run = lambda args: '' if '--delete-bootnext' in args else run(args)
            with self.assertRaises(refresh.Refuse) as caught:
                refresh.repair_slots(f.identity)
            self.assertIn('the next start still names an Emaki entry', str(caught.exception))

    def test_no_generation_to_rebuild_writes_nothing(self):
        with slots.machine(self) as (f, fw):
            generation = updated_once(self, f, fw)
            damage(f, 'a', 'grub')
            damage(f, 'b', 'grub')
            # A GRUB package changed since every generation: none can be rebuilt with it.
            f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'another GRUB version')
            before = {slot: slots.esp_files(f, slot) for slot in secureboot.SLOTS}
            entries, order = dict(fw.entries), list(fw.order)
            with patch.object(refresh, 'LOG', io.StringIO()) as log, \
                    self.assertRaises(refresh.Refuse) as caught:
                refresh.repair_slots(f.identity)
            self.assertEqual(str(caught.exception), refresh.REPAIR_NO_GENERATION)
            self.assertIn('No boot generation of this Emaki can be rebuilt with its GRUB package',
                          log.getvalue())
            self.assertNotIn('GRUB', str(caught.exception))
            self.assertEqual({slot: slots.esp_files(f, slot) for slot in secureboot.SLOTS}, before)
            self.assertEqual((fw.entries, fw.order), (entries, order))
            self.assertTrue((generation / 'manifest.json').is_file())

    def test_before_the_first_update_the_installers_loader_is_rebuilt(self):
        with slots.machine(self) as (f, fw):
            slots.installed(f, fw)
            # grub-install's modules in /boot/grub, as the installer leaves them.
            for module in f.mapped('/usr/lib/grub/x86_64-efi').glob('*.mod'):
                f.put(f.mapped('/boot/grub/x86_64-efi') / module.name, module.read_bytes())
            damage(f, 'a', 'grub')
            lose_entries_and_put_windows_first(fw)
            refresh.repair_slots(f.identity)
            record = secureboot.read_slot(f.mapped('/efi'), 'a')
            self.assertIsNotNone(record)
            self.assertIsNone(record['generation'])
            image = f.mapped('/boot/grub/x86_64-efi/emaki-repair.efi').read_bytes()
            self.assertIn(b'source ($root)/boot/grub/grub.cfg', image)
            self.assertEqual(slots.esp_files(f, 'a')[secureboot.GRUB], image)
            first, = self.emaki_entries(fw)
            self.assertEqual(fw.order, [first, '0000', '0007'])

    def test_a_machine_without_the_key_is_refused(self):
        with slots.machine(self) as (f, fw):
            slots.installed(f, fw)
            f.mapped(str(secureboot.KEY)).unlink()
            with patch.object(refresh, 'key_missing', return_value=False):
                with self.assertRaises(refresh.Refuse) as caught:
                    refresh.repair_slots(f.identity)
            self.assertEqual(str(caught.exception), refresh.REPAIR_NOT_SIGNING)

    def test_main_runs_the_repair_in_a_chroot_and_on_the_usb(self):
        # Only --repair-slots passes the early return for the live system and chroots.
        with patch.object(refresh, 'repair_main', return_value=0) as repair_main, \
                patch.object(refresh.subprocess, 'run') as detect:
            self.assertEqual(refresh.main(['--repair-slots']), 0)
        repair_main.assert_called_once_with()
        detect.assert_not_called()

    def test_the_installed_repair_refuses_copied_disks_too(self):
        # R9S10 #3: inside the installed root the refresh's own check for copied disks runs
        # before anything is written (check_refresh_storage, as every boot refresh does).
        cloned = refresh.Refuse('The root filesystem UUID must match its mounted block devices; '
                                'check for cloned disks.')
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as directory, \
                patch.object(refresh, 'LOG_PATH', str(Path(directory) / 'refresh.log')), \
                patch.object(refresh.os, 'geteuid', return_value=0), \
                patch.object(refresh, 'discover', return_value={'uuid': FSUUID, 'luks': None}), \
                patch.object(refresh, 'check_refresh_storage', side_effect=cloned) as storage, \
                patch.object(refresh, 'repair_slots') as repair_slots, \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(refresh.repair_main(), 1)
        storage.assert_called_once_with({'uuid': FSUUID, 'luks': None})
        repair_slots.assert_not_called()
        self.assertIn('check for cloned disks', err.getvalue())


KEY = b'-----BEGIN PRIVATE KEY-----\nrepair test machine key\n-----END PRIVATE KEY-----\n'
FSUUID = '11111111-2222-4333-8444-555555555555'
OTHER_FSUUID = '66666666-7777-4888-9999-aaaaaaaaaaaa'
LUKS_UUID = '99999999-2222-4333-8444-555555555555'
ESP_UUID = 'ABCD-1234'
# Windows' own ESP next to Emaki's (installer mode 'alongside' since 0.9.0).
WINDOWS_ESP_UUID = '5A6B-7C8D'
WINDOWS_ESP_PARTUUID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
MARKER = 'var/lib/emaki/alongside-boot.json'


def installed_tree(top, uuid, refresh_text='--repair-slots'):
    """A btrfs filesystem's top level holding an installed Emaki: @, @log, @secureboot."""
    root = top / '@'
    (root / 'usr/lib/emaki').mkdir(parents=True)
    (root / 'usr/lib/emaki/os-release').write_text('NAME="Emaki"\nID=arch\nVARIANT_ID=emaki\n')
    (root / 'usr/lib/emaki/boot/emaki_boot').mkdir(parents=True)
    (root / 'usr/lib/emaki/boot/emaki_boot/refresh.py').write_text(f"parser.add_argument('{refresh_text}')\n")
    (root / 'etc').mkdir()
    (root / 'etc/fstab').write_text(
        f'UUID={uuid}\t/\tbtrfs\trw,noatime,compress=zstd:3,subvol=/@\t0 0\n'
        f'UUID={uuid}\t/var/log\tbtrfs\trw,noatime,subvol=/@log\t0 0\n'
        f'UUID={uuid}\t/var/lib/emaki/secureboot\tbtrfs\trw,noatime,subvol=/@secureboot\t0 0\n'
        f'UUID={ESP_UUID}\t/efi\tvfat\trw,relatime,fmask=0077,dmask=0077\t0 2\n')
    for name in ('var/lib/emaki/secureboot', 'var/log', 'efi'):
        (root / name).mkdir(parents=True)
    (top / '@log').mkdir()
    (top / '@secureboot').mkdir()
    (top / '@secureboot/machine.key').write_bytes(KEY)
    return top


class Machine:
    """Block devices, mounts and arch-chroot of a live system, as the repair sees them."""

    def __init__(self, test, work, devices, filesystems, esp, chroot_status=0, key_read_only=True):
        self.test, self.work, self.devices, self.filesystems, self.esp = test, work, devices, filesystems, esp
        self.chroot_status, self.key_read_only = chroot_status, key_read_only
        self.commands, self.mounted, self.calls, self.options = [], [], [], {}
        self.sources = {}

    def attach(self, target, source):
        target = Path(target)
        self.test.assertTrue(target.is_dir() and not target.is_symlink(), target)
        self.test.assertEqual(list(target.iterdir()), [], f'{target} is not an empty mountpoint')
        # Real mounts are directories, not symlinks. Move the fixture tree into view so
        # containment checks exercise real directory entries without privileged mounts.
        source = Path(source)
        source.rename(target)
        self.sources[str(target)] = source

    def run(self, argv):
        args = [str(item) for item in argv]
        self.commands.append(args)
        if args[0] == 'lsblk':
            return json.dumps({'blockdevices': self.devices})
        if args[0] == 'mount':
            target, rest = args[-1], args[1:-1]
            options = rest[rest.index('-o') + 1].split(',') if '-o' in rest else []
            if '--bind' in rest:
                source = Path(rest[rest.index('--bind') + 1])
                self.attach(target, os.path.realpath(source))
            else:
                device = rest[-1]
                top = self.filesystems.get(device) if device != self.esp[0] else None
                if device == self.esp[0]:
                    self.attach(target, self.esp[1])
                elif top is None:
                    raise repair.RepairError(f'mount failed: wrong fs type on {device}')
                else:
                    sub = next((item.split('=', 1)[1].strip('/') for item in options
                                if item.startswith('subvol=')), '')
                    self.attach(target, top / sub if sub else top)
            self.mounted.append(target)
            self.options[target] = options
            return ''
        if args[0] == 'umount':
            target = args[1]
            self.test.assertEqual(self.mounted[-1], target, 'unmounted out of order')
            self.mounted.pop()
            Path(target).rename(self.sources.pop(target))
            Path(target).mkdir()
            return ''
        if args[0] == 'findmnt':
            target = args[-1]
            ro = 'ro' in self.options[target] and (self.key_read_only or
                                                     not target.endswith('/secureboot'))
            return ('ro' if ro else 'rw') + ',nosuid,nodev,noexec\n'
        raise AssertionError('unexpected command: ' + repr(args))

    def call(self, argv):
        args = [str(item) for item in argv]
        self.calls.append(args)
        root = Path(args[2] if args[1] == '-r' else args[1])
        # Inside the chroot the installed system finds its key where it always is, read-only.
        self.test.assertEqual((root / 'var/lib/emaki/secureboot/machine.key').read_bytes(), KEY)
        self.test.assertTrue((root / 'efi').is_dir())
        return self.chroot_status


def device(path, fstype, uuid=None, partuuid=None, children=None):
    item = {'path': path, 'type': 'part', 'fstype': fstype, 'uuid': uuid, 'partuuid': partuuid}
    if children:
        item['children'] = children
    return item


class LiveSideChecks(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='boot-repair-', dir=evidence)
        self.work = Path(self.directory.name)
        self.base = self.work / 'run'
        self.base.mkdir()
        self.live = self.work / 'archiso'
        self.live.mkdir()
        self.esp = self.work / 'esp'
        self.esp.mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def fresh(self):
        """A new, empty machine for the next subtest."""
        self.tearDown()
        self.setUp()

    def repair(self, machine, *argv, live=True):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(repair.os, 'geteuid', return_value=0), redirect_stdout(out), redirect_stderr(err):
            status = repair.main(list(argv), machine.run, machine.call,
                                 self.live if live else self.work / 'missing', self.base)
        return status, out.getvalue(), err.getvalue()

    def standard(self, **options):
        top = installed_tree(self.work / 'vda2', FSUUID)
        self.record_own_esp(top)
        devices = [device('/dev/sr0', 'iso9660', '2026-10-15-00-00-00-00'),
                   device('/dev/vda1', 'vfat', ESP_UUID, ESP_PARTUUID),
                   device('/dev/vda2', 'btrfs', FSUUID)]
        return Machine(self, self.work, devices, {'/dev/vda2': top}, ('/dev/vda1', self.esp), **options), top

    def record_own_esp(self, top):
        (top / '@' / MARKER).write_text(json.dumps({
            'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID,
            'emaki_esp_partuuid': ESP_PARTUUID}) + '\n')

    def assert_clean(self, machine, root='vda2'):
        self.assertEqual(machine.mounted, [])
        self.assertEqual(list(self.base.iterdir()), [])
        # The key exists only where it was; no copy anywhere else in this machine.
        holders = [path for path in self.work.rglob('*')
                   if path.is_file() and not path.is_symlink() and KEY in path.read_bytes()]
        self.assertEqual(holders, [self.work / root / '@secureboot/machine.key'])

    def test_finds_mounts_with_the_key_read_only_and_runs_the_installed_repair(self):
        machine, top = self.standard()
        status, out, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        self.assertIn(repair.REPAIRING.format(root='/dev/vda2', esp='/dev/vda1'), out)
        self.assertIn(repair.DONE, out)
        mounts = [args for args in machine.commands if args[0] == 'mount']
        root = mounts[2][-1]
        self.assertEqual(machine.calls, [['arch-chroot', '-r', root, 'emaki-boot-refresh', '--repair-slots']])
        # Both probes are read-only; then @, @log, @secureboot read-only, the ESP.
        self.assertEqual(mounts.pop(1)[:6],
                         ['mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', '/dev/vda1'])
        self.assertEqual(mounts[0][:5], ['mount', '-t', 'btrfs', '-o', 'ro,subvolid=5'])
        self.assertEqual(mounts[1], ['mount', '-t', 'btrfs', '-o', 'subvol=/@', '/dev/vda2', root])
        self.assertEqual(mounts[2], ['mount', '-t', 'btrfs', '-o', 'nosuid,nodev,noexec,subvol=/@log',
                                     '/dev/vda2', root + '/var/log'])
        self.assertEqual(mounts[3], ['mount', '-t', 'btrfs', '-o', 'ro,nosuid,nodev,noexec,subvol=/@secureboot',
                                     '/dev/vda2', root + '/var/lib/emaki/secureboot'])
        self.assertEqual(mounts[4], ['mount', '-t', 'vfat', '-o', 'rw,nosuid,nodev,noexec', '/dev/vda1',
                                     root + '/efi'])
        self.assertIn(['findmnt', '--noheadings', '--output', 'OPTIONS', '--mountpoint',
                       root + '/var/lib/emaki/secureboot'], machine.commands)
        # Everything is unmounted, newest first.
        unmounts = [args[1] for args in machine.commands if args[0] == 'umount']
        self.assertEqual(unmounts[2:], [root + '/efi', root + '/var/lib/emaki/secureboot', root + '/var/log', root])
        self.assert_clean(machine)

    def test_symlinked_mount_directories_and_key_parents_are_refused(self):
        for relative in ('var', 'efi', 'var/log', 'var/lib', 'var/lib/emaki',
                         'var/lib/emaki/secureboot'):
            with self.subTest(path=relative):
                self.fresh()
                machine, top = self.standard()
                # Keep discovery independent of the corrupt tree so this exercises the
                # writable mount preparation, including paths containing the ESP record.
                system = repair.installed_in(top / '@', {
                    'path': '/dev/vda2', 'uuid': FSUUID, 'fstype': 'btrfs'})
                system['inspected_esp'] = os.path.realpath('/dev/vda1')
                outside = self.work / 'outside'
                outside.mkdir()
                (outside / 'sentinel').write_text('unchanged')
                target = top / '@' / relative
                shutil.rmtree(target)
                target.symlink_to(outside, target_is_directory=True)
                mounts = repair.Mounts(lambda argv: mount_run(argv))
                escaped = []

                def mount_run(argv):
                    if argv[0] == 'mount' and Path(argv[-1]).resolve().is_relative_to(outside):
                        escaped.append([str(item) for item in argv])
                        raise repair.RepairError('outside mount attempted')
                    return machine.run(argv)

                root = self.base / 'root'
                root.mkdir()
                try:
                    with self.assertRaisesRegex(repair.RepairError, 'directory'):
                        repair.mount_system(mounts, system, {
                            'path': '/dev/vda1', 'partuuid': ESP_PARTUUID}, root)
                finally:
                    self.assertTrue(mounts.close())
                self.assertEqual(escaped, [])
                self.assertEqual(sorted(str(path.relative_to(outside)) for path in outside.rglob('*')),
                                 ['sentinel'])
                self.assertEqual((outside / 'sentinel').read_text(), 'unchanged')

    def test_arch_chroot_mount_targets_cannot_escape_through_symlinks(self):
        for relative in ('proc', 'sys', 'sys/firmware', 'sys/firmware/efi',
                         'sys/firmware/efi/efivars', 'dev', 'dev/pts', 'dev/shm', 'run', 'tmp'):
            with self.subTest(path=relative):
                self.fresh()
                machine, top = self.standard()
                outside = self.work / 'outside'
                outside.mkdir()
                (outside / 'sentinel').write_text('unchanged')
                target = top / '@' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(outside, target_is_directory=True)
                status, _, err = self.repair(machine)
                self.assertEqual(status, 1, err)
                self.assertIn('not a safe directory', err)
                self.assertEqual(machine.calls, [])
                self.assertEqual(list(outside.iterdir()), [outside / 'sentinel'])
                self.assertEqual((outside / 'sentinel').read_text(), 'unchanged')
                self.assert_clean(machine)

    def test_arch_chroot_leaves_resolver_symlinks_alone(self):
        machine, top = self.standard()
        outside = self.work / 'resolver'
        outside.write_text('unchanged')
        (top / '@/etc/resolv.conf').symlink_to(outside)
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        self.assertEqual(machine.calls[0][:2], ['arch-chroot', '-r'])
        self.assertEqual(outside.read_text(), 'unchanged')
        self.assert_clean(machine)

    def test_windows_loader_overrides_every_writable_esp_record(self):
        for record in ('absent', 'manual', 'alongside'):
            for named in ((), ('--esp', '/dev/vda1')):
                with self.subTest(record=record, named=named):
                    self.fresh()
                    machine, top = self.standard()
                    if record != 'alongside':
                        (top / '@' / MARKER).unlink()
                    if record == 'manual':
                        (top / '@' / repair.OWN_ESP).write_text(json.dumps({
                            'version': 1, 'emaki_esp_partuuid': ESP_PARTUUID}))
                    loader = self.esp / 'EFI/Microsoft/Boot/bootmgfw.efi'
                    loader.parent.mkdir(parents=True)
                    loader.write_bytes(b'MZ Windows Boot Manager')
                    status, _, err = self.repair(machine, *named)
                    fats = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
                    self.assertTrue(fats)
                    self.assertTrue(all('ro' in args[4].split(',') for args in fats), fats)
                    self.assertEqual(status, 0, err)
                    self.assertEqual(len(machine.calls), 1)
                    self.assertTrue(fats[-1][-1].endswith('/root/efi'))
                    self.assertEqual(loader.read_bytes(), b'MZ Windows Boot Manager')
                    self.assert_clean(machine)

    def test_failed_probe_unmount_stops_discovery_and_retries_cleanup(self):
        for installed in (False, True):
            for persistent in (False, True):
                with self.subTest(installed=installed, persistent=persistent):
                    self.fresh()
                    machine, top = self.standard()
                    if not installed:
                        (top / '@/usr/lib/emaki/os-release').unlink()
                    machine.devices.append(device('/dev/vdb2', 'btrfs', 'other-root'))
                    original_run = machine.run
                    attempts = []

                    def failing_unmount(argv):
                        if argv[0] == 'umount':
                            attempts.append(str(argv[-1]))
                            if persistent or len(attempts) == 1:
                                raise repair.RepairError('device is busy')
                        return original_run(argv)

                    machine.run = failing_unmount
                    status, out, err = self.repair(machine)
                    self.assertEqual(status, 1)
                    self.assertIn('could not be unmounted', err)
                    self.assertNotIn(repair.NONE_FOUND, err)
                    self.assertNotIn(repair.NAMED_NOT_EMAKI.format(device='/dev/vda2'), err)
                    self.assertNotIn(repair.DONE, out)
                    self.assertEqual(machine.calls, [])
                    mounts = [args for args in machine.commands if args[0] == 'mount']
                    self.assertEqual(len(mounts), 1, 'a failed cleanup must stop probing')
                    self.assertEqual(len(attempts), 2, 'the outer cleanup retries the tracked mount')
                    self.assertEqual(len(machine.mounted), int(persistent))
                    if not persistent:
                        self.assertEqual(list(self.base.iterdir()), [])

    def test_a_shared_esp_is_mounted_as_the_installed_system_mounts_it(self):
        machine, top = self.standard()
        fstab = top / '@/etc/fstab'
        fstab.write_text(fstab.read_text().replace('vfat\trw,relatime', 'vfat\tro,noatime'))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        esp = [args for args in machine.commands if args[0] == 'mount' and '/dev/vda1' in args]
        self.assertEqual(esp[0][4], 'ro,nosuid,nodev,noexec')

    def test_no_installed_system_is_a_clear_refusal_and_writes_nothing(self):
        plain = self.work / 'vda2'
        (plain / 'etc').mkdir(parents=True)
        (plain / 'etc/fstab').write_text('UUID=x / ext4 rw 0 1\n')
        devices = [device('/dev/vda1', 'vfat', ESP_UUID, ESP_PARTUUID), device('/dev/vda2', 'ext4', FSUUID)]
        machine = Machine(self, self.work, devices, {'/dev/vda2': plain}, ('/dev/vda1', self.esp))
        status, out, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.NONE_FOUND, err)
        self.assertEqual(machine.calls, [])
        mounts = [args for args in machine.commands if args[0] == 'mount']
        self.assertEqual(mounts, [['mount', '-t', 'ext4', '-o', 'ro', '/dev/vda2', mounts[0][-1]]])
        self.assertEqual(list(self.esp.iterdir()), [])
        self.assertEqual(machine.mounted, [])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_a_locked_disk_is_named(self):
        devices = [device('/dev/vda1', 'vfat', ESP_UUID, ESP_PARTUUID),
                   device('/dev/vda2', 'crypto_LUKS', '99999999-2222-4333-8444-555555555555')]
        machine = Machine(self, self.work, devices, {}, ('/dev/vda1', self.esp))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.LOCKED.format(device='/dev/vda2'), err)
        self.assertEqual(machine.calls, [])

    def test_copied_locked_encrypted_disks_are_refused_before_unlock_advice(self):
        devices = [device('/dev/vda2', 'crypto_LUKS', LUKS_UUID),
                   device('/dev/vdb2', 'crypto_LUKS', LUKS_UUID)]
        machine = Machine(self, self.work, devices, {}, ('/dev/vda1', self.esp))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.CLONED.format(devices='/dev/vda2, /dev/vdb2'), err)
        self.assertNotIn('unlock it', err)
        self.assertNotIn('same Emaki', err)
        self.assertIn('same file system or encrypted disk', err)
        self.assertEqual(machine.calls, [])
        self.assertEqual(self.writable_mounts(machine), [])

    def test_two_installed_systems_need_a_name(self):
        machine, top = self.standard()
        other = installed_tree(self.work / 'vdb1', OTHER_FSUUID)
        self.record_own_esp(other)
        machine.devices.append(device('/dev/vdb1', 'btrfs', OTHER_FSUUID))
        machine.filesystems['/dev/vdb1'] = other
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.SEVERAL.format(devices='/dev/vda2, /dev/vdb1', first='/dev/vda2'), err)
        self.assertEqual(machine.calls, [])
        # Named, the one system is repaired.
        status, _, err = self.repair(machine, '--root', '/dev/vdb1')
        self.assertEqual(status, 0, err)
        self.assertEqual(len(machine.calls), 1)
        status, _, err = self.repair(machine, '--root', '/dev/vda1')
        self.assertEqual(status, 1)
        self.assertIn(repair.NAMED_NOT_EMAKI.format(device='/dev/vda1'), err)

    def writable_mounts(self, machine):
        return [args for args in machine.commands if args[0] == 'mount' and not any(
            option == 'ro' for item in args[1:] for option in item.split(','))]

    def test_two_devices_with_one_root_uuid_are_refused_before_any_writable_mount(self):
        # R9S10 #3: a copied disk. Deduplicating by UUID looked at one of them and repaired it.
        machine, _ = self.standard()
        machine.devices.append(device('/dev/vdb1', 'btrfs', FSUUID))
        machine.filesystems['/dev/vdb1'] = installed_tree(self.work / 'vdb1', FSUUID)
        for named in (None, '/dev/vda2', '/dev/vdb1'):
            with self.subTest(named=named):
                status, _, err = self.repair(machine, *(('--root', named) if named else ()))
                self.assertEqual(status, 1, err)
                self.assertIn(repair.CLONED.format(devices='/dev/vda2, /dev/vdb1'), err)
                self.assertEqual(machine.calls, [])
                self.assertEqual(self.writable_mounts(machine), [])
                self.assertEqual(machine.mounted, [])
                self.assertEqual(list(self.base.iterdir()), [])

    def test_a_copied_encrypted_disk_is_refused(self):
        machine, top = self.standard()
        mapper = device('/dev/mapper/emaki-root', 'btrfs', FSUUID)
        machine.devices[2] = device('/dev/vda2', 'crypto_LUKS', LUKS_UUID, children=[mapper])
        machine.devices.append(device('/dev/vdb2', 'crypto_LUKS', LUKS_UUID))
        machine.filesystems = {'/dev/mapper/emaki-root': top}
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1, err)
        self.assertIn(repair.CLONED.format(devices='/dev/vda2, /dev/vdb2'), err)
        self.assertEqual(machine.calls, [])
        self.assertEqual(self.writable_mounts(machine), [])

    def test_one_device_listed_under_two_parents_is_one_system(self):
        machine, _ = self.standard()
        machine.devices.append(dict(machine.devices[2]))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        self.assertEqual(len(machine.calls), 1)

    def test_a_key_that_is_not_read_only_stops_before_the_repair(self):
        machine, _ = self.standard(key_read_only=False)
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.KEY_NOT_READ_ONLY, err)
        self.assertEqual(machine.calls, [])
        self.assert_clean(machine)

    def test_a_failed_repair_still_unmounts_everything(self):
        machine, _ = self.standard(chroot_status=1)
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.FAILED, err)
        self.assert_clean(machine)

    def test_an_older_installed_emaki_is_refused(self):
        machine, top = self.standard()
        (top / '@/usr/lib/emaki/boot/emaki_boot/refresh.py').write_text("parser.add_argument('--check')\n")
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.TOO_OLD, err)
        self.assertEqual(machine.calls, [])
        self.assert_clean(machine)

    def test_a_missing_esp_is_named(self):
        machine, _ = self.standard()
        machine.devices = [item for item in machine.devices if item['path'] != '/dev/vda1']
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.NO_ESP.format(source='UUID=' + ESP_UUID), err)
        self.assertEqual(machine.calls, [])

    def alongside(self, marker='own', windows_uuid=WINDOWS_ESP_UUID):
        """A disk installed next to Windows: Windows' ESP (/dev/vda1, Windows Boot Manager on
        it), Windows (/dev/vda2), Emaki's own ESP labelled EMAKI (/dev/vda3) mounted rw at /efi,
        Emaki's root (/dev/vda4). Windows' ESP is listed first, so a first-vfat choice takes it.
        marker: 'own' (the installer's record since 0.9.0), 'shared' (the old record), 'damaged'
        or None."""
        top = installed_tree(self.work / 'vda4', FSUUID)
        self.windows = self.work / 'windows-esp'
        boot = self.windows / 'EFI/Microsoft/Boot'
        boot.mkdir(parents=True)
        (boot / 'bootmgfw.efi').write_bytes(b'MZ Windows Boot Manager')
        record = {'own': {'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID,
                          'emaki_esp_partuuid': ESP_PARTUUID, 'windows_esp_uuid': windows_uuid},
                  'shared': {'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID}}.get(marker)
        if record is not None:
            (top / '@' / MARKER).write_text(json.dumps(record) + '\n')
        elif marker == 'damaged':
            (top / '@' / MARKER).write_text('{"version": 1, "esp_partuuid": ')
        devices = [device('/dev/sr0', 'iso9660', '2026-10-15-00-00-00-00'),
                   device('/dev/vda1', 'vfat', windows_uuid, WINDOWS_ESP_PARTUUID),
                   device('/dev/vda2', 'ntfs', '0123456789ABCDEF'),
                   device('/dev/vda3', 'vfat', ESP_UUID, ESP_PARTUUID),
                   device('/dev/vda4', 'btrfs', FSUUID)]
        return Machine(self, self.work, devices, {'/dev/vda4': top}, ('/dev/vda3', self.esp)), top

    def windows_tree(self):
        return {str(path.relative_to(self.windows)): path.read_bytes()
                for path in sorted(self.windows.rglob('*')) if path.is_file()}

    def assert_windows_esp_never_mounted(self, machine):
        self.assertEqual([args for args in machine.commands if args[0] == 'mount' and '/dev/vda1' in args], [])

    def test_next_to_windows_emaki_s_own_esp_is_mounted_and_windows_esp_never(self):
        # 0.9.0: Emaki has its own ESP next to Windows'. The repair takes the one the installed
        # fstab mounts at /efi, proves its recorded partition GUID, and never mounts Windows' ESP.
        for marker in ('own',):
            with self.subTest(marker=marker):
                self.fresh()
                machine, _ = self.alongside(marker)
                before = self.windows_tree()
                status, out, err = self.repair(machine)
                self.assertEqual(status, 0, err)
                self.assertIn(repair.REPAIRING.format(root='/dev/vda4', esp='/dev/vda3'), out)
                root = machine.calls[0][2]
                self.assertIn(['mount', '-t', 'vfat', '-o', 'rw,nosuid,nodev,noexec', '/dev/vda3',
                               root + '/efi'], machine.commands)
                self.assert_windows_esp_never_mounted(machine)
                self.assertEqual(self.windows_tree(), before)
                self.assert_clean(machine, 'vda4')

    def assert_unproven_esp_refused(self, machine, *args):
        status, _, err = self.repair(machine, *args)
        self.assertEqual(self.writable_mounts(machine), [])
        self.assertEqual(machine.calls, [])
        self.assertEqual(status, 1)
        self.assertIn('Nothing was changed.', err)
        self.assert_clean(machine, 'vda4')

    def test_damaged_record_never_mounts_an_esp_writable(self):
        for record in ('{', 'null', '{"version": 2}',
                       json.dumps({'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID,
                                   'emaki_esp_partuuid': WINDOWS_ESP_PARTUUID}),
                       json.dumps({'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID,
                                   'emaki_esp_partuuid': 'not-a-partition-guid'})):
            for named in ((), ('--esp', '/dev/vda1'), ('--esp', '/dev/vda3')):
                with self.subTest(record=record, named=named):
                    self.fresh()
                    machine, top = self.alongside()
                    (top / '@' / MARKER).write_text(record)
                    machine.filesystems['/dev/vda1'] = self.windows
                    self.assert_unproven_esp_refused(machine, *named)

    def test_erase_without_record_uses_read_only_fstab_and_esp_probe(self):
        machine, top = self.standard()
        (top / '@' / MARKER).unlink()
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        mounts = [args for args in machine.commands if args[0] == 'mount']
        self.assertIn('ro', mounts[0][4].split(','))
        fats = [args for args in mounts if 'vfat' in args]
        self.assertEqual([args[4] for args in fats],
                         ['ro,nosuid,nodev,noexec', 'rw,nosuid,nodev,noexec'])
        self.assertLess(machine.commands.index(fats[0]),
                        machine.commands.index(self.writable_mounts(machine)[0]))
        self.assert_clean(machine)

    def test_manual_without_record_resolves_fstab_partuuid(self):
        machine, top = self.standard()
        (top / '@' / MARKER).unlink()
        fstab = top / '@/etc/fstab'
        fstab.write_text(fstab.read_text().replace('UUID=' + ESP_UUID, 'PARTUUID=' + ESP_PARTUUID))
        status, _, err = self.repair(machine, '--esp', '/dev/vda1')
        self.assertEqual(status, 0, err)
        fats = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
        self.assertEqual([args[4].split(',')[0] for args in fats], ['ro', 'rw'])
        self.assert_clean(machine)

    def test_windows_loader_without_record_uses_read_only_shared_repair(self):
        machine, top = self.standard()
        (top / '@' / MARKER).unlink()
        loader = self.esp / 'EFI/Microsoft/Boot/bootmgfw.efi'
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b'MZ Windows Boot Manager')
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        fats = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
        self.assertEqual([args[4] for args in fats], ['ro,nosuid,nodev,noexec'] * 2)
        self.assertEqual(len(machine.calls), 1)
        self.assertEqual(loader.read_bytes(), b'MZ Windows Boot Manager')
        self.assert_clean(machine)

    def test_partial_windows_directory_is_shared_even_without_loader(self):
        for recorded in (False, True):
            with self.subTest(recorded=recorded):
                self.fresh()
                machine, top = self.standard()
                if not recorded:
                    (top / '@' / MARKER).unlink()
                microsoft = self.esp / 'EFI/Microsoft'
                microsoft.mkdir(parents=True)
                status, _, err = self.repair(machine)
                self.assertEqual(status, 0, err)
                fats = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
                self.assertEqual([args[4] for args in fats], ['ro,nosuid,nodev,noexec'] * 2)
                self.assert_clean(machine)

    def test_no_record_ambiguous_fstab_cannot_be_overridden_by_name(self):
        for source in ('UUID', 'PARTUUID'):
            with self.subTest(source=source):
                self.fresh()
                machine, top = self.standard()
                (top / '@' / MARKER).unlink()
                if source == 'PARTUUID':
                    fstab = top / '@/etc/fstab'
                    fstab.write_text(fstab.read_text().replace('UUID=' + ESP_UUID,
                                                              'PARTUUID=' + ESP_PARTUUID))
                machine.devices.append(device('/dev/vdb1', 'vfat', ESP_UUID, ESP_PARTUUID))
                status, _, err = self.repair(machine, '--esp', '/dev/vda1')
                self.assertEqual(status, 1)
                self.assertIn(repair.NO_ESP.format(source=source + '=' +
                              (ESP_UUID if source == 'UUID' else ESP_PARTUUID)), err)
                self.assertEqual(self.writable_mounts(machine), [])
                self.assertEqual(machine.calls, [])
                self.assert_clean(machine)

    def test_new_all_layout_record_uses_strict_path(self):
        for recorded in (ESP_PARTUUID, OTHER_FSUUID):
            with self.subTest(recorded=recorded):
                self.fresh()
                machine, top = self.standard()
                (top / '@' / MARKER).unlink()
                (top / '@/var/lib/emaki/boot-esp.json').write_text(json.dumps({
                    'version': 1, 'emaki_esp_partuuid': recorded}))
                status, _, err = self.repair(machine, '--esp', '/dev/vda1')
                self.assertEqual(status, int(recorded != ESP_PARTUUID), err)
                fats = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
                self.assertEqual([args[4].split(',')[0] for args in fats],
                                 ['ro', 'rw'] if recorded == ESP_PARTUUID else [])
                self.assert_clean(machine)

    def test_missing_record_does_not_allow_a_named_unrelated_esp(self):
        machine, _ = self.alongside(None)
        machine.filesystems['/dev/vda1'] = self.windows
        self.assert_unproven_esp_refused(machine, '--esp', '/dev/vda1')

    def test_shared_record_blocks_writable_windows_esp_even_without_loader(self):
        machine, top = self.standard()
        (top / '@' / MARKER).write_text(json.dumps({'version': 1, 'esp_partuuid': ESP_PARTUUID}))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.WINDOWS_ESP.format(device='/dev/vda1'), err)
        self.assertEqual(self.writable_mounts(machine), [])
        self.assertEqual(machine.calls, [])
        self.assert_clean(machine)

    def test_duplicate_fstab_efi_rows_are_ambiguous(self):
        machine, top = self.standard()
        (top / '@' / MARKER).unlink()
        fstab = top / '@/etc/fstab'
        fstab.write_text(fstab.read_text() + f'PARTUUID={ESP_PARTUUID} /efi vfat rw 0 2\n')
        status, _, err = self.repair(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.ESP_UNPROVEN, err)
        self.assertEqual(self.writable_mounts(machine), [])
        self.assert_clean(machine)

    def test_damaged_or_conflicting_all_layout_record_never_falls_back(self):
        for record in ('{', json.dumps({'version': 2}),
                       json.dumps({'version': 1, 'emaki_esp_partuuid': OTHER_FSUUID})):
            with self.subTest(record=record):
                self.fresh()
                machine, top = self.standard()
                (top / '@/var/lib/emaki/boot-esp.json').write_text(record)
                status, _, err = self.repair(machine)
                self.assertEqual(status, 1)
                self.assertIn(repair.ESP_UNPROVEN, err)
                self.assertEqual(self.writable_mounts(machine), [])
                self.assertEqual(machine.calls, [])
                self.assert_clean(machine)

    def test_a_cloned_own_esp_guid_is_refused_even_when_named(self):
        machine, _ = self.alongside()
        machine.devices.append(device('/dev/vdb1', 'vfat', '1234-5678', ESP_PARTUUID))
        self.assert_unproven_esp_refused(machine, '--esp', '/dev/vda3')

    def test_recordless_esp_inspection_must_finish_read_only_and_unmount(self):
        for failure in ('mount', 'read-only', 'unmount'):
            with self.subTest(failure=failure):
                self.fresh()
                machine, top = self.standard()
                (top / '@' / MARKER).unlink()
                original_run = machine.run
                failed = False

                def failing_run(argv):
                    nonlocal failed
                    is_mount = argv[0] == 'mount' and 'vfat' in argv
                    is_readonly = argv[0] == 'findmnt'
                    is_unmount = argv[0] == 'umount' and str(argv[-1]) in machine.mounted \
                        and machine.sources[str(argv[-1])] == self.esp
                    if not failed:
                        if failure == 'read-only' and is_readonly:
                            failed = True
                            return 'rw\n'
                        if (failure == 'mount' and is_mount) or (failure == 'unmount' and is_unmount):
                            failed = True
                            raise repair.RepairError('inspection failed')
                    return original_run(argv)

                machine.run = failing_run
                status, _, err = self.repair(machine)
                self.assertEqual(status, 1, err)
                self.assertTrue(failed)
                self.assertEqual(self.writable_mounts(machine), [])
                self.assertEqual(machine.calls, [])
                self.assert_clean(machine)

    def test_unrelated_named_fat_is_refused_before_any_writable_mount(self):
        machine, _ = self.alongside()
        third = self.work / 'third-esp'
        third.mkdir()
        machine.devices.append(device('/dev/vdb1', 'vfat', '1234-5678', OTHER_FSUUID))
        machine.filesystems['/dev/vdb1'] = third
        self.assert_unproven_esp_refused(machine, '--esp', '/dev/vdb1')

    def test_own_record_named_partition_can_recover_a_changed_fat_uuid(self):
        machine, top = self.alongside()
        fstab = top / '@/etc/fstab'
        fstab.write_text(fstab.read_text().replace('UUID=' + ESP_UUID, 'UUID=0000-0000'))
        status, _, err = self.repair(machine, '--esp', '/dev/vda3')
        self.assertEqual(status, 0, err)
        self.assert_windows_esp_never_mounted(machine)
        self.assert_clean(machine, 'vda4')

    def test_valid_record_repairs_with_an_unrelated_duplicate_fat_id(self):
        machine, _ = self.alongside()
        machine.devices.append(device('/dev/vdb1', 'vfat', ESP_UUID, OTHER_FSUUID))
        status, _, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        self.assertEqual(len(machine.calls), 1)
        mounts = [args for args in machine.commands if args[0] == 'mount' and 'vfat' in args]
        self.assertEqual(len(mounts), 2)
        self.assertEqual(mounts[0][4:6], ['ro,nosuid,nodev,noexec', '/dev/vda3'])
        self.assertEqual(mounts[1][4:6], ['rw,nosuid,nodev,noexec', '/dev/vda3'])
        self.assert_clean(machine, 'vda4')

    def test_next_to_windows_one_fat_volume_id_on_both_esps_takes_emaki_s_own(self):
        # Two FAT volume ids can be equal (32 bits). The installer's record names Emaki's own
        # ESP and Windows' by partition GUID: the repair takes Emaki's and never mounts Windows'.
        machine, _ = self.alongside(windows_uuid=ESP_UUID)
        before = self.windows_tree()
        status, out, err = self.repair(machine)
        self.assertEqual(status, 0, err)
        self.assertIn(repair.REPAIRING.format(root='/dev/vda4', esp='/dev/vda3'), out)
        self.assert_windows_esp_never_mounted(machine)
        self.assertEqual(self.windows_tree(), before)
        self.assert_clean(machine, 'vda4')

    def test_next_to_windows_one_fat_volume_id_without_the_record_is_a_clear_refusal(self):
        # Without the record the two ESPs cannot be told apart: nothing is mounted writable.
        for marker in ('shared', 'damaged', None):
            with self.subTest(marker=marker):
                self.fresh()
                machine, _ = self.alongside(marker, windows_uuid=ESP_UUID)
                status, _, err = self.repair(machine)
                self.assertEqual(status, 1)
                self.assertIn(repair.NO_ESP.format(source='UUID=' + ESP_UUID), err)
                self.assertEqual(machine.calls, [])
                self.assertEqual(self.writable_mounts(machine), [])
                self.assert_windows_esp_never_mounted(machine)
                self.assert_clean(machine, 'vda4')

    def test_naming_windows_esp_is_refused_before_it_is_mounted(self):
        machine, _ = self.alongside()
        before = self.windows_tree()
        status, _, err = self.repair(machine, '--esp', '/dev/vda1')
        self.assert_windows_esp_never_mounted(machine)
        self.assertEqual(status, 1)
        self.assertIn(repair.WINDOWS_ESP.format(device='/dev/vda1'), err)
        self.assertEqual(machine.calls, [])
        self.assertEqual(self.windows_tree(), before)
        self.assert_clean(machine, 'vda4')
        # Emaki's own ESP, named, is repaired.
        status, out, err = self.repair(machine, '--esp', '/dev/vda3')
        self.assertEqual(status, 0, err)
        self.assertIn(repair.REPAIRING.format(root='/dev/vda4', esp='/dev/vda3'), out)
        self.assert_windows_esp_never_mounted(machine)

    def test_an_old_shared_esp_is_still_repaired_read_only(self):
        # Before 0.9.0 Emaki started from Windows' ESP, mounted read-only at /efi; the old record
        # names it. Such an install is repaired as before, on that ESP.
        top = installed_tree(self.work / 'vda4', FSUUID)
        fstab = top / '@/etc/fstab'
        fstab.write_text(fstab.read_text().replace(f'UUID={ESP_UUID}\t/efi\tvfat\trw,relatime',
                                                   f'UUID={WINDOWS_ESP_UUID}\t/efi\tvfat\tro,noatime'))
        (top / '@' / MARKER).write_text(json.dumps({'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID}) + '\n')
        devices = [device('/dev/vda1', 'vfat', WINDOWS_ESP_UUID, WINDOWS_ESP_PARTUUID),
                   device('/dev/vda4', 'btrfs', FSUUID)]
        machine = Machine(self, self.work, devices, {'/dev/vda4': top}, ('/dev/vda1', self.esp))
        for named in ((), ('--esp', '/dev/vda1')):
            with self.subTest(named=named):
                status, out, err = self.repair(machine, *named)
                self.assertEqual(status, 0, err)
                self.assertIn(repair.REPAIRING.format(root='/dev/vda4', esp='/dev/vda1'), out)
                esp = [args for args in machine.commands if args[0] == 'mount' and '/dev/vda1' in args]
                self.assertEqual(esp[-1][4], 'ro,nosuid,nodev,noexec')
                self.assert_clean(machine, 'vda4')

    def test_what_the_repair_printed_comes_before_the_chroot_output(self):
        # S9V H-6x: without a terminal Python buffers its own lines, and arch-chroot's came first.
        events = []

        class Buffered(io.StringIO):
            def flush(self):
                events.append('flush')
                super().flush()

        out, err = Buffered(), Buffered()
        with patch.object(repair.sys, 'stdout', out), patch.object(repair.sys, 'stderr', err), \
                patch.object(repair.subprocess, 'run',
                             side_effect=lambda argv: events.append('run') or SimpleNamespace(returncode=0)):
            print('Repairing …', file=repair.sys.stdout)
            self.assertEqual(repair.call(['arch-chroot', '/mnt/x', 'emaki-boot-refresh', '--repair-slots']), 0)
        self.assertEqual(events, ['flush', 'flush', 'run'])

    def test_only_on_the_installation_usb(self):
        machine, _ = self.standard()
        status, _, err = self.repair(machine, live=False)
        self.assertEqual(status, 1)
        self.assertIn(repair.NOT_LIVE, err)
        self.assertEqual(machine.commands, [])


if __name__ == '__main__':
    unittest.main()
