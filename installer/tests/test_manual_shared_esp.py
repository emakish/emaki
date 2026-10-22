# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A manual layout that keeps Windows' ESP as /efi is the older shared layout (IV11I F0).

The installed boot code demands /var/lib/emaki/alongside-boot.json wherever /efi holds
EFI/Microsoft (grub/emaki_boot/refresh.py shared_esp, 00-emaki-alongside.hook); the
installer must write that record, the read-only fstab row and the hibernate drop-ins for
exactly that case, and never for an ESP of Emaki's own.
"""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import inventory as inventory_module
from emaki_installer.boot import WINDOWS_UNSAFE, windows_bootnext_menu
from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import windows_loader
from emaki_installer.planner import alongside_fingerprint, make_plan
from emaki_installer.render import kept_windows_esp, normalize_fstab
from emaki_installer.runtime import Redactor
from emaki_installer import worker as worker_module
from emaki_installer.worker import Worker
from support import RecordingRunner, alongside_on, config, inventory, manual

# The IV11I target: vda1 is Windows' ESP (partition GUID as sfdisk prints it, upper case).
WINDOWS_ESP = '93667E4D-3498-4C76-B781-3B14CF989025'
MICROSOFT = {'EFI/Microsoft/Boot/bootmgfw.efi': 'fixture-sha256', 'EFI/Microsoft/Boot/BCD': 'fixture-sha256',
             'EFI/BOOT/BOOTX64.EFI': 'fixture-sha256'}
RECORD = '/var/lib/emaki/alongside-boot.json'
DROPINS = [f'/etc/systemd/system/systemd-{mode}.service.d/50-emaki-shared-esp.conf'
           for mode in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate')]


def kept_esp_plan(loaders=MICROSOFT, **mount):
    """A manual plan keeping vda1 (its probed loaders) as /efi and formatting vda2 as root."""
    c, inv = manual(), inventory(partitions=True)
    esp = inv['disks'][0]['partitions'][0]
    esp['_efi_loaders'] = copy.deepcopy(loaders)
    # What the probe's efi_microsoft finds next to such a manifest.
    esp['_efi_microsoft'] = any(name.casefold().startswith('efi/microsoft/') for name in loaders or {})
    esp['_partuuid'] = WINDOWS_ESP
    c['mounts'][0].update(mount)
    return make_plan(c, inv)


def fstab(plan):
    """genfstab's lines for the plan's mounts, /efi as it mounts a FAT: rw,relatime, fsck pass 2."""
    lines = []
    for part, mp, subvolume in plan.mounts:
        options = part.options + ([f'subvol=/{subvolume}', 'subvolid=256'] if subvolume else [])
        if mp == '/efi':
            options = [*options, 'rw', 'relatime']
        lines.append(f'UUID={part.uuid or "test-" + str(part.number)} {mp} {part.fs} {",".join(options)} 0 {2 if mp == "/efi" else 0}')
    return '\n'.join(lines) + '\n'


def mount_efi(root, microsoft):
    """The target's mounted /efi as the worker reads it (shared_layout): EFI/Microsoft or not."""
    (root / 'efi/EFI/BOOT').mkdir(parents=True, exist_ok=True)
    if microsoft:
        (root / 'efi/EFI/Microsoft/Boot').mkdir(parents=True, exist_ok=True)
        (root / 'efi/EFI/Microsoft/Boot/bootmgfw.efi').write_bytes(b'MZ fixture')


def reviewed_microsoft(plan):
    """The plan's probed /efi evidence (_efi_microsoft), what an unchanged mount shows."""
    esp = next((p for p in plan.partitions if p.esp and p.mountpoint == '/efi'), None)
    probed = next((part for part in plan.disk['partitions'] if esp and part.get('path') == esp.path), {})
    return probed.get('_efi_microsoft') is True and not esp.format


