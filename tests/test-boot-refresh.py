#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rootless boot generation validation and publication fault injection."""
from pathlib import Path
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
import io
import json
import os
import runpy
import signal
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub'), str(ROOT / 'upkeep')]
import emaki_boot_defaults as boot_defaults
from emaki_installer import boot
from emaki_installer.render import mkinitcpio_preset
sys.modules['emaki_boot.boot'] = boot
from emaki_boot import refresh

LUKS = '01234567-89ab-cdef-0123-456789abcdef'
FSUUID = 'abcdef01-2345-6789-abcd-ef0123456789'
GENERATION = Path('/boot/emaki') / ('a' * 32)


def image(prefix, payload=b''):
    data = bytearray(65536)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<H', data, 132, 0x8664)
    struct.pack_into('<H', data, 152, 0x20b)
    content = prefix.encode() + b'\0' + payload
    data[1024:1024 + len(content)] = content
    return bytes(data)


class ImageChecks(unittest.TestCase):
    def test_requires_exact_prefix_and_complete_encrypted_payload(self):
        prefix = '(cryptouuid/' + LUKS.replace('-', '') + ')/@' + str(GENERATION / 'grub')
        early = b'source (memdisk)/unlock.cfg\n'
        memdisk = b'tar-header\ncryptomount -u ' + LUKS.encode() + b'\nunlock-artwork'
        candidate = image(prefix, early + memdisk)
        refresh.verify_image(candidate, prefix, early, memdisk, LUKS)
        cases = [
            (candidate, prefix + '/other', early, memdisk, LUKS),
            (image(prefix + '/other', early + memdisk), prefix, early, memdisk, LUKS),
            (image(prefix, memdisk), prefix, early, memdisk, LUKS),
            (image(prefix, early + memdisk[:-1]), prefix, early, memdisk, LUKS),
            (candidate, prefix, early, memdisk, FSUUID),
        ]
        for args in cases:
            with self.subTest(args=args[1:2]), self.assertRaises(refresh.Refuse):
                refresh.verify_image(*args)

    def test_rejects_malformed_or_truncated_pe_headers(self):
        valid = image('/boot/emaki/g/grub')
        refresh.verify_image(valid, '/boot/emaki/g/grub')
        cases = [valid[:65535], b'XX' + valid[2:]]
        for offset, value in ((0x3c, b'\xff' * 4), (128, b'BAD!'),
                              (132, b'\x4c\x01'), (152, b'\x0b\x01')):
            damaged = bytearray(valid)
            damaged[offset:offset + len(value)] = value
            cases.append(bytes(damaged))
        for candidate in cases:
            with self.subTest(size=len(candidate)), self.assertRaises(refresh.Refuse):
                refresh.verify_image(candidate, '/boot/emaki/g/grub')


