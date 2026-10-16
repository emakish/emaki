# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A kept ESP must hold a Secure Boot install's two boot slots and later boot updates (R9S7 P1, R9S8)."""
import io
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from emaki_installer import secureboot
from emaki_installer import planner
from emaki_installer import worker as worker_module
from emaki_installer.constants import MIB, PHASES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.protocol import Controller
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, config, inventory, manual

CLUSTER = 4096
SHIM, MOK = 1_036_008, 874_360
MODDEP = b'normal: crypto extcmd\ncrypto:\nextcmd:\nsearch_fs_uuid:\nunused: normal\n'
GRUB = {'usr/lib/grub/x86_64-efi/kernel.img': 30_000, 'usr/lib/grub/x86_64-efi/normal.mod': 5_000,
        'usr/lib/grub/x86_64-efi/crypto.mod': 3_000, 'usr/lib/grub/x86_64-efi/extcmd.mod': 2_000,
        'usr/lib/grub/x86_64-efi/search_fs_uuid.mod': 1_000, 'usr/lib/grub/x86_64-efi/unused.mod': 7_000,
        'usr/lib/grub/x86_64-efi/command.lst': 700, 'usr/lib/grub/x86_64-efi/modinfo.sh': 400,
        'usr/lib/grub/x86_64-efi/core.efi.sig': 300, 'usr/lib/grub/i386-pc/biosonly.mod': 9_999,
        'usr/share/locale/de/LC_MESSAGES/grub.mo': 9_000, 'usr/share/locale/de/LC_MESSAGES/other.mo': 5_000,
        'usr/share/grub/unicode.pf2': 2_400_000, 'usr/share/grub/ascii.pf2': 4_000}
CONFIG = {'usr/share/emaki/grub/fonts/menu-24.pf2': 50_000, 'usr/share/emaki/grub/fonts/menu-46.pf2': 150_000,
          'usr/share/emaki/grub/theme/face.pf2': 20_000, 'usr/share/emaki/grub/background.png': 100_000}
SHIM_FILES = {'usr/share/emaki-shim-signed/shimx64.efi': SHIM, 'usr/share/emaki-shim-signed/mmx64.efi': MOK,
              'usr/share/licenses/emaki-shim-signed/LICENSE': 1_500}


def package(directory, name, members, contents=None):
    """A .pkg.tar.zst with members of the given sizes (zeros), read the way pacman's are."""
    path = Path(directory) / f'{name}-1.0-1-x86_64.pkg.tar.zst'
    with tarfile.open(path, 'w:zst') as archive:
        for member, size in members.items():
            data = (contents or {}).get(member, b'\0' * size)
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def archives(directory):
    """The verified archives the worker gets: grub (with its moddep.lst), emaki-config, the shim."""
    moddep = 'usr/lib/grub/x86_64-efi/moddep.lst'
    return [package(directory, 'grub', {**GRUB, moddep: len(MODDEP)}, {moddep: MODDEP}),
            package(directory, 'emaki-config', CONFIG), package(directory, 'emaki-shim-signed', SHIM_FILES),
            package(directory, 'bash', {'usr/bin/bash': 1_000})]


