#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise boot-time snapshot repair and its publication boundary without mounts."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import runpy
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = runpy.run_path(str(ROOT / 'tests/test-boot-refresh.py'))
refresh = SUPPORT['refresh']


class SnapshotRepairChecks(unittest.TestCase):
    fixture = SUPPORT['RefreshOrchestrationChecks'].fixture

    @contextmanager
    def prepared(self, missing=False, empty=False):
        with self.fixture(False, plain_fsroot='/@') as f:
            f.put(f.mapped('/etc/grub.d/41_snapshots-btrfs'), '# fixture generator\n')
            directory = f.mapped('/.snapshots')
            directory.mkdir()
            if not empty:
                (directory / '1/snapshot').mkdir(parents=True)
                f.put(directory / '1/info.xml', '<snapshot/>\n')
                os.utime(directory / '1/info.xml', ns=(1, 1))
                os.utime(directory / '1', ns=(1, 1))
            os.utime(directory, ns=(1, 1))
            menu = f.mapped('/boot/grub/grub-btrfs.cfg')
            f.generated = 'linux /@snapshots/1/snapshot/boot/vmlinuz-linux\n'
            f.originals['/boot/grub/grub-btrfs.cfg'] = f.generated.encode()
            menu.write_text(f.generated)
            if missing:
                menu.unlink()
            else:
                os.utime(menu, ns=(2, 2))
            run = refresh.run.side_effect

            def generate(arguments):
                result = run(arguments)
                if str(arguments[0]) == 'unshare':
                    (Path(arguments[-1]) / 'grub/grub-btrfs.cfg').write_text(f.generated)
                return result

            with patch.object(refresh, 'pause_snapshots') as paused, \
                    patch.object(refresh, 'run', side_effect=generate):
                f.paused = paused
                yield f

    def assert_repaired(self, f):
        self.assertTrue(f.mapped('/boot/grub/grub-btrfs.cfg').is_file())
        self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), f.generated)
        self.assertEqual(f.output.getvalue(), 'The snapshot boot menu was repaired.\n')
        f.paused.assert_called_once()
        self.assertEqual(sum(command[0] == 'unshare' for command in f.commands), 1)
        self.assertIn(['sync', '-f', str(f.mapped('/boot/grub/grub-btrfs.cfg'))], f.commands)
        for path in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
            self.assertEqual(f.mapped(path).read_bytes(), f.originals[path])
        self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def test_missing_fragment_is_regenerated_and_durable(self):
        with self.prepared(missing=True) as f:
            run = refresh.run.side_effect

            def ordered(arguments):
                if list(map(str, arguments)) == ['sync', '-f', str(f.mapped('/boot/grub/grub-btrfs.cfg'))]:
                    self.assertEqual(f.output.getvalue(), '')
                    self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), f.generated)
                return run(arguments)

            with patch.object(refresh, 'run', side_effect=ordered):
                refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)
            before = list(f.commands)
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, before)
            self.assertEqual(f.output.getvalue().count('repaired'), 1)

    def test_foreign_fallback_menu_repair_preserves_esp_on_small_and_large_esps(self):
        for size in (100, 512):
            with self.subTest(esp_mib=size), self.prepared(missing=True) as f:
                foreign = b'foreign fallback loader'
                f.put(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI'), foreign)
                f.originals['/efi/EFI/BOOT/BOOTX64.EFI'] = foreign
                f.put(f.mapped('/efi/EFI/Microsoft/Boot/bootmgfw.efi'), b'Windows loader')
                f.put(f.mapped('/efi/EFI/Microsoft/Boot/BCD'), b'Windows boot data')
                esp = f.mapped('/efi')
                writes = []
                atomic = refresh.atomic
                statvfs = refresh.os.statvfs

                def record(target, data):
                    if target.is_relative_to(esp):
                        writes.append(target)
                    return atomic(target, data)

                def space(path):
                    if path == esp:
                        return SimpleNamespace(f_blocks=size * refresh.MIB,
                                               f_bavail=32 * refresh.MIB, f_frsize=1)
                    return statvfs(path)

                with patch.object(refresh, 'atomic', side_effect=record), \
                        patch.object(refresh.os, 'statvfs', side_effect=space):
                    with SUPPORT['RefreshOrchestrationChecks'].shared_esp(self, f) as gate:
                        before = {p: p.read_bytes() for p in esp.rglob('*') if p.is_file()}
                        refresh.repair_snapshot_menu(f.identity)
                        self.assertTrue(gate.opened)
                        self.assert_repaired(f)
                        self.assertTrue(writes)
                        self.assertEqual(set(writes), {f.mapped('/efi/EFI/Emaki/boot-intent.json')})
                        self.assertEqual(before, {p: p.read_bytes() for p in esp.rglob('*') if p.is_file()})
                        self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-state.json').exists())
                    # Outside the simulated alongside operation, a foreign fallback
                    # must still prevent a dedicated-ESP loader refresh.
                    with patch.object(refresh, 'shared_esp', return_value=False), \
                            self.assertRaisesRegex(refresh.Kept, 'fallback loader is not recognized'):
                        refresh.refresh(f.identity)
                    self.assertEqual(before, {p: p.read_bytes() for p in esp.rglob('*') if p.is_file()})

    def test_existing_generation_state_is_still_validated_during_menu_repair(self):
        for state in ({}, {'good': None, 'newest': None, 'fallback_sha256': '0' * 64}):
            with self.subTest(state=state), self.prepared(missing=True) as f:
                f.put(f.mapped('/efi/EFI/Emaki/boot-state.json'), json.dumps(state))
                f.put(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI'), b'foreign fallback loader')
                before = {p: p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
                with self.assertRaises(refresh.Refuse):
                    refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(before, {p: p.read_bytes() for p in f.root.rglob('*') if p.is_file()})
                self.assertFalse(any(command[0] == 'unshare' for command in f.commands))

    def test_repair_recreates_and_syncs_live_stage_before_generation(self):
        with self.prepared(missing=True) as f:
            stage = f.mapped('/boot/grub/.emaki-snapshots')
            self.assertFalse(stage.exists())
            sync = refresh.sync_directory
            synced = []
            run = refresh.run.side_effect

            def flushed(path):
                result = sync(path)
                if path == stage.parent and stage.is_dir():
                    synced.append(path)
                return result

            def generate(arguments):
                if str(arguments[0]) == 'unshare':
                    self.assertTrue(stage.is_dir())
                    self.assertIn(stage.parent, synced)
                return run(arguments)

            with patch.object(refresh, 'sync_directory', side_effect=flushed), \
                    patch.object(refresh, 'run', side_effect=generate):
                refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)

    def test_live_stage_symlink_is_refused(self):
        with self.prepared(missing=True) as f:
            stage = f.mapped('/boot/grub/.emaki-snapshots')
            stage.symlink_to(f.mapped('/boot/grub'), target_is_directory=True)
            with self.assertRaisesRegex(refresh.Refuse, 'real directory'):
                refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            f.paused.assert_not_called()

    def test_fragment_referencing_deleted_snapshot_needs_repair(self):
        with self.prepared() as f:
            shutil.rmtree(f.mapped('/.snapshots/1'))
            f.generated = '# No snapshots available.\n'
            self.assertTrue(refresh.snapshot_menu_needs_repair())
            refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)
            self.assertFalse(refresh.snapshot_menu_needs_repair())

    def test_omitted_existing_snapshot_does_no_work(self):
        for kernel in (False, True):
            with self.subTest(kernel=kernel), self.prepared() as f:
                boot = f.mapped('/.snapshots/2/snapshot/boot')
                boot.mkdir(parents=True)
                if kernel:
                    f.put(boot / 'vmlinuz-linux', 'snapshot kernel\n')
                self.assertFalse(refresh.snapshot_menu_needs_repair())
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_healthy_fragment_does_no_work(self):
        with self.prepared() as f:
            before = f.mapped('/boot/grub/grub-btrfs.cfg').stat()
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()
            after = f.mapped('/boot/grub/grub-btrfs.cfg').stat()
            self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
            self.assertFalse(f.mapped('/boot/emaki').exists())

    def test_only_info_xml_cleanup_changed_does_no_work(self):
        with self.prepared() as f:
            menu = f.mapped('/boot/grub/grub-btrfs.cfg')
            before = menu.stat()
            f.put(f.mapped('/.snapshots/1/info.xml'),
                  '<snapshot><cleanup>number</cleanup></snapshot>\n')
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()
            self.assertEqual(menu.read_bytes(), f.originals['/boot/grub/grub-btrfs.cfg'])
            self.assertEqual(menu.stat().st_mtime_ns, before.st_mtime_ns)

    def test_upstream_selection_settings_do_not_require_all_snapshots(self):
        for limit, expected in ((None, range(3, 53)), ('2', (52, 51)), ('0', ())):
            with self.subTest(limit=limit), self.prepared() as f:
                for number in range(2, 53):
                    f.mapped(f'/.snapshots/{number}/snapshot').mkdir(parents=True)
                if limit is not None:
                    f.put(f.mapped('/etc/default/grub-btrfs/config'),
                          'GRUB_BTRFS_LIMIT="1"\nGRUB_BTRFS_IGNORE_SNAPSHOT_TYPE=("single")\n'
                          'GRUB_BTRFS_IGNORE_SNAPSHOT_DESCRIPTION=("timeline")\n')
                    defaults = f.mapped('/etc/default/grub')
                    defaults.write_text(defaults.read_text() + f'GRUB_BTRFS_LIMIT="{limit}"\n')
                menu = f.mapped('/boot/grub/grub-btrfs.cfg')
                menu.write_text('# Snapshot menu\n' + ''.join(
                    f'linux /@snapshots/{number}/snapshot/boot/vmlinuz-linux\n' for number in expected))
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.mapped('/.snapshots/53/snapshot').mkdir(parents=True)
                self.assertFalse(refresh.snapshot_menu_needs_repair())
                # A deleted reference requires repair regardless of selection settings.
                menu.write_text(menu.read_text() + 'linux /@snapshots/99/snapshot/boot/vmlinuz-linux\n')
                self.assertTrue(refresh.snapshot_menu_needs_repair())

    def test_empty_fragment_needs_repair(self):
        for empty_inventory in (False, True):
            for content in ('', ' \n\t'):
                with self.subTest(empty_inventory=empty_inventory, content=content), \
                        self.prepared(empty=empty_inventory) as f:
                    f.mapped('/boot/grub/grub-btrfs.cfg').write_text(content)
                    if empty_inventory:
                        f.generated = '# No snapshots available.\n'
                    self.assertTrue(refresh.snapshot_menu_needs_repair())
                    refresh.repair_snapshot_menu(f.identity)
                    self.assert_repaired(f)
                    self.assertFalse(refresh.snapshot_menu_needs_repair())

    def test_ext4_and_absent_snapshot_setup_are_quiet(self):
        for missing in ('ext4', 'directory', 'generator'):
            with self.subTest(missing=missing), self.prepared(missing=True) as f:
                if missing == 'ext4':
                    f.identity['fstype'] = 'ext4'
                elif missing == 'directory':
                    shutil.rmtree(f.mapped('/.snapshots'))
                else:
                    f.mapped('/etc/grub.d/41_snapshots-btrfs').unlink()
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_generator_failure_keeps_both_old_menus(self):
        with self.prepared() as f:
            shutil.rmtree(f.mapped('/.snapshots/1'))
            with patch.object(refresh, 'run', side_effect=refresh.Transient('generator failed')):
                with self.assertRaises(refresh.Transient):
                    refresh.repair_snapshot_menu(f.identity)
            for path in ('/boot/grub/grub.cfg', '/boot/grub/grub-btrfs.cfg'):
                self.assertEqual(f.mapped(path).read_bytes(), f.originals[path])
            self.assertEqual(f.output.getvalue(), '')
            self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def test_zero_snapshots_without_generator_fragment_is_idempotent(self):
        with self.prepared(missing=True, empty=True) as f:
            run = refresh.run.side_effect

            def no_fragment(arguments):
                result = run(arguments)
                if str(arguments[0]) == 'unshare':
                    (Path(arguments[-1]) / 'grub/grub-btrfs.cfg').unlink()
                return result

            with patch.object(refresh, 'run', side_effect=no_fragment):
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), '# No snapshots available.\n')
                before = list(f.commands)
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, before)
                self.assertEqual(f.output.getvalue().count('repaired'), 1)

    def test_missing_generated_fragment_with_snapshots_refuses_old_copy(self):
        with self.prepared() as f:
            (f.mapped('/.snapshots') / '2/snapshot').mkdir(parents=True)
            shutil.rmtree(f.mapped('/.snapshots/1'))
            run = refresh.run.side_effect

            def no_fragment(arguments):
                if str(arguments[0]) != 'unshare':
                    return run(arguments)
                # Generate only the main menu; never touch the copied fragment.
                f.commands.append(list(map(str, arguments)))
                rows = []
                for kernel in refresh.KERNELS:
                    rows += [f"menuentry 'Emaki {kernel}' {{",
                             f'linux /@/boot/vmlinuz-{kernel} root=UUID={f.identity["uuid"]}',
                             f'initrd /@/boot/initramfs-{kernel}.img', '}']
                (Path(arguments[-1]) / 'grub/grub.cfg').write_text('\n'.join(rows) + '\n')
                return ''

            with patch.object(refresh, 'run', side_effect=no_fragment):
                with self.assertRaisesRegex(refresh.Refuse, 'did not produce a menu'):
                    refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_bytes(), f.originals['/boot/grub/grub-btrfs.cfg'])
            self.assertEqual(f.output.getvalue(), '')

    def test_recovery_roots_skip_through_real_discovery(self):
        for kind in ('overlay', 'snapshot'):
            with self.subTest(kind=kind), self.prepared(missing=True) as f:
                f.mapped('/run').mkdir()
                mount = {'target': '/', 'uuid': None, 'fstype': 'overlay' if kind == 'overlay' else 'btrfs',
                         'source': 'overlay', 'fsroot': '/' if kind == 'overlay' else '/@snapshots/1/snapshot',
                         'options': 'rw', 'partuuid': None}
                with patch.object(refresh, 'run', return_value=json.dumps({'filesystems': [mount]})), \
                        patch.object(refresh, 'open', side_effect=lambda path, *args: open(f.mapped(path), *args), create=True), \
                        patch.object(refresh.os, 'geteuid', return_value=0), \
                        patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                        patch.object(refresh, 'repair_snapshot_menu') as repair:
                    self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                    repair.assert_not_called()
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_three_healthy_boots_with_omitted_snapshot_write_nothing(self):
        with self.prepared() as f:
            f.mapped('/.snapshots/2/snapshot/boot').mkdir(parents=True)
            lock = f.mapped('/var/lib/pacman/db.lck')
            f.put(lock, 'another package operation\n')
            before = lock.stat()

            def contents():
                return {str(path.relative_to(f.root)): (
                    path.lstat().st_ino, path.lstat().st_mtime_ns, path.lstat().st_ctime_ns,
                    path.read_bytes() if path.is_file() else None)
                    for path in f.root.rglob('*')}

            original = contents()
            with patch.object(refresh, 'discover', side_effect=refresh.Refuse('identity mismatch')) as discover, \
                    patch.object(refresh, 'pacman_lock') as pacman, \
                    patch.object(refresh, 'open_run_lock') as run_lock, \
                    patch.object(refresh, 'BoundedLog') as log, \
                    patch.object(refresh.os, 'geteuid', return_value=1000) as uid, \
                    patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as process:
                for boot in range(3):
                    with self.subTest(boot=boot):
                        self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                        self.assertEqual(contents(), original)
                for operation in (discover, pacman, run_lock, log, uid):
                    operation.assert_not_called()
                self.assertEqual(process.call_args_list, [
                    call(['systemd-detect-virt', '--chroot', '--quiet'],
                         stdout=refresh.subprocess.DEVNULL, stderr=refresh.subprocess.DEVNULL)
                ] * 3)
            self.assertEqual(lock.read_text(), 'another package operation\n')
            self.assertEqual(lock.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()

    def test_busy_run_lock_defers_repair_successfully(self):
        with self.prepared(missing=True) as f:
            with patch.object(refresh, 'open_run_lock'), \
                    patch.object(refresh, 'wait_run_lock', side_effect=BlockingIOError) as wait, \
                    patch.object(refresh.os, 'geteuid', return_value=0), \
                    patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                    patch.object(refresh, 'discover') as discover, \
                    patch.object(refresh, 'pacman_lock') as pacman:
                self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                self.assertEqual(wait.call_args.kwargs, {})
                discover.assert_not_called()
                pacman.assert_not_called()
            self.assertEqual(f.mapped(refresh.LOG_PATH).read_text(),
                             'Another boot loader operation is running; retry at the next boot.\n')
            self.assertEqual(f.output.getvalue(), '')
            self.assertFalse(f.mapped('/var/lib/pacman/db.lck').exists())

    def test_boot_dispatch_holds_package_lock_during_repair(self):
        with self.prepared(missing=True) as f:
            f.mapped('/run').mkdir()
            f.mapped('/var/lib/pacman').mkdir(parents=True)
            run = refresh.run.side_effect

            def locked_generation(arguments):
                if str(arguments[0]) == 'unshare':
                    self.assertEqual(f.mapped('/var/lib/pacman/db.lck').read_text(),
                                     'emaki-boot-refresh:' + SUPPORT['BOOT_ID'])
                return run(arguments)

            with patch.object(refresh, 'open', side_effect=lambda path, *args: open(f.mapped(path), *args), create=True), \
                    patch.object(refresh.os, 'geteuid', return_value=0), \
                    patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                    patch.object(refresh, 'discover', return_value=f.identity), \
                    patch.object(refresh, 'run', side_effect=locked_generation):
                self.assertEqual(refresh.main(['--repair-snapshots']), 0)
            self.assert_repaired(f)
            self.assertFalse(f.mapped('/var/lib/pacman/db.lck').exists())
            self.assertFalse(f.mapped('/var/lib/emaki/boot-refresh-pending').exists())

    @contextmanager
    def lettering(self, mode, menu_font, defaults_font=False):
        """A healthy snapshot fragment; the canonical menu loads `menu_font` (None: GRUB's)."""
        with self.prepared() as f:
            info = bytearray(0x40)
            info[0x0f] = 0x70
            info[0x12:0x16] = mode[0].to_bytes(2, 'little') + mode[1].to_bytes(2, 'little')
            f.put(f.mapped('/sys/kernel/boot_params/data'), bytes(info))
            for size in (24, 32, 46):
                f.put(f.mapped(f'/usr/share/emaki/grub/fonts/menu-{size}.pf2'), b'font')
            header = ('if loadfont $font ; then\n' if menu_font is None
                      else f'if loadfont /@{menu_font} ; then\n')
            f.put(f.mapped('/boot/grub/grub.cfg'), header + '  set gfxmode=1024x768,800x600,640x480,auto\nfi\n')
            if defaults_font:
                defaults = f.mapped('/etc/default/grub')
                f.put(defaults, defaults.read_text() + 'GRUB_FONT=/boot/grub/fonts/unicode.pf2\n')
            yield f

    def test_menu_drawn_in_another_mode_is_regenerated_once(self):
        support = runpy.run_path(str(ROOT / 'tests/test-menu-font.py'))
        generator = support['GeneratedHeader']()
        self.addCleanup(generator.doCleanups)
        generator.setUp()
        fonts = '/usr/share/emaki/grub/fonts'
        headers = {}
        for size in (None, 32, 46):
            raw = generator.header_for(str(ROOT / f'grub/fonts/menu-{size}.pf2') if size else None)
            raw = raw.replace(str(ROOT / 'grub/fonts'), fonts)
            headers[size] = refresh.boot.menu_font_fallback(raw)
        for filesystem in ('btrfs', 'ext4'):
            for mode, previous, chosen in (((2560, 1600), None, 32), ((1024, 768), 46, None)):
                with self.subTest(filesystem=filesystem, mode=mode), self.lettering(mode, None) as f:
                    f.identity['fstype'] = filesystem
                    if filesystem == 'ext4':
                        f.identity['fsroot'] = ''
                        f.mapped('/etc/grub.d/41_snapshots-btrfs').unlink()
                        shutil.rmtree(f.mapped('/.snapshots'))
                        f.mapped('/boot/grub/grub-btrfs.cfg').unlink()
                    menu = f.mapped('/boot/grub/grub.cfg')
                    f.put(menu, headers[previous])
                    run = refresh.run.side_effect

                    def generate(arguments):
                        result = run(arguments)
                        if str(arguments[0]) == 'unshare':
                            stage = Path(arguments[-1]) / 'grub'
                            staged_menu = stage / 'grub.cfg'
                            staged_menu.write_text(headers[chosen] + staged_menu.read_text())
                            if filesystem == 'ext4':
                                (stage / 'grub-btrfs.cfg').unlink()
                        return result

                    with patch.object(refresh, 'run', side_effect=generate):
                        self.assertTrue(refresh.menu_font_needs_repair())
                        refresh.repair_snapshot_menu(f.identity)
                        self.assertEqual(f.output.getvalue(), 'The boot menu lettering was resized.\n')
                        expected = f'{fonts}/menu-{chosen}.pf2' if chosen else None
                        self.assertEqual(refresh.boot.generated_menu_font(menu.read_text()), expected)
                        self.assertFalse(refresh.menu_font_needs_repair())
                        self.assertEqual(sum(command[0] == 'unshare' for command in f.commands), 1)
                        if filesystem == 'btrfs':
                            self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), f.generated)
                        else:
                            self.assertFalse(f.mapped('/boot/grub/grub-btrfs.cfg').exists())
                        f.paused.assert_called_once()
                        for path in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
                            self.assertEqual(f.mapped(path).read_bytes(), f.originals[path])
                        commands = list(f.commands)
                        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in f.root.rglob('*') if path.is_file()}
                        with patch.object(refresh, 'write', wraps=refresh.write) as writes, \
                                patch.object(refresh, 'atomic', wraps=refresh.atomic) as atomic:
                            refresh.repair_snapshot_menu(f.identity)
                            writes.assert_not_called()
                            atomic.assert_not_called()
                        self.assertEqual(f.commands, commands)
                        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns)
                                                  for path in f.root.rglob('*') if path.is_file()})
                        self.assertEqual(f.output.getvalue(), 'The boot menu lettering was resized.\n')

    def test_matching_or_local_lettering_writes_nothing(self):
        for mode, font, local in (((2560, 1600), '/usr/share/emaki/grub/fonts/menu-32.pf2', False),
                                  ((1024, 768), None, False), ((1920, 1080), '/usr/share/emaki/grub/fonts/menu-24.pf2', False),
                                  ((3840, 2160), None, True)):
            with self.subTest(mode=mode, font=font, local=local), self.lettering(mode, font, local) as f:
                self.assertFalse(refresh.menu_font_needs_repair())
                with patch.object(refresh.os, 'geteuid', return_value=1000):
                    self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_unknown_mode_or_menu_writes_nothing(self):
        with self.lettering((2560, 1600), None) as f:
            f.mapped('/sys/kernel/boot_params/data').unlink()
            self.assertFalse(refresh.menu_font_needs_repair())
        with self.lettering((2560, 1600), None) as f:
            f.put(f.mapped('/boot/grub/grub.cfg'), 'old canonical menu\n')
            self.assertFalse(refresh.menu_font_needs_repair())
        with self.lettering((2560, 1600), None) as f:
            f.mapped('/usr/share/emaki/grub/fonts/menu-32.pf2').unlink()
            self.assertFalse(refresh.menu_font_needs_repair())

    def test_boot_unit_order_and_delivery(self):
        unit = (ROOT / 'systemd/emaki-snapshot-menu.service').read_text()
        self.assertIn('After=multi-user.target emaki-boot-complete.service emaki-boot-refresh.service', unit)
        self.assertIn('RequiresMountsFor=/boot /efi /.snapshots', unit)
        conditions = [line for line in unit.splitlines() if line.startswith('Condition')]
        self.assertEqual(conditions, ['ConditionPathExists=/boot/grub/grub.cfg'])
        self.assertIn('ExecStart=/usr/bin/emaki-boot-refresh --repair-snapshots', unit)
        self.assertIn('WantedBy=multi-user.target', unit)
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('install -Dm644 systemd/emaki-snapshot-menu.service', makefile)
        self.assertIn('ln -sfn ../emaki-snapshot-menu.service', makefile)


class RepairMutants(unittest.TestCase):
    def assert_killed(self, method, replacement):
        suite = unittest.TestSuite([SnapshotRepairChecks(method)])
        with replacement:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertFalse(result.wasSuccessful(), 'repair mutant survived')
        self.assertTrue(result.failures, 'mutant must fail an assertion, not crash the fixture')

    def test_skipped_repair_is_killed(self):
        self.assert_killed('test_missing_fragment_is_regenerated_and_durable',
                           patch.object(refresh, 'repair_snapshot_menu', return_value=None))

    def test_copied_fragment_guard_is_killed(self):
        unlink = Path.unlink

        def keep_copy(path, *args, **kwargs):
            if path.name == 'grub-btrfs.cfg' and 'emaki' in path.parts:
                return
            return unlink(path, *args, **kwargs)

        self.assert_killed('test_missing_generated_fragment_with_snapshots_refuses_old_copy',
                           patch.object(Path, 'unlink', keep_copy))

    def test_unnecessary_repair_is_killed(self):
        self.assert_killed('test_healthy_fragment_does_no_work',
                           patch.object(refresh, 'snapshot_menu_needs_repair', return_value=True))


if __name__ == '__main__':
    unittest.main()