class WorkerCase(unittest.TestCase):
    """A worker whose target is a temporary directory; settings() runs the real step."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=self.root)
        self.worker.runner = RecordingRunner()

    def settings(self, plan):
        self.worker.plan = plan
        self.worker.shared_esp_layout = None
        mount_efi(self.root, reviewed_microsoft(plan))
        for p in plan.partitions:
            p.path = p.path or '/dev/vda' + str(p.number)
            if p.esp:
                p.partuuid = WINDOWS_ESP
        self.worker.files.write('/etc/passwd', 'vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n')
        with patch.object(self.worker, 'repositories'), \
                patch('emaki_installer.worker.enable_units') as enable:
            self.worker.settings()
        return enable


class KeptWindowsEsp(WorkerCase):
    def test_kept_windows_esp_is_the_manual_plan_with_microsoft_loaders(self):
        plan = kept_esp_plan()
        self.assertIs(kept_windows_esp(plan), next(p for p in plan.partitions if p.esp))
        self.assertEqual(kept_windows_esp(plan).path, '/dev/vda1')
        # The probe's directory test decides, not the loader manifest (the boot code has no manifest).
        self.assertTrue(windows_loader({'_efi_microsoft': True, '_efi_loaders': {}}))
        self.assertFalse(windows_loader({'_efi_loaders': {'EFI/microsoft/boot/bootmgfw.efi': 'x'}}))
        for value in (False, None):
            self.assertFalse(windows_loader({'_efi_microsoft': value}))
        for loaders in ({}, None, {'EFI/BOOT/BOOTX64.EFI': 'x'}, {'EFI/ubuntu/shimx64.efi': 'x', 'EFI/BOOT/BOOTX64.EFI': 'x'},
                        {'EFI/Emaki/grubx64.efi': 'x'}):
            with self.subTest(loaders=loaders):
                self.assertIsNone(kept_windows_esp(kept_esp_plan(loaders)))
        # An ESP this install formats or Emaki's own (erase) holds no Windows files.
        self.assertIsNone(kept_windows_esp(kept_esp_plan({}, format=True)))
        self.assertIsNone(kept_windows_esp(make_plan(config(), inventory())))

    def test_manual_install_keeping_windows_esp_records_the_shared_layout(self):
        enable = self.settings(kept_esp_plan())
        text = self.worker.files.read(RECORD)
        self.assertEqual(json.loads(text), {'version': 1, 'esp_partuuid': WINDOWS_ESP.lower()})
        self.assertEqual(list(json.loads(text)), ['version', 'esp_partuuid'])
        self.assertTrue(text.endswith('}\n'))
        self.assertEqual(self.worker.files.path(RECORD).stat().st_mode & 0o777, 0o644)
        # The own-ESP identity for USB repair is recorded as well, as on the IV11I target.
        self.assertEqual(json.loads(self.worker.files.read('/var/lib/emaki/boot-esp.json')),
                         {'version': 1, 'emaki_esp_partuuid': WINDOWS_ESP.lower()})
        for dropin in DROPINS:
            with self.subTest(dropin=dropin):
                text = self.worker.files.read(dropin)
                self.assertIn('ExecStartPre=emaki-update-boot hibernate-pre\n', text)
                self.assertIn('ExecStopPost=emaki-update-boot hibernate-post\n', text)
        self.assertIn(['emaki-boot-order.service'], [call.args[2] for call in enable.call_args_list])

    def test_own_or_new_esp_gets_no_shared_record(self):
        cases = {'erase': make_plan(config(), inventory()),
                 'manual, formatted ESP': kept_esp_plan({}, format=True),
                 'manual, kept empty ESP': kept_esp_plan({}),
                 'manual, kept ESP with another loader': kept_esp_plan({'EFI/ubuntu/shimx64.efi': 'x'})}
        for name, plan in cases.items():
            with self.subTest(layout=name):
                self.setUp()
                enable = self.settings(plan)
                self.assertFalse(self.worker.files.path(RECORD).exists())
                for dropin in DROPINS:
                    self.assertFalse(self.worker.files.path(dropin).exists())
                self.assertNotIn(['emaki-boot-order.service'], [call.args[2] for call in enable.call_args_list])
                self.assertTrue(self.worker.files.path('/var/lib/emaki/boot-esp.json').exists())

    @alongside_on()
    def test_alongside_plan_records_its_own_esp_once_and_never_the_shared_layout(self):
        from test_alongside import windows
        plan = make_plan(*windows())
        self.assertIsNone(kept_windows_esp(plan))
        with patch.object(self.worker, 'record_alongside_boot') as own, \
                patch.object(self.worker, 'record_shared_esp') as shared:
            self.settings(plan)
        own.assert_called_once_with()
        shared.assert_not_called()

    def test_shared_record_refuses_an_unidentified_esp(self):
        self.worker.plan = kept_esp_plan()
        esp = next(p for p in self.worker.plan.partitions if p.esp)
        esp.path, esp.partuuid = '/dev/vda1', 'not-a-guid'
        with self.assertRaises(InstallError) as caught:
            self.worker.record_shared_esp()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertFalse(self.worker.files.path(RECORD).exists())

    def efi_row(self, plan):
        """The /efi row as write_fstab renders it, with the worker's one shared_layout decision."""
        self.worker.plan, self.worker.shared_esp_layout = plan, None
        mount_efi(self.root, reviewed_microsoft(plan))
        text = normalize_fstab(fstab(plan), plan, shared=self.worker.shared_layout())
        return next(line.split() for line in text.splitlines() if line.split()[1] == '/efi')

    def test_fstab_keeps_windows_esp_read_only_without_fsck(self):
        row = self.efi_row(kept_esp_plan())
        options = row[3].split(',')
        self.assertIn('ro', options)
        self.assertIn('noatime', options)
        self.assertFalse({'rw', 'relatime'} & set(options))
        self.assertEqual(row[5], '0')
        for name, other in (('kept ESP without Windows', kept_esp_plan({'EFI/BOOT/BOOTX64.EFI': 'x'})),
                            ('formatted ESP', kept_esp_plan({}, format=True))):
            with self.subTest(layout=name):
                self.setUp()
                row = self.efi_row(other)
                self.assertTrue({'rw', 'relatime'} <= set(row[3].split(',')))
                self.assertNotIn('ro', row[3].split(','))
                self.assertEqual(row[5], '2')


