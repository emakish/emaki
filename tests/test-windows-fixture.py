# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the guest fixture's no-write evidence without opening a device."""
import importlib.util
import copy
import hashlib
import io
import json
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import sys
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
FIXTURES = ROOT / 'tests/vm/fixtures'


def load(name):
    spec = importlib.util.spec_from_file_location(name, FIXTURES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsFixtureTests(unittest.TestCase):
    def test_alongside_overlay_installs_branch_transaction_hooks(self):
        harness = load('../alongside-update-check')
        guest = load('alongside-update-guest')
        hooks = ('00-emaki-alongside.hook', '04-emaki-update-boot.hook',
                 '06-emaki-update-boot.hook')
        data = harness.overlay_archive()
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for name in hooks:
                self.assertIn('hooks/' + name, archive.getnames())
                self.assertEqual(archive.extractfile('hooks/' + name).read(),
                                 (ROOT / 'grub' / name).read_bytes())
        self.assertEqual(guest.OVERLAY['hooks/'],
                         guest.OVERLAY['bin/'].parent / 'share/libalpm/hooks')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destinations = {prefix: root / prefix for prefix in guest.OVERLAY}
            for target in destinations.values():
                target.mkdir()
            for name in hooks:
                (destinations['hooks/'] / name).write_text('old packaged hook\n')
            archive = root / 'overlay.tar'
            archive.write_bytes(data)
            with patch.object(guest, 'OVERLAY', destinations), \
                    patch.object(guest, 'run', return_value=SimpleNamespace(stdout='', stderr='')):
                evidence = guest.overlay(archive)
                self.assertEqual(guest.overlay(archive)['changed'], [])
            changed = {item['path']: item for item in evidence['changed']}
            for name in hooks:
                target = destinations['hooks/'] / name
                content = (ROOT / 'grub' / name).read_bytes()
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(target.stat().st_mode & 0o777, 0o644)
                self.assertEqual(changed[str(target)]['sha256'], hashlib.sha256(content).hexdigest())
                self.assertFalse(changed[str(target)]['new'])

    def test_alongside_manifest_excludes_only_the_emaki_directory(self):
        fixture = load('alongside-update-guest')
        with tempfile.TemporaryDirectory() as directory:
            esp = Path(directory)
            names = ('EFI/Emaki/loader.efi', 'eFi/eMaKi/trial.env',
                     'EFI/EmakiOld/loader.efi', 'EFI/EmakiBackup/BCD',
                     'EFI/Microsoft/Boot/BCD', 'EFI/BOOT/BOOTX64.EFI')
            for name in names:
                path = esp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'original')
            mount = json.dumps({'filesystems': [{'options': 'ro'}]})
            with patch.object(fixture, 'Path', return_value=esp), \
                    patch.object(fixture, 'run', return_value=SimpleNamespace(stdout=mount)):
                before = fixture.manifest()
                for name in names[:2]:
                    (esp / name).write_bytes(b'updated')
                self.assertEqual(fixture.manifest()['windows_sha256'], before['windows_sha256'])
                for name in names[2:]:
                    with self.subTest(path=name):
                        self.assertIn(name, before['windows'])
                        (esp / name).write_bytes(b'changed')
                        self.assertNotEqual(fixture.manifest()['windows_sha256'], before['windows_sha256'])
                        (esp / name).write_bytes(b'original')

    def test_alongside_update_rejects_emaki_prefixed_foreign_paths(self):
        fixture = load('../alongside-update-check')
        with tempfile.TemporaryDirectory() as directory:
            vm = Path(directory)
            (vm / 'pkgs').mkdir()
            guest = SimpleNamespace(vm=vm, ssh=lambda *a, **kw: SimpleNamespace(stdout=b'installed'),
                                    sudo=lambda *a, **kw: SimpleNamespace(stdout=b'pacman exit 0', stderr=b''))
            for name, allowed in (('EFI/Emaki/loader.efi', True), ('eFi/eMaKi/trial.env', True),
                                  ('EFI/EmakiOld/loader.efi', False), ('EFI/EmakiBackup/BCD', False)):
                with self.subTest(path=name):
                    before = {'windows': {}, 'files': {}, 'mount': {'options': 'ro'}}
                    after = dict(before, files={name: 'new'})
                    with patch.object(fixture, 'manifest', side_effect=[before, after]), \
                            patch.object(fixture, 'record'), patch('builtins.print'):
                        if allowed:
                            fixture.update(guest, 'test', False)
                        else:
                            with self.assertRaisesRegex(RuntimeError, 'outside EFI/Emaki'):
                                fixture.update(guest, 'test', False)

    def test_alongside_refusal_recognizes_the_current_hibernation_message(self):
        fixture = load('../alongside-update-check')
        shared = load('../../../grub/emaki_boot/shared')
        with tempfile.TemporaryDirectory() as directory:
            vm = Path(directory)
            (vm / 'pkgs').mkdir()
            guest = SimpleNamespace(vm=vm, ssh=lambda *a, **kw: SimpleNamespace(stdout=b'installed'),
                                    sudo=lambda *a, **kw: SimpleNamespace(stdout=b'pacman exit 1',
                                                                         stderr=shared.HIBERNATED.encode()))
            unchanged = {'windows': {}, 'files': {}, 'mount': {'options': 'ro'}}
            with patch.object(fixture, 'manifest', return_value=unchanged), \
                    patch.object(fixture, 'record'), patch('builtins.print'):
                fixture.update(guest, 'hibernated', True)

    def test_no_fallback_variant_requires_new_x64_loader(self):
        fixture = load('windows-disk')
        baseline = {'no_fallback': True, 'esp': {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture.check_added_fallback(root, baseline, False)
            with self.assertRaisesRegex(RuntimeError, 'did not create'):
                fixture.check_added_fallback(root, baseline, True)
            fallback = root / 'EFI/BOOT/BOOTX64.EFI'
            fallback.parent.mkdir(parents=True)
            for content in (b'', b'foreign loader', b'MZ' + bytes(100)):
                fallback.write_bytes(content)
                with self.assertRaisesRegex(RuntimeError, 'not an x86-64 PE'):
                    fixture.check_added_fallback(root, baseline, True)
            loader = bytearray(128)
            loader[:2] = b'MZ'
            loader[60:64] = (64).to_bytes(4, 'little')
            loader[64:70] = b'PE\0\0\x64\x86'
            fallback.write_bytes(loader)
            with patch('builtins.print') as evidence:
                fixture.check_added_fallback(root, baseline, True)
                self.assertIn('sha256=', evidence.call_args.args[0])
            with self.assertRaisesRegex(RuntimeError, 'before installation'):
                fixture.check_added_fallback(root, baseline, False)
            baseline['esp']['EFI/BOOT/BOOTX64.EFI'] = 'old hash'
            with self.assertRaisesRegex(RuntimeError, 'baseline unexpectedly'):
                fixture.check_added_fallback(root, baseline, True)

    def test_recovery_digest_covers_whole_partition(self):
        fixture = load('windows-disk')
        with tempfile.TemporaryDirectory() as directory:
            recovery = Path(directory) / 'recovery'
            with recovery.open('wb') as stream:
                stream.truncate(3 * fixture.MIB)
            baseline = fixture.recovery_digest(recovery)
            for offset in (0, fixture.MIB + 17, 3 * fixture.MIB - 1):
                with recovery.open('r+b') as stream:
                    stream.seek(offset)
                    stream.write(b'x')
                self.assertNotEqual(fixture.recovery_digest(recovery), baseline)
                with recovery.open('r+b') as stream:
                    stream.seek(offset)
                    stream.write(b'\0')
                self.assertEqual(fixture.recovery_digest(recovery), baseline)

    def fixture_layout(self, recovery=True):
        fixture = load('windows-disk')
        parts = [dict(partn=n, start=start, size=size, partuuid=f'uuid-{n}',
                      parttype=f'type-{n}', partlabel=f'name-{n}', fstype=fs)
                 for n, start, size, fs in [(1, 2048, 100 * fixture.MIB, 'vfat'),
                                            (2, 206848, 16 * fixture.MIB, None),
                                            (3, 239616, 90 * 1024 * fixture.MIB, 'ntfs')]]
        if recovery:
            parts.append(dict(partn=4, start=parts[2]['start'] + parts[2]['size'] // 512,
                              size=1024 * fixture.MIB, partuuid='uuid-4', parttype='recovery',
                              partlabel='Windows RE tools', fstype=None))
        baseline = dict(disk={'children': parts}, esp={}, ntfs={},
                        gpt={str(p['partn']): {'Last sector': p['start'] + p['size'] // 512 - 1,
                                              'Attribute flags': '8000000000000001'} for p in parts})
        if recovery:
            baseline['recovery_sha256'] = 'preserved'
        freed = 32 * 1024 * fixture.MIB
        current = copy.deepcopy(baseline['disk'])
        current['children'][2]['size'] -= freed
        current['children'].append(dict(partn=len(parts) + 1,
                                       start=parts[2]['start'] + current['children'][2]['size'] // 512,
                                       size=freed))
        metadata = {int(n): copy.deepcopy(p) for n, p in baseline['gpt'].items()}
        metadata[3]['Last sector'] -= freed // 512
        return fixture, baseline, current, metadata, freed

    def check_layout(self, fixture, baseline, current, metadata, freed, recovery='preserved'):
        with ExitStack() as stack:
            for name, value in [('disk_info', current), ('raw_gpt', metadata),
                                ('hashes', {}), ('recovery_digest', recovery)]:
                stack.enter_context(patch.object(fixture, name, return_value=value))
            stack.enter_context(patch.object(fixture, 'gpt_metadata', side_effect=lambda text: text))
            stack.enter_context(patch.object(fixture, 'mounted', side_effect=lambda *args: nullcontext(Path('/tmp'))))
            stack.enter_context(patch.object(fixture.Path, 'exists', return_value=False))
            stack.enter_context(patch('builtins.print'))
            fixture.check(baseline, freed)

    def test_root_occupies_middle_extent_with_recovery_preserved(self):
        self.check_layout(*self.fixture_layout())

    def test_three_partition_baselines_remain_supported(self):
        self.check_layout(*self.fixture_layout(recovery=False))

    def test_recovery_corruption_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'recovery partition bytes changed'):
            self.check_layout(*self.fixture_layout(), recovery='changed')

    def test_recovery_metadata_change_is_rejected(self):
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        metadata[4]['Attribute flags'] = '0'
        with self.assertRaises(AssertionError):
            self.check_layout(fixture, baseline, current, metadata, freed)

    def test_root_must_fill_exactly_the_freed_middle_extent(self):
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][-1]['start'] += 2048
        with self.assertRaisesRegex(RuntimeError, 'root is not exactly inside the freed extent'):
            self.check_layout(fixture, baseline, current, metadata, freed)

    def test_digest_covers_backup_gpt_and_ntfs_tail(self):
        fixture = load('windows-refusals')
        with tempfile.TemporaryDirectory() as directory:
            disk, ntfs = (Path(directory) / name for name in ('disk', 'ntfs'))
            for path in (disk, ntfs):
                with path.open('wb') as stream:
                    stream.truncate(40 * fixture.MIB)
            fixture.DISK, fixture.NTFS = str(disk), str(ntfs)
            baseline = fixture.digest()
            for path in (disk, ntfs):
                with path.open('r+b') as stream:
                    stream.seek(-512, 2)
                    stream.write(b'changed')
                self.assertNotEqual(fixture.digest(), baseline)
                with path.open('r+b') as stream:
                    stream.seek(-512, 2)
                    stream.write(bytes(7))
                self.assertEqual(fixture.digest(), baseline)

    def test_write_counters_select_whole_device_write_fields(self):
        fixture = load('windows-refusals')
        with patch.object(fixture, 'Path') as path:
            path.return_value.resolve.return_value.name = 'vda'
            stat = path.return_value.__truediv__.return_value.__truediv__.return_value
            stat.read_text.return_value = '10 11 12 13 14 15 16 17 18 19 20'
            self.assertEqual(fixture.write_counters(), (14, 16))
            path.return_value.resolve.assert_called_once_with(strict=True)
            path.return_value.__truediv__.assert_called_once_with('vda')

    def test_injected_failure_never_executes_resize_command(self):
        fixture = load('windows-shrink')
        runner = fixture.ResizeRunner(refuse=True)
        with patch.object(fixture.ProbeRunner, 'run') as command:
            result = runner.run(['ntfsresize', '--no-action', '--size', '1024', '/dev/fixture'])
            self.assertIn('ERROR', result)
            self.assertTrue(runner.saw_dry_run)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