class ConfigurationChecks(unittest.TestCase):
    def test_packaged_menu_defaults_match_shared_installer_renderer(self):
        packaged = refresh.assignments((ROOT / 'grub/defaults.cfg').read_text())
        rendered = refresh.assignments(boot.grub_defaults())
        self.assertEqual(set(packaged), set(refresh.LEGACY))
        self.assertEqual(packaged, {key: rendered[key] for key in packaged})

    def test_future_defaults_replace_legacy_values_but_keep_personal_values(self):
        future = (ROOT / 'grub/defaults.cfg').read_text().replace('GRUB_TIMEOUT=5', 'GRUB_TIMEOUT=9')
        future = future.replace('GRUB_GFXMODE=1024x768,800x600,640x480,auto', 'GRUB_GFXMODE=auto')
        legacy = '\n'.join(f'{key}="{value}"' for key, value in refresh.LEGACY.items()) + '\n'
        # The migration baseline must survive future changes in the shared renderer too.
        with patch.object(boot, 'grub_defaults', return_value=future):
            migrated = refresh.migrate_defaults(legacy, future)
            custom = refresh.migrate_defaults(legacy.replace('GRUB_TIMEOUT="5"', 'GRUB_TIMEOUT="12"'), future)
        self.assertIn('GRUB_TIMEOUT=5\n', migrated)
        self.assertLess(migrated.index('GRUB_GFXMODE='), migrated.index(refresh.INCLUDE))
        self.assertEqual(refresh.assignments(custom)['GRUB_TIMEOUT'], '12')

    def test_defaults_remain_sourceable_after_removal_and_adopt_future_values(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'defaults.cfg'
            package.write_text('GRUB_TIMEOUT=9\n')
            for original in (boot.grub_defaults(), refresh.OLD_INCLUDE + '\nGRUB_CMDLINE_LINUX="resume=UUID=abc"\n'):
                migrated = refresh.migrate_defaults(original, (ROOT / 'grub/defaults.cfg').read_text())
                script = migrated.replace('/usr/share/emaki/grub/defaults.cfg', str(package))
                script += 'printf "%s" "$GRUB_TIMEOUT"\n'
                result = subprocess.run(['sh', '-ec', script], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, '9')
                package.unlink()
                result = subprocess.run(['sh', '-ec', script], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, '5')
                package.write_text('GRUB_TIMEOUT=9\n')

    def test_btrfs_title_migration_and_refresh_share_one_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'etc/default/grub-btrfs/config'
            defaults = root / 'usr/share/emaki/boot/grub-btrfs.conf'
            config.parent.mkdir(parents=True)
            defaults.parent.mkdir(parents=True)
            original = '# Upstream option\nGRUB_BTRFS_LIMIT="50"\nGRUB_BTRFS_SUBMENUNAME="Emaki snapshots"\n'
            defaults.write_text('GRUB_BTRFS_SUBMENUNAME="Future snapshots"\n')
            config.write_text(original)
            boot_defaults.apply(root)
            migrated = config.read_text()
            self.assertEqual(boot.grub_btrfs_config(migrated), migrated)
            self.assertEqual(boot.grub_btrfs_config(original), migrated)
            config.write_text(boot.grub_btrfs_config(original))
            boot_defaults.apply(root)
            self.assertEqual(config.read_text(), migrated)
            script = migrated.replace('/usr/share/emaki/boot/grub-btrfs.conf', str(defaults))
            result = subprocess.run(['sh', '-ec', script + '\nprintf "%s" "$GRUB_BTRFS_SUBMENUNAME"'],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, 'Future snapshots')
            defaults.unlink()
            subprocess.run(['sh', '-ec', script], capture_output=True, text=True, check=True)
            custom = original.replace('Emaki snapshots', 'My snapshots')
            self.assertEqual(boot.grub_btrfs_config(custom), custom)
            override = migrated + 'GRUB_BTRFS_SUBMENUNAME="My snapshots"\n'
            self.assertEqual(boot.grub_btrfs_config(override), override)

    def test_migration_preserves_disk_resume_and_personal_overrides(self):
        defaults = (ROOT / 'grub/defaults.cfg').read_text()
        before = boot.grub_defaults(True, LUKS, FSUUID, 314159)
        before = before.replace('GRUB_TIMEOUT=5', 'GRUB_TIMEOUT=12')
        before += '# Personal menu choice\nGRUB_SAVEDEFAULT=true\n'
        after = refresh.migrate_defaults(before, defaults)
        self.assertIn(refresh.INCLUDE + '\n', after)
        self.assertLess(after.index('GRUB_GFXMODE='), after.index(refresh.INCLUDE))
        self.assertIn('GRUB_TIMEOUT=12\n', after)
        self.assertIn('# Personal menu choice\nGRUB_SAVEDEFAULT=true\n', after)
        old, new = refresh.assignments(before), refresh.assignments(after)
        for key in ('GRUB_CMDLINE_LINUX', 'GRUB_CMDLINE_LINUX_DEFAULT', 'GRUB_DISABLE_OS_PROBER',
                    'GRUB_ENABLE_CRYPTODISK', 'GRUB_PRELOAD_MODULES'):
            self.assertEqual(new[key], old[key], key)
        self.assertEqual(refresh.migrate_defaults(after, defaults), after)

    def test_migration_keeps_windows_os_prober_enabled(self):
        before = boot.grub_defaults() + '\nGRUB_DISABLE_OS_PROBER=false\n'
        after = refresh.migrate_defaults(before, (ROOT / 'grub/defaults.cfg').read_text())
        self.assertEqual(refresh.assignments(after)['GRUB_DISABLE_OS_PROBER'], 'false')

    def test_migration_refuses_foreign_defaults(self):
        with self.assertRaises(refresh.Refuse):
            refresh.migrate_defaults('GRUB_DISTRIBUTOR="Arch"\n', 'GRUB_TIMEOUT=5\n')

    def test_private_generation_never_runs_or_reconfigures_mkinitcpio(self):
        with tempfile.TemporaryDirectory(prefix='boot-generate-') as directory:
            stage = Path(directory)
            (stage / 'btrfs-config').write_text('GRUB_BTRFS_DIRNAME="/@snapshots"\n')
            with patch.object(refresh, 'run') as command:
                refresh.generate(stage)
            calls = [list(map(str, call.args[0])) for call in command.call_args_list]
            self.assertEqual(calls, [
                ['mount', '--bind', str(stage / 'defaults'), '/etc/default/grub'],
                ['mount', '--bind', str(stage / 'btrfs-config'), '/etc/default/grub-btrfs/config'],
                ['mount', '--bind', str(stage / 'grub'), '/boot/grub'],
                ['grub-mkconfig', '-o', '/boot/grub/grub.cfg'],
            ])

    def test_menu_keeps_live_kernels_and_tags_only_ordinary_entries(self):
        for fsroot in ('', '/@'):
            with self.subTest(fsroot=fsroot):
                ordinary = []
                for kernel in ('linux', 'linux-lts'):
                    ordinary += [f'linux {fsroot}/boot/vmlinuz-{kernel} root=UUID={FSUUID}',
                                 f'initrd {fsroot}/boot/initramfs-{kernel}.img']
                snapshot = ('linux /@snapshots/42/snapshot/boot/vmlinuz-linux root=UUID=' + FSUUID
                            + '\ninitrd /@snapshots/42/snapshot/boot/initramfs-linux.img\n')
                before = '\n'.join(ordinary) + '\n' + snapshot
                before += f'loadfont {fsroot}/boot/grub/fonts/unicode.pf2\n'
                before += 'source ${prefix}/grub-btrfs.cfg\nsource $prefix/grub-btrfs.cfg\n'
                after = refresh.rewrite_menu(before, GENERATION, fsroot)
                self.assertIn(snapshot, after)
                for kernel in ('linux', 'linux-lts'):
                    self.assertIn(f'linux {fsroot}/boot/vmlinuz-{kernel} ', after)
                    self.assertIn(' emaki.generation=${emaki_generation}', after)
                    self.assertIn(f'initrd {fsroot}/boot/initramfs-{kernel}.img\n', after)
                self.assertIn(f'loadfont {fsroot}{GENERATION}/grub/fonts/unicode.pf2\n', after)
                self.assertEqual(after.count(f'source ($root){fsroot}/boot/grub/grub-btrfs.cfg\n'), 2)

    def test_menu_validation_rejects_wrong_disk_or_frozen_kernels(self):
        identity = {'uuid': FSUUID, 'luks': LUKS, 'fsroot': '/@'}
        rows = []
        for kernel in ('linux', 'linux-lts'):
            rows += [f'linux /@/boot/vmlinuz-{kernel} root=UUID={FSUUID} '
                     f'cryptdevice=UUID={LUKS}:emaki-root emaki.generation=${{emaki_generation}}',
                     f'initrd /@/boot/initramfs-{kernel}.img']
        valid = '\n'.join(rows) + '\n'
        refresh.verify_menu(valid, identity, GENERATION)
        for bad in (valid.replace(FSUUID, LUKS), valid.replace(LUKS, FSUUID),
                    valid.replace('/boot/initramfs-linux-lts.img', str(GENERATION / 'initramfs-linux-lts.img')),
                    valid.replace('/boot/vmlinuz-linux', str(GENERATION / 'vmlinuz-linux'))):
            with self.subTest(menu=bad), self.assertRaises(refresh.Refuse):
                refresh.verify_menu(bad, identity, GENERATION)


class LoaderPayloadChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='boot-payload-')
        self.addCleanup(self.directory.cleanup)
        self.grub = Path(self.directory.name) / 'grub'
        self.contents = {
            'x86_64-efi/normal.mod': b'normal-module\0\xff',
            'x86_64-efi/configfile.mod': b'configfile-module\0',
            'x86_64-efi/moddep.lst': b'normal: configfile\n',
            'x86_64-efi/modinfo.sh': b'grub_modinfo_target_cpu=x86_64\n',
            'fonts/unicode.pf2': b'font-payload',
            'locale/en.mo': b'locale-payload',
        }
        for name, data in self.contents.items():
            path = self.grub / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.grub / 'grub.cfg').write_text('old menu must not overwrite dispatcher\n')
        self.font = (ROOT / 'installer/assets/grub/unlock-24.pf2').read_bytes()

    def unpack(self, data):
        self.assertEqual(data[257:263], b'ustar\0')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = archive.getmembers()
            self.assertEqual([m.name for m in members], sorted(m.name for m in members))
            for member in members:
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.mode, 0o644)
                self.assertEqual((member.uid, member.gid), (0, 0))
                self.assertEqual(member.pax_headers, {})
                self.assertTrue(member.isfile())
            return {m.name: archive.extractfile(m).read() for m in members}

    def test_encrypted_payload_is_self_contained_across_root_restore(self):
        for fsroot in ('/@', ''):
            with self.subTest(fsroot=fsroot):
                identity = {'luks': LUKS, 'fsroot': fsroot, 'uuid': FSUUID}
                disk = '(cryptouuid/' + LUKS.replace('-', '') + ')'
                cfg = f'cryptomount -u {LUKS}\nset root={disk}\n'
                result = refresh.loader_payload(self.grub, GENERATION, identity, cfg, self.font)
                self.assertEqual(result, refresh.loader_payload(self.grub, GENERATION, identity, cfg, self.font))
                prefix, early, memdisk = result
                self.assertEqual(prefix, '(memdisk)/boot/grub')
                self.assertIn(b'set prefix=(memdisk)/boot/grub\n', early)
                self.assertIn(f'set emaki_generation={GENERATION.name}\n'.encode(), early)
                self.assertIn(b'export emaki_generation\n', early)
                self.assertIn(b'source (memdisk)/unlock.cfg\n', early)
                files = self.unpack(memdisk)
                for name, expected in self.contents.items():
                    self.assertEqual(files['boot/grub/' + name], expected)
                self.assertEqual(files['visible.pf2'], self.font)
                self.assertIn(f'cryptomount -u {LUKS}'.encode(), files['unlock.cfg'])
                self.assertTrue(any(n.startswith('unlock-') and n.endswith('.png') for n in files))
                self.assertIn(
                    f'if [ -f {disk}{fsroot}/boot/grub/grub.cfg ]; then\n'
                    f'  configfile {disk}{fsroot}/boot/grub/grub.cfg\n'
                    f'else\n  configfile {disk}{fsroot}{GENERATION}/grub/grub.cfg\nfi\n', files['boot/grub/grub.cfg'].decode())
                self.assertEqual(files['boot/grub/grub-btrfs.cfg'].decode(),
                                 f'configfile {disk}{fsroot}/boot/grub/grub-btrfs.cfg\n')

    def test_plain_root_retains_filesystem_search_and_embeds_updated_modules(self):
        identity = {'luks': None, 'fsroot': '', 'uuid': FSUUID}
        cfg = f'search.fs_uuid {FSUUID} root\nset prefix=($root){GENERATION}/grub\n'
        first = refresh.loader_payload(self.grub, GENERATION, identity, cfg, b'')
        prefix, early, memdisk = first
        self.assertEqual(prefix, '(memdisk)/boot/grub')
        self.assertTrue(early.startswith(b'source (memdisk)/start.cfg\nset prefix=(memdisk)/boot/grub\n'))
        self.assertIn(f'set emaki_generation={GENERATION.name}\n'.encode(), early)
        self.assertIn(b'export emaki_generation\n', early)
        files = self.unpack(memdisk)
        self.assertEqual(files['start.cfg'], cfg.encode())
        self.assertNotIn('unlock.cfg', files)
        self.assertIn(b'if [ -f ($root)/boot/grub/grub.cfg ]; then\n'
                      b'  configfile ($root)/boot/grub/grub.cfg\n', files['boot/grub/grub.cfg'])
        updated = b'new-package-normal-module\0'
        (self.grub / 'x86_64-efi/normal.mod').write_bytes(updated)
        second = refresh.loader_payload(self.grub, GENERATION, identity, cfg, b'')
        self.assertNotEqual(first[2], second[2])
        self.assertEqual(self.unpack(second[2])['boot/grub/x86_64-efi/normal.mod'], updated)
        self.assertEqual(files['boot/grub/x86_64-efi/normal.mod'], self.contents['x86_64-efi/normal.mod'])