class ProbedKeptEsp(WorkerCase):
    """The probe (Inventory._inspect over a real directory tree) decides the shared layout by the
    boot code's own test (an entry Microsoft in the ESP's EFI directory), whatever the loader
    manifest says: a Windows ESP whose manifest is empty or refused still gets the record."""

    def probe(self, files=(), dirs=(), oversized=None, efi_link=False):
        esp = self.root / 'probe-esp'
        esp.mkdir()
        if efi_link:
            (self.root / 'elsewhere/Microsoft/Boot').mkdir(parents=True)
            (esp / 'EFI').symlink_to(self.root / 'elsewhere')
        for name in dirs:
            (esp / name).mkdir(parents=True)
        for name in files:
            (esp / name).parent.mkdir(parents=True, exist_ok=True)
            (esp / name).write_bytes(b'MZ fixture')
        if oversized:
            (esp / oversized).parent.mkdir(parents=True, exist_ok=True)
            with (esp / oversized).open('wb') as stream:
                stream.truncate(inventory_module.EFI_MANIFEST_MAX_FILE_BYTES + 1)  # sparse
        c, machine = manual(), inventory(partitions=True)
        part = machine['disks'][0]['partitions'][0]
        part['_partuuid'] = WINDOWS_ESP
        # The real inspector reads the tree; only the mount directory and its removal are replaced.
        with patch.object(inventory_module.tempfile, 'mkdtemp', return_value=str(esp)), \
                patch.object(Path, 'rmdir'):
            inventory_module.Inventory(RecordingRunner())._inspect(machine['disks'][0], part)
        return c, machine, part

    def assertShared(self, plan, shared):
        enable = self.settings(plan)
        self.assertEqual(self.worker.files.path(RECORD).exists(), shared)
        if shared:
            self.assertEqual(json.loads(self.worker.files.read(RECORD)),
                             {'version': 1, 'esp_partuuid': WINDOWS_ESP.lower()})
        for dropin in DROPINS:
            self.assertEqual(self.worker.files.path(dropin).exists(), shared)
        self.assertEqual(['emaki-boot-order.service'] in [call.args[2] for call in enable.call_args_list], shared)
        row = next(line.split() for line in normalize_fstab(fstab(plan), plan, shared=self.worker.shared_layout())
                   .splitlines() if line.split()[1] == '/efi')
        options = row[3].split(',')
        if shared:
            self.assertTrue({'ro', 'noatime'} <= set(options))
            self.assertFalse({'rw', 'relatime'} & set(options))
            self.assertEqual(row[5], '0')
        else:
            self.assertTrue({'rw', 'relatime'} <= set(options))
            self.assertEqual(row[5], '2')

    def test_windows_esp_without_files_under_microsoft_is_shared(self):
        # A damaged or half-removed Windows: no file to fingerprint, the boot code still sees it.
        c, machine, part = self.probe(dirs=['EFI/Microsoft/Boot'], files=['EFI/BOOT/BOOTX64.EFI'])
        self.assertIs(part['_efi_microsoft'], True)
        self.assertFalse(any('microsoft' in name.casefold() for name in part['_efi_loaders']))
        self.assertShared(make_plan(c, machine), True)

    def test_windows_esp_with_a_refused_manifest_is_shared(self):
        # One unrelated file over the manifest's size bound refuses the whole manifest.
        c, machine, part = self.probe(files=['EFI/Microsoft/Boot/bootmgfw.efi', 'EFI/Microsoft/Boot/BCD'],
                                      oversized='EFI/Linux/other.efi')
        self.assertEqual(part['_efi_loaders'], {})
        self.assertTrue(part['_efi_error'])
        self.assertIs(part['_efi_microsoft'], True)
        self.assertShared(make_plan(c, machine), True)

    def test_intact_windows_esp_is_shared(self):
        c, machine, part = self.probe(files=['efi/MICROSOFT/Boot/bootmgfw.efi'])
        self.assertIn('EFI/Microsoft/Boot/bootmgfw.efi', part['_efi_loaders'])
        self.assertShared(make_plan(c, machine), True)

    def test_another_vendors_esp_is_not_shared(self):
        c, machine, part = self.probe(files=['EFI/ubuntu/shimx64.efi', 'EFI/BOOT/BOOTX64.EFI'])
        self.assertIs(part['_efi_microsoft'], False)
        self.assertShared(make_plan(c, machine), False)

    def test_an_esp_whose_efi_directory_is_a_link_cannot_be_kept(self):
        # The installed boot code refuses every write there (UNCHECKED).
        c, machine, part = self.probe(efi_link=True)
        self.assertIsNone(part['_efi_microsoft'])
        with self.assertRaises(InstallError) as caught:
            make_plan(c, machine)
        self.assertEqual(caught.exception.code, Code.MANUAL_LAYOUT)
        self.assertEqual(str(caught.exception), inventory_module.EFI_MANIFEST_UNSAFE)

    def test_efi_microsoft_is_the_boot_codes_test(self):
        root = self.root / 'cases'
        for name, build, expected in (
                ('empty', [], False), ('fallback only', ['EFI/BOOT/'], False),
                ('vendor dir', ['efi/microsoft/'], True), ('vendor file', ['EFI/MICROSOFT'], True),
                ('EFI a file', ['EFI'], None)):
            with self.subTest(case=name):
                tree = root / name
                tree.mkdir(parents=True)
                for entry in build:
                    path = tree / entry.rstrip('/')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.mkdir() if entry.endswith('/') else path.write_bytes(b'')
                self.assertIs(inventory_module.efi_microsoft(tree), expected)


