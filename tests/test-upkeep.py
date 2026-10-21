#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the packaged migration on scratch roots; never clean host snapshots."""
import configparser
import contextlib
import io
import importlib.machinery
import importlib.util
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('snapshot_policy', str(ROOT / 'upkeep/emaki-snapshot-policy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
policy = importlib.util.module_from_spec(spec)
loader.exec_module(policy)
LEGACY = ('SUBVOLUME="/"\nFSTYPE="btrfs"\nFREE_LIMIT="0.2"\n'
          'TIMELINE_CREATE="yes"\nNUMBER_CLEANUP="yes"\nTIMELINE_CLEANUP="yes"\n'
          + ''.join(f'{key}="{old}"\n' for key, (old, _) in policy.LIMITS.items()))


class UpkeepTests(unittest.TestCase):
    def test_transaction_space_hook_delivery_and_order(self):
        hook_name = '00-emaki-update-space.hook'
        hook = (ROOT / 'upkeep' / hook_name).read_text()
        self.assertLess(hook_name, '05-snap-pac-pre.hook')
        for setting in ('When = PreTransaction', 'AbortOnFail', 'NeedsTargets',
                        'Operation = Install', 'Operation = Upgrade'):
            self.assertIn(setting, hook.splitlines())
        self.assertNotIn('Operation = Remove', hook)
        with tempfile.TemporaryDirectory() as directory:
            prefix = '/opt/fixture'
            subprocess.run(['make', '-s', 'install-upkeep', f'DESTDIR={directory}',
                            f'PREFIX={prefix}'], cwd=ROOT, check=True, capture_output=True)
            staged = Path(directory)
            installed = staged / prefix.lstrip('/') / 'share/libalpm/hooks' / hook_name
            action = next(line[7:] for line in installed.read_text().splitlines() if line.startswith('Exec = '))
            self.assertEqual(action, prefix + '/share/libalpm/scripts/emaki-update-space')
            helper = staged / action.lstrip('/')
            self.assertTrue(helper.stat().st_mode & 0o111)
            self.assertIn(prefix + '/bin/python3 -IB ' + prefix + '/libexec/emaki/emaki_update_space.py', helper.read_text())
        inventory = (ROOT / 'packaging/emaki-config/expected-files.list').read_text()
        self.assertIn('/usr/share/libalpm/hooks/' + hook_name + '\n', inventory)

    def test_space_launcher_only_aborts_a_measured_shortage(self):
        template = (ROOT / 'upkeep/emaki-update-space').read_text()
        warning = 'Warning: the update space check is unavailable; the package transaction will continue.\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / 'launcher'
            launcher.write_text(template.replace('@PYTHON@', shlex.quote(sys.executable)).replace('@LIBEXECDIR@', directory))
            probe = root / 'emaki_update_space.py'
            # Status 3 is reserved for measured shortages, independent of translated text.
            for code, output, status in ((0, '', 0), (1, 'traceback', 0), (2, 'odd output', 0),
                                         (3, 'Translated shortage: free some space.', 1),
                                         (3, 'Not enough free space for this update: it needs about 2 GB, 1 GB is free. Clean up.', 1)):
                with self.subTest(code=code, output=output):
                    probe.write_text(f'import sys\nsys.stdout.write({output!r})\nraise SystemExit({code})\n')
                    result = subprocess.run(['/bin/sh', str(launcher)], input='example\n', text=True, capture_output=True)
                    self.assertEqual(result.returncode, status)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(result.stderr, output + '\n' if status else (warning if output else ''))
            # The estimate must also fail open before Python can handle an error.
            probe.unlink()
            result = subprocess.run(['/bin/sh', str(launcher)], text=True, capture_output=True)
            self.assertEqual((result.returncode, result.stderr), (0, warning))
            launcher.write_text(template.replace('@PYTHON@', str(root / 'missing-python')).replace('@LIBEXECDIR@', directory))
            result = subprocess.run(['/bin/sh', str(launcher)], text=True, capture_output=True)
            self.assertEqual((result.returncode, result.stderr), (0, warning))

    def test_legacy_ranges_and_idempotence(self):
        actual = policy.migrate(LEGACY)
        for key, (_, new) in policy.LIMITS.items():
            self.assertIn(f'{key}="{new}"', actual)
        self.assertIn('FREE_LIMIT="0.2"', actual)
        self.assertEqual(policy.migrate(actual), actual)
        # A later administrator change back to an old value stays theirs.
        changed = actual.replace('NUMBER_LIMIT="6-10"', 'NUMBER_LIMIT="20"')
        self.assertEqual(policy.migrate(changed), changed)

    def test_custom_values_and_other_configs_survive(self):
        for old, new in [('NUMBER_LIMIT="20"', 'NUMBER_LIMIT="40"'),
                         ('SUBVOLUME="/"', 'SUBVOLUME="/home"'),
                         ('FSTYPE="btrfs"', 'FSTYPE="ext4"')]:
            custom = LEGACY.replace(old, new)
            self.assertEqual(policy.migrate(custom), custom)
        custom = LEGACY.replace('TIMELINE_LIMIT_MONTHLY="10"', 'TIMELINE_LIMIT_MONTHLY="4"')
        self.assertIn('TIMELINE_LIMIT_MONTHLY="4"', policy.migrate(custom))
        duplicate = LEGACY + 'NUMBER_LIMIT="80"\n'
        self.assertEqual(policy.migrate(duplicate), duplicate)

    def test_real_file_mode_symlinks_absent_ext4(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy.apply(root)
            self.assertEqual(list(root.iterdir()), [])
            config = root / 'etc/snapper/configs/root'
            config.parent.mkdir(parents=True)
            config.write_text(LEGACY)
            config.chmod(0o640)
            result = subprocess.run(['python3', str(ROOT / 'upkeep/emaki-snapshot-policy'), tmp],
                                    check=True, text=True, capture_output=True)
            self.assertEqual(result.stdout,
                             'Emaki snapshot cleanup now keeps 6 to 10 numbered snapshots instead of 20.\n')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                policy.apply(root)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(config.read_text(), policy.migrate(LEGACY))
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)
            target = root / 'local-config'
            config.rename(target)
            config.symlink_to(target)
            target.write_text(LEGACY)
            policy.apply(root)
            self.assertEqual(target.read_text(), LEGACY)

    def test_installer_and_package_limits_match(self):
        import sys
        sys.path.insert(0, str(ROOT / 'installer'))
        from emaki_installer.render import snapper_config
        fresh = snapper_config(LEGACY)
        for key, (_, new) in policy.LIMITS.items():
            self.assertIn(f'{key}="{new}"', fresh)
        edited = fresh.replace('NUMBER_LIMIT="6-10"', 'NUMBER_LIMIT="20"')
        self.assertEqual(policy.migrate(edited), edited)

    def test_delivery_and_native_cache_retention(self):
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        self.assertIn("'pacman-contrib'", recipe)
        inventory = (ROOT / 'packaging/emaki-config/expected-files.list').read_text()
        self.assertNotIn('/usr/lib/systemd/system/timers.target.wants/paccache.timer\n', inventory)
        self.assertIn('/usr/share/libalpm/scripts/emaki-system-migrate', inventory)
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['make', 'install-upkeep', f'DESTDIR={tmp}'], cwd=ROOT,
                           check=True, capture_output=True)
            staged = Path(tmp)
            timer = staged / 'usr/lib/systemd/system/timers.target.wants/paccache.timer'
            self.assertFalse(timer.is_symlink())
            self.assertTrue((staged / 'usr/share/libalpm/scripts/emaki-system-migrate').is_file())
            self.assertFalse((staged / 'usr/lib/systemd/system/snapper-cleanup.service.d').exists())
        self.assertNotIn('emaki-snapshot-policy', ''.join(
            path.read_text() for path in (ROOT / 'systemd').rglob('*') if path.is_file()))

    def test_staged_sudo_policy_matches_native_directory_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            directory = staged / 'etc/sudoers.d'
            for existing in (False, True):
                with self.subTest(existing=existing):
                    if existing:
                        directory.chmod(0o755)
                    subprocess.run(['make', '-s', 'install-upkeep', f'DESTDIR={tmp}'],
                                   cwd=ROOT, check=True, capture_output=True)
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o750)
                    gateway = directory / '10-emaki-wheel'
                    self.assertEqual(gateway.stat().st_mode & 0o777, 0o440)
                    self.assertEqual(gateway.read_bytes(),
                                     (ROOT / 'upkeep/defaults/10-emaki-wheel').read_bytes())


    @unittest.skipUnless(shutil.which('paccache') and shutil.which('pacsort'),
                         'pacman-contrib is required for native cache-retention proof')
    def test_native_timer_ownership_and_three_version_retention(self):
        unit_dir = Path('/usr/lib/systemd/system')
        timer = unit_dir / 'paccache.timer'
        service = unit_dir / 'paccache.service'
        for path in (timer, service, Path('/usr/bin/paccache')):
            owner = subprocess.run(['pacman', '-Qoq', str(path)], check=True,
                                   capture_output=True, text=True)
            self.assertEqual(owner.stdout.strip(), 'pacman-contrib')
        timer_config = configparser.ConfigParser(interpolation=None)
        timer_config.read(timer)
        self.assertEqual(timer_config['Timer'].get('Unit', 'paccache.service'), 'paccache.service')
        self.assertEqual(timer_config['Timer']['OnCalendar'], 'weekly')
        service_config = configparser.ConfigParser(interpolation=None)
        service_config.read(service)
        command = shlex.split(service_config['Service']['ExecStart'])
        self.assertEqual(command[0], '/usr/bin/paccache')
        # Exercise the vendor service's default arguments, without local overrides.
        command = [arg for arg in command if arg != '$PACCACHE_ARGS']
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'
            cache.mkdir()
            for version in range(1, 7):
                (cache / f'upkeep-retention-{version}-1-any.pkg.tar.zst').touch()
            subprocess.run([*command, '--cachedir', str(cache)], check=True, capture_output=True)
            self.assertEqual(sorted(path.name for path in cache.iterdir()),
                             [f'upkeep-retention-{version}-1-any.pkg.tar.zst' for version in (4, 5, 6)])


if __name__ == '__main__':
    unittest.main()