class RefreshOrchestrationChecks(unittest.TestCase):
    """Real staging/publication on private files; external generators are fakes."""
    @contextmanager
    def fixture(self, encrypted, foreign=False, fail=None):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='boot-orchestration-', dir=evidence) as directory:
            root = Path(directory)

            def mapped(value):
                path = Path(value)
                return root / str(path).lstrip('/') if path.is_absolute() and not path.is_relative_to(root) else path

            def put(path, data):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.encode() if isinstance(data, str) else data)

            identity = {'fsroot': '/@' if encrypted else '', 'luks': LUKS if encrypted else None,
                        'uuid': FSUUID, 'fstype': 'btrfs' if encrypted else 'ext4', 'esp_uuid': 'ABCD-1234'}
            originals = {
                '/efi/EFI/Emaki/grubx64.efi': image('/old-prefix'),
                '/efi/EFI/BOOT/BOOTX64.EFI': b'foreign EFI loader' if foreign else image('/old-prefix'),
                '/etc/default/grub': boot.grub_defaults(False, identity['luks'], FSUUID, 123).encode(),
                '/etc/mkinitcpio.conf': b'HOOKS=(base udev encrypt resume filesystems)\n',
                '/boot/grub/grub.cfg': b'old canonical menu\n',
                '/boot/grub/grub-btrfs.cfg': b'old snapshot menu\n',
            }
            for name, data in originals.items():
                put(mapped(name), data)
            put(mapped('/usr/share/emaki/grub/defaults.cfg'), (ROOT / 'grub/defaults.cfg').read_bytes())
            put(mapped('/usr/share/emaki/grub/unlock-24.pf2'),
                (ROOT / 'installer/assets/grub/unlock-24.pf2').read_bytes())
            modules = mapped('/usr/lib/grub/x86_64-efi')
            for name in refresh.IMAGE_MODULES:
                put(modules / (name + '.mod'), b'module ' + name.encode())
            for name in ('vmlinuz-linux', 'vmlinuz-linux-lts', *refresh.INITRAMFS):
                put(mapped('/boot') / name, b'K' * (refresh.MIB + 1))
            for kernel in ('linux', 'linux-lts'):
                put(mapped('/etc/mkinitcpio.d') / (kernel + '.preset'), mkinitcpio_preset(kernel))
            commands = []

            def fake_run(arguments):
                args = list(map(str, arguments))
                command = args[0]
                commands.append(args)
                if command == fail:
                    raise refresh.Refuse('injected ' + command + ' failure')
                options = dict(arg.split('=', 1) for arg in args[1:] if arg.startswith('--') and '=' in arg)
                if command == 'grub-install' and '--version' in args:
                    return 'grub-install test-version'
                if command == 'grub-install':
                    self.assertIn('--no-nvram', args)
                    generation = Path(options['--boot-directory'])
                    staged_esp = Path(options['--efi-directory'])
                    self.assertNotEqual(staged_esp, mapped('/efi'))
                    for module in modules.glob('*.mod'):
                        put(generation / 'grub/x86_64-efi' / module.name, module.read_bytes())
                    expected = identity['fsroot'] + str(generation / 'grub')
                    if encrypted:
                        expected = '(cryptouuid/' + LUKS.replace('-', '') + ')' + expected
                        cfg = f'cryptomount -u {LUKS}\n'
                    else:
                        cfg = f'search.fs_uuid {FSUUID} root\nset prefix=($root){expected}\n'
                    put(generation / 'grub/x86_64-efi/load.cfg', cfg)
                    put(staged_esp / 'EFI/Emaki/grubx64.efi', image(expected))
                elif command == 'grub-mkimage':
                    self.assertIn('--compression=none', args)
                    early = Path(options['--config']).read_bytes()
                    memdisk = Path(options['--memdisk']).read_bytes()
                    put(Path(options['--output']), image(options['--prefix'], early + memdisk))
                elif command == 'unshare':
                    generation = Path(args[-1])
                    rows = []
                    for kernel in refresh.KERNELS:
                        if not mapped('/boot/vmlinuz-' + kernel).exists():
                            continue
                        for suffix in ('', '-fallback'):
                            name = f'initramfs-{kernel}{suffix}.img'
                            rows += [f"menuentry 'Emaki {kernel}{suffix}' {{",
                                     f'linux {identity["fsroot"]}/boot/vmlinuz-{kernel} root=UUID={FSUUID}'
                                     + (f' cryptdevice=UUID={LUKS}:emaki-root' if encrypted else ''),
                                     f'initrd {identity["fsroot"]}/boot/{name}', '}']
                    put(generation / 'grub/grub.cfg', '\n'.join(rows) + '\n')
                    put(generation / 'grub/grub-btrfs.cfg', 'new snapshot menu\n')
                elif command == 'lsinitcpio':
                    return 'hooks/emaki-resume\nhooks/emaki-resume-upstream\netc/cryptsetup-keys.d/emaki-root.key'
                elif command not in ('sync', 'grub-script-check'):
                    self.fail('unexpected external command: ' + repr(args))
                return ''

            with ExitStack() as stack:
                stack.enter_context(patch.object(refresh, 'Path', side_effect=mapped))
                stack.enter_context(patch.object(refresh, 'MODULES', modules))
                stack.enter_context(patch.object(refresh, 'run', side_effect=fake_run))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                yield SimpleNamespace(root=root, mapped=mapped, put=put, identity=identity,
                                      originals=originals, commands=commands, output=output)

    def exercise(self, encrypted, foreign=False, fail=None):
        with self.fixture(encrypted, foreign, fail) as f:
            if foreign or fail:
                with self.assertRaises(refresh.Refuse):
                    refresh.refresh(f.identity)
                for name, expected in f.originals.items():
                    self.assertEqual(f.mapped(name).read_bytes(), expected, name)
                self.assertNotIn('Boot loader and menu updated', f.output.getvalue())
                return
            refresh.refresh(f.identity)
            self.assertFalse(list(f.mapped('/efi').glob('.emaki-stage-*')))
            vendor = f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes()
            self.assertEqual(vendor, f.originals['/efi/EFI/Emaki/grubx64.efi'])
            self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(),
                             f.originals['/efi/EFI/BOOT/BOOTX64.EFI'])
            self.assertIn(refresh.INCLUDE, f.mapped('/etc/default/grub').read_text())
            self.assertEqual(f.mapped('/etc/mkinitcpio.conf').read_bytes(),
                             f.originals['/etc/mkinitcpio.conf'])
            generations = [p for p in f.mapped('/boot/emaki').iterdir() if (p / 'manifest.json').exists()]
            self.assertEqual(len(generations), 1)
            generation = generations[0]
            menu = f.mapped('/boot/grub/grub.cfg').read_text()
            self.assertEqual(menu, (generation / 'grub/grub.cfg').read_text())
            manifest = json.loads((generation / 'manifest.json').read_text())
            candidate = f.mapped('/efi/EFI/Emaki') / ('loader-' + generation.name + '.efi')
            self.assertEqual(manifest['efi_sha256'], refresh.digest(candidate.read_bytes()))
            self.assertEqual(manifest['prefix'], '(memdisk)/boot/grub')
            for name in refresh.INITRAMFS:
                self.assertIn('/boot/' + name, menu)
                self.assertFalse((generation / name).exists())
            for kernel in refresh.KERNELS:
                self.assertFalse((generation / ('vmlinuz-' + kernel)).exists())
            self.assertFalse(list(f.root.rglob('*.emaki-old-*')))
            self.assertFalse(any(c[0] in ('mkinitcpio', 'lsinitcpio') for c in f.commands))

    def test_encrypted_and_plain_refresh_preserve_both_firmware_loaders(self):
        for encrypted in (True, False):
            with self.subTest(encrypted=encrypted):
                self.exercise(encrypted)

    def test_foreign_fallback_refuses_without_changes(self):
        for encrypted in (True, False):
            with self.subTest(encrypted=encrypted):
                self.exercise(encrypted, foreign=True)

    def test_generator_failures_leave_original_loaders_settings_and_menu(self):
        for encrypted in (True, False):
            for command in ('grub-install', 'grub-mkimage', 'unshare', 'grub-script-check'):
                with self.subTest(encrypted=encrypted, command=command):
                    self.exercise(encrypted, fail=command)

    def test_success_then_kernel_upgrade_and_refusal_still_boots_live_kernel(self):
        for encrypted in (True, False):
            with self.subTest(encrypted=encrypted), self.fixture(encrypted) as f:
                refresh.refresh(f.identity)
                primary = f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes()
                generation, = f.mapped('/boot/emaki').iterdir()
                menu = (generation / 'grub/grub.cfg').read_text()
                f.put(f.mapped('/boot/vmlinuz-linux'), b'upgraded kernel' * 100000)
                f.put(f.mapped('/boot/initramfs-linux.img'), b'upgraded initramfs' * 100000)
                for name in ('vmlinuz-linux-lts', 'initramfs-linux-lts.img', 'initramfs-linux-lts-fallback.img'):
                    f.mapped('/boot/' + name).unlink()
                f.put(f.mapped('/etc/mkinitcpio.conf'), 'HOOKS=($CUSTOM_HOOKS)\n')
                with patch.object(refresh.os, 'statvfs', return_value=SimpleNamespace(
                        f_bavail=0, f_frsize=1, f_blocks=1024 * refresh.MIB)):
                    with self.assertRaises(refresh.Refuse):
                        refresh.refresh(f.identity)
                self.assertEqual(primary, f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes())
                self.assertEqual(menu, (generation / 'grub/grub.cfg').read_text())
                self.assertIn(f'linux {f.identity["fsroot"]}/boot/vmlinuz-linux ', menu)
                self.assertIn(f'initrd {f.identity["fsroot"]}/boot/initramfs-linux.img', menu)
                self.assertNotIn(str(generation / 'vmlinuz'), menu)
                self.assertFalse((generation / 'vmlinuz-linux').exists())
                # Kernel removal and customized initramfs hooks no longer prevent menu refresh.
                refresh.refresh(f.identity)
                self.assertNotIn('vmlinuz-linux-lts', f.mapped('/boot/grub/grub.cfg').read_text())

    def test_menu_only_refresh_retains_root_and_kernel_validation(self):
        for fault in ('grub-symlink', 'missing-initramfs', 'incomplete-kernel'):
            with self.subTest(fault=fault), self.fixture(True) as f:
                refresh.refresh(f.identity)
                before = {p: p.read_bytes() for p in f.mapped('/efi').rglob('*') if p.is_file()}
                if fault == 'grub-symlink':
                    grub = f.mapped('/boot/grub')
                    private = f.root / 'personal-grub'
                    grub.rename(private)
                    grub.symlink_to(private)
                elif fault == 'missing-initramfs':
                    f.mapped('/boot/initramfs-linux.img').unlink()
                else:
                    f.put(f.mapped('/boot/vmlinuz-linux'), b'incomplete kernel')
                with self.assertRaises(refresh.Refuse):
                    refresh.refresh(f.identity)
                self.assertEqual(before, {p: p.read_bytes() for p in f.mapped('/efi').rglob('*') if p.is_file()})

    def test_missing_fallback_refuses_without_changing_primary(self):
        with self.fixture(False) as f:
            f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').unlink()
            with self.assertRaises(refresh.Refuse):
                refresh.refresh(f.identity)
            self.assertEqual(f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes(),
                             f.originals['/efi/EFI/Emaki/grubx64.efi'])
            self.assertFalse(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').exists())

    def test_small_windows_esp_refuses_before_staging(self):
        with self.fixture(False) as f, patch.object(refresh.os, 'statvfs',
                return_value=SimpleNamespace(f_bavail=100 * refresh.MIB, f_frsize=1,
                                             f_blocks=100 * refresh.MIB)):
            with self.assertRaisesRegex(refresh.Refuse, 'smaller than 256 MiB'):
                refresh.refresh(f.identity)
            for name, expected in f.originals.items():
                self.assertEqual(f.mapped(name).read_bytes(), expected)
            self.assertFalse(f.mapped('/boot/emaki').exists())

    def complete_boot(self, f, generation):
        f.put(f.mapped('/proc/cmdline'), f'root=UUID={FSUUID} emaki.generation={generation.name}\n')
        f.mapped('/usr/lib/modules/test-kernel').mkdir(parents=True, exist_ok=True)
        with patch.object(refresh.os, 'uname', return_value=SimpleNamespace(release='test-kernel')):
            refresh.mark_good(f.identity)

    def test_boot_completion_promotes_and_retains_exact_good_and_newest(self):
        with self.fixture(False) as f:
            refresh.refresh(f.identity)
            good, = f.mapped('/boot/emaki').iterdir()
            self.complete_boot(f, good)
            promoted = f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes()
            self.assertEqual(promoted, f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes())
            for change in range(3):
                f.put(f.mapped('/usr/lib/grub/x86_64-efi/normal.mod'), b'changed module ' + bytes([change]))
                refresh.refresh(f.identity)
                generations = list(f.mapped('/boot/emaki').iterdir())
                self.assertEqual(len(generations), 2)
                self.assertIn(good, generations)
                self.assertEqual(promoted, f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes())
            newest, = [p for p in generations if p != good]
            self.complete_boot(f, newest)
            self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(),
                             f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes())
            self.assertNotEqual(promoted, f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes())
            self.assertEqual(list(f.mapped('/boot/emaki').iterdir()), [newest])

    def test_boot_completion_refuses_unproven_or_corrupt_generation(self):
        for fault in ('no-tag', 'missing-manifest', 'missing-modules', 'corrupt-candidate'):
            with self.subTest(fault=fault), self.fixture(False) as f:
                refresh.refresh(f.identity)
                generation, = f.mapped('/boot/emaki').iterdir()
                f.put(f.mapped('/proc/cmdline'), '' if fault == 'no-tag'
                      else f'emaki.generation={generation.name}\n')
                if fault != 'missing-modules':
                    f.mapped('/usr/lib/modules/test-kernel').mkdir(parents=True)
                if fault == 'missing-manifest':
                    (generation / 'manifest.json').unlink()
                if fault == 'corrupt-candidate':
                    f.put(f.mapped('/efi/EFI/Emaki') / ('loader-' + generation.name + '.efi'), b'corrupt')
                with patch.object(refresh.os, 'uname', return_value=SimpleNamespace(release='test-kernel')):
                    if fault in ('no-tag', 'missing-manifest'):
                        refresh.mark_good(f.identity)
                    else:
                        with self.assertRaises(refresh.Refuse):
                            refresh.mark_good(f.identity)
                self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(),
                                 f.originals['/efi/EFI/BOOT/BOOTX64.EFI'])

    def freeze_publication_steps(self, f, operation, include_writes=False):
        """Capture completed writes and renames without exception rollback."""
        snapshots = []
        replace, write = refresh.os.replace, refresh.write
        with tempfile.TemporaryDirectory(prefix='powercut-', dir=f.root.parent) as directory:
            def snapshot(target):
                frozen = Path(directory) / str(len(snapshots))
                shutil.copytree(f.root, frozen, symlinks=True)
                snapshots.append((frozen, str(target)))
            def capture(source, target):
                replace(source, target)
                snapshot(target)
            def capture_write(target, data):
                write(target, data)
                if include_writes:
                    snapshot(target)
            with patch.object(refresh.os, 'replace', side_effect=capture), \
                    patch.object(refresh, 'write', side_effect=capture_write):
                operation()
            self.assertTrue(snapshots)
            for frozen, target in snapshots:
                shutil.rmtree(f.root)
                shutil.copytree(frozen, f.root, symlinks=True)
                yield target

    def assert_clean_publication(self, f):
        self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())
        self.assertFalse(list(f.mapped('/efi').glob('.emaki-stage-*')))
        self.assertFalse(list(f.root.rglob('*.emaki-*')))

    def test_menu_commit_precedes_state_publication_and_intent_removal(self):
        # Represent the view of a reader which cannot replay the filesystem log.
        # A file readback alone does not advance this committed view.
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), self.fixture(encrypted) as f:
                f.put(f.mapped('/boot/grub/grubenv'), b'environment fixture')
                f.put(f.mapped('/etc/default/grub-btrfs/config'),
                      'GRUB_BTRFS_TITLE_FORMAT="Default"\n')
                menu = f.mapped('/boot/grub/grub.cfg')
                intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
                state = f.mapped('/efi/EFI/Emaki/boot-state.json')
                committed = menu.read_bytes()
                commits = []
                run, replace, unlink = refresh.run, os.replace, Path.unlink

                def sync(arguments):
                    nonlocal committed
                    result = run(arguments)
                    if list(map(str, arguments)) == ['sync', '-f', str(menu)]:
                        self.assertTrue(intent.exists())
                        committed = menu.read_bytes()
                        commits.append(committed)
                    return result

                def publish(source, target):
                    if target == state:
                        self.assertEqual(committed, menu.read_bytes())
                        self.assertIn(b'emaki.generation=', committed)
                    return replace(source, target)

                def forget(path, *args, **kwargs):
                    if path == intent:
                        self.assertEqual(committed, menu.read_bytes())
                        self.assertIn(b'emaki.generation=', committed)
                    return unlink(path, *args, **kwargs)

                with patch.object(refresh, 'run', side_effect=sync), \
                        patch.object(os, 'replace', side_effect=publish), \
                        patch.object(Path, 'unlink', forget):
                    refresh.refresh(f.identity)
                    self.assertEqual(len(commits), 1)
                    refresh.refresh(f.identity)
                    self.assertEqual(len(commits), 2)
                self.assert_clean_publication(f)

    def test_failed_menu_commit_and_rollback_keep_recovery_intent(self):
        with self.fixture(True) as f:
            menu = f.mapped('/boot/grub/grub.cfg')
            run = refresh.run
            attempts = []

            def fail_commit(arguments):
                if list(map(str, arguments)) == ['sync', '-f', str(menu)]:
                    attempts.append(menu.read_bytes())
                    raise refresh.Refuse('injected filesystem commit failure')
                return run(arguments)

            with patch.object(refresh, 'run', side_effect=fail_commit):
                with self.assertRaisesRegex(refresh.Refuse, 'commit failure'):
                    refresh.refresh(f.identity)
            self.assertEqual(len(attempts), 2)
            self.assertIn(b'emaki.generation=', attempts[0])
            self.assertEqual(attempts[1], f.originals['/boot/grub/grub.cfg'])
            intent = f.mapped('/efi/EFI/Emaki/boot-intent.json')
            self.assertTrue(json.loads(intent.read_text())['rollback'])
            self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-state.json').exists())
            refresh.refresh(f.identity)
            self.assert_clean_publication(f)

    def test_first_refresh_recovers_after_every_durable_write_and_rename(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), self.fixture(encrypted) as f:
                for target in self.freeze_publication_steps(f, lambda: refresh.refresh(f.identity), include_writes=True):
                    with self.subTest(target=target):
                        # A boot may occur before another package transaction.
                        f.put(f.mapped('/proc/cmdline'), 'root=UUID=' + FSUUID + '\n')
                        refresh.mark_good(f.identity)
                        refresh.refresh(f.identity)
                        state = json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())
                        self.assertTrue((f.mapped('/boot/emaki') / state['newest']).is_dir())
                        for name in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                            self.assertEqual(f.mapped(name).read_bytes(), f.originals[name])
                        self.assert_clean_publication(f)

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires process fork and SIGKILL')
    def test_first_refresh_recovers_after_sigkill_at_every_write_and_rename(self):
        """Kill real processes; private files and mocked generators exercise userspace only."""
        for encrypted in (False, True):
            events = []
            write, replace = refresh.write, refresh.os.replace

            def observe_write(target, data):
                write(target, data)
                events.append('write')

            def observe_replace(source, target):
                replace(source, target)
                events.append('rename')

            with self.fixture(encrypted) as f, \
                    patch.object(refresh, 'write', side_effect=observe_write), \
                    patch.object(refresh.os, 'replace', side_effect=observe_replace):
                refresh.refresh(f.identity)
            self.assertIn('write', events)
            self.assertIn('rename', events)

            for cut, event in enumerate(events, 1):
                with self.subTest(encrypted=encrypted, step=cut, event=event), self.fixture(encrypted) as f:
                    pid = os.fork()
                    if pid == 0:
                        # No Python unwinding or fixture cleanup may run at the cut.
                        seen = []

                        def completed(kind):
                            seen.append(kind)
                            if seen != events[:len(seen)]:
                                os._exit(2)
                            if len(seen) == cut:
                                os.kill(os.getpid(), signal.SIGKILL)

                        def kill_after_write(target, data):
                            write(target, data)
                            completed('write')

                        def kill_after_replace(source, target):
                            replace(source, target)
                            completed('rename')

                        try:
                            with patch.object(refresh, 'write', side_effect=kill_after_write), \
                                    patch.object(refresh.os, 'replace', side_effect=kill_after_replace):
                                refresh.refresh(f.identity)
                        except BaseException:
                            os._exit(3)
                        os._exit(4)

                    deadline = time.monotonic() + 15
                    while True:
                        waited, status = os.waitpid(pid, os.WNOHANG)
                        if waited == pid:
                            break
                        if time.monotonic() >= deadline:
                            os.kill(pid, signal.SIGKILL)
                            os.waitpid(pid, 0)
                            self.fail('refresh child did not reach the selected step in 15 seconds')
                        time.sleep(0.01)
                    self.assertTrue(os.WIFSIGNALED(status), f'child exited with wait status {status}')
                    self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                    loaders = ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI')
                    for name in loaders:
                        self.assertEqual(f.mapped(name).read_bytes(), f.originals[name])
                    f.put(f.mapped('/proc/cmdline'), 'root=UUID=' + FSUUID + '\n')
                    refresh.mark_good(f.identity)
                    refresh.refresh(f.identity)
                    state = json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())
                    self.assertTrue((f.mapped('/boot/emaki') / state['newest']).is_dir())
                    for name in loaders:
                        self.assertEqual(f.mapped(name).read_bytes(), f.originals[name])
                    self.assert_clean_publication(f)
            print(f'SIGKILL recovery encrypted={encrypted}: {len(events)} children, '
                  f'{events.count("write")} writes, {events.count("rename")} renames', file=sys.stderr)

    def test_promotion_recovers_after_every_durable_rename(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            expected = (generation / 'grub/x86_64-efi/emaki-early.efi').read_bytes()
            for target in self.freeze_publication_steps(f, lambda: self.complete_boot(f, generation)):
                with self.subTest(target=target):
                    self.assertTrue(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())
                    self.complete_boot(f, generation)
                    state = json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())
                    self.assertEqual(state['good'], generation.name)
                    self.assertEqual(f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes(), expected)
                    self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), expected)
                    self.assert_clean_publication(f)
                    refresh.refresh(f.identity)

    def test_repeated_refusals_remove_unpublished_generations(self):
        for failure in ('grub-install', 'grub-mkimage', 'unshare', 'grub-script-check'):
            with self.subTest(failure=failure), self.fixture(True, fail=failure) as f:
                for _ in range(12):
                    with self.assertRaises(refresh.Refuse):
                        refresh.refresh(f.identity)
                    self.assertEqual(list(f.mapped('/boot/emaki').iterdir()), [])
                    self.assert_clean_publication(f)

    def test_repeated_boot_completion_has_no_writes_or_success_message(self):
        with self.fixture(False) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            self.complete_boot(f, generation)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in f.mapped('/efi').rglob('*') if p.is_file()}
            f.output.seek(0)
            f.output.truncate(0)
            with patch.object(refresh, 'atomic', wraps=refresh.atomic) as writes:
                for _ in range(3):
                    self.complete_boot(f, generation)
            self.assertEqual(writes.call_count, 0)
            self.assertEqual(f.output.getvalue(), '')
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                      for p in f.mapped('/efi').rglob('*') if p.is_file()})

    def test_trial_menu_uses_candidate_and_persistent_two_attempt_counter(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            menu = f.mapped('/boot/grub/grub.cfg').read_text()
            self.assertIn('set default=0', menu)
            self.assertNotIn('--id emaki-loader-trial', menu)
            self.assertIn('set fallback=0', menu)
            self.assertIn('"$emaki_generation" != "' + generation.name + '"', menu)
            self.assertIn('"$emaki_trial" = "0" -o "$emaki_trial" = "1"', menu)
            self.assertIn('set emaki_trial=1', menu)
            self.assertIn('set emaki_trial=2', menu)
            self.assertIn('set emaki_trial_source=$cmdpath', menu)
            self.assertIn('save_env -f ($emaki_esp)/EFI/Emaki/trial.env', menu)
            self.assertIn('chainloader ($emaki_esp)/EFI/Emaki/loader-' + generation.name + '.efi', menu)
            self.assertLess(menu.index("menuentry 'Emaki linux'"), menu.index('chainloader '))
            self.assertLess(menu.index('chainloader '), menu.index('linux /@/boot/vmlinuz-linux '))
            self.assertEqual(menu.count('menuentry '), 4)
            counter = f.mapped('/efi/EFI/Emaki/trial.env').read_bytes()
            self.assertEqual(len(counter), 1024)
            self.assertTrue(counter.startswith(b'# GRUB Environment Block\n'))
            self.assertIn(('emaki_candidate=' + generation.name + '\n').encode(), counter)
            self.assertIn(b'emaki_trial=0\n', counter)

    def test_hibernation_resumes_restore_attempts_until_a_real_boot(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            counter = f.mapped('/efi/EFI/Emaki/trial.env')
            original = counter.read_bytes()
            for _ in range(3):
                refresh.sleep_trial('pre')
                trial = refresh.trial_state()
                trial.update(emaki_trial='1', emaki_trial_source='(hd0,gpt1)/EFI/BOOT')
                counter.write_bytes(refresh.trial_block(trial))
                refresh.sleep_trial('post')
                self.assertEqual(counter.read_bytes(), original)
                self.assertFalse(refresh.failed_trial({'newest': generation.name, 'good': None}))
            # A cold boot has no restored /run marker; its attempt still counts.
            trial['emaki_trial'] = '1'
            counter.write_bytes(refresh.trial_block(trial))
            refresh.sleep_trial('post')
            self.assertEqual(refresh.trial_state()['emaki_trial'], '1')
            self.complete_boot(f, generation)
            state = json.loads(f.mapped('/efi/EFI/Emaki/boot-state.json').read_text())
            self.assertEqual(state['good'], generation.name)

    def test_resume_does_not_reset_a_different_candidate(self):
        with self.fixture(False) as f:
            refresh.refresh(f.identity)
            refresh.sleep_trial('pre')
            trial = {'emaki_candidate': 'b' * 32, 'emaki_trial': '1'}
            f.mapped('/efi/EFI/Emaki/trial.env').write_bytes(refresh.trial_block(trial))
            refresh.sleep_trial('post')
            self.assertEqual(refresh.trial_state(), trial)

    def test_exhausted_trial_keeps_old_loaders_and_does_not_rearm_unchanged_build(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            state_path = f.mapped('/efi/EFI/Emaki/boot-state.json')
            before = state_path.read_bytes()
            state = json.loads(before)
            trial = ('# GRUB Environment Block\nemaki_candidate=' + state['newest']
                     + '\nemaki_trial=2\nemaki_trial_source=(hd0,gpt1)/EFI/BOOT\n').encode()
            f.put(f.mapped('/efi/EFI/Emaki/trial.env'), trial.ljust(1024, b'#'))
            f.output.seek(0)
            f.output.truncate(0)
            refresh.refresh(f.identity)
            self.assertEqual(state_path.read_bytes(), before)
            self.assertIn(b'emaki_trial=2\n', f.mapped('/efi/EFI/Emaki/trial.env').read_bytes())
            for name in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                self.assertEqual(f.mapped(name).read_bytes(), f.originals[name])
            self.assertEqual(f.output.getvalue().strip(), refresh.TRIAL_FAILED)
            self.assertNotIn('chainloader ', f.mapped('/boot/grub/grub.cfg').read_text())

    def test_fallback_trial_completion_promotes_and_preserves_its_firmware_origin(self):
        with self.fixture(True) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            trial = ('# GRUB Environment Block\nemaki_candidate=' + generation.name
                     + '\nemaki_trial=1\nemaki_trial_source=(hd0,gpt1)/EFI/BOOT\n').encode()
            f.put(f.mapped('/efi/EFI/Emaki/trial.env'), trial.ljust(1024, b'#'))
            self.complete_boot(f, generation)
            expected = (generation / 'grub/x86_64-efi/emaki-early.efi').read_bytes()
            self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), expected)
            self.assertEqual(f.mapped('/efi/EFI/Emaki/grubx64.efi').read_bytes(), expected)
            counter = f.mapped('/efi/EFI/Emaki/trial.env').read_bytes()
            self.assertIn(b'emaki_trial=0\n', counter)
            self.assertIn(b'emaki_trial_source=(hd0,gpt1)/EFI/BOOT\n', counter)

    def hook(self, f):
        errors = io.StringIO()
        builtin_open = open
        def private_open(path, *args, **kwargs):
            return builtin_open(f.mapped(path), *args, **kwargs)
        f.mapped('/run').mkdir(exist_ok=True)
        f.mapped('/var/log').mkdir(parents=True, exist_ok=True)
        with patch.object(refresh.os, 'geteuid', return_value=0), \
                patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                patch.object(refresh, 'discover', return_value=f.identity), \
                patch.object(refresh, 'pause_snapshots', return_value=nullcontext()), \
                patch.object(refresh, 'open', side_effect=private_open, create=True), \
                patch.object(refresh.fcntl, 'flock'), redirect_stderr(errors):
            status = refresh.main(['--hook'])
        return status, f.output.getvalue() + errors.getvalue()

    def test_foreign_fallback_hook_is_one_plain_sentence_and_success_status(self):
        with self.fixture(False, foreign=True) as f:
            status, output = self.hook(f)
            self.assertEqual(status, 0)
            self.assertEqual(len(output.strip().splitlines()), 1)
            self.assertNotIn('WARNING', output)
            self.assertNotIn('Resolve', output)
            for name, expected in f.originals.items():
                self.assertEqual(f.mapped(name).read_bytes(), expected)

    def test_migrated_hibernation_images_finish_before_one_loader_refresh(self):
        rebuild = runpy.run_path(str(ROOT / 'upkeep/emaki-initramfs-refresh'))['refresh']
        with self.fixture(True) as f:
            released = next(line for line, choices in boot_defaults.LEGACY_CHOICES.items()
                            if choices == (True, True, True))
            f.put(f.mapped('/etc/mkinitcpio.conf'), released)
            self.assertTrue(boot_defaults.apply(f.root))
            pending = f.mapped('/var/lib/emaki/migrations/initramfs-pending')
            self.assertEqual(pending.read_text(), 'linux\nlinux-lts\n')
            self.assertIn(' encrypt emaki-resume filesystems',
                          f.mapped('/etc/mkinitcpio.conf').read_text())

            def build(arguments, check):
                self.assertTrue(check)
                self.assertEqual(arguments, ['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'])
                f.commands.append(arguments)
                for name in refresh.INITRAMFS:
                    f.put(f.mapped('/boot') / name, b'rebuilt image' * refresh.MIB)

            rebuild(f.root, build)
            self.assertFalse(pending.exists())
            refresh.refresh(f.identity)
            self.assertEqual(f.commands[0][0], 'mkinitcpio')
            # The private generator invokes grub-mkconfig once in its namespace.
            self.assertEqual(sum(command[0] == 'unshare' for command in f.commands), 1)
            self.assertEqual(sum(command[0] == 'mkinitcpio' for command in f.commands), 1)

    def test_failed_migration_rebuild_blocks_loader_and_menu_changes(self):
        rebuild = runpy.run_path(str(ROOT / 'upkeep/emaki-initramfs-refresh'))['refresh']
        with self.fixture(True) as f:
            pending = f.mapped('/var/lib/emaki/migrations/initramfs-pending')
            f.put(pending, 'linux\nlinux-lts\n')
            def fail(arguments, check):
                raise RuntimeError('initramfs build failed')
            with self.assertRaisesRegex(RuntimeError, 'initramfs build failed'):
                rebuild(f.root, fail)
            self.assertTrue(pending.exists())
            status, output = self.hook(f)
            self.assertEqual(status, 1)
            self.assertIn('/var/log/emaki-boot-refresh.log', output)
            self.assertIn('emaki-initramfs-refresh',
                          f.mapped('/var/log/emaki-boot-refresh.log').read_text())
            self.assertEqual(f.commands, [])
            for name, expected in f.originals.items():
                self.assertEqual(f.mapped(name).read_bytes(), expected)

    def test_dangling_migration_marker_blocks_refresh(self):
        with self.fixture(False) as f:
            pending = f.mapped('/var/lib/emaki/migrations/initramfs-pending')
            pending.parent.mkdir(parents=True)
            pending.symlink_to('missing')
            with self.assertRaisesRegex(refresh.Refuse, 'initramfs images are still pending'):
                refresh.refresh(f.identity)
            self.assertEqual(f.commands, [])

    def test_generator_failure_hook_names_log_and_keeps_details_out_of_terminal(self):
        with self.fixture(False, fail='grub-mkimage') as f:
            status, output = self.hook(f)
            self.assertEqual(status, 1)
            self.assertEqual(len(output.strip().splitlines()), 1)
            self.assertIn('/var/log/emaki-boot-refresh.log', output)
            self.assertNotIn('injected', output)
            self.assertIn('injected grub-mkimage failure',
                          f.mapped('/var/log/emaki-boot-refresh.log').read_text())

    def test_refresh_success_is_one_sentence_without_generation_identifier(self):
        with self.fixture(False) as f:
            refresh.refresh(f.identity)
            generation, = f.mapped('/boot/emaki').iterdir()
            self.assertEqual(len(f.output.getvalue().strip().splitlines()), 1)
            self.assertNotIn(generation.name, f.output.getvalue())

    def test_space_check_counts_nested_boot_tree(self):
        with self.fixture(False) as f:
            nested = f.mapped('/boot/grub/deep/nested/asset')
            f.put(nested, b'nested asset')
            initial = sum(p.stat().st_size for p in f.mapped('/boot').rglob('*') if p.is_file())
            self.assertEqual(refresh.tree_size(f.mapped('/boot')), initial)
            with patch.object(refresh, 'room', wraps=refresh.room) as checked:
                refresh.refresh(f.identity)
            boot_checks = [call.args for call in checked.call_args_list if call.args[0] == f.mapped('/boot')]
            self.assertTrue(boot_checks)
            self.assertGreaterEqual(boot_checks[0][1], initial)

    def test_45_refreshes_keep_storage_bounded_and_fallback_unchanged(self):
        with self.fixture(False) as f:
            sizes = []
            for _ in range(45):
                refresh.refresh(f.identity)
                generations = list(f.mapped('/boot/emaki').iterdir())
                self.assertLessEqual(len(generations), 2)
                self.assertFalse(list(f.root.rglob('*.emaki-old-*')))
                self.assertFalse(list(f.root.rglob('*.emaki-new-*')))
                self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(),
                                 f.originals['/efi/EFI/BOOT/BOOTX64.EFI'])
                sizes.append(sum(p.stat().st_size for p in f.mapped('/boot/emaki').rglob('*') if p.is_file()))
            self.assertLessEqual(max(sizes), 2 * sizes[0] + 4096)


