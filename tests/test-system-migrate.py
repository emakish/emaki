#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise package adoption and service history on scratch install roots."""
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'upkeep'))
loader = importlib.machinery.SourceFileLoader('system_migrate', str(ROOT / 'upkeep/emaki-system-migrate'))
spec = importlib.util.spec_from_loader(loader.name, loader)
migration = importlib.util.module_from_spec(spec)
loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['make', '-s', 'install-upkeep', f'DESTDIR={self.root}'], cwd=ROOT,
                       check=True, capture_output=True)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_legacy_files_adopt_package_content_once(self):
        wheel = self.put('etc/sudoers.d/10-wheel', migration.LEGACY_WHEEL)
        zram = self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM)
        snapshot = self.put('etc/snapper/configs/root', 'SUBVOLUME="/"\nFSTYPE="btrfs"\nNUMBER_LIMIT="20"\nTIMELINE_LIMIT_MONTHLY="4"\n')
        script = (ROOT / 'packaging/emaki-config/emaki-config.install').read_text()
        script = script.replace('/usr/share/libalpm/scripts/emaki-initramfs-refresh --install-hooks', ':')
        staged = self.root / 'usr/share/libalpm/scripts/emaki-system-migrate'
        script = script.replace('/usr/share/libalpm/scripts/emaki-system-migrate',
                                f'"{staged}" "{self.root}"')
        # The per-account installer-config migration has its own tests; it is not part of this root.
        script = script.replace('/usr/bin/python3 -I /usr/bin/emaki-migrate-installer-config', ':')
        subprocess.run(['bash', '-eu', '-c', script + '\npost_upgrade 0.2.0-4 0.2.0-1\n'
                        + f'\n"{staged}" "{self.root}" --transaction\n'],
                       check=True, capture_output=True, text=True)
        self.assertFalse(wheel.exists())
        self.assertEqual(zram.read_text(), migration.LEGACY_ZRAM)
        optin = self.root / 'etc/emaki/sudoers.d/wheel'
        self.assertEqual(str(optin.readlink()), '/usr/share/emaki/defaults/wheel')
        vendor = self.root / str(optin.readlink()).lstrip('/')
        vendor.chmod(0o640)
        vendor.write_text(vendor.read_text().replace('ALL=(ALL:ALL)', 'ALL=(root)'))
        self.assertIn('ALL=(root)', (self.root / str(optin.readlink()).lstrip('/')).read_text())
        self.assertIn('NUMBER_LIMIT="6-10"', snapshot.read_text())
        self.assertIn('TIMELINE_LIMIT_MONTHLY="4"', snapshot.read_text())
        # Returning to an old value after migration is a deliberate local choice.
        wheel.write_text(migration.LEGACY_WHEEL)
        zram.write_text(migration.LEGACY_ZRAM)
        snapshot.write_text(snapshot.read_text().replace('6-10', '20'))
        migration.apply(self.root)
        self.assertEqual(wheel.read_text(), migration.LEGACY_WHEEL)
        self.assertEqual(zram.read_text(), migration.LEGACY_ZRAM)
        self.assertIn('NUMBER_LIMIT="20"', snapshot.read_text())

    def legacy_niri(self):
        return (migration.NIRI_DEFAULTS + '\noutput "DP-1" {\n    scale 1\n'
                '    layout {\n        struts {\n            top -2\n        }\n    }\n}\n'
                '\noutput "eDP-1" {\n    scale 1.25\n'
                '    layout {\n        struts {\n            top -2.4\n        }\n    }\n}\n')

    def check_niri(self, path):
        # Only redirect the installed include to this checkout's package defaults.
        # Keep the complete generated layout and any later personal overrides.
        fixture = self.put('validate-' + path.name, path.read_text().replace(
            '/usr/share/emaki/niri/default.kdl', str(ROOT / 'niri/default.kdl')))
        result = subprocess.run(['niri', 'validate', '-c', str(fixture)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return fixture

    def strut_config(self, top):
        return (migration.NIRI_DEFAULTS
                + f'\nlayout {{\n    struts {{\n        top {top}\n    }}\n}}\n')

    def test_old_installer_outputs_retired_without_touching_personal_files(self):
        self.put('var/lib/emaki/migrations/state.json', '{"version": 1}\n')
        old = self.legacy_niri()
        managed = self.put('etc/emaki/niri.kdl', old)
        managed.chmod(0o644)
        original = ('// Your settings go below this include.\ninclude "/etc/emaki/niri.kdl"\n'
                    'output "eDP-1" { scale 1.5; }\n')
        personal = self.put('home/test/.config/niri/config.kdl', original)
        migration.apply(self.root)
        self.assertEqual(managed.read_text(), self.strut_config(-2.4))
        self.check_niri(managed)
        self.assertEqual(managed.stat().st_mode & 0o777, 0o644)
        backup = managed.with_name('niri.kdl.installer-outputs-backup')
        self.assertEqual(backup.read_text(), old)
        self.assertEqual(personal.read_text(), original)
        # The older per-account migration also leaves this layout untouched.
        legacy = runpy.run_path(str(ROOT / 'scripts/emaki-migrate-installer-config'))
        legacy['migrate'](personal.parents[2])
        self.assertEqual(personal.read_text(), original)
        # Subsequent system edits are local choices, not repeated migration input.
        managed.write_text(old)
        migration.apply(self.root)
        self.assertEqual(managed.read_text(), old)
        self.assertEqual(backup.read_text(), old)

    def test_installer_scale_only_layout_is_retired(self):
        old = migration.NIRI_DEFAULTS + '\noutput "eDP-1" {\n    scale 1.5\n}\n'
        managed = self.put('etc/emaki/niri.kdl', old)
        migration.retire_installer_outputs(self.root)
        self.assertEqual(managed.read_text(), migration.NIRI_DEFAULTS)
        self.check_niri(managed)

    def test_installer_fractional_scale_rounding_is_recognized(self):
        for scale, top, migrated in (('1.33333', '-2.25', '-2.250006'),
                                     ('1.25', '-1.6', '-2.4')):
            with self.subTest(scale=scale, top=top):
                old = self.legacy_niri().replace('scale 1.25', 'scale ' + scale).replace('top -2.4', 'top ' + top)
                managed = self.put('etc/emaki/niri.kdl', old)
                migration.retire_installer_outputs(self.root)
                self.assertEqual(managed.read_text(), self.strut_config(migrated))
                self.check_niri(managed)
                managed.with_name('niri.kdl.installer-outputs-backup').unlink()

    def test_migrated_strut_scale_table(self):
        # Scale, global top for a single output, tile top with shared top -2.
        table = (
            (1, '-2', 52), (1.25, '-2.4', 52.8), (1.5, '-2', 52),
            (1.75, '-2.285715', 52 + 1 / 1.75), (2, '-2', 52),
            (2.25, '-2.222223', 52 + 1 / 2.25), (2.5, None, 52),
            (2.75, None, 52 + 1 / 2.75), (3, None, 52),
            (3.25, None, 52 + 1 / 3.25), (3.5, None, 52),
            (3.75, None, 52 + 1 / 3.75), (4, None, 52),
        )
        cases = [(row,) for row in table]
        # Ordered pairs also prove that connector order does not affect selection.
        cases += [(left, right) for left in table for right in table]
        cases.append(table)
        for rows in cases:
            with self.subTest(scales=[row[0] for row in rows]):
                old = migration.NIRI_DEFAULTS
                for index, (scale, _, _) in enumerate(rows):
                    top = -math.floor(2 * scale + .5) / scale
                    old += (f'\noutput "DP-{index}" {{\n    scale {scale:g}\n'
                            f'    layout {{\n        struts {{\n            top {top:g}\n'
                            '        }\n    }\n}\n')
                managed = self.put('etc/emaki/niri.kdl', old)
                migration.retire_installer_outputs(self.root)
                high_scale = any(row[0] >= 2.5 for row in rows)
                value = None if high_scale else min((row[1] for row in rows), key=float)
                expected = migration.NIRI_DEFAULTS if value is None else self.strut_config(value)
                self.assertEqual(managed.read_text(), expected)
                self.check_niri(managed)
                for scale, _, fallback_top in rows:
                    gap = math.floor(2 * scale + .5) / scale
                    tile_top = math.ceil((52 + float(value or -2)) * scale) / scale + gap
                    expected_top = fallback_top if high_scale else 52
                    self.assertAlmostEqual(tile_top, expected_top)
                    self.assertAlmostEqual(tile_top - 44, expected_top - 44)
                    self.assertGreaterEqual(tile_top, 52)
                backup = managed.with_name('niri.kdl.installer-outputs-backup')
                self.assertEqual(backup.read_text(), old)
                migration.retire_installer_outputs(self.root)
                self.assertEqual(managed.read_text(), expected)
                backup.unlink()

    def test_scale_only_high_output_also_prevents_global_strut(self):
        old = self.legacy_niri() + '\noutput "DP-2" {\n    scale 2.5\n}\n'
        managed = self.put('etc/emaki/niri.kdl', old)
        migration.retire_installer_outputs(self.root)
        self.assertEqual(managed.read_text(), migration.NIRI_DEFAULTS)
        self.check_niri(managed)

    def test_migrated_strut_preserves_fractional_gap_and_accepts_personal_overrides(self):
        managed = self.put('etc/emaki/niri.kdl', self.legacy_niri())
        migration.retire_installer_outputs(self.root)
        text = managed.read_text()
        checked_managed = self.check_niri(managed)
        self.assertNotIn('output ', text)
        self.assertNotIn('scale ', text)
        self.assertIn('top -2.4', text)
        # The managed global strut keeps the old tile origin at both saved scales.
        for scale in (1, 1.25, 2):
            gap = math.floor(max(1, 2 * scale) + .5) / scale
            tile_top = math.ceil((52 - 2.4) * scale) / scale + gap
            self.assertEqual(tile_top, 52)
            self.assertEqual(tile_top - 44, 8)
        # Later global and connector-specific personal settings remain valid;
        # the managed include has no output block to preempt the connector.
        personal = self.put('personal.kdl', f'include {json.dumps(str(checked_managed))}\n'
                            + 'layout { struts { top -3; }; }\n'
                            + 'output "eDP-1" { scale 1.5; layout { struts { top -2; }; }; }\n')
        self.check_niri(personal)
        migration.retire_installer_outputs(self.root)
        self.assertEqual(managed.read_text(), text)

    def test_custom_managed_output_settings_survive(self):
        for old in (self.legacy_niri() + '// Local settings\n',
                    self.legacy_niri().replace('top -2.4', 'top -3'),
                    self.legacy_niri().replace('scale 1.25', 'off'),
                    migration.NIRI_DEFAULTS):
            with self.subTest(old=old):
                managed = self.put('etc/emaki/niri.kdl', old)
                migration.retire_installer_outputs(self.root)
                self.assertEqual(managed.read_text(), old)
                self.assertFalse(managed.with_name('niri.kdl.installer-outputs-backup').exists())

    def test_output_migration_refuses_links_and_preserves_existing_backup(self):
        old = self.legacy_niri()
        outside = self.put('outside.kdl', old)
        managed = self.root / 'etc/emaki/niri.kdl'
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.symlink_to(outside)
        migration.retire_installer_outputs(self.root)
        self.assertEqual(outside.read_text(), old)
        managed.unlink()
        os.link(outside, managed)
        migration.retire_installer_outputs(self.root)
        self.assertEqual(outside.read_text(), old)
        managed.unlink()
        managed.write_text(old)
        backup = self.put('etc/emaki/niri.kdl.installer-outputs-backup', 'saved settings\n')
        with self.assertRaises(OSError):
            migration.retire_installer_outputs(self.root)
        self.assertEqual(managed.read_text(), old)
        self.assertEqual(backup.read_text(), 'saved settings\n')

    def test_edits_survive_and_vendor_zram_is_masked(self):
        wheel = self.put('etc/sudoers.d/10-wheel', '# wheel access disabled\n')
        zram = self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM.replace('8192', '4096'))
        migration.apply(self.root)
        self.assertEqual(wheel.read_text(), '# wheel access disabled\n')
        self.assertIn('4096', zram.read_text())
        self.assertFalse((self.root / 'etc/emaki/sudoers.d/wheel').is_symlink())
        self.assertEqual(str((self.root / 'etc/systemd/zram-generator.conf.d/00-emaki.conf').readlink()), '/dev/null')

    def test_release_restores_wheel_and_keeps_zram_without_vendor_files(self):
        wheel = self.put('etc/sudoers.d/10-wheel', migration.LEGACY_WHEEL)
        zram = self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM)
        migration.apply(self.root)
        self.assertFalse(wheel.exists())
        migration.release_defaults(self.root, removing=True)
        self.assertIn(migration.LEGACY_WHEEL, wheel.read_text())
        self.assertEqual(wheel.stat().st_mode & 0o777, 0o440)
        self.assertEqual(zram.read_text(), migration.LEGACY_ZRAM)
        migration.release_defaults(self.root, removing=True)
        (self.root / 'usr/share/emaki/defaults/wheel').unlink()
        (self.root / 'usr/lib/systemd/zram-generator.conf.d/00-emaki.conf').unlink()
        self.assertIn(migration.LEGACY_WHEEL, wheel.read_text())
        self.assertEqual(zram.read_text(), migration.LEGACY_ZRAM)

    def test_release_fallback_is_adopted_only_while_unchanged(self):
        wheel = self.put('etc/sudoers.d/10-wheel', migration.LEGACY_WHEEL)
        self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM)
        migration.apply(self.root)
        migration.release_defaults(self.root)
        migration.apply(self.root)
        self.assertFalse(wheel.exists())
        migration.release_defaults(self.root)
        wheel.chmod(0o640)
        wheel.write_text('# Access disabled locally\n')
        migration.apply(self.root)
        self.assertEqual(wheel.read_text(), '# Access disabled locally\n')

    def test_fresh_zram_fallback_reclaimed_after_each_upgrade(self):
        migration.apply(self.root, fresh=True)
        zram = self.root / 'etc/systemd/zram-generator.conf'
        vendor = self.root / 'usr/lib/systemd/zram-generator.conf.d/00-emaki.conf'
        ledger = self.root / 'var/lib/emaki/migrations/released-zram'
        for old_size, size in (('8192', '4096'), ('4096', '2048')):
            migration.release_defaults(self.root)
            self.assertEqual(zram.read_text(), vendor.read_text())
            vendor.write_text(vendor.read_text().replace(old_size, size))
            migration.apply(self.root)
            self.assertFalse(zram.exists())
            self.assertFalse(ledger.exists())
        mask = self.root / 'etc/systemd/zram-generator.conf.d/00-emaki.conf'
        mask.parent.mkdir(parents=True, exist_ok=True)
        mask.symlink_to('/dev/null')
        migration.release_defaults(self.root)
        migration.apply(self.root)
        self.assertFalse(zram.exists())
        self.assertEqual(str(mask.readlink()), '/dev/null')

    def test_zram_reclaim_keeps_local_edit_and_original_legacy_file(self):
        zram = self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM)
        migration.apply(self.root)
        migration.release_defaults(self.root)
        migration.apply(self.root)
        self.assertEqual(zram.read_text(), migration.LEGACY_ZRAM)
        zram.unlink()
        migration.release_defaults(self.root)
        zram.write_text('# Locally disabled\n')
        migration.apply(self.root)
        self.assertEqual(zram.read_text(), '# Locally disabled\n')

    def test_zram_reclaim_waits_for_vendor_and_respects_new_mask(self):
        migration.apply(self.root, fresh=True)
        migration.release_defaults(self.root, removing=True)
        zram = self.root / 'etc/systemd/zram-generator.conf'
        vendor = self.root / 'usr/lib/systemd/zram-generator.conf.d/00-emaki.conf'
        data = vendor.read_text()
        vendor.unlink()
        migration.reclaim_defaults(self.root)
        self.assertEqual(zram.read_text(), data)
        vendor.write_text(data)
        mask = self.root / 'etc/systemd/zram-generator.conf.d/00-emaki.conf'
        mask.parent.mkdir(parents=True, exist_ok=True)
        mask.symlink_to('/dev/null')
        migration.apply(self.root)
        self.assertFalse(zram.exists())
        self.assertEqual(str(mask.readlink()), '/dev/null')

    def test_release_recovers_zram_deleted_by_earlier_migration(self):
        self.put('etc/systemd/zram-generator.conf', migration.LEGACY_ZRAM)
        migration.apply(self.root)
        zram = self.root / 'etc/systemd/zram-generator.conf'
        zram.unlink()
        migration.release_defaults(self.root)
        self.assertIn(migration.LEGACY_ZRAM, zram.read_text())

    def test_release_keeps_custom_and_masked_settings(self):
        wheel = self.put('etc/sudoers.d/10-wheel', '# Disabled\n')
        migration.apply(self.root)
        migration.release_defaults(self.root, removing=True)
        self.assertEqual(wheel.read_text(), '# Disabled\n')
        self.assertFalse((self.root / 'etc/systemd/zram-generator.conf').exists())

    def test_fresh_package_install_leaves_vendor_zram_enabled(self):
        script = (ROOT / 'packaging/emaki-config/emaki-config.install').read_text()
        script = script.replace('/usr/share/libalpm/scripts/emaki-initramfs-refresh --install-hooks', ':')
        staged = self.root / 'usr/share/libalpm/scripts/emaki-system-migrate'
        script = script.replace('/usr/share/libalpm/scripts/emaki-system-migrate',
                                f'"{staged}" "{self.root}"')
        # The per-account installer-config migration has its own tests; it is not part of this root.
        script = script.replace('/usr/bin/python3 -I /usr/bin/emaki-migrate-installer-config', ':')
        subprocess.run(['bash', '-eu', '-c', script + '\npost_install\n'
                        + f'\n"{staged}" "{self.root}" --transaction\n'],
                       check=True, capture_output=True, text=True)
        mask = self.root / 'etc/systemd/zram-generator.conf.d/00-emaki.conf'
        self.assertFalse(mask.is_symlink())
        self.assertTrue((self.root / 'usr/lib/systemd/zram-generator.conf.d/00-emaki.conf').is_file())

    def test_deleted_zram_config_keeps_zram_disabled(self):
        migration.apply(self.root)
        mask = self.root / 'etc/systemd/zram-generator.conf.d/00-emaki.conf'
        self.assertTrue(mask.is_symlink())
        self.assertEqual(str(mask.readlink()), '/dev/null')
        mask.unlink()
        migration.apply(self.root)
        self.assertFalse(mask.is_symlink())

    def test_vendor_zram_dropin_sorts_before_personal_dropins(self):
        dropins = self.root / 'usr/lib/systemd/zram-generator.conf.d'
        self.assertEqual([p.name for p in dropins.iterdir()], ['00-emaki.conf'])
        self.assertLess('00-emaki.conf', '10-local.conf')

    @unittest.skipUnless(shutil.which('visudo'), 'visudo unavailable')
    def test_sudo_gateway_parses_without_granting_edited_old_rule(self):
        migration.apply(self.root)
        gateway = self.root / 'etc/sudoers.d/10-emaki-wheel'
        # Bind the packaged include to this scratch directory for native parsing.
        gateway.chmod(0o640)
        gateway.write_text(gateway.read_text().replace('/etc/emaki', str(self.root / 'etc/emaki')))
        result = subprocess.run(['visudo', '-cf', str(gateway)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((self.root / 'etc/emaki/sudoers.d').iterdir()), [])

    def test_symlinked_or_hardlinked_files_stay_untouched(self):
        external = self.put('custom', migration.LEGACY_ZRAM)
        zram = self.root / 'etc/systemd/zram-generator.conf'
        zram.parent.mkdir(parents=True, exist_ok=True)
        zram.symlink_to(external)
        migration.apply(self.root)
        self.assertEqual(external.read_text(), migration.LEGACY_ZRAM)
        self.assertTrue(zram.is_symlink())

    @unittest.skipUnless(shutil.which('systemctl'), 'systemctl unavailable')
    def test_new_service_enabled_once_and_local_disable_retained(self):
        preset = self.put('usr/lib/systemd/system-preset/50-emaki.preset',
                          'enable emaki-refresh-mirrors.timer\nenable bluetooth.service\n')
        migration.apply(self.root)
        link = self.root / 'etc/systemd/system/timers.target.wants/emaki-refresh-mirrors.timer'
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / 'etc/systemd/system/bluetooth.target.wants/bluetooth.service').exists())
        subprocess.run(['systemctl', '--root=' + str(self.root), 'disable', 'emaki-refresh-mirrors.timer'], check=True, capture_output=True)
        migration.apply(self.root)
        self.assertFalse(link.is_symlink())
        self.put('usr/lib/systemd/system/emaki-next.timer', '[Unit]\nDescription=Next\n[Timer]\nOnBootSec=1h\n[Install]\nWantedBy=timers.target\n')
        preset.write_text(preset.read_text() + 'enable emaki-next.timer\n')
        migration.apply(self.root)
        self.assertTrue((self.root / 'etc/systemd/system/timers.target.wants/emaki-next.timer').is_symlink())
        self.assertFalse(link.is_symlink())

    @unittest.skipUnless(shutil.which('systemctl'), 'systemctl unavailable')
    def test_rich_firmware_timer_enabled_once_and_mask_respected(self):
        # Minimal has no Rich preset; introducing Rich must offer the timer once.
        self.put('usr/lib/systemd/system/fwupd-refresh.timer',
                 '[Timer]\nOnCalendar=hourly\n[Install]\nWantedBy=timers.target\n')
        migration.apply(self.root)
        link = self.root / 'etc/systemd/system/timers.target.wants/fwupd-refresh.timer'
        self.assertFalse(link.is_symlink())
        self.put('usr/lib/systemd/system-preset/45-emaki-apps.preset',
                 'enable fwupd-refresh.timer\n')
        migration.apply(self.root)
        self.assertTrue(link.is_symlink())
        link.unlink()
        migration.apply(self.root)
        self.assertFalse(link.is_symlink())
        # A mask before the first offer must survive as well.
        (self.root / 'var/lib/emaki/migrations/state.json').unlink()
        mask = self.root / 'etc/systemd/system/fwupd-refresh.timer'
        mask.symlink_to('/dev/null')
        migration.apply(self.root)
        self.assertEqual(str(mask.readlink()), '/dev/null')
        self.assertFalse(link.is_symlink())

    def test_local_mask_before_first_migration_is_kept(self):
        self.put('usr/lib/systemd/system-preset/50-emaki.preset', 'enable emaki-refresh-mirrors.timer\n')
        local = self.root / 'etc/systemd/system/emaki-refresh-mirrors.timer'
        local.parent.mkdir(parents=True)
        local.symlink_to('/dev/null')
        migration.apply(self.root)
        self.assertEqual(str(local.readlink()), '/dev/null')
        self.assertIn('emaki-refresh-mirrors.timer', json.loads((self.root / 'var/lib/emaki/migrations/state.json').read_text())['units'])

    @unittest.skipUnless(shutil.which('systemctl'), 'systemctl unavailable')
    def test_old_install_receives_cache_timer_unless_masked(self):
        self.put('usr/lib/systemd/system-preset/50-emaki.preset', 'enable paccache.timer\n')
        self.put('usr/lib/systemd/system/paccache.timer', '[Timer]\nOnCalendar=weekly\n[Install]\nWantedBy=timers.target\n')
        migration.apply(self.root)
        link = self.root / 'etc/systemd/system/timers.target.wants/paccache.timer'
        self.assertTrue(link.is_symlink())
        link.unlink()
        migration.apply(self.root)
        self.assertFalse(link.is_symlink())
        (self.root / 'var/lib/emaki/migrations/state.json').unlink()
        mask = self.root / 'etc/systemd/system/paccache.timer'
        mask.symlink_to('/dev/null')
        migration.apply(self.root)
        self.assertFalse(link.is_symlink())
        self.assertEqual(str(mask.readlink()), '/dev/null')

    @unittest.skipUnless(shutil.which('systemctl'), 'systemctl unavailable')
    def test_pre_upgrade_remembers_vendor_link_even_when_reported_disabled(self):
        self.put('usr/lib/systemd/system/paccache.timer', '[Timer]\nOnCalendar=weekly\n[Install]\nWantedBy=timers.target\n')
        link = self.root / 'usr/lib/systemd/system/timers.target.wants/paccache.timer'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to('../paccache.timer')
        result = subprocess.run(['systemctl', '--root=' + str(self.root), 'is-enabled',
                                 'paccache.timer'], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'disabled')
        script = (ROOT / 'packaging/emaki-config/emaki-config.install').read_text()
        for name in ('usr/lib/systemd/system/timers.target.wants/paccache.timer',
                     'var/lib/emaki/migrations'):
            script = script.replace('/' + name, str(self.root / name))
        script = script.replace('--root=/', '--root=' + str(self.root))
        def pre_upgrade():
            subprocess.run(['bash', '-eu', '-c', script + '\npre_upgrade\n'],
                           check=True, capture_output=True, text=True)
        marker = self.root / 'var/lib/emaki/migrations/paccache-enabled'
        pre_upgrade()
        self.assertTrue(marker.is_file())
        marker.unlink()
        mask = self.root / 'etc/systemd/system/paccache.timer'
        mask.parent.mkdir(parents=True, exist_ok=True)
        mask.symlink_to('/dev/null')
        pre_upgrade()
        self.assertFalse(marker.exists())
        mask.unlink()
        link.unlink()
        pre_upgrade()
        self.assertFalse(marker.exists())

    def test_vendor_cache_enablement_transfers_once(self):
        self.put('usr/lib/systemd/system-preset/50-emaki.preset', 'enable paccache.timer\n')
        self.put('usr/lib/systemd/system/paccache.timer', '[Timer]\nOnCalendar=weekly\n[Install]\nWantedBy=timers.target\n')
        self.put('var/lib/emaki/migrations/paccache-enabled', '')
        migration.apply(self.root)
        link = self.root / 'etc/systemd/system/timers.target.wants/paccache.timer'
        self.assertTrue(link.is_symlink())
        link.unlink()
        migration.apply(self.root)
        self.assertFalse(link.is_symlink())

    def test_one_transaction_hook_owns_migration_before_initramfs(self):
        script = (ROOT / 'packaging/emaki-config/emaki-config.install').read_text()
        self.assertIn('/usr/share/libalpm/scripts/emaki-system-migrate --mark-fresh', script)
        self.assertEqual(script.count('/usr/share/libalpm/scripts/emaki-system-migrate'), 1)
        apps = (ROOT / 'packaging/emaki-apps/emaki-apps.install').read_text()
        self.assertNotIn('emaki-system-migrate', apps)
        hook = (ROOT / 'upkeep/89-emaki-system-migrate.hook').read_text()
        self.assertIn('Target = emaki-config\nTarget = emaki-apps', hook)
        self.assertIn('When = PostTransaction', hook)
        self.assertIn('Exec = /usr/share/libalpm/scripts/emaki-system-migrate --transaction', hook)
        self.assertLess('89-emaki-system-migrate.hook', '90-mkinitcpio-install.hook')
        self.assertIn('post_upgrade()', script)
        self.assertNotIn('preset-all', script)


if __name__ == '__main__':
    unittest.main()
