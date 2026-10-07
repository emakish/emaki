#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery and loader-trial regressions under independently changing filesystems."""
import importlib.util
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot_fixture', ROOT / 'tests/test-boot-refresh.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
refresh = fixture_module.refresh
if os.environ.get('EMAKI_REFRESH_BASELINE'):
    spec = importlib.util.spec_from_file_location('emaki_boot.baseline', os.environ['EMAKI_REFRESH_BASELINE'])
    refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh)
    fixture_module.refresh = refresh


class RecoveryAttacks(unittest.TestCase):
    fixture = fixture_module.RefreshOrchestrationChecks.fixture
    freeze_publication_steps = fixture_module.RefreshOrchestrationChecks.freeze_publication_steps
    assert_clean_publication = fixture_module.RefreshOrchestrationChecks.assert_clean_publication
    hook = fixture_module.RefreshOrchestrationChecks.hook

    def state(self, f):
        return json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())

    def boot(self, f, tag=''):
        f.put(f.mapped('/proc/cmdline'), 'root=UUID=' + fixture_module.FSUUID + ' emaki.generation=' + tag + '\n')
        f.mapped('/usr/lib/modules/test-kernel').mkdir(parents=True, exist_ok=True)
        with patch.object(refresh.os, 'uname', return_value=SimpleNamespace(release='test-kernel')):
            refresh.mark_good(f.identity)

    def exhaust(self, f):
        tag = self.state(f)['newest']
        data = ('# GRUB Environment Block\nemaki_candidate=' + tag + '\nemaki_trial=2\n').encode()
        f.put(f.mapped('/efi/EFI/Emaki/trial.env'), data.ljust(1024, b'#'))

    def firmware_kept(self, f):
        for name in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
            self.assertEqual(f.mapped(name).read_bytes(), f.originals[name])

    def successful_recovery(self, f):
        self.boot(f)
        for _ in range(3):
            refresh.refresh(f.identity)
            state = self.state(f)
            self.assertTrue((f.mapped('/boot/emaki') / state['newest'] / 'manifest.json').is_file())
            self.assertTrue(f.mapped('/efi/EFI/Emaki/loader-' + state['newest'] + '.efi').is_file())
            self.assert_clean_publication(f)
            self.firmware_kept(f)

    def test_snapshot_writer_does_not_wedge_interrupted_publication(self):
        with self.fixture(True) as f:
            checked = 0
            for target in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                if not f.mapped('/efi/EFI/Emaki/boot-intent.json').exists():
                    continue
                with self.subTest(target=target):
                    checked += 1
                    f.put(f.mapped('/boot/grub/grub-btrfs.cfg'), 'snapshot menu with another snapshot\n')
                    self.successful_recovery(f)
            self.assertGreater(checked, 1)

    def test_root_rollback_removing_sidecars_does_not_wedge_esp_intent(self):
        with self.fixture(True) as f:
            checked = 0
            for target in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                if not f.mapped('/efi/EFI/Emaki/boot-intent.json').exists():
                    continue
                with self.subTest(target=target):
                    checked += 1
                    for name in ('/etc/default/grub', '/boot/grub/grub.cfg', '/boot/grub/grub-btrfs.cfg'):
                        for sidecar in f.mapped(name).parent.glob(Path(name).name + '.emaki-*'):
                            sidecar.unlink()
                        f.put(f.mapped(name), f.originals[name])
                    shutil.rmtree(f.mapped('/boot/emaki'), ignore_errors=True)
                    self.successful_recovery(f)
            self.assertGreater(checked, 1)

    def test_torn_rename_missing_new_candidate_is_recoverable(self):
        with self.fixture(True) as f:
            checked = 0
            for target in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                if not intent.exists():
                    continue
                record = json.loads(intent.read_text())
                candidates = [entry for entry in record['entries'] if Path(entry['target']).name.startswith('loader-')]
                if not candidates:
                    continue
                entry = candidates[0]
                candidate = f.mapped(entry['target'])
                sidecar = candidate.with_name(candidate.name + '.emaki-new-' + record['tag'])
                if not sidecar.exists() or candidate.exists():
                    continue
                with self.subTest(target=target):
                    checked += 1
                    sidecar.unlink()
                    self.successful_recovery(f)
            self.assertGreater(checked, 0)

    def test_promoted_good_survives_lost_new_candidate_and_later_refreshes(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            good = self.state(f)['newest']
            self.boot(f, good)
            firmware = {name: f.mapped(name).read_bytes() for name in
                        ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI')}
            module = f.mapped('/usr/lib/grub/x86_64-efi/normal.mod')
            module.write_bytes(module.read_bytes() + b'new loader version')
            checked = 0
            for stop in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                if not intent.exists():
                    continue
                record = json.loads(intent.read_text())
                entries = [entry for entry in record['entries']
                           if Path(entry['target']).name.startswith('loader-')]
                if not entries:
                    continue
                with self.subTest(stop=stop):
                    checked += 1
                    candidate = f.mapped(entries[0]['target'])
                    candidate.unlink(missing_ok=True)
                    candidate.with_name(candidate.name + '.emaki-new-' + record['tag']).unlink(missing_ok=True)
                    self.boot(f, good)
                    for _ in range(3):
                        refresh.refresh(f.identity)
                        current = self.state(f)
                        self.assertEqual(current['good'], good)
                        self.assertTrue(f.mapped('/efi/EFI/Emaki/loader-' + current['newest'] + '.efi').is_file())
                        for name, data in firmware.items():
                            self.assertEqual(f.mapped(name).read_bytes(), data)
                        self.assert_clean_publication(f)
            self.assertGreater(checked, 1)

    def test_promotion_recovers_missing_canonical_target_and_new_sidecar(self):
        for missing in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
            with self.subTest(missing=missing), self.fixture(True) as f:
                refresh.refresh(f.identity)
                tag = self.state(f)['newest']
                image = f.mapped('/efi/EFI/Emaki/loader-' + tag + '.efi').read_bytes()
                checked = 0
                for stop in self.freeze_publication_steps(f, lambda: self.boot(f, tag)):
                    intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                    if not intent.exists():
                        continue
                    with self.subTest(stop=stop):
                        checked += 1
                        record = json.loads(intent.read_text())
                        target = f.mapped(missing)
                        target.unlink(missing_ok=True)
                        target.with_name(target.name + '.emaki-new-' + record['tag']).unlink(missing_ok=True)
                        self.boot(f, tag)
                        self.assertEqual(self.state(f)['good'], tag)
                        for name in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                            self.assertEqual(f.mapped(name).read_bytes(), image)
                        for _ in range(3):
                            refresh.refresh(f.identity)
                            self.assertEqual(self.state(f)['newest'], tag)
                            self.assertEqual(self.state(f)['good'], tag)
                            self.assert_clean_publication(f)
                self.assertGreater(checked, 1)

    def test_torn_loader_targets_recover_verified_bytes(self):
        for promotion in (False, True):
            with self.subTest(promotion=promotion), self.fixture(True) as f:
                if promotion:
                    refresh.refresh(f.identity)
                    tag = self.state(f)['newest']
                    operation = lambda: self.boot(f, tag)
                else:
                    operation = lambda: refresh.refresh(f.identity)
                checked = 0
                for stop in self.freeze_publication_steps(f, operation):
                    intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                    if not intent.exists():
                        continue
                    record = json.loads(intent.read_text())
                    loaders = [entry for entry in record['entries']
                               if entry['target'].lower().endswith('.efi')]
                    with self.subTest(stop=stop):
                        checked += 1
                        for entry in loaders:
                            f.put(f.mapped(entry['target']), b'torn loader')
                        available = {refresh.digest(path.read_bytes()) for path in f.mapped('/efi').rglob('*')
                                     if path.is_file()}
                        direction = 'new' if all(entry['new'] in available for entry in loaders) else 'old'
                        refresh.recover(f.identity)
                        for entry in loaders:
                            target = f.mapped(entry['target'])
                            actual = refresh.digest(target.read_bytes()) if target.exists() else None
                            self.assertEqual(actual, entry[direction])
                        refresh.cleanup()
                        self.assert_clean_publication(f)
                        refresh.recover(f.identity)
                self.assertGreater(checked, 1)

    def test_interrupted_menu_only_refresh_keeps_promoted_loader_after_snapshot_rewrite(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            tag = self.state(f)['newest']
            self.boot(f, tag)
            firmware = f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes()
            f.put(f.mapped('/boot/vmlinuz-linux'), b'N' * (refresh.MIB + 1))
            checked = 0
            for stop in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                if not f.mapped('/efi/EFI/Emaki/boot-intent.json').exists():
                    continue
                with self.subTest(stop=stop):
                    checked += 1
                    f.put(f.mapped('/boot/grub/grub-btrfs.cfg'), 'externally regenerated snapshot menu\n')
                    self.boot(f, tag)
                    for _ in range(3):
                        refresh.refresh(f.identity)
                        self.assertEqual(self.state(f)['good'], tag)
                        self.assertEqual(self.state(f)['newest'], tag)
                        self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), firmware)
                        self.assertNotIn('chainloader ($emaki_esp)', f.mapped('/boot/grub/grub.cfg').read_text())
                        self.assert_clean_publication(f)
            self.assertGreater(checked, 1)

    def test_lost_esp_metadata_target_and_sidecar_can_recover_from_intent(self):
        for missing in ('/efi/EFI/Emaki/boot-state.json', '/efi/EFI/Emaki/trial.env'):
            with self.subTest(missing=missing), self.fixture(True) as f:
                checked = 0
                for stop in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                    intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                    if not intent.exists():
                        continue
                    with self.subTest(stop=stop):
                        checked += 1
                        record = json.loads(intent.read_text())
                        target = f.mapped(missing)
                        target.unlink(missing_ok=True)
                        target.with_name(target.name + '.emaki-new-' + record['tag']).unlink(missing_ok=True)
                        self.successful_recovery(f)
                        self.assertEqual(len(f.mapped('/efi/EFI/Emaki/trial.env').read_bytes()), 1024)
                self.assertGreater(checked, 1)

    def test_exhausted_candidate_missing_after_root_restore_can_refresh(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            self.exhaust(f)
            shutil.rmtree(f.mapped('/boot/emaki'))
            for name in ('/etc/default/grub', '/boot/grub/grub.cfg', '/boot/grub/grub-btrfs.cfg'):
                f.put(f.mapped(name), f.originals[name])
            f.put(f.mapped('/boot/vmlinuz-linux'), b'N' * (refresh.MIB + 1))
            self.successful_recovery(f)

    def test_failed_loader_is_not_rearmed_by_kernel_or_menu_changes(self):
        for changed in ('/boot/vmlinuz-linux', '/boot/initramfs-linux.img', '/etc/default/grub',
                        '/boot/grub/grub-btrfs.cfg', '/usr/share/emaki/grub/defaults.cfg'):
            with self.subTest(changed=changed), self.fixture(True) as f:
                refresh.refresh(f.identity)
                before = self.state(f)
                self.exhaust(f)
                data = f.mapped(changed).read_bytes()
                f.put(f.mapped(changed), data + (b'\nGRUB_TIMEOUT=7\nGRUB_CMDLINE_LINUX_DEFAULT=quiet\n'
                          if changed.endswith(('/grub', '/defaults.cfg'))
                          else b'\n# another snapshot\n' if changed.endswith('grub-btrfs.cfg') else b'new bytes'))
                f.output.seek(0)
                f.output.truncate(0)
                refresh.refresh(f.identity)
                self.assertEqual(self.state(f)['newest'], before['newest'])
                self.assertIn(b'emaki_trial=2\n', f.mapped('/efi/EFI/Emaki/trial.env').read_bytes())
                self.assertIn('old loader', f.output.getvalue().lower())
                self.firmware_kept(f)
                self.assert_clean_publication(f)

    def test_promoted_loader_kernel_update_does_not_create_trial(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            tag = self.state(f)['newest']
            self.boot(f, tag)
            before = self.state(f)
            f.put(f.mapped('/boot/vmlinuz-linux'), b'N' * (refresh.MIB + 1))
            refresh.refresh(f.identity)
            self.assertEqual(self.state(f)['newest'], before['good'])
            self.assertEqual(self.state(f)['good'], before['good'])

    def test_ordinary_boot_preserves_untried_candidate_and_is_idempotent(self):
        for tag in ('', 'a' * 32):
            with self.subTest(tag=tag), self.fixture(True) as f:
                refresh.refresh(f.identity)
                before = self.state(f)
                counter = f.mapped('/efi/EFI/Emaki/trial.env').read_bytes()
                self.boot(f, tag)
                self.boot(f, tag)
                self.assertEqual(f.mapped('/efi/EFI/Emaki/trial.env').read_bytes(), counter)
                self.firmware_kept(f)
                f.output.seek(0)
                f.output.truncate(0)
                refresh.refresh(f.identity)
                self.assertEqual(self.state(f), before)
                self.assertNotIn('did not complete a boot', f.output.getvalue())
                self.assertIn('chainloader ', f.mapped('/boot/grub/grub.cfg').read_text())

    def test_counter_failure_evidence_disarms_only_its_candidate_once(self):
        for reason in ('unwritable', 'unreadable'):
            for current in (False, True):
                with self.subTest(reason=reason, current=current), self.fixture(True) as f:
                    refresh.refresh(f.identity)
                    tag = self.state(f)['newest']
                    evidence = tag if current else 'f' * 32
                    cmdline = 'a' * 32 + ' emaki.trial=' + reason + ' emaki.trial_candidate=' + evidence
                    self.boot(f, cmdline)
                    self.assertEqual(refresh.trial_state()['emaki_trial'], '2' if current else '0')
                    with patch.object(refresh, 'atomic', wraps=refresh.atomic) as atomic, \
                            patch.object(refresh, 'refresh_menu', wraps=refresh.refresh_menu) as menu:
                        self.boot(f, cmdline)
                        atomic.assert_not_called()
                        menu.assert_not_called()
                    self.firmware_kept(f)

    def test_completion_recovery_preserves_untried_candidate(self):
        with self.fixture(True) as f:
            checked = 0
            for stop in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity)):
                if not f.mapped('/efi/EFI/Emaki/boot-intent.json').exists():
                    continue
                with self.subTest(stop=stop):
                    checked += 1
                    self.boot(f)
                    self.boot(f)
                    self.assertEqual(refresh.trial_state()['emaki_trial'], '0')
                    self.assertIn('chainloader ', f.mapped('/boot/grub/grub.cfg').read_text())
                    self.firmware_kept(f)
            self.assertGreater(checked, 1)

    def test_trial_menu_passes_failure_evidence_only_on_normal_kernel_lines(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            menu = f.mapped('/boot/grub/grub.cfg').read_text()
            self.assertIn('set emaki_trial_failure=unreadable', menu)
            self.assertIn('set emaki_trial_failure=unwritable', menu)
            kernels = [line for line in menu.splitlines() if line.startswith('linux ')]
            self.assertEqual(len(kernels), 4)
            for line in kernels:
                self.assertIn('emaki.trial=${emaki_trial_failure}', line)
                self.assertIn('emaki.trial_candidate=' + self.state(f)['newest'], line)
            snapshot = "menuentry 'snapshot' {\nlinux /snapshot/vmlinuz-linux root=overlay\n}\n"
            self.assertTrue(refresh.trial_menu(self.state(f)['newest'], 'ABCD-1234',
                                              menu + snapshot).endswith(snapshot))

    def test_missing_or_corrupt_counter_exhausts_without_rearming(self):
        for data in (None, b'damaged'.ljust(1024, b'#'), b'# GRUB Environment Block\nemaki_trial=0\n',
                     b'# GRUB Environment Block\n' + b'#' * 1100):
            with self.subTest(length=None if data is None else len(data)), self.fixture(True) as f:
                refresh.refresh(f.identity)
                before = self.state(f)
                path = f.mapped('/efi/EFI/Emaki/trial.env')
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                refresh.refresh(f.identity)
                self.assertEqual(self.state(f)['newest'], before['newest'])
                self.assertEqual(len(path.read_bytes()), 1024)
                self.assertIn(b'emaki_trial=2\n', path.read_bytes())
                self.firmware_kept(f)

    def test_100_mib_esp_keeps_owned_and_foreign_loaders_with_success_line(self):
        for foreign in (False, True):
            with self.subTest(foreign=foreign), self.fixture(False, foreign=foreign) as f, patch.object(
                    refresh.os, 'statvfs', return_value=SimpleNamespace(
                        f_bavail=70 * refresh.MIB, f_frsize=1, f_blocks=100 * refresh.MIB)):
                status, output = self.hook(f)
                self.assertEqual(status, 0)
                self.assertEqual(len(output.strip().splitlines()), 1)
                self.assertIn('kept', output.lower())
                if foreign:
                    self.assertEqual(output.strip(), refresh.FOREIGN)
                else:
                    self.assertIn('smaller than 256 MiB', output)
                for name, expected in f.originals.items():
                    self.assertEqual(f.mapped(name).read_bytes(), expected)

    @contextmanager
    def observe_durable_reads(self):
        """Observe kernel fsync and actual descriptor reads, independent of helpers."""
        events = []
        real_fsync, real_fdopen, real_replace = os.fsync, os.fdopen, os.replace
        real_read_bytes = Path.read_bytes

        def descriptor_path(fd):
            return Path(os.readlink('/proc/self/fd/' + str(fd)))

        def fsync(fd):
            result = real_fsync(fd)
            events.append(('fsync', descriptor_path(fd)))
            return result

        class Stream:
            def __init__(self, stream, path):
                self.stream, self.path = stream, path

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read(self, *args):
                value = self.stream.read(*args)
                events.append(('read', self.path))
                return value

        def fdopen(fd, *args, **kwargs):
            path = descriptor_path(fd)
            return Stream(real_fdopen(fd, *args, **kwargs), path)

        def read_bytes(path):
            value = real_read_bytes(path)
            events.append(('read', path))
            return value

        def replace(source, target):
            result = real_replace(source, target)
            events.append(('replace', Path(target)))
            return result

        with patch.object(os, 'fsync', side_effect=fsync), \
                patch.object(os, 'fdopen', side_effect=fdopen), \
                patch.object(os, 'replace', side_effect=replace), \
                patch.object(Path, 'read_bytes', new=read_bytes):
            yield events

    def durable_before_removal(self, events, target):
        replacements = [i for i, event in enumerate(events) if event == ('replace', target)]
        last = replacements[-1] if replacements else -1
        syncs = [i for i, event in enumerate(events) if i > last and event == ('fsync', target)]
        return bool(syncs and ('read', target) in events[syncs[-1] + 1:])

    def test_surviving_target_is_synced_and_reread_before_sidecar_deletion(self):
        with self.fixture(True) as f, self.observe_durable_reads() as events:
            real_unlink = Path.unlink
            checked, unsafe = [], []

            def unlink(path, *args, **kwargs):
                if path.exists() and re.search(r'\.emaki-(?:new|old)-', path.name):
                    target = path.with_name(re.split(r'\.emaki-(?:new|old)-', path.name)[0])
                    if target.exists():
                        checked.append(str(target))
                        if not self.durable_before_removal(events, target):
                            unsafe.append(str(target))
                return real_unlink(path, *args, **kwargs)

            with patch.object(Path, 'unlink', new=unlink):
                refresh.refresh(f.identity)
            self.assertTrue(checked, 'No surviving target sidecars were removed')
            self.assertFalse(unsafe, 'Sidecars removed without target fsync and reread: ' + repr(unsafe))

    def test_published_targets_are_synced_and_reread_before_intent_deletion(self):
        with self.fixture(True) as f, self.observe_durable_reads() as events:
            real_unlink = Path.unlink
            checked, unsafe = [], []

            def unlink(path, *args, **kwargs):
                if path.name == 'boot-intent.json' and path.exists():
                    record = json.loads(path.read_text())
                    for entry in record['entries']:
                        target = Path(entry['target'])
                        if target.exists():
                            checked.append(str(target))
                            if not self.durable_before_removal(events, target):
                                unsafe.append(str(target))
                return real_unlink(path, *args, **kwargs)

            with patch.object(Path, 'unlink', new=unlink):
                refresh.refresh(f.identity)
            self.assertTrue(checked, 'No published targets checked')
            self.assertFalse(unsafe, 'Intent removed without target fsync and reread: ' + repr(unsafe))

    def test_changed_embedded_loader_inputs_authorize_fresh_trial(self):
        for changed in ('/usr/lib/grub/x86_64-efi/normal.mod', '/usr/share/emaki/grub/unlock-24.pf2',
                        '/usr/lib/emaki/boot/refresh.py', '/usr/lib/emaki/boot/grub_artwork/initial/native.png',
                        '/usr/bin/grub-mkimage', '/usr/bin/grub-install'):
            with self.subTest(changed=changed), self.fixture(True) as f:
                refresh.refresh(f.identity)
                before = self.state(f)['newest']
                self.exhaust(f)
                path = f.mapped(changed)
                f.put(path, (path.read_bytes() if path.exists() else b'') + b'changed embedded input')
                refresh.refresh(f.identity)
                self.assertNotEqual(self.state(f)['newest'], before)
                self.assertIn(b'emaki_trial=0\n', f.mapped('/efi/EFI/Emaki/trial.env').read_bytes())
                self.firmware_kept(f)

    def test_completion_service_requires_generation_and_accepts_first_intent(self):
        text = Path(os.environ.get('EMAKI_BOOT_SERVICE_BASELINE',
                                   ROOT / 'systemd/emaki-boot-complete.service')).read_text()
        conditions = [line.strip() for line in text.splitlines() if line.startswith('Condition')]
        self.assertIn('ConditionPathExists=|/efi/EFI/Emaki/boot-intent.json', conditions)
        self.assertIn('ConditionPathExists=|/efi/EFI/Emaki/boot-state.json', conditions)
        self.assertIn('ConditionKernelCommandLine=emaki.generation', conditions)
        self.assertIn('ExecStart=/usr/bin/emaki-boot-refresh --mark-good', text)

    def test_trial_does_not_add_visible_menu_entry(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            tag = self.state(f)['newest']
            menu = f.mapped('/boot/grub/grub.cfg').read_text()
            self.assertNotIn('--id emaki-loader-trial', menu)
            self.assertEqual(len(re.findall(r'^\s*menuentry\s', menu, re.M)), 4)
            self.assertIn('chainloader ($emaki_esp)/EFI/Emaki/loader-' + tag + '.efi', menu)
            self.assertIn('set fallback=0', menu)


if __name__ == '__main__':
    unittest.main()