def fat(size):
    return -(-size // CLUSTER) * CLUSTER


def image(directory, modules=('normal', 'search_fs_uuid')):
    return secureboot.signed_grub_bytes(archives(directory), modules)


def expected_need(grub, removable):
    """Two slots (each file in clusters, a temporary copy of the largest, the manifest and the
    directory), two clusters for EFI/Emaki, EFI/BOOT's copies, the refresh's GRUB stage, the
    small state files and the reserve: the boot refresh's rule, written out."""
    files = (SHIM, MOK, grub, 4096)
    slot = sum(fat(size) for size in files) + fat(max(files)) + 4 * CLUSTER
    need = 2 * slot + 2 * CLUSTER + fat(grub) + 2 * CLUSTER + 7 * 2 * CLUSTER + 32 * MIB
    if removable:
        need += fat(SHIM) + fat(MOK) + fat(grub) + 2 * CLUSTER
    return need


class Need(unittest.TestCase):
    def test_need_is_two_slots_the_grub_stage_and_the_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            need = secureboot.secure_boot_esp_need(archives(directory), CLUSTER, ('normal', 'search_fs_uuid'))
            grub = image(directory)
        # The memdisk: the three small menu files, every x86_64-efi module and list (not
        # kernel.img, not other platforms), modinfo.sh, GRUB's locale, unicode.pf2 and
        # emaki-config's menu and theme faces; as ustar with 512-byte headers, padded records.
        memdisk = [1024, 1024, 1024, 5_000, 3_000, 2_000, 1_000, 7_000, 700, len(MODDEP), 400, 9_000,
                   2_400_000, 50_000, 150_000, 20_000]
        tar = sum(512 + -(-size // 512) * 512 for size in memdisk) + 1024
        tar = -(-tar // 10240) * 10240
        # Built in: kernel.img, normal and its dependencies crypto and extcmd, search_fs_uuid.
        self.assertEqual(grub, tar + 30_000 + 5_000 + 3_000 + 2_000 + 1_000 + MIB)
        self.assertEqual(need, expected_need(grub, False))

    def test_the_removable_copies_are_counted_when_efi_boot_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            found, grub = archives(directory), image(directory)
            plain = secureboot.secure_boot_esp_need(found, CLUSTER, ('normal', 'search_fs_uuid'))
            removable = secureboot.secure_boot_esp_need(found, CLUSTER, ('normal', 'search_fs_uuid'), removable=True)
        self.assertEqual(removable, expected_need(grub, True))
        self.assertEqual(removable - plain, fat(SHIM) + fat(MOK) + fat(grub) + 2 * CLUSTER)

    def test_a_missing_package_or_module_cannot_be_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            found = archives(directory)
            for drop in ('grub', 'emaki-config', 'emaki-shim-signed'):
                with self.subTest(missing=drop), self.assertRaisesRegex(secureboot.SecureBootError, drop):
                    secureboot.secure_boot_esp_need([a for a in found if not a.name.startswith(drop + '-1.0')],
                                                    CLUSTER, ('normal',))
            with self.assertRaisesRegex(secureboot.SecureBootError, 'lacks the modules absent'):
                secureboot.secure_boot_esp_need(found, CLUSTER, ('absent',))


class PlanReview(unittest.TestCase):
    """Plan review without readable archives (no esp_need here): it refuses a kept ESP by the
    PLAN_* sizes, which no real Secure Boot install undercuts, so the exact check after Install
    stays the final gate. ReviewFromArchives tests review with the archives."""

    def plan(self, free, secure=True, cluster=CLUSTER):
        data = inventory(partitions=True)
        if secure:
            data['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
        esp = data['disks'][0]['partitions'][0]
        esp['_esp_free_bytes'], esp['_esp_cluster_bytes'] = free, cluster
        return make_plan(manual(), data)

    def test_a_kept_esp_without_room_for_two_slots_is_refused_at_review(self):
        need = planner.plan_esp_need(CLUSTER, True)
        self.assertTrue(self.plan(need).secure_boot)
        with self.assertRaises(InstallError) as caught:
            self.plan(need - CLUSTER)
        self.assertEqual(caught.exception.code, Code.ESP_SPACE)
        self.assertEqual(caught.exception.message,
                         "The disk's startup partition is too full for Secure Boot updates: Emaki needs about "
                         f"{-(-need // 1000000)} MB free there (it has {(need - CLUSTER) // 1000000} MB). "
                         'Making it bigger needs a backup and a disk tool. Do not delete Windows startup files.')
        # Without Secure Boot the same ESP plans as before.
        self.assertFalse(self.plan(need - CLUSTER, secure=False).secure_boot)
        self.assertFalse(self.plan(33 * MIB, secure=False).secure_boot)

    def test_the_review_bound_never_exceeds_a_real_install(self):
        # Measured: Fedora shim 16.1-7, and grub 2:2.16-1's x86_64-efi modules and lists and
        # unicode.pf2, all carried by the signed image's memdisk.
        self.assertLessEqual(planner.PLAN_SHIM_BYTES, 1_036_008)
        self.assertLessEqual(planner.PLAN_MOK_MANAGER_BYTES, 874_360)
        self.assertLessEqual(planner.PLAN_SIGNED_GRUB_BYTES, 3_857_977 + 2_412_512)
        moddep = 'usr/lib/grub/x86_64-efi/moddep.lst'
        with tempfile.TemporaryDirectory() as directory:
            real = [package(directory, 'grub', {**GRUB, moddep: len(MODDEP),
                                                'usr/lib/grub/x86_64-efi/all.mod': 3_857_977,
                                                'usr/share/grub/unicode.pf2': 2_412_512}, {moddep: MODDEP}),
                    package(directory, 'emaki-config', CONFIG), package(directory, 'emaki-shim-signed', SHIM_FILES)]
            for cluster in (512, 4096, 16384):
                for removable in (False, True):
                    exact = secureboot.secure_boot_esp_need(real, cluster, ('normal',), removable=removable)
                    self.assertLessEqual(planner.plan_esp_need(cluster, removable), exact, (cluster, removable))

    def test_review_counts_efi_boot_by_the_same_rule(self):
        self.assertEqual(planner.plan_esp_need(CLUSTER, True) - planner.plan_esp_need(CLUSTER, False),
                         fat(planner.PLAN_SHIM_BYTES) + fat(planner.PLAN_MOK_MANAGER_BYTES)
                         + fat(planner.PLAN_SIGNED_GRUB_BYTES) + 2 * CLUSTER)


def real_archives(directory):
    """Archives whose signed GRUB is as large as grub 2:2.16-1 builds it (every x86_64-efi module
    and list, 3,857,977 B, and unicode.pf2, 2,412,512 B): above the old PLAN_* bounds."""
    moddep = 'usr/lib/grub/x86_64-efi/moddep.lst'
    return [package(directory, 'grub', {**GRUB, moddep: len(MODDEP), 'usr/lib/grub/x86_64-efi/all.mod': 3_857_977,
                                        'usr/share/grub/unicode.pf2': 2_412_512}, {moddep: MODDEP}),
            package(directory, 'emaki-config', CONFIG), package(directory, 'emaki-shim-signed', SHIM_FILES),
            package(directory, 'bash', {'usr/bin/bash': 1_000})]


class ReviewFromArchives(unittest.TestCase):
    """R9S9 #9: plan review reads the installation's own archives (the offline repository on the
    USB) and applies the worker's rule, secureboot.secure_boot_esp_need, to them: a kept ESP
    between the old lower bounds and the real need is refused at review, not after Install."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'run/archiso/bootmnt/emaki/repo'
        self.repo.mkdir(parents=True)
        self.archives = real_archives(self.repo)
        self.lines = []
        for patcher in (patch.object(worker_module, 'SIGNED_IMAGE_BASE', ('normal', 'search_fs_uuid')),
                        patch.object(secureboot, 'GRUB_MODULES', ())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def machine(self, free):
        data = inventory(partitions=True)
        data['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
        esp = data['disks'][0]['partitions'][0]
        esp['_esp_free_bytes'], esp['_esp_cluster_bytes'] = free, CLUSTER
        return data

    def fake(self, free):
        fake = FakeInventory(self.machine(free))
        fake.esp_need = worker_module.review_esp_need(self.root, self.lines.append)
        return fake

    def review(self, free):
        """The plan_ack of a plan request, as the installer's UI sends it."""
        controller = Controller(self.fake(free), None)
        return controller.handle({'type': 'plan', 'id': 'review', 'config': manual()})[0]

    def worker_need(self, encryption='none'):
        """The need check_secure_boot_esp computes (its log line) from the same archives."""
        data, lines = self.machine(10 ** 9), []
        worker = Worker(None, FakeInventory(data), Redactor(), lambda kind, **kw: None, lines.append)
        worker.plan = make_plan(manual(), data)
        worker.plan.config['encryption'] = encryption
        data['disks'][0]['partitions'][0]['_esp_free_bytes'] = 0
        with self.assertRaises(InstallError):
            worker.check_secure_boot_esp(self.archives, data)
        line = next(line for line in lines if 'later boot updates need' in line)
        return int(line.rsplit(' ', 1)[1].rstrip('.'))

    def test_review_need_equals_the_worker_need_for_the_same_archives(self):
        font = self.root / 'unlock-24.pf2'
        font.write_bytes(b'\0' * 300_000)
        need = self.fake(0).esp_need
        with patch.object(worker_module, 'GRUB_VISIBLE_FONT', font):
            for encryption in ('none', 'account'):
                with self.subTest(encryption=encryption):
                    # A manual layout keeps an ESP without fallback files: EFI/BOOT is written.
                    self.assertEqual(need(CLUSTER, True, encryption != 'none'), self.worker_need(encryption))
        self.assertEqual(need(CLUSTER, True, False),
                         secureboot.secure_boot_esp_need(self.archives, CLUSTER, ('normal', 'search_fs_uuid'),
                                                         removable=True))

    def test_an_esp_between_the_old_bound_and_the_real_need_is_refused_at_review(self):
        old, real = planner.plan_esp_need(CLUSTER, True), self.worker_need()
        # The reviewer's ESP: 64 MiB free passed the old bound and failed after Install.
        free = 67_108_864
        self.assertLess(old, free)
        self.assertLess(free, real)
        message = ("The disk's startup partition is too full for Secure Boot updates: Emaki needs about "
                   f"{-(-real // 1000000)} MB free there (it has {free // 1000000} MB). "
                   'Making it bigger needs a backup and a disk tool. Do not delete Windows startup files.')
        self.assertEqual(self.review(free)['errors'], [{'code': 'esp_space', 'msg': message}])
        self.assertEqual(self.review(real - 1)['errors'][0]['code'], 'esp_space')
        ack = self.review(real)
        self.assertEqual(ack['errors'], [])
        self.assertTrue(ack['token'])
        self.assertEqual(self.lines, [])

    def test_archives_that_cannot_be_read_fall_back_to_the_old_bounds_with_a_log_line(self):
        old = planner.plan_esp_need(CLUSTER, True)
        second = self.repo / 'grub-2.0-1-x86_64.pkg.tar.zst'
        second.write_bytes(self.archives[0].read_bytes())
        for broken in ('two grub archives', 'an empty repository'):
            with self.subTest(broken):
                if broken == 'an empty repository':
                    for path in sorted(self.repo.iterdir()):
                        path.unlink()
                self.lines.clear()
                self.assertEqual(self.review(old)['errors'], [])
                self.assertEqual(self.review(old - 1)['errors'][0]['code'], 'esp_space')
                self.assertTrue(self.lines)
                self.assertTrue(all(line.startswith(
                    'WARNING: plan review could not read the boot loader packages, so it checks the startup '
                    'partition by lower bounds; the exact check runs before the first disk write: ')
                    for line in self.lines), self.lines)

    def test_the_daemon_and_the_worker_plan_with_the_same_rule(self):
        root = Path(worker_module.__file__).parent
        daemon = (root / 'daemon.py').read_text()
        self.assertTrue('inventory.esp_need = review_esp_need(inventory.root, controller.log)' in daemon)
        protocol = (root / 'protocol.py').read_text()
        self.assertIn("make_plan(config, self.inventory.probe(), self.test_mode,\n"
                      "                                 esp_need=getattr(self.inventory, 'esp_need', None))", protocol)
        source = Path(worker_module.__file__).read_text()
        self.assertEqual(source.count("esp_need=getattr(self.inventory, 'esp_need', None)"), 2)


class Admission(unittest.TestCase):
    """The worker's exact check from the archives, before any disk write. Plan review's bound
    (planner.plan_esp_need) is neutralised here; PlanReview tests it."""

    def checked_need(self, encryption='none'):
        """The need check_secure_boot_esp computes (from its log line) for a kept ESP."""
        data = inventory(partitions=True)
        data['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
        lines = []
        worker = Worker(None, FakeInventory(data), Redactor(), lambda kind, **kw: None, lines.append)
        worker.plan = make_plan(manual(), data)
        worker.plan.config['encryption'] = encryption
        with tempfile.TemporaryDirectory() as directory:
            worker.check_secure_boot_esp(archives(directory), data)
        line = next(line for line in lines if 'later boot updates need' in line)
        return int(line.rsplit(' ', 1)[1].rstrip('.'))

    def test_the_worker_counts_every_built_in_module_and_the_unlock_screen(self):
        # The image builds in SIGNED_IMAGE_BASE and secureboot.GRUB_MODULES (and their
        # dependencies); encrypted, the memdisk also holds the unlock font and pictures.
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(worker_module, 'SIGNED_IMAGE_BASE', ('search_fs_uuid',)), \
                patch.object(secureboot, 'GRUB_MODULES', ('normal',)):
            # A manual layout keeps an ESP without fallback files: EFI/BOOT will be written.
            plain = secureboot.secure_boot_esp_need(archives(directory), CLUSTER, ('search_fs_uuid', 'normal'),
                                                    removable=True)
            self.assertEqual(self.checked_need(), plain)
            font = Path(directory) / 'unlock-24.pf2'
            font.write_bytes(b'\0' * 300_000)
            with patch.object(worker_module, 'GRUB_VISIBLE_FONT', font):
                self.assertGreaterEqual(self.checked_need('account') - plain, 300_000)

    def run_install(self, free, settings=None, secure=True, cluster=CLUSTER, loaders=None):
        data = inventory(partitions=True)
        if secure:
            data['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
        esp = data['disks'][0]['partitions'][0]
        esp['_esp_free_bytes'], esp['_esp_cluster_bytes'] = free, cluster
        if loaders is not None:
            esp['_efi_loaders'] = loaders
        inv = FakeInventory(data)
        floor = patch.object(planner, 'plan_esp_need', return_value=0)
        with floor:
            plan = make_plan(settings or manual(), inv.probe())
        events, phases, runners = [], [], []

        class PhaseWorker(Worker):
            pass

        for phase in PHASES:
            setattr(PhaseWorker, phase, lambda self, phase=phase: phases.append(phase))

        def runner_factory(*args):
            runners.append(RecordingRunner(*args))
            return runners[-1]

        worker = PhaseWorker(None, inv, Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
                             lambda line: None, backend_factory=Mock(), runner_factory=runner_factory)
        with tempfile.TemporaryDirectory() as directory, \
                patch('emaki_installer.worker.preflight_repo', return_value=archives(directory)), \
                patch('emaki_installer.worker.verify_packages'), \
                patch('emaki_installer.worker.verify_snapshot_hook'), \
                patch('emaki_installer.worker.verify_account_name'), \
                patch('emaki_installer.worker.choose_wifi', return_value=Mock(uuid=None, reason='none', count=0)), \
                patch('emaki_installer.worker.cleanup'), \
                patch.object(worker_module, 'SIGNED_IMAGE_BASE', ('normal', 'search_fs_uuid')), \
                patch.object(secureboot, 'GRUB_MODULES', ()), floor:
            worker.run(plan, threading.Event())
        commands = [argv for runner in runners for argv, _ in runner.commands]
        return events, phases, commands

    def need(self, removable=True):
        with tempfile.TemporaryDirectory() as directory, patch.object(secureboot, 'GRUB_MODULES', ()):
            return secureboot.secure_boot_esp_need(archives(directory), CLUSTER, ('normal', 'search_fs_uuid'),
                                                   removable=removable)

    def test_exactly_enough_is_admitted_one_cluster_less_is_refused_before_any_disk_write(self):
        need = self.need()
        events, phases, commands = self.run_install(need)
        self.assertNotIn('error', [event['type'] for event in events], events[-1:])
        self.assertIn('prepare_disk', phases)
        events, phases, commands = self.run_install(need - CLUSTER)
        error = events[-1]
        self.assertEqual(error['type'], 'error')
        self.assertEqual(error['code'], 'esp_space')
        self.assertEqual(error['message'],
                         "The disk's startup partition is too full for Secure Boot updates: Emaki needs about "
                         f"{-(-need // 1000000)} MB free there (it has {(need - CLUSTER) // 1000000} MB). "
                         'Making it bigger needs a backup and a disk tool. Do not delete Windows startup files.')
        self.assertEqual(phases, [])
        disk_tools = ('sgdisk', 'mkfs', 'mkfs.fat', 'mkfs.btrfs', 'mkfs.ext4', 'cryptsetup', 'wipefs',
                      'ntfsresize', 'mount', 'pacstrap', 'parted', 'sfdisk')
        self.assertFalse([argv for argv in commands if argv and Path(argv[0]).name in disk_tools])

    def test_the_old_32_mib_floor_no_longer_admits_a_secure_boot_install(self):
        # R9S7 P1: 36 MiB free passed the old 32 MiB check, yet after this payload (about 4.5 MiB
        # of GRUB, shim and MokManager here) no shim update could leave 32 MiB for Windows.
        events, phases, commands = self.run_install(36 * MIB)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['code'], 'esp_space')
        self.assertIn('too full for Secure Boot updates', events[-1]['message'])
        self.assertEqual(phases, [])

    def test_a_kept_esp_without_fallback_files_budgets_efi_boot(self):
        # R9S8 #4: a manual layout keeps an ESP with no EFI/BOOT/BOOTX64.EFI, so the bootloader
        # phase writes the removable copies; room for two slots alone is not enough.
        self.assertGreater(self.need(), self.need(removable=False))
        for loaders in (None, {}, {'EFI/BOOT/BOOTX64.EFI': 'old'}):
            with self.subTest(loaders=loaders):
                events, phases, _ = self.run_install(self.need(removable=False), loaders=loaders)
                self.assertEqual((events[-1]['type'], events[-1]['code']), ('error', 'esp_space'))
                self.assertEqual(phases, [])
        events, phases, _ = self.run_install(self.need())
        self.assertNotIn('error', [event['type'] for event in events])

    def test_the_removable_rule_is_the_bootloader_phases(self):
        # The check resolves EFI/BOOT from the inventory by removable_fallback, the rule the
        # bootloader phase applies on the mounted ESP; Emaki's own fallback cannot be told
        # apart before mounting, so a fallback there counts as written.
        # Next to Windows the ESP is Emaki's own (0.9.0): the mode no longer counts.
        self.assertTrue(planner.probed_removable({}))
        self.assertTrue(planner.probed_removable({'_efi_loaders': {'EFI/BOOT/BOOTX64.EFI': 'x'}}))
        self.assertFalse(planner.probed_removable({'_efi_loaders': {'EFI/boot/grubx64.efi': 'x'}}))
        for fallback in (False, True):
            for own in (False, True):
                for sidecars in (False, True):
                    rule = planner.removable_fallback(False, fallback, own, sidecars)
                    self.assertEqual(rule, not fallback or own)
                    secure = planner.removable_fallback(True, fallback, own, sidecars)
                    self.assertEqual(secure, rule and (own or not sidecars))
        source = Path(worker_module.__file__).read_text()
        self.assertEqual(source.count('removable_fallback('), 1)
        self.assertIn('removable=probed_removable(probed)', source)

    def test_secure_boot_off_and_a_formatted_esp_are_not_checked(self):
        for settings, secure in ((manual(), False), (config(), True)):
            with self.subTest(mode=settings['mode'], secure=secure):
                events, phases, _ = self.run_install(33 * MIB, settings, secure)
                self.assertNotIn('error', [event['type'] for event in events])
                self.assertIn('prepare_disk', phases)

    def test_reserve_value_lives_in_one_place(self):
        root = Path(__file__).resolve().parents[1] / 'emaki_installer'
        for name in ('planner.py', 'inventory.py'):
            text = (root / name).read_text()
            self.assertNotIn('32 * MIB', text, name)
        # A kept (manual) ESP keeps the reserve; next to Windows Emaki's own ESP needs none (0.9.0).
        self.assertIn('SHARED_ESP_RESERVE', (root / 'planner.py').read_text())


if __name__ == '__main__':
    unittest.main()