class OutputChecks(unittest.TestCase):
    def test_generator_output_goes_to_log_and_never_to_terminal(self):
        result = SimpleNamespace(returncode=0, stdout='menu generated\n',
                                 stderr='Found snapshot: private-name\n')
        output, errors, log = io.StringIO(), io.StringIO(), io.StringIO()
        with patch.object(refresh.subprocess, 'run', return_value=result), \
                patch.object(refresh, 'LOG', log, create=True), \
                redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(refresh.run(['grub-mkconfig']), 'menu generated')
        self.assertEqual(output.getvalue() + errors.getvalue(), '')
        self.assertIn(result.stdout, log.getvalue())
        self.assertIn(result.stderr, log.getvalue())


class DiscoveryChecks(unittest.TestCase):
    def exercise(self, encrypted=True, fault=None):
        with tempfile.TemporaryDirectory(prefix='boot-discovery-') as directory:
            root = Path(directory)

            def mapped(value):
                path = Path(value)
                return root / str(path).lstrip('/') if path.is_absolute() and not path.is_relative_to(root) else path

            firmware = mapped('/sys/firmware/efi/efivars')
            firmware.mkdir(parents=True)
            (firmware / 'SecureBoot-test').write_bytes(b'\x07\0\0\0' + (b'\x01' if fault == 'secure-boot' else b'\0'))
            if fault == 'malformed-secure-boot':
                (firmware / 'SecureBoot-test').write_bytes(b'\0')
            mapped('/etc/default').mkdir(parents=True)
            mapped('/etc/fstab').write_text('UUID=' + ('BAD-ID' if fault == 'wrong-esp' else 'ABCD-1234')
                                            + ' /efi vfat defaults 0 2\n')
            configured = FSUUID if fault == 'wrong-cryptdevice' else LUKS
            mapped('/etc/default/grub').write_text(boot.grub_defaults(luks_uuid=configured if encrypted else None))
            mounted_root = {'target': '/', 'uuid': FSUUID, 'fstype': 'btrfs' if encrypted else 'ext4',
                            'source': '/dev/mapper/emaki-root' if encrypted else '/dev/vda2',
                            'partuuid': None, 'fsroot': '/@' if encrypted else '/', 'options': 'rw,relatime'}
            if fault == 'snapshot':
                mounted_root['fsroot'] = '/@snapshots/12/snapshot'
            if fault == 'overlay':
                mounted_root.update(fstype='overlay', source='overlay', fsroot='/', uuid=None,
                                    options='rw,relatime,lowerdir=/run/root,upperdir=/run/upper,workdir=/run/work')
            if fault == 'readonly-root':
                mounted_root['options'] = 'ro,relatime'
            boot_mount = {**mounted_root, 'target': '/boot' if fault == 'separate-boot' else '/'}
            esp = {'fstype': 'ext4' if fault == 'wrong-esp-filesystem' else 'vfat',
                   'target': '/efi', 'source': '/dev/vda1', 'fsroot': '/',
                   'options': 'ro' if fault == 'readonly-esp' else 'rw', 'uuid': 'ABCD-1234', 'partuuid': FSUUID}
            for name, mount in (('root', mounted_root), ('boot', boot_mount), ('esp', esp)):
                if fault == 'missing-' + name + '-uuid':
                    mount['uuid'] = None

            def probe(args):
                if args[0] == 'findmnt':
                    path = args[-1]
                    self.assertEqual(args, ['findmnt', '--json', '--output',
                                            'TARGET,SOURCE,FSTYPE,FSROOT,UUID,PARTUUID,OPTIONS',
                                            '--target' if path == '/boot' else '--mountpoint', path])
                    mount = {'/': mounted_root, '/boot': boot_mount, '/efi': esp}[path]
                    return json.dumps({'filesystems': [] if path == '/efi' and fault == 'unmounted-esp'
                                       else [mount]})
                if args == ['grub-probe', '--target=cryptodisk_uuid', '/boot']:
                    return (LUKS + ' ' + FSUUID) if fault == 'multiple-disks' else LUKS if encrypted else ''
                self.assertEqual(args, ['grub-probe', '--target=fs_uuid', '/boot'])
                return LUKS if fault == 'root-uuid' else FSUUID

            builtin_open = open

            def private_open(path, *args, **kwargs):
                return builtin_open(mapped(path), *args, **kwargs)

            mapped('/run').mkdir()
            with ExitStack() as stack:
                stack.enter_context(patch.object(refresh, 'Path', side_effect=mapped))
                commands = stack.enter_context(patch.object(refresh, 'run', side_effect=probe))
                stack.enter_context(patch.object(refresh.os, 'geteuid', return_value=0))
                stack.enter_context(patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)))
                stack.enter_context(patch.object(refresh, 'open', side_effect=private_open, create=True))
                stack.enter_context(patch.object(refresh.fcntl, 'flock'))
                updater = stack.enter_context(patch.object(refresh, 'refresh'))
                marker = stack.enter_context(patch.object(refresh, 'mark_good'))
                snapshots = stack.enter_context(patch.object(refresh, 'pause_snapshots'))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                errors = stack.enter_context(redirect_stderr(io.StringIO()))
                if fault in ('snapshot', 'overlay'):
                    self.assertIsNone(refresh.discover())
                    for mode in ('--check', '--hook', '--mark-good'):
                        with self.subTest(mode=mode):
                            self.assertEqual(refresh.main([mode]), 0)
                            self.assertEqual(output.getvalue(), '')
                            self.assertEqual(errors.getvalue(), '')
                    self.assertEqual([call.args[0][-1] for call in commands.call_args_list], ['/'] * 4)
                elif fault:
                    with self.assertRaises(refresh.Refuse):
                        refresh.discover()
                    self.assertEqual(refresh.main(['--check']), 1)
                    self.assertIn('/var/log/emaki-boot-refresh.log', errors.getvalue())
                    self.assertNotIn('WARNING', errors.getvalue())
                else:
                    self.assertEqual(refresh.discover(), {
                        'fsroot': '/@' if encrypted else '', 'luks': LUKS if encrypted else None,
                        'uuid': FSUUID, 'fstype': 'btrfs' if encrypted else 'ext4', 'esp_uuid': 'ABCD-1234'})
                    self.assertEqual(refresh.main(['--check']), 0)
                updater.assert_not_called()
                marker.assert_not_called()
                snapshots.assert_not_called()

    def test_encrypted_and_plain_installed_identity(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted):
                self.exercise(encrypted)

    def test_uncertain_mount_disk_and_secure_boot_identity_refuses_refresh(self):
        for fault in ('wrong-esp', 'unmounted-esp', 'wrong-esp-filesystem', 'readonly-esp',
                      'root-uuid', 'wrong-cryptdevice', 'multiple-disks', 'separate-boot',
                      'missing-root-uuid', 'missing-boot-uuid', 'missing-esp-uuid',
                      'readonly-root', 'secure-boot', 'malformed-secure-boot'):
            with self.subTest(fault=fault):
                self.exercise(fault=fault)

    def test_snapshot_and_overlay_roots_skip_silently_before_boot_and_esp_discovery(self):
        for fault in ('snapshot', 'overlay'):
            with self.subTest(fault=fault):
                self.exercise(fault=fault)

    def test_iso_chroot_skips_silently_before_discovery(self):
        with patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(refresh, 'discover') as discover, \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(refresh.main(['--hook']), 0)
            discover.assert_not_called()
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue(), '')

    def test_free_space_reserves_are_required_before_staging(self):
        needed, reserve = 7 * refresh.MIB, 32 * refresh.MIB
        for available in (needed - 1, needed, needed + reserve - 1, needed + reserve):
            with self.subTest(available=available), patch.object(refresh.os, 'statvfs',
                    return_value=SimpleNamespace(f_bavail=available, f_frsize=1)):
                if available < needed + reserve:
                    with self.assertRaisesRegex(refresh.Refuse, '39 MiB free'):
                        refresh.room(Path('/unused'), needed, reserve)
                else:
                    refresh.room(Path('/unused'), needed, reserve)


class PublicationChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='boot-refresh-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.targets = [self.root / name for name in ('vendor.efi', 'fallback.efi', 'grub.cfg')]
        self.originals = [b'old-vendor\0', b'old-fallback\xff', b'old-menu\n']
        for target, old in zip(self.targets, self.originals):
            target.write_bytes(old)

    def prepare(self, tag='test'):
        transaction = refresh.Transaction(tag)
        for target in self.targets:
            transaction.add(target, b'new-' + target.name.encode())
        return transaction

    def assert_originals(self):
        self.assertEqual([p.read_bytes() for p in self.targets], self.originals)

    def test_success_cleans_recovery_copies_and_candidates(self):
        transaction = self.prepare()
        transaction.publish()
        transaction.cleanup()
        for target, old in zip(self.targets, self.originals):
            self.assertEqual(target.read_bytes(), b'new-' + target.name.encode())
            self.assertFalse(target.with_name(target.name + '.emaki-old-test').exists())
        self.assertFalse(list(self.root.glob('*.emaki-new-*')))

    def test_staging_write_failure_leaves_every_active_file_untouched(self):
        original_write = refresh.write
        for failed_call in range(1, 7):
            with self.subTest(failed_call=failed_call), tempfile.TemporaryDirectory(dir=self.root) as directory:
                target = Path(directory) / 'loader'
                target.write_bytes(b'old')
                transaction = refresh.Transaction('test')
                calls = 0

                def fail_write(path, data, *args):
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise OSError('injected staging failure')
                    return original_write(path, data, *args)

                with patch.object(refresh, 'write', side_effect=fail_write):
                    with self.assertRaises(OSError):
                        for index in range(3):
                            other = target.with_name('loader' + str(index))
                            other.write_bytes(b'old')
                            transaction.add(other, b'new')
                transaction.cleanup()
                for active in Path(directory).glob('loader[0-9]'):
                    self.assertEqual(active.read_bytes(), b'old')
                self.assert_originals()

    def test_each_publish_rename_failure_restores_original_bytes(self):
        replace = refresh.os.replace
        for failed_index in range(len(self.targets)):
            with self.subTest(failed_index=failed_index):
                tag = 'test' + str(failed_index)
                transaction = self.prepare(tag)

                def fail_replace(source, target):
                    if Path(source).name.endswith('.emaki-new-' + tag) and target == self.targets[failed_index]:
                        raise OSError('injected publication failure')
                    return replace(source, target)

                with patch.object(refresh.os, 'replace', side_effect=fail_replace), self.assertRaises(OSError):
                    transaction.publish()
                self.assert_originals()
                transaction.cleanup()
                for target, old in zip(self.targets, self.originals):
                    backup = target.with_name(target.name + '.emaki-old-' + tag)
                    self.assertFalse(backup.exists(), f'rolled-back recovery copy leaked: {backup.name}')
                for backup in self.root.glob('*.emaki-old-*'):
                    backup.unlink()

    def test_sync_failure_after_rename_restores_original_bytes(self):
        transaction = self.prepare()
        real_sync = refresh.sync_directory
        calls = 0

        def fail_sync(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected directory sync failure')
            return real_sync(path)

        with patch.object(refresh, 'sync_directory', side_effect=fail_sync), self.assertRaises(OSError):
            transaction.publish()
        self.assert_originals()
        transaction.cleanup()

    def test_concurrent_change_is_preserved_and_prior_replacements_are_rolled_back(self):
        transaction = self.prepare()
        self.targets[1].write_bytes(b'external change')
        with self.assertRaises(refresh.Refuse):
            transaction.publish()
        self.assertEqual(self.targets[0].read_bytes(), self.originals[0])
        self.assertEqual(self.targets[1].read_bytes(), b'external change')
        self.assertEqual(self.targets[2].read_bytes(), self.originals[2])
        transaction.cleanup()

    def test_rollback_removes_a_newly_created_target(self):
        self.targets[0].unlink()
        transaction = self.prepare()
        self.targets[1].write_bytes(b'external change')
        with self.assertRaises(refresh.Refuse):
            transaction.publish()
        self.assertFalse(self.targets[0].exists())
        transaction.cleanup()


if __name__ == '__main__':
    unittest.main()
