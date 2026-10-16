# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the guest fixture's no-write evidence without opening a device."""
import importlib.util
import copy
import hashlib
import io
import json
import os
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


def firmware_entry(number, label, partition, partuuid, loader):
    """An efibootmgr -v line, made from the captured "Emaki" entry of a real listing."""
    captured = next(line for line in (ROOT / 'tests/fixtures/firmware/mac-booted.txt').read_text().splitlines()
                    if line.startswith('Boot0001* Emaki\t'))
    return (captured.replace('Boot0001* Emaki', f'Boot{number}* {label}')
            .replace('HD(1,', f'HD({partition},')
            .replace('22222222-2222-4222-8222-222222222222', partuuid)
            .replace('\\EFI\\Emaki\\grubx64.efi', loader) + '\n')


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

    def test_alongside_manifest_reads_windows_esp_as_device(self):
        fixture = load('alongside-update-guest')
        with tempfile.TemporaryDirectory() as directory:
            esp, device, marker = (Path(directory) / name for name in ('esp', 'windows-esp', 'marker.json'))
            names = ('EFI/Emaki/loader.efi', 'eFi/eMaKi/trial.env', 'EFI/BOOT/BOOTX64.EFI')
            for name in names:
                path = esp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'original')
            with device.open('wb') as stream:
                stream.truncate(3 * 1024**2)
            marker.write_text(json.dumps({'version': 1, 'esp_partuuid': 'w', 'emaki_esp_partuuid': 'e'}))
            mounts = {'stdout': ''}
            mount = json.dumps({'filesystems': [{'options': 'rw', 'partuuid': 'e'}]})

            def run(*argv, check=True):
                self.assertIn(argv[0], ('findmnt', 'efibootmgr'), 'the manifest must never mount')
                if '--source' in argv:
                    self.assertEqual(argv[argv.index('--source') + 1], os.path.realpath(device))
                    return SimpleNamespace(stdout=mounts['stdout'])
                return SimpleNamespace(stdout=mount if '--json' in argv else '')
            with patch.object(fixture, 'Path', return_value=esp), patch.object(fixture, 'run', side_effect=run), \
                    patch.object(fixture, 'WINDOWS_ESP', str(device)), patch.object(fixture, 'MARKER', marker):
                before = fixture.manifest()
                self.assertEqual(set(before['files']) - {'EFI', 'EFI/Emaki', 'eFi', 'eFi/eMaKi', 'EFI/BOOT'},
                                 set(names))
                self.assertEqual(before['marker']['emaki_esp_partuuid'], 'e')
                self.assertEqual(before['windows']['mounted'], [])
                for name in names:
                    (esp / name).write_bytes(b'updated')
                # Emaki's own files are not Windows' ESP: only the device bytes count there.
                self.assertEqual(fixture.manifest()['windows'], before['windows'])
                for offset in (0, 1024**2 + 17, 3 * 1024**2 - 1):
                    with self.subTest(offset=offset):
                        with device.open('r+b') as stream:
                            stream.seek(offset)
                            stream.write(b'x')
                        self.assertNotEqual(fixture.manifest()['windows']['sha256'], before['windows']['sha256'])
                        with device.open('r+b') as stream:
                            stream.seek(offset)
                            stream.write(b'\0')
                self.assertEqual(fixture.manifest()['windows'], before['windows'])
                mounts['stdout'] = '/mnt/windows-esp\n'
                self.assertEqual(fixture.manifest()['windows']['mounted'], ['/mnt/windows-esp'])

    def update_state(self, files=None, sha256='windows', mounted=(), options='rw,relatime', partuuid='own'):
        return {'files': files or {}, 'windows': {'sha256': sha256, 'mounted': list(mounted)},
                'mount': {'options': options, 'partuuid': partuuid},
                'marker': {'version': 1, 'esp_partuuid': 'win', 'emaki_esp_partuuid': 'own'}}

    def run_update(self, before, after, hibernated=False, stdout=b'pacman exit 0', stderr=b''):
        fixture = load('../alongside-update-check')
        with tempfile.TemporaryDirectory() as directory:
            vm = Path(directory)
            (vm / 'pkgs').mkdir()
            guest = SimpleNamespace(vm=vm, ssh=lambda *a, **kw: SimpleNamespace(stdout=b'installed'),
                                    sudo=lambda *a, **kw: SimpleNamespace(stdout=stdout, stderr=stderr))
            with patch.object(fixture, 'manifest', side_effect=[before, after]), \
                    patch.object(fixture, 'record'), patch('builtins.print'):
                fixture.update(guest, 'test', hibernated)

    def test_alongside_update_rejects_emaki_and_boot_prefixed_foreign_paths(self):
        for name, allowed in (('EFI/Emaki/loader.efi', True), ('eFi/eMaKi/trial.env', True),
                              ('EFI/BOOT/BOOTX64.EFI', True), ('efi/boot/grubx64.efi', True),
                              ('EFI/EmakiOld/loader.efi', False), ('EFI/EmakiBackup/BCD', False),
                              ('EFI/BOOTOLD/BOOTX64.EFI', False), ('EFI/Microsoft/Boot/BCD', False),
                              ('FSCK0000.REC', False)):
            with self.subTest(path=name):
                after = self.update_state(files={name: 'new'})
                if allowed:
                    self.run_update(self.update_state(), after)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'outside EFI/Emaki and EFI/BOOT'):
                        self.run_update(self.update_state(), after)

    def test_alongside_update_requires_windows_esp_untouched_and_own_esp_writable(self):
        for after, message in ((self.update_state(sha256='changed'), "Windows' ESP bytes changed"),
                               (self.update_state(mounted=['/efi']), "Windows' ESP is mounted"),
                               (self.update_state(options='ro,relatime'), 'not mounted read-write'),
                               (self.update_state(options='rw,ro'), 'not mounted read-write'),
                               (self.update_state(partuuid='win'), "names as Emaki's own")):
            with self.subTest(message=message, after=after):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.run_update(self.update_state(), after)
        with self.assertRaisesRegex(RuntimeError, "Windows' ESP is mounted"):
            self.run_update(self.update_state(mounted=['/mnt']), self.update_state())
        with self.assertRaisesRegex(RuntimeError, 'did not succeed'):
            self.run_update(self.update_state(), self.update_state(), stdout=b'pacman exit 1')

    def test_alongside_hibernated_windows_update_recognizes_the_current_refusal_message(self):
        shared = load('../../../grub/emaki_boot/shared')
        # Emaki writes only its own ESP: a hibernated Windows must not stop the update.
        self.run_update(self.update_state(), self.update_state(), hibernated=True)
        with self.assertRaisesRegex(RuntimeError, 'did not succeed'):
            self.run_update(self.update_state(), self.update_state(), hibernated=True,
                            stdout=b'pacman exit 1', stderr=shared.HIBERNATED.encode())
        with self.assertRaisesRegex(RuntimeError, 'hibernated Windows was reported'):
            self.run_update(self.update_state(), self.update_state(), hibernated=True,
                            stdout=shared.HIBERNATED.encode() + b'\npacman exit 0')

    def test_emaki_esp_requires_new_x64_removable_loader(self):
        fixture = load('windows-disk')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'did not create'):
                fixture.check_removable_loader(root)
            fallback = root / 'EFI/BOOT/BOOTX64.EFI'
            fallback.parent.mkdir(parents=True)
            for content in (b'', b'foreign loader', b'MZ' + bytes(100)):
                fallback.write_bytes(content)
                with self.assertRaisesRegex(RuntimeError, 'not an x86-64 PE'):
                    fixture.check_removable_loader(root)
            loader = bytearray(128)
            loader[:2] = b'MZ'
            loader[60:64] = (64).to_bytes(4, 'little')
            loader[64:70] = b'PE\0\0\x64\x86'
            fallback.write_bytes(loader)
            with patch('builtins.print') as evidence:
                fixture.check_removable_loader(root)
                self.assertIn('sha256=', evidence.call_args.args[0])
            # Windows' loader on Emaki's ESP would make the boot side treat it as shared.
            (root / 'EFI/Microsoft/Boot').mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, 'shared ESP'):
                fixture.check_removable_loader(root)

    def test_partition_digest_covers_whole_partition(self):
        fixture = load('windows-disk')
        with tempfile.TemporaryDirectory() as directory:
            recovery = Path(directory) / 'recovery'
            with recovery.open('wb') as stream:
                stream.truncate(3 * fixture.MIB)
            baseline = fixture.partition_digest(recovery)
            for offset in (0, fixture.MIB + 17, 3 * fixture.MIB - 1):
                with recovery.open('r+b') as stream:
                    stream.seek(offset)
                    stream.write(b'x')
                self.assertNotEqual(fixture.partition_digest(recovery), baseline)
                with recovery.open('r+b') as stream:
                    stream.seek(offset)
                    stream.write(b'\0')
                self.assertEqual(fixture.partition_digest(recovery), baseline)

    def fixture_layout(self, recovery=True, esp_first=True):
        fixture = load('windows-disk')
        parts = [dict(partn=n, path=f'/dev/vda{n}', start=start, size=size, partuuid=f'uuid-{n}',
                      parttype=f'type-{n}', partlabel=f'name-{n}', fstype=fs, label=None, uuid=None,
                      mountpoints=[None])
                 for n, start, size, fs in [(1, 2048, 100 * fixture.MIB, 'vfat'),
                                            (2, 206848, 16 * fixture.MIB, None),
                                            (3, 239616, 90 * 1024 * fixture.MIB, 'ntfs')]]
        if recovery:
            parts.append(dict(partn=4, path='/dev/vda4', start=parts[2]['start'] + parts[2]['size'] // 512,
                              size=1024 * fixture.MIB, partuuid='uuid-4', parttype='recovery',
                              partlabel='Windows RE tools', fstype=None, label=None, uuid=None,
                              mountpoints=[None]))
        baseline = dict(disk={'children': parts}, esp={}, ntfs={}, esp_sha256='windows esp',
                        gpt={str(p['partn']): {'Last sector': p['start'] + p['size'] // 512 - 1,
                                              'Attribute flags': '8000000000000001'} for p in parts})
        if recovery:
            baseline['recovery_sha256'] = 'preserved'
        freed = 32 * 1024 * fixture.MIB
        current = copy.deepcopy(baseline['disk'])
        current['children'][2]['size'] -= freed
        edge = parts[2]['start'] + current['children'][2]['size'] // 512
        esp = dict(partn=len(parts) + 1, path=f'/dev/vda{len(parts) + 1}', size=fixture.EMAKI_ESP_SIZE,
                   parttype=fixture.ESP_TYPE.upper(), fstype='vfat', label=fixture.EMAKI_ESP_LABEL,
                   uuid='EEEE-0001', partuuid='own-esp', mountpoints=[None])
        root = dict(partn=len(parts) + 2, path=f'/dev/vda{len(parts) + 2}', size=freed - fixture.EMAKI_ESP_SIZE,
                    parttype='linux', fstype='crypto_LUKS', label=None, uuid='r', partuuid='root',
                    mountpoints=[None])
        first, second = (esp, root) if esp_first else (root, esp)
        first['start'] = edge
        second['start'] = edge + first['size'] // 512
        current['children'] += [esp, root]
        metadata = {int(n): copy.deepcopy(p) for n, p in baseline['gpt'].items()}
        metadata[3]['Last sector'] -= freed // 512
        return fixture, baseline, current, metadata, freed

    def check_layout(self, fixture, baseline, current, metadata, freed, recovery='preserved',
                     windows_esp='windows esp', fat='FAT32', installed=False):
        digests = {fixture.DISK + '-part1': windows_esp, fixture.DISK + '-part4': recovery}
        with ExitStack() as stack:
            for name, value in [('disk_info', current), ('raw_gpt', metadata),
                                ('hashes', {})]:
                stack.enter_context(patch.object(fixture, name, return_value=value))
            stack.enter_context(patch.object(fixture, 'partition_digest', side_effect=digests.__getitem__))
            stack.enter_context(patch.object(fixture, 'run', return_value=fat + '\n'))
            stack.enter_context(patch.object(fixture, 'gpt_metadata', side_effect=lambda text: text))
            mounted = stack.enter_context(patch.object(fixture, 'mounted',
                                                       side_effect=lambda *args: nullcontext(Path('/tmp'))))
            loader = stack.enter_context(patch.object(fixture, 'check_removable_loader'))
            layout = stack.enter_context(patch.object(fixture, 'check_installed_layout'))
            stack.enter_context(patch.object(fixture, 'MARKER', SimpleNamespace(is_file=lambda: installed)))
            stack.enter_context(patch.object(fixture.Path, 'is_mount', return_value=installed))
            stack.enter_context(patch.object(fixture.Path, 'exists', return_value=False))
            stack.enter_context(patch('builtins.print'))
            fixture.check(baseline, freed)
        # Windows' ESP is read as a device only; Emaki's own ESP and NTFS are mounted read-only.
        sources = [call.args[0] for call in mounted.call_args_list]
        self.assertNotIn(fixture.DISK + '-part1', sources)
        esp = next(p for p in current['children'] if p.get('label') == fixture.EMAKI_ESP_LABEL)
        self.assertEqual(sources, [esp['path'], fixture.DISK + '-part3'])
        loader.assert_called_once_with(Path('/tmp'))
        if installed:
            layout.assert_called_once_with(baseline, esp)
        else:
            layout.assert_not_called()

    def test_own_esp_and_root_occupy_middle_extent_with_recovery_preserved(self):
        self.check_layout(*self.fixture_layout())
        self.check_layout(*self.fixture_layout(esp_first=False))
        self.check_layout(*self.fixture_layout(), installed=True)

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

    def test_windows_esp_change_or_mount_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Windows' ESP bytes changed"):
            self.check_layout(*self.fixture_layout(), windows_esp='changed')
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][0]['mountpoints'] = ['/efi']
        with self.assertRaisesRegex(RuntimeError, "Windows' ESP is mounted"):
            self.check_layout(fixture, baseline, current, metadata, freed)
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        del baseline['esp_sha256']
        with self.assertRaisesRegex(RuntimeError, 'no Windows ESP digest'):
            self.check_layout(fixture, baseline, current, metadata, freed)

    def test_new_partitions_must_fill_exactly_the_freed_middle_extent(self):
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][-2]['start'] += 2048
        with self.assertRaisesRegex(RuntimeError, 'not exactly inside the freed extent'):
            self.check_layout(fixture, baseline, current, metadata, freed)
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][-1]['size'] -= fixture.MIB
        with self.assertRaisesRegex(RuntimeError, 'do not exactly fill the freed extent'):
            self.check_layout(fixture, baseline, current, metadata, freed)
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'].pop()
        with self.assertRaisesRegex(RuntimeError, 'unexpected partition count'):
            self.check_layout(fixture, baseline, current, metadata, freed)

    def test_emaki_esp_must_be_a_new_1_gib_fat32_emaki_partition(self):
        for key, value, fat in (('label', 'NO NAME', 'FAT32'), ('fstype', 'ext4', 'FAT32'),
                                ('label', 'EMAKI', 'FAT16')):
            with self.subTest(key=key, value=value, fat=fat):
                fixture, baseline, current, metadata, freed = self.fixture_layout()
                current['children'][-2][key] = value
                with self.assertRaisesRegex(RuntimeError, '1 GiB FAT32 volume labelled EMAKI'):
                    self.check_layout(fixture, baseline, current, metadata, freed, fat=fat)
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][-2]['size'] += fixture.MIB
        current['children'][-1]['size'] -= fixture.MIB
        current['children'][-1]['start'] += 2048
        with self.assertRaisesRegex(RuntimeError, '1 GiB FAT32 volume labelled EMAKI'):
            self.check_layout(fixture, baseline, current, metadata, freed)
        fixture, baseline, current, metadata, freed = self.fixture_layout()
        current['children'][-2]['parttype'] = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
        with self.assertRaisesRegex(RuntimeError, 'exactly one ESP'):
            self.check_layout(fixture, baseline, current, metadata, freed)

    WINDOWS_ESP = '0f1e2d3c-aaaa-bbbb-cccc-112233445566'
    EMAKI_ESP = 'abcdefab-1111-2222-3333-444455556666'

    def installed_layout(self, marker=None, mode=0o100644, options='rw,relatime,fmask=0077',
                         fstab='UUID=EEEE-0001\t/efi\tvfat\trw,relatime,fmask=0077\t0 2\n',
                         order='0003,0001', emaki_entry=EMAKI_ESP, windows_entry=WINDOWS_ESP, extra=''):
        fixture = load('windows-disk')
        esp = {'path': '/dev/vda5', 'uuid': 'EEEE-0001', 'partuuid': self.EMAKI_ESP.upper()}
        baseline = {'windows_esp_partuuid': self.WINDOWS_ESP, 'windows_esp_uuid': 'abcd-1234',
                    'windows_boot_number': '0001'}
        if marker is None:
            marker = {'version': 1, 'esp_partuuid': self.WINDOWS_ESP.upper(),
                      'emaki_esp_partuuid': self.EMAKI_ESP, 'windows_esp_uuid': 'ABCD-1234'}
        firmware = (f'BootOrder: {order}\n'
                    + firmware_entry('0001', 'Windows Boot Manager', 1, windows_entry.upper(),
                                     '\\EFI\\Microsoft\\Boot\\bootmgfw.efi')
                    + firmware_entry('0003', 'Emaki', 5, emaki_entry, '\\EFI\\Emaki\\grubx64.efi')
                    + extra)

        def run(*argv):
            if argv[0] == 'findmnt':
                return f'/dev/vda5 vfat {options}\n'
            if argv[0] == 'efibootmgr':
                return firmware
            raise AssertionError(argv)
        status = SimpleNamespace(st_uid=0, st_gid=0, st_mode=mode)
        stub = SimpleNamespace(stat=lambda: status, read_text=lambda: json.dumps(marker))
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / 'fstab'
            table.write_text('UUID=root\t/\tbtrfs\trw\t0 0\n' + fstab)
            real = fixture.Path
            with patch.object(fixture, 'run', side_effect=run), \
                    patch.object(fixture, 'Path', side_effect=lambda value: table if value == '/etc/fstab' else real(value)), \
                    patch.object(fixture, 'MARKER', stub), patch('builtins.print'):
                fixture.check_installed_layout(baseline, esp)

    def test_installed_layout_requires_own_writable_esp_and_new_marker(self):
        self.installed_layout()
        cases = [
            ({'marker': {'version': 1, 'esp_partuuid': self.WINDOWS_ESP}}, 'does not describe the two ESPs'),
            ({'marker': {'version': 1, 'esp_partuuid': self.EMAKI_ESP, 'emaki_esp_partuuid': self.EMAKI_ESP,
                         'windows_esp_uuid': 'ABCD-1234'}}, 'does not describe the two ESPs'),
            ({'marker': {'version': 1, 'esp_partuuid': self.WINDOWS_ESP, 'emaki_esp_partuuid': self.EMAKI_ESP,
                         'windows_esp_uuid': 'ABCD-1234', 'shared': False}}, 'does not describe the two ESPs'),
            ({'mode': 0o100600}, 'root-owned 0644'),
            ({'options': 'ro,relatime'}, 'not mounted read-write'),
            ({'fstab': 'UUID=EEEE-0001\t/efi\tvfat\tro,noatime\t0 0\n'}, 'read-write, pass 2'),
            ({'fstab': 'UUID=EEEE-0001\t/efi\tvfat\trw,relatime\t0 0\n'}, 'read-write, pass 2'),
            ({'fstab': 'UUID=ABCD-1234\t/efi\tvfat\trw\t0 2\n'}, 'read-write, pass 2'),
            ({'order': '0001,0003'}, 'first firmware entry is not Emaki'),
            ({'emaki_entry': self.WINDOWS_ESP}, 'first firmware entry is not Emaki'),
            ({'windows_entry': self.EMAKI_ESP}, "no longer names Windows' ESP"),
            ({'extra': firmware_entry('0004', 'Emaki (previous)', 1, self.WINDOWS_ESP, '\\EFI\\Emaki\\b.efi')},
             "other than Windows Boot Manager names Windows' ESP"),
        ]
        for change, message in cases:
            with self.subTest(change=change):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.installed_layout(**change)

    def test_alongside_hibernate_attempt_reports_shared_esp_dropins(self):
        guest = load('alongside-update-guest')
        names = [path.name for path in guest.SHARED_DROPINS]
        self.assertEqual(set(names), {'50-emaki-shared-esp.conf'})
        self.assertEqual([path.parent.name for path in guest.SHARED_DROPINS],
                         [f'systemd-{mode}.service.d' for mode in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate')])
        harness = load('../alongside-update-check')
        state = {'efi_options': 'rw,relatime', 'update_env': 'counters', 'update_env_mtime': 1.0}
        for result, message in (
                ({'shared_esp_dropins': ['/etc/x/50-emaki-shared-esp.conf'], 'after': state}, 'shared-ESP hibernate drop-ins'),
                ({'shared_esp_dropins': [], 'after': dict(state, efi_options='ro,relatime')}, 'not mounted read-write'),
                ({'shared_esp_dropins': [], 'after': dict(state, efi_options='')}, 'not mounted read-write'),
                ({'shared_esp_dropins': [], 'after': dict(state, update_env='changed')}, 'counters changed'),
                ({'shared_esp_dropins': [], 'after': state}, None)):
            with self.subTest(message=message):
                argv = ['alongside-update-check', '--dir', str(Path.home() / 'VMs/unit-never-created'),
                        '--ssh-port', '1', 'hibernate-attempt']
                result = dict(result, before=state)
                with patch.object(harness, 'guest_step', return_value=result), patch.object(sys, 'argv', argv), \
                        patch('builtins.print'), patch.object(harness.os, 'umask'):
                    if message:
                        with self.assertRaisesRegex(RuntimeError, message):
                            harness.main()
                    else:
                        harness.main()

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