class SharedEspRun(unittest.TestCase):
    """The real Worker.run() from the review to settings(), with a mocked backend and probe."""

    @staticmethod
    def machine(shared):
        inv = inventory(partitions=True)
        esp = inv['disks'][0]['partitions'][0]
        esp.update(_efi_loaders=copy.deepcopy(MICROSOFT) if shared else {}, _efi_microsoft=shared,
                   _partuuid=WINDOWS_ESP.lower(), os_hint='windows' if shared else None)
        return inv

    def run_worker(self, reviewed, *probes, mounted=None, copy=False):
        """Run the worker on a plan reviewed from `reviewed`; each fresh probe returns the next of `probes`.

        backend.prepare mounts the target's /efi with EFI/Microsoft when `mounted` is True (by
        default as the last probe saw it), 'link' makes its EFI directory a link. The phases are
        the write boundary, the fstab render (end of the bootloader phase) and settings; `copy`
        adds copy_packages after the write boundary, with the real package choice."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        plan = make_plan(manual(), self.machine(reviewed))
        events, lines, backend = [], [], Mock(console_font=None)
        probe = Mock(side_effect=[self.machine(shared) for shared in probes])
        factory = Mock(return_value=backend)
        runner = RecordingRunner()
        runner.responses[('genfstab', '-U', '-f', str(root), str(root))] = fstab(plan)
        mounted = probes[-1] if mounted is None else mounted

        def prepare():
            if mounted == 'link':
                (root / 'elsewhere/Microsoft').mkdir(parents=True)
                (root / 'efi').mkdir()
                (root / 'efi/EFI').symlink_to(root / 'elsewhere')
            else:
                mount_efi(root, mounted)
        backend.prepare.side_effect = prepare
        worker = Worker(None, SimpleNamespace(probe=probe), Redactor(), lambda kind, **kw: events.append({'type': kind, **kw}),
                        lines.append, target=root, runner_factory=lambda *args: runner, backend_factory=factory)
        worker.files.write('/etc/passwd', 'vmuser:x:1001:987:VM User:/home/vmuser:/bin/bash\n')
        worker.api = SimpleNamespace(locale=SimpleNamespace(LocaleConfiguration=Mock()))
        phases = {'prepare_disk': 40, **({'copy_packages': 0} if copy else {}), 'write_fstab': 20, 'settings': 40}
        with contextlib.ExitStack() as stack:
            for name in ('verify_packages', 'verify_snapshot_hook', 'verify_account_name', 'cleanup', 'enable_units'):
                stack.enter_context(patch('emaki_installer.worker.' + name))
            preflight = stack.enter_context(patch('emaki_installer.worker.preflight_repo', return_value=[]))
            stack.enter_context(patch('emaki_installer.worker.PHASES', phases))
            for name in ('capture_live_wifi', 'check_secure_boot_esp', 'repositories', 'fwupd_keep_keys',
                         'populate_keyring'):
                stack.enter_context(patch.object(worker, name))
            worker.run(plan, threading.Event())
        self.assertEqual(probe.call_count, len(probes))
        return SimpleNamespace(event=events[-1], worker=worker, backend=backend, factory=factory, root=root, lines=lines,
                               preflight=preflight)

    def assertInstalled(self, result, shared):
        """done, and the fstab row, the record and the three drop-ins all follow `shared`."""
        self.assertEqual(result.event['type'], 'done', result.event)
        result.backend.prepare.assert_called_once()
        self.assertIs((result.root / RECORD.lstrip('/')).is_file(), shared)
        if shared:
            self.assertEqual(json.loads((result.root / RECORD.lstrip('/')).read_text()),
                             {'version': 1, 'esp_partuuid': WINDOWS_ESP.lower()})
        for dropin in DROPINS:
            self.assertIs((result.root / dropin.lstrip('/')).is_file(), shared)
        row = next(line.split() for line in (result.root / 'etc/fstab').read_text().splitlines()
                   if line.split() and line.split()[1] == '/efi')
        options = set(row[3].split(','))
        if shared:
            self.assertTrue({'ro', 'noatime'} <= options, row)
            self.assertFalse({'rw', 'relatime'} & options, row)
            self.assertEqual(row[5], '0')
        else:
            self.assertTrue({'rw', 'relatime'} <= options, row)
            self.assertNotIn('ro', options)
            self.assertEqual(row[5], '2')


class RecheckSharedEsp(SharedEspRun):
    """Worker.run()'s two fresh probes (after the review, at the write boundary) with the kept
    ESP's EFI/Microsoft evidence changed between the review and that probe (REVF0B P2). The
    fingerprint does not see it; settings() would install with the reviewed plan's policy."""

    def assertRefused(self, result, backend_made):
        self.assertEqual(result.event['type'], 'error', result.event)
        self.assertEqual(result.event['code'], Code.DISK_CHANGED.value)
        self.assertEqual(result.event['message'], worker_module.SHARED_ESP_CHANGED)
        self.assertEqual(result.event['phase'], 'prepare_disk')
        self.assertEqual(result.factory.called, backend_made)
        # Nothing reached the disk: no partitioning, no disk-changed mark, no files in the target.
        result.backend.prepare.assert_not_called()
        self.assertFalse(result.worker.disk_changed)
        self.assertFalse((result.root / RECORD.lstrip('/')).exists())

    def test_the_fingerprint_does_not_see_the_microsoft_evidence(self):
        self.assertEqual(make_plan(manual(), self.machine(True)).fingerprint,
                         make_plan(manual(), self.machine(False)).fingerprint)

    def test_microsoft_evidence_appearing_before_the_first_recheck_refuses(self):
        result = self.run_worker(False, True)
        self.assertRefused(result, False)

    def test_microsoft_evidence_disappearing_before_the_first_recheck_refuses(self):
        result = self.run_worker(True, False)
        self.assertRefused(result, False)

    def test_microsoft_evidence_appearing_before_the_write_boundary_refuses(self):
        self.assertRefused(self.run_worker(False, False, True), True)

    def test_microsoft_evidence_disappearing_before_the_write_boundary_refuses(self):
        self.assertRefused(self.run_worker(True, True, False), True)

    def test_unchanged_evidence_installs_with_the_reviewed_policy(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                result = self.run_worker(shared, shared, shared)
                self.assertInstalled(result, shared)
                self.assertFalse([line for line in result.lines if line.startswith('Shared startup partition')])


class MountedSharedEsp(SharedEspRun):
    """The kept ESP's EFI/Microsoft evidence changed after the last recheck, before the target is
    mounted (REVF0C P2): the mounted /efi decides once, for the fstab row, record and drop-ins."""

    def test_microsoft_evidence_appearing_after_the_last_probe_installs_the_shared_layout(self):
        # The hook's shared_esp() is true on such an /efi; without the record it refuses every update.
        result = self.run_worker(False, False, False, mounted=True)
        self.assertInstalled(result, True)
        self.assertIn('Shared startup partition check: the mounted /efi now holds EFI/Microsoft after the last '
                      'recheck; it is installed as shared with Windows.', result.lines)

    def test_microsoft_evidence_disappearing_after_the_last_probe_keeps_the_reviewed_shared_layout(self):
        result = self.run_worker(True, True, True, mounted=False)
        self.assertInstalled(result, True)
        self.assertIn('Shared startup partition check: the mounted /efi no longer holds EFI/Microsoft after the '
                      'last recheck; it is installed as shared with Windows.', result.lines)

    def test_an_unreadable_mounted_efi_directory_stops_before_fstab_and_record(self):
        # The installed boot code refuses every write there (UNCHECKED): fail closed.
        for reviewed in (True, False):
            with self.subTest(reviewed=reviewed):
                result = self.run_worker(reviewed, reviewed, reviewed, mounted='link')
                self.assertEqual(result.event['type'], 'error', result.event)
                self.assertEqual(result.event['code'], Code.BOOT_VERIFY.value)
                self.assertEqual(result.event['phase'], 'write_fstab')
                self.assertEqual(result.event['message'], inventory_module.EFI_MANIFEST_UNSAFE)
                self.assertFalse((result.root / 'etc/fstab').exists())
                self.assertFalse((result.root / RECORD.lstrip('/')).exists())
                for dropin in DROPINS:
                    self.assertFalse((result.root / dropin.lstrip('/')).exists())

    def test_the_decision_is_taken_once(self):
        # settings() reuses the fstab render's decision: a later change of /efi changes neither.
        result = self.run_worker(False, False, False, mounted=True)
        mount_efi(result.root, False)
        (result.root / 'efi/EFI/Microsoft/Boot/bootmgfw.efi').unlink()
        (result.root / 'efi/EFI/Microsoft/Boot').rmdir()
        (result.root / 'efi/EFI/Microsoft').rmdir()
        self.assertIs(result.worker.shared_layout(), True)


# GRUB 2.16's 30_os-prober for Windows' ESP kept as /efi on the IV11F target (/dev/vda1, FAT
# volume C14E-0C72), as its generator and grub-mkconfig_lib print it: EFI-only wrapper, hints.
OS_PROBER_WINDOWS = """### BEGIN /etc/grub.d/30_os-prober ###
# This menu entry is supported only on EFI platforms.
if [ "$grub_platform" = "efi" ]; then
menuentry 'Windows Boot Manager (on /dev/vda1)' --class windows --class os $menuentry_id_option 'osprober-efi-C14E-0C72' {
\tinsmod part_gpt
\tinsmod fat
\tset root='hd0,gpt1'
\tif [ x$feature_platform_search_hint = xy ]; then
\t  search --no-floppy --fs-uuid --set=root --hint-bios=hd0,gpt1 --hint-efi=hd0,gpt1 --hint-baremetal=ahci0,gpt1  C14E-0C72
\telse
\t  search --no-floppy --fs-uuid --set=root  C14E-0C72
\tfi
\tchainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
fi
### END /etc/grub.d/30_os-prober ###
"""
EMAKI_ENTRY = 'Boot0001* Emaki\tHD(1,GPT,93667e4d-3498-4c76-b781-3b14cf989025,0x800,0x32000)/\\EFI\\Emaki\\grubx64.efi\n'


class OsProberOnSharedEsp(WorkerCase):
    """Windows' ESP kept as /efi (F1-011I): every update's menu refresh (grub/emaki_boot/refresh.py
    generate) replaces os-prober's Windows entry by windows_bootnext_menu and refuses without
    exactly one, so the installation installs os-prober and enables it exactly as next to Windows."""

    def copy(self, plan):
        """The packages copy_packages straps for `plan`, with /efi mounted as the probe saw it."""
        self.worker.plan, self.worker.shared_esp_layout = plan, None
        mount_efi(self.root, reviewed_microsoft(plan))
        self.worker.backend = Mock(console_font=None)
        self.worker.api = SimpleNamespace(locale=SimpleNamespace(LocaleConfiguration=Mock()))
        with patch.object(self.worker, 'populate_keyring'):
            self.worker.copy_packages()
        return self.worker.backend.instance.pacman.strap.call_args.args[0]

    def test_kept_windows_esp_installs_and_enables_os_prober(self):
        packages = self.copy(kept_esp_plan())
        self.assertEqual(packages[-2:], ['os-prober', 'ntfs-3g'])
        self.assertIs(self.worker.os_prober(), True)
        self.assertTrue(worker_module.os_prober_possible(self.worker.plan))

    def test_other_manual_and_erase_layouts_install_no_os_prober(self):
        cases = {'erase': make_plan(config(), inventory()),
                 'manual, formatted ESP': kept_esp_plan({}, format=True),
                 'manual, kept empty ESP': kept_esp_plan({}),
                 'manual, kept ESP with another loader': kept_esp_plan({'EFI/ubuntu/shimx64.efi': 'x'})}
        for name, plan in cases.items():
            with self.subTest(layout=name):
                self.setUp()
                packages = self.copy(plan)
                self.assertNotIn('os-prober', packages)
                self.assertNotIn('ntfs-3g', packages)
                self.assertIs(self.worker.os_prober(), False)

    @alongside_on()
    def test_alongside_still_installs_os_prober(self):
        from test_alongside import windows
        packages = self.copy(make_plan(*windows()))
        self.assertEqual(packages[-2:], ['os-prober', 'ntfs-3g'])
        self.assertIs(self.worker.os_prober(), True)

    def test_the_preflight_proves_the_packages_for_every_kept_esp(self):
        # It resolves before /efi is mounted, so before shared_layout() can decide: the superset.
        for name, plan, possible in (('erase', make_plan(config(), inventory()), False),
                                     ('manual, formatted ESP', kept_esp_plan({}, format=True), False),
                                     ('manual, kept empty ESP', kept_esp_plan({}), True),
                                     ('manual, kept Windows ESP', kept_esp_plan(), True)):
            with self.subTest(layout=name):
                self.assertIs(worker_module.os_prober_possible(plan), possible)
        requested = []

        def run(argv):
            if '-Sp' in argv:
                requested.extend(argv[argv.index('--logfile') + 2:])
                return (self.root / 'fixture.pkg.tar.zst').as_uri()
            return ''
        (self.root / 'fixture.pkg.tar.zst').write_bytes(b'fixture')
        (self.root / 'fixture.pkg.tar.zst.sig').write_bytes(b'signature')
        with patch('emaki_installer.worker.offline_config', return_value=self.root / 'pacman.conf'), \
                patch('emaki_installer.worker.ensure_clock'):
            worker_module.preflight_repo(Mock(run=run), os_prober=True)
        self.assertEqual(requested[-2:], ['os-prober', 'ntfs-3g'])

    def test_windows_entry_on_the_kept_esp_passes_the_refresh_check(self):
        partuuid = WINDOWS_ESP.lower()
        listing = 'BootCurrent: 0001\nBootOrder: 0001\n' + EMAKI_ENTRY
        # IV11F: no firmware entry for Windows; the chainload stays only without shim verification.
        menu = windows_bootnext_menu(OS_PROBER_WINDOWS, partuuid, 'C14E-0C72', listing)
        self.assertIn("menuentry 'Windows startup help'", menu)
        self.assertIn('if [ "$shim_lock" = y ]; then', menu)
        windows = ('Boot0000* Windows Boot Manager\tHD(1,GPT,' + partuuid
                   + ',0x800,0x32000)/\\EFI\\Microsoft\\Boot\\bootmgfw.efi\n')
        menu = windows_bootnext_menu(
            OS_PROBER_WINDOWS, partuuid, 'C14E-0C72', 'BootCurrent: 0001\nBootOrder: 0001,0000\n' + EMAKI_ENTRY + windows)
        self.assertIn('bootnext 0000', menu)
        self.assertNotIn('chainloader', menu)
        # GRUB_DISABLE_OS_PROBER=true leaves the section empty: the refusal IV11F's updates got.
        with self.assertRaises(InstallError) as caught:
            windows_bootnext_menu(
                '### BEGIN /etc/grub.d/30_os-prober ###\n### END /etc/grub.d/30_os-prober ###\n',
                partuuid, 'C14E-0C72', listing)
        self.assertEqual(str(caught.exception), WINDOWS_UNSAFE)


class OsProberRun(SharedEspRun):
    def test_the_run_proves_os_prober_for_a_kept_esp(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                result = self.run_worker(shared, shared, shared, copy=True)
                self.assertInstalled(result, shared)
                self.assertIs(result.preflight.call_args.kwargs['os_prober'], True)
                packages = result.backend.instance.pacman.strap.call_args.args[0]
                self.assertIs('os-prober' in packages and 'ntfs-3g' in packages, shared)

    def test_the_copy_decides_from_the_mounted_esp(self):
        # Windows' files appear after the last probe: copy_packages, the first use of
        # shared_layout(), already sees them on the mounted /efi, and the fstab row agrees.
        result = self.run_worker(False, False, False, mounted=True, copy=True)
        self.assertInstalled(result, True)
        self.assertEqual(result.backend.instance.pacman.strap.call_args.args[0][-2:], ['os-prober', 'ntfs-3g'])
        result = self.run_worker(True, True, True, mounted='link', copy=True)
        self.assertEqual((result.event['type'], result.event['phase']), ('error', 'copy_packages'))
        self.assertEqual(result.event['message'], inventory_module.EFI_MANIFEST_UNSAFE)
        result.backend.instance.pacman.strap.assert_not_called()


class AlongsideFingerprint(unittest.TestCase):
    @alongside_on()
    def test_the_microsoft_evidence_leaves_the_alongside_fingerprint_unchanged(self):
        from test_alongside import windows
        c, machine = windows()
        disk = machine['disks'][0]
        before = alongside_fingerprint(disk)
        for part in disk['partitions']:
            if part['esp']:
                part['_efi_microsoft'] = True
        self.assertEqual(alongside_fingerprint(disk), before)
        self.assertEqual(make_plan(c, machine).fingerprint, make_plan(*windows()).fingerprint)


if __name__ == '__main__':
    unittest.main()
