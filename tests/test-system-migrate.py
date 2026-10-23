#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise package adoption and service history on scratch install roots."""
import contextlib
import io
import importlib.machinery
import importlib.util
import ctypes.util
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'upkeep'))
loader = importlib.machinery.SourceFileLoader('system_migrate', str(ROOT / 'upkeep/emaki-system-migrate'))
spec = importlib.util.spec_from_loader(loader.name, loader)
migration = importlib.util.module_from_spec(spec)
loader.exec_module(migration)


class SudoRemovalTests(unittest.TestCase):
    def setUp(self):
        self.broker = mock.MagicMock(spec=Path)
        self.broker.__str__.return_value = '/opt/emaki/libexec/emaki-sudo-broker'
        self.broker.exists.return_value = True
        self.broker.is_absolute.return_value = True
        self.broker.is_file.return_value = True
        self.broker.stat.return_value = mock.Mock(st_uid=0, st_mode=0o100755)
        self.runtime = mock.Mock(spec=Path)
        self.runtime.exists.return_value = True
        self.runtime.is_symlink.return_value = False
        def path(value):
            if value == migration.SUDO_BROKER:
                return self.broker
            if value == '/run/emaki-sudo':
                return self.runtime
            return Path(value)
        patcher = mock.patch.object(migration, 'Path', side_effect=path)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(migration, 'safe', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(migration.subprocess, 'run')
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_precedes_checked_bounded_drain(self):
        migration.drain_sudo_keys(Path('/'))
        self.assertEqual(self.run.call_args_list, [
            mock.call(['systemctl', 'stop', 'emaki-sudo.service'], check=True, timeout=120),
            mock.call([str(self.broker), '--cleanup'], check=True, timeout=40),
        ])

    def test_stop_failure_vetoes_before_cleanup_or_configuration_changes(self):
        self.run.side_effect = subprocess.CalledProcessError(1, 'systemctl')
        with mock.patch.object(migration, 'write') as write:
            with self.assertRaises(subprocess.CalledProcessError):
                migration.release_defaults(Path('/'), removing=True)
            write.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_cleanup_failure_and_timeout_veto_removal(self):
        for error in (subprocess.CalledProcessError(1, 'cleanup'),
                      subprocess.TimeoutExpired('cleanup', 40)):
            with self.subTest(error=type(error).__name__):
                self.run.reset_mock()
                self.run.side_effect = [None, error]
                with mock.patch.object(migration, 'write') as write:
                    with self.assertRaises(type(error)):
                        migration.release_defaults(Path('/'), removing=True)
                    write.assert_not_called()
                self.assertEqual(self.run.call_count, 2)

    def test_absent_old_feature_requires_no_commands(self):
        self.broker.exists.return_value = False
        self.runtime.exists.return_value = False
        migration.drain_sudo_keys(Path('/'))
        self.run.assert_not_called()

    def test_installed_service_that_never_ran_needs_no_drain(self):
        self.runtime.exists.return_value = False
        migration.drain_sudo_keys(Path('/'))
        self.run.assert_called_once_with(['systemctl', 'stop', 'emaki-sudo.service'],
                                         check=True, timeout=120)

    def test_missing_broker_with_retained_runtime_refuses(self):
        self.broker.exists.return_value = False
        self.broker.is_file.return_value = False
        self.runtime.exists.return_value = True
        with self.assertRaises(OSError):
            migration.drain_sudo_keys(Path('/'))
        self.run.assert_not_called()

    def test_unsafe_executable_refuses_without_commands(self):
        for uid, mode in ((1000, 0o100755), (0, 0o100775), (0, 0o100644)):
            with self.subTest(uid=uid, mode=mode):
                self.broker.stat.return_value = mock.Mock(st_uid=uid, st_mode=mode)
                with self.assertRaises(OSError):
                    migration.drain_sudo_keys(Path('/'))
        self.run.assert_not_called()

    def test_offline_root_never_probes_or_stops_host(self):
        with tempfile.TemporaryDirectory() as directory:
            migration.drain_sudo_keys(Path(directory))
        self.run.assert_not_called()
        self.broker.exists.assert_not_called()
        self.runtime.exists.assert_not_called()


@unittest.skipUnless(ctypes.util.find_library('alpm') and shutil.which('pacman'),
                     'libalpm and pacman unavailable')
class DesktopApplicationMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'var/lib/pacman'
        (self.database / 'local').mkdir(parents=True)
        (self.database / 'local/ALPM_DB_VERSION').write_text('9\n')
        self.state = self.root / 'var/lib/emaki/migrations/state.json'
        self.state.parent.mkdir(parents=True)
        self.state.write_text('{"version": 3}\n')
        self.package('emaki-desktop', reason=0)

    def package(self, name, reason=1):
        directory = self.database / 'local' / (name + '-1-1')
        directory.mkdir(exist_ok=True)
        (directory / 'desc').write_text(
            f'%NAME%\n{name}\n\n%VERSION%\n1-1\n\n%REASON%\n{reason}\n\n')

    def explicit(self):
        result = subprocess.run(['pacman', '--root', str(self.root), '--dbpath',
                                 str(self.database), '-Qeq'],
                                capture_output=True, text=True, check=True)
        return set(result.stdout.splitlines())

    def apply(self, fresh=False):
        with mock.patch('emaki_boot_defaults.apply'), mock.patch.object(migration, 'services'):
            migration.apply(self.root, fresh=fresh)

    def test_existing_applications_become_explicit_under_transaction_lock(self):
        for name in migration.FORMER_DESKTOP_DEPENDENCIES:
            self.package(name)
        self.package('unrelated')
        lock = self.database / 'db.lck'
        lock.write_text('held by the package transaction\n')
        self.apply()
        self.assertEqual(self.explicit(), {'emaki-desktop', *migration.FORMER_DESKTOP_DEPENDENCIES})
        self.assertEqual(lock.read_text(), 'held by the package transaction\n')
        self.assertEqual(json.loads(self.state.read_text())['version'], 7)

    def test_removed_applications_stay_removed_and_later_reason_changes_stay(self):
        self.package('firefox')
        self.apply()
        self.assertEqual(self.explicit(), {'emaki-desktop', 'firefox'})
        self.assertEqual(len(list((self.database / 'local').glob('*/desc'))), 2)
        self.package('firefox', reason=1)
        self.apply()
        self.assertEqual(self.explicit(), {'emaki-desktop'})

    def test_fresh_install_does_not_change_package_reasons(self):
        self.package('firefox')
        self.apply(fresh=True)
        self.assertEqual(self.explicit(), {'emaki-desktop'})
        self.assertEqual(json.loads(self.state.read_text())['version'], 7)

    def test_without_desktop_package_does_not_change_package_reasons(self):
        shutil.rmtree(self.database / 'local/emaki-desktop-1-1')
        self.package('firefox')
        self.package('unrelated', reason=0)
        self.apply()
        self.assertEqual(self.explicit(), {'unrelated'})

    def test_failed_migration_keeps_version_for_retry(self):
        with mock.patch.object(migration, 'preserve_desktop_applications',
                               side_effect=OSError('package database unavailable')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(json.loads(self.state.read_text())['version'], 3)
        self.package('firefox')
        self.apply()
        self.assertIn('firefox', self.explicit())


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
        # Broker restart is covered by the sudo packaging test; never contact the host.
        script = script.replace('systemctl try-restart emaki-sudo.service', ':')
        # Package lifecycle service operations stay inside the fixture.
        script = 'systemctl() { :; }\n' + script
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

    def as_root(self, *arguments):
        # A user namespace maps this account to uid 0, so the fixture is root-owned there.
        script = self.root / 'usr/share/libalpm/scripts/emaki-system-migrate'
        return subprocess.run(['unshare', '-r', sys.executable, '-B', str(script), str(self.root), *arguments],
                              capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('unshare'), 'unshare unavailable')
    def test_old_root_owned_sudoers_directory_mode_is_repaired_once(self):
        directory = self.root / 'etc/sudoers.d'
        self.assertEqual(directory.stat().st_mode & 0o7777, 0o750)  # what new installs get
        directory.chmod(0o755)
        probe = subprocess.run(['unshare', '-r', 'stat', '-c', '%u', str(directory)], capture_output=True, text=True)
        if probe.returncode:
            self.skipTest('user namespaces unavailable: ' + probe.stderr.strip())
        self.assertEqual(probe.stdout.strip(), '0')
        state = self.root / 'var/lib/emaki/migrations/state.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({'version': 2, 'units': sorted(migration.LEGACY_UNITS)}) + '\n')
        result = self.as_root('--transaction')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(directory.stat().st_mode & 0o7777, 0o750)
        self.assertEqual(json.loads(state.read_text())['version'], 7)
        # Once only: an administrator's later choice stays.
        directory.chmod(0o755)
        self.assertEqual(self.as_root('--transaction').returncode, 0)
        self.assertEqual(directory.stat().st_mode & 0o7777, 0o755)

    @unittest.skipUnless(shutil.which('unshare'), 'unshare unavailable')
    def test_other_sudoers_directory_modes_owners_and_links_stay(self):
        directory = self.root / 'etc/sudoers.d'
        for mode in (0o700, 0o775, 0o750):
            with self.subTest(mode=oct(mode)):
                directory.chmod(mode)
                (self.root / 'var/lib/emaki/migrations/state.json').unlink(missing_ok=True)
                result = self.as_root()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(directory.stat().st_mode & 0o7777, mode)
        # Owned by an ordinary account (this test's own uid outside the namespace).
        directory.chmod(0o755)
        (self.root / 'var/lib/emaki/migrations/state.json').unlink()
        migration.apply(self.root)
        self.assertEqual(directory.stat().st_mode & 0o7777, 0o755)
        # A linked directory is never followed.
        target = self.root / 'elsewhere'
        directory.rename(target)
        directory.symlink_to(target)
        (self.root / 'var/lib/emaki/migrations/state.json').unlink()
        self.assertEqual(self.as_root().returncode, 0)
        self.assertEqual(target.stat().st_mode & 0o7777, 0o755)

    @unittest.skipUnless(shutil.which('unshare'), 'unshare unavailable')
    def test_root_only_flatpak_installation_becomes_readable_once(self):
        # What Programs & Updates 0.8.0 left with umask 077 (a VM install of one Flathub app).
        base = self.root / 'var/lib/flatpak'
        deployment = base / 'app/org.example.App/x86_64/stable/abc123'
        runtime = base / 'runtime/org.example.Platform/x86_64/25.08/def456'
        exports = base / 'exports/share/applications'
        for directory in (deployment / 'files/bin', deployment / 'export/share/applications',
                          runtime / 'files', exports, base / 'repo/refs/heads/deploy'):
            directory.mkdir(parents=True)
        private = deployment / 'files/private'
        private.mkdir()
        files = {deployment / 'deploy': 0o600, deployment / 'export/share/applications/a.desktop': 0o600,
                 exports / 'mimeinfo.cache': 0o600, deployment / 'files/bin/app': 0o700,
                 deployment / 'metadata': 0o644, base / 'repo/refs/heads/deploy/ref': 0o600}
        for path, mode in files.items():
            path.write_text('x\n')
            path.chmod(mode)
        (exports / 'org.example.App.desktop').symlink_to(
            '../../../app/org.example.App/x86_64/stable/abc123/export/share/applications/a.desktop')
        for directory in (*base.rglob('*'), base / 'exports', base / 'app', base / 'runtime'):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)
        private.chmod(0o700)
        state = self.root / 'var/lib/emaki/migrations/state.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({'version': 4, 'units': sorted(migration.LEGACY_UNITS)}) + '\n')
        result = self.as_root('--transaction')
        if 'unshare' in result.stderr and result.returncode:
            self.skipTest('user namespaces unavailable: ' + result.stderr.strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        mode = lambda path: path.lstat().st_mode & 0o7777
        for directory in (base / 'exports', exports, base / 'app', deployment.parent, deployment,
                          deployment / 'export/share/applications', runtime, base / 'repo/refs/heads/deploy'):
            self.assertEqual(mode(directory), 0o755, directory)
        for path in (deployment / 'deploy', deployment / 'export/share/applications/a.desktop',
                     exports / 'mimeinfo.cache', base / 'repo/refs/heads/deploy/ref'):
            self.assertEqual(mode(path), 0o644, path)
        # The checked-out program keeps its committed modes.
        self.assertEqual(mode(private), 0o700)
        self.assertEqual(mode(deployment / 'files/bin/app'), 0o700)
        self.assertEqual(mode(deployment / 'files/bin'), 0o700)
        # The files directory itself is libflatpak's in a partial (Locale) checkout.
        self.assertEqual(mode(deployment / 'files'), 0o755)
        self.assertEqual(mode(deployment / 'metadata'), 0o644)
        self.assertEqual(json.loads(state.read_text())['version'], 7)
        # Once only: a later root-only choice stays.
        exports.chmod(0o700)
        self.assertEqual(self.as_root('--transaction').returncode, 0)
        self.assertEqual(mode(exports), 0o700)

    @unittest.skipUnless(shutil.which('unshare'), 'unshare unavailable')
    def test_flatpak_entries_of_other_owners_or_with_group_bits_stay(self):
        exports = self.root / 'var/lib/flatpak/exports'
        exports.mkdir(parents=True)
        exports.chmod(0o750)
        mine = self.root / 'var/lib/flatpak/app'
        mine.mkdir()
        mine.chmod(0o700)
        # Owned by this account outside the namespace, so not by root: unchanged.
        migration.repair_flatpak_modes(self.root)
        self.assertEqual(mine.stat().st_mode & 0o7777, 0o700)
        result = self.as_root('--transaction')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(exports.stat().st_mode & 0o7777, 0o750)
        # A linked installation is never followed.
        shutil.rmtree(self.root / 'var/lib/flatpak')
        target = self.root / 'elsewhere'
        (target / 'exports').mkdir(parents=True)
        (target / 'exports').chmod(0o700)
        (self.root / 'var/lib/flatpak').symlink_to(target)
        (self.root / 'var/lib/emaki/migrations/state.json').unlink()
        self.assertEqual(self.as_root('--transaction').returncode, 0)
        self.assertEqual((target / 'exports').stat().st_mode & 0o7777, 0o700)

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


class LanguageMigrationTests(unittest.TestCase):
    """Updated systems generate every shipped language, so Settings can offer them."""
    STOCK = ('# Uncomment the locales you need.\n#en_US.UTF-8 UTF-8  \nen_US.UTF-8 UTF-8\n'
             '#es_MX.UTF-8 UTF-8  \n#es_MX ISO-8859-1  \n#pt_BR.UTF-8 UTF-8  \n#pt_BR ISO-8859-1  \n')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.gen = self.root / 'etc/locale.gen'
        self.gen.parent.mkdir(parents=True)

    def test_stock_lines_are_uncommented_and_nothing_else_changes(self):
        self.gen.write_text(self.STOCK)
        self.gen.chmod(0o644)
        self.assertTrue(migration.enable_languages(self.root))
        self.assertEqual(self.gen.read_text(), self.STOCK.replace('#es_MX.UTF-8 UTF-8  \n', 'es_MX.UTF-8 UTF-8\n')
                         .replace('#pt_BR.UTF-8 UTF-8  \n', 'pt_BR.UTF-8 UTF-8\n')
                         + 'de_DE.UTF-8 UTF-8\nfr_FR.UTF-8 UTF-8\nru_RU.UTF-8 UTF-8\n')
        self.assertEqual(self.gen.stat().st_mode & 0o777, 0o644)
        self.assertFalse(migration.enable_languages(self.root))

    def test_missing_lines_are_appended_once(self):
        self.gen.write_text('en_US.UTF-8 UTF-8')
        self.assertTrue(migration.enable_languages(self.root))
        self.assertEqual(self.gen.read_text(), 'en_US.UTF-8 UTF-8\n' + ''.join(
            language + ' UTF-8\n' for language in migration.EMAKI_LANGUAGES))
        self.assertFalse(migration.enable_languages(self.root))

    def test_missing_or_linked_file_is_left_alone(self):
        self.assertFalse(migration.enable_languages(self.root))
        target = self.root / 'elsewhere'
        target.write_text(self.STOCK)
        self.gen.symlink_to(target)
        self.assertFalse(migration.enable_languages(self.root))
        self.assertEqual(target.read_text(), self.STOCK)

    def test_offline_root_never_builds_on_the_host(self):
        self.gen.write_text(self.STOCK)
        with mock.patch.object(migration.subprocess, 'run') as run:
            self.assertTrue(migration.generate_languages(self.root))
        run.assert_not_called()

    def test_running_system_builds_and_a_failure_is_retried(self):
        self.gen.write_text(self.STOCK)
        # Arch's locale-gen exits 0 when one locale fails to build: success is the languages being there.
        quiet = mock.Mock(returncode=0, stderr='localedef: cannot open es_MX')
        with mock.patch.object(migration, 'enable_languages', return_value=True), \
                mock.patch.object(migration, 'languages_generated', return_value=False), \
                mock.patch.object(migration.subprocess, 'run', return_value=quiet) as run:
            self.assertFalse(migration.generate_languages(Path('/')))
        self.assertEqual(run.call_args.args[0], ['locale-gen'])
        with mock.patch.object(migration, 'enable_languages', return_value=True), \
                mock.patch.object(migration, 'languages_generated', return_value=True), \
                mock.patch.object(migration.subprocess, 'run', return_value=mock.Mock(returncode=0, stderr='')):
            self.assertTrue(migration.generate_languages(Path('/')))
        with mock.patch.object(migration, 'enable_languages', return_value=False), \
                mock.patch.object(migration, 'languages_generated', return_value=True), \
                mock.patch.object(migration.subprocess, 'run') as run:
            self.assertTrue(migration.generate_languages(Path('/')))
        run.assert_not_called()

    def test_a_hung_or_missing_command_never_stops_the_migration(self):
        for error in (migration.subprocess.TimeoutExpired('locale-gen', 900), FileNotFoundError('locale-gen')):
            with mock.patch.object(migration, 'enable_languages', return_value=True), \
                    mock.patch.object(migration.subprocess, 'run', side_effect=error):
                self.assertFalse(migration.generate_languages(Path('/')))

    def test_fresh_install_leaves_the_build_to_the_installer(self):
        with mock.patch.object(migration, 'enable_languages', return_value=True), \
                mock.patch.object(migration.subprocess, 'run') as run:
            self.assertTrue(migration.generate_languages(Path('/'), build=False))
        run.assert_not_called()

    def test_previous_language_migration_is_extended_once(self):
        state = self.root / 'var/lib/emaki/migrations/state.json'
        state.parent.mkdir(parents=True)
        state.write_text('{"version": 6}\n')
        self.gen.write_text('en_US.UTF-8 UTF-8\nes_MX.UTF-8 UTF-8\npt_BR.UTF-8 UTF-8\n')
        with mock.patch.object(migration, 'reclaim_defaults'), \
                mock.patch.object(migration, 'services'), \
                mock.patch('emaki_boot_defaults.apply'), \
                mock.patch.object(migration, 'generate_languages', wraps=migration.generate_languages) as generate:
            migration.apply(self.root)
            self.assertEqual(json.loads(state.read_text())['version'], 7)
            self.assertTrue(all(locale + ' UTF-8\n' in self.gen.read_text()
                                for locale in migration.EMAKI_LANGUAGES))
            migration.apply(self.root)
            self.assertEqual(generate.call_count, 1)


    def apply(self):
        with mock.patch.object(migration, 'reclaim_defaults'), \
                mock.patch.object(migration, 'services'), \
                mock.patch('emaki_boot_defaults.apply'):
            migration.apply(self.root)

    def state(self, version):
        path = self.root / 'var/lib/emaki/migrations/state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': version}) + '\n')
        return path

    def test_each_upgrade_only_enables_languages_introduced_after_its_version(self):
        source = ('en_US.UTF-8 UTF-8\n#es_MX.UTF-8 UTF-8  # disabled\n'
                  '#pt_BR.UTF-8 UTF-8\n#de_DE.UTF-8 UTF-8\n#fr_FR.UTF-8 UTF-8\n'
                  '#ru_RU.UTF-8 UTF-8  # not needed\nuk_UA.UTF-8 UTF-8\n')
        for version in (5, 6, 7):
            with self.subTest(version=version):
                state = self.state(version)
                self.gen.write_text(source)
                with mock.patch.object(migration, 'generate_languages', wraps=migration.generate_languages) as generate:
                    self.apply()
                    result = self.gen.read_text()
                    self.assertEqual(json.loads(state.read_text())['version'], 7)
                    self.assertEqual(generate.call_args_list, [
                        mock.call(self.root, build=True, languages=languages)
                        for step, languages in migration.LANGUAGE_MIGRATIONS.items() if step > version])
                    self.apply()
                    self.assertEqual(self.gen.read_text(), result)
                self.assertIn('uk_UA.UTF-8 UTF-8\n', result)
                for step, languages in migration.LANGUAGE_MIGRATIONS.items():
                    for language in languages:
                        expected = ('' if step > version else '#') + language + ' UTF-8'
                        self.assertTrue(any(line.startswith(expected) for line in result.splitlines()))
                if version == 7:
                    self.assertEqual(result, source)
                else:
                    self.assertEqual(result.count('ru_RU.UTF-8'), 1)
                    self.assertIn('ru_RU.UTF-8 UTF-8  # not needed\n', result)

    def test_active_trailing_comments_are_not_duplicated_or_rewritten(self):
        source = 'ru_RU.UTF-8\tUTF-8  # enabled\n#ru_RU.UTF-8 UTF-8\n'
        self.gen.write_text(source)
        self.assertFalse(migration.enable_languages(self.root, ('ru_RU.UTF-8',)))
        self.assertEqual(self.gen.read_text(), source)

    def test_failure_retains_exact_step_and_retry_does_not_reenable_prior_set(self):
        for failed_step in (6, 7):
            with self.subTest(step=failed_step):
                state = self.state(5)
                self.gen.write_text(self.STOCK)
                def generate(root, build, languages):
                    migration.enable_languages(root, languages)
                    return languages != migration.LANGUAGE_MIGRATIONS[failed_step]
                with mock.patch.object(migration, 'generate_languages', side_effect=generate):
                    self.apply()
                self.assertEqual(json.loads(state.read_text())['version'], failed_step - 1)
                if failed_step == 7:
                    self.gen.write_text(self.gen.read_text().replace('es_MX.UTF-8 UTF-8\n', '#es_MX.UTF-8 UTF-8\n'))
                with mock.patch.object(migration, 'generate_languages', wraps=migration.generate_languages) as generate:
                    self.apply()
                self.assertEqual(json.loads(state.read_text())['version'], 7)
                self.assertEqual(generate.call_args_list[0].kwargs['languages'],
                                 migration.LANGUAGE_MIGRATIONS[failed_step])
                if failed_step == 7:
                    self.assertIn('#es_MX.UTF-8 UTF-8\n', self.gen.read_text())

    def test_generated_check_uses_only_the_requested_set(self):
        available = mock.Mock(stdout='de_DE.utf8 fr_FR.utf8 ru_RU.utf8\n')
        with mock.patch.object(migration.subprocess, 'run', return_value=available):
            self.assertTrue(migration.languages_generated(migration.LANGUAGE_MIGRATIONS[7]))
            self.assertFalse(migration.languages_generated(migration.LANGUAGE_MIGRATIONS[6]))
        languages = migration.LANGUAGE_MIGRATIONS[7]
        with mock.patch.object(migration, 'enable_languages', return_value=False) as enable, \
                mock.patch.object(migration, 'languages_generated', return_value=True) as generated, \
                mock.patch.object(migration.subprocess, 'run') as run:
            self.assertTrue(migration.generate_languages(Path('/'), languages=languages))
        enable.assert_called_once_with(Path('/'), languages)
        generated.assert_called_once_with(languages)
        run.assert_not_called()

    def test_warning_identifies_the_failed_language_set(self):
        for languages in migration.LANGUAGE_MIGRATIONS.values():
            output = io.StringIO()
            with mock.patch.object(migration, 'enable_languages', side_effect=OSError('failed')), \
                    contextlib.redirect_stderr(output):
                self.assertFalse(migration.generate_languages(Path('/'), languages=languages))
            self.assertIn(', '.join(languages), output.getvalue())
            self.assertIn('the next update retries', output.getvalue())

    def test_installer_live_image_and_migration_have_the_same_languages(self):
        source = runpy.run_path(str(ROOT / 'installer/emaki_installer/i18n.py'))
        self.assertEqual(set(source['LOCALES']), {'en_US.UTF-8', *migration.EMAKI_LANGUAGES})
        live = (ROOT / 'iso/profile/airootfs/etc/locale.gen').read_text().splitlines()
        self.assertEqual({line.split()[0] for line in live if line and not line.startswith('#')},
                         set(source['LOCALES']))


class ConsoleFontMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.console = self.root / 'etc/vconsole.conf'
        self.console.parent.mkdir()
        self.locale = self.root / 'etc/locale.conf'
        self.locale.write_text('LANG=ru_RU.UTF-8\n')

    def test_old_font_is_replaced_for_all_languages_without_other_changes(self):
        for locale in ('en_US.UTF-8', 'es_MX.UTF-8', 'fr_FR.UTF-8', 'ru_RU.UTF-8'):
            for font in ('ter-124b', '"ter-124b"', "'ter-124b'"):
                with self.subTest(locale=locale, font=font):
                    self.locale.write_text('LANG=' + locale + '\n')
                    source = 'KEYMAP=us\nXKBLAYOUT=us,ru\nFONT=' + font + ' # default\n'
                    self.console.write_text(source)
                    self.console.chmod(0o640)
                    migration.repair_console_font(self.root)
                    self.assertEqual(self.console.read_text(), source.replace('ter-124b', 'ter-v24b'))
                    self.assertEqual(self.console.stat().st_mode & 0o777, 0o640)

    def test_russian_unset_font_uses_large_unicode_font_and_keeps_keyboard(self):
        source = 'KEYMAP=us\nXKBLAYOUT=us,ru'
        self.console.write_text(source)
        migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), source + '\nFONT=ter-v32b\n')
        migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), source + '\nFONT=ter-v32b\n')

    def test_custom_empty_and_ambiguous_fonts_are_preserved(self):
        for font in ('FONT=latarcyrheb-sun32', 'FONT=', 'FONT=""',
                     'FONT=ter-124b\nFONT=custom', 'FONT=ter-v32b'):
            with self.subTest(font=font):
                self.console.write_text('KEYMAP=us\n' + font + '\n')
                original = self.console.read_bytes()
                with mock.patch('emaki_boot_defaults.queue_presets') as queue:
                    migration.repair_console_font(self.root)
                queue.assert_not_called()
                self.assertEqual(self.console.read_bytes(), original)

    def test_font_changes_queue_both_images_before_changing_the_input(self):
        from emaki_boot_defaults import KERNELS, queue_presets
        pending = self.root / 'var/lib/emaki/migrations/initramfs-pending'
        for source, selected in (('FONT=ter-124b\n', 'ter-v24b'), ('KEYMAP=us\n', 'ter-v32b')):
            with self.subTest(font=selected):
                self.console.write_text(source)
                def queue(root, kernels):
                    self.assertEqual(self.console.read_text(), source)
                    queue_presets(root, kernels)
                with mock.patch('emaki_boot_defaults.queue_presets', side_effect=queue) as request:
                    migration.repair_console_font(self.root)
                request.assert_called_once_with(self.root, KERNELS)
                self.assertEqual(pending.read_text(), 'linux\nlinux-lts\n')
                self.assertIn('FONT=' + selected + '\n', self.console.read_text())
                with mock.patch('emaki_boot_defaults.queue_presets') as request:
                    migration.repair_console_font(self.root)
                request.assert_not_called()

    def test_queue_failure_preserves_console_and_write_failure_retains_queue(self):
        source = 'FONT=ter-124b\n'
        self.console.write_text(source)
        with mock.patch('emaki_boot_defaults.queue_presets', side_effect=OSError('queue failed')):
            with self.assertRaises(OSError):
                migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), source)
        with mock.patch.object(migration, 'write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), source)
        pending = self.root / 'var/lib/emaki/migrations/initramfs-pending'
        self.assertEqual(pending.read_text(), 'linux\nlinux-lts\n')

    def test_missing_linked_and_non_russian_unset_configs_are_preserved(self):
        migration.repair_console_font(self.root)
        self.assertFalse(self.console.exists())
        self.console.symlink_to(self.locale)
        migration.repair_console_font(self.root)
        self.assertEqual(self.locale.read_text(), 'LANG=ru_RU.UTF-8\n')
        self.console.unlink()
        self.console.write_text('KEYMAP=us\n')
        self.locale.write_text('LANG=en_US.UTF-8\n')
        with mock.patch('emaki_boot_defaults.queue_presets') as queue:
            migration.repair_console_font(self.root)
        queue.assert_not_called()
        self.assertEqual(self.console.read_text(), 'KEYMAP=us\n')
        self.locale.unlink()
        migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), 'KEYMAP=us\n')

    def test_ambiguous_locale_and_linked_locale_leave_unset_font_alone(self):
        self.console.write_text('KEYMAP=us\n')
        for value in ('LANG=ru_RU.UTF-8\nLANG=en_US.UTF-8\n', 'LANG="ru_RU.UTF-8\n'):
            with self.subTest(value=value):
                self.locale.write_text(value)
                migration.repair_console_font(self.root)
                self.assertEqual(self.console.read_text(), 'KEYMAP=us\n')
        target = self.root / 'locale-copy'
        target.write_text('LANG=ru_RU.UTF-8\n')
        self.locale.unlink()
        self.locale.symlink_to(target)
        migration.repair_console_font(self.root)
        self.assertEqual(self.console.read_text(), 'KEYMAP=us\n')

    def test_migration_is_once_even_for_an_already_current_language_version(self):
        state = self.root / 'var/lib/emaki/migrations/state.json'
        state.parent.mkdir(parents=True)
        state.write_text('{"version": 7}\n')
        self.console.write_text('FONT=ter-124b\n')
        with mock.patch.object(migration, 'reclaim_defaults'), \
                mock.patch.object(migration, 'services'), mock.patch('emaki_boot_defaults.apply'):
            migration.apply(self.root)
            self.assertEqual(self.console.read_text(), 'FONT=ter-v24b\n')
            self.assertTrue(json.loads(state.read_text())['unicode_console_font'])
            self.console.write_text('FONT=ter-124b\n')
            migration.apply(self.root)
            self.assertEqual(self.console.read_text(), 'FONT=ter-124b\n')

    def test_failed_font_write_is_retried(self):
        state = self.root / 'var/lib/emaki/migrations/state.json'
        state.parent.mkdir(parents=True)
        state.write_text('{"version": 7}\n')
        self.console.write_text('FONT=ter-124b\n')
        with mock.patch.object(migration, 'reclaim_defaults'), \
                mock.patch.object(migration, 'services'), mock.patch('emaki_boot_defaults.apply'):
            with mock.patch.object(migration, 'write', side_effect=OSError('write failed')):
                with self.assertRaises(OSError):
                    migration.apply(self.root)
            self.assertNotIn('unicode_console_font', json.loads(state.read_text()))
            migration.apply(self.root)
        self.assertEqual(self.console.read_text(), 'FONT=ter-v24b\n')



if __name__ == '__main__':
    unittest.main()
