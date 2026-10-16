#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Space estimates, private probes and refusals without a real package manager."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import tarfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'update-manager'))
import emaki_update_space as space

SUMMARY = ''':: Starting full system upgrade...
Packages (2) old-package-1 [removal] new-package-2

Total Download Size:    12.50 MiB
Total Installed Size:  90.00 MiB
Net Upgrade Size:      -3.00 MiB

:: Proceed with installation? [Y/n]
'''


def filesystem(identity, free):
    return SimpleNamespace(f_fsid=identity, f_bavail=free, f_frsize=1)


class Space(unittest.TestCase):
    def test_native_summary_replacements_and_rounding(self):
        download, net, installed = space.transaction_sizes(SUMMARY)
        self.assertEqual(download, 13112443)
        self.assertEqual(net, -3140485)
        self.assertEqual(space.transaction_sizes(' there is nothing to do\n'), (0, 0, 0))
        cached = SUMMARY.replace('Total Download Size:    12.50 MiB\n', '')
        self.assertEqual(space.transaction_sizes(cached)[0], 0)
        fresh = SUMMARY.replace('Net Upgrade Size:      -3.00 MiB\n', '')
        self.assertEqual(space.transaction_sizes(fresh)[1], 94377083)

    def test_bad_or_incomplete_summary_fails_closed(self):
        for text in ('', 'error: failed', SUMMARY.replace('MiB', 'MB'),
                     SUMMARY.replace(space.FINAL_PROMPT, ''),
                     SUMMARY + 'Total Installed Size: 1.00 MiB\n'):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, 'could not finish'):
                space.transaction_sizes(text)

    def test_shared_allocation_pool_adds_cache_install_and_snapshot_margin(self):
        # Distinct btrfs subvolumes have distinct f_fsid values but share a source.
        self.assertEqual(space.requirements(100, 200, filesystem(7, 3000), filesystem(8, 2000), pools=('/dev/test', '/dev/test')),
                         [(space.MARGIN + 300, 2000)])

    def test_separate_cache_pool_does_not_charge_root_for_download(self):
        self.assertEqual(space.requirements(100, 200, filesystem(7, 3000), filesystem(8, 2000), pools=('/dev/root', '/dev/cache')),
                         [(space.MARGIN + 200, 3000), (100, 2000)])
        self.assertEqual(space.requirements(100, -200, filesystem(7, 3000), filesystem(8, 2000), pools=('/dev/root', '/dev/cache'))[0][0], space.MARGIN)

    def test_refusal_numbers_and_boundary(self):
        with self.assertRaisesRegex(RuntimeError, '^Not enough free space for this update: it needs about 1.1 GB, 500.0 MB is free. Delete unneeded files or clear the package cache in Settings > Storage, then try again.$'):
            space.check_space(10 ** 6, 10 ** 6, filesystem(7, 500 * 10 ** 6), filesystem(7, 500 * 10 ** 6))
        space.check_space(100, 200, filesystem(7, space.MARGIN + 300), filesystem(7, space.MARGIN + 300))
        with self.assertRaisesRegex(RuntimeError, '10.0 MB, 9.0 MB'):
            space.check_space(10 ** 7, 200, filesystem(7, 10 ** 10), filesystem(8, 9 * 10 ** 6), pools=('/dev/root', '/dev/cache'))

    def test_btrfs_subvolumes_share_backing_pool_and_unknown_combines(self):
        mounts = ('10 1 0:30 /@ / rw - btrfs /dev/test rw\n'
                  '11 10 0:31 /@pkg /var/cache/pacman/pkg rw - btrfs /dev/test rw\n')
        root = space.mount_details('/', mounts)
        cache = space.mount_details('/var/cache/pacman/pkg', mounts)
        self.assertEqual(root, ('/dev/test', 'btrfs'))
        self.assertEqual(cache, root)
        with self.assertRaisesRegex(RuntimeError, 'Not enough free space'):
            space.check_space(1500 * 10 ** 6, 800 * 10 ** 6,
                              filesystem(30, 2500 * 10 ** 6), filesystem(31, 2500 * 10 ** 6),
                              pools=(root[0], cache[0]))
        self.assertEqual(space.mount_details('/', 'malformed'), (None, None))
        self.assertEqual(len(space.requirements(1, 1, filesystem(1, 10), filesystem(2, 10))), 1)
        self.assertEqual(len(space.requirements(1, 1, filesystem(1, 10), filesystem(2, 10),
                                              pools=('/dev/root', None))), 1)

    STACKED = ('10 1 0:30 / / rw - btrfs /dev/root rw\n'
               '11 10 0:31 / /var/cache/pacman/pkg rw - btrfs /dev/hidden rw\n'
               '12 11 0:32 / /var/cache/pacman/pkg rw - ext4 /dev/visible rw\n')
    PACKAGE_PATHS = ('/var/cache/pacman/pkg', '/var/cache/pacman/pkg/package.pkg.tar.zst')

    def assert_visible_mounts(self, details):
        # Stacked at one path: the top of the stack is visible.
        for path in self.PACKAGE_PATHS:
            self.assertEqual(details(path, self.STACKED), ('/dev/visible', 'ext4'))
        # A later, shallower mount covers both: the package cache is on it now.
        covered = self.STACKED + '13 10 0:33 / /var/cache rw - ext4 /dev/shallower rw\n'
        for path in (*self.PACKAGE_PATHS, '/var/cache'):
            self.assertEqual(details(path, covered), ('/dev/shallower', 'ext4'))
        # A propagated mount on the covered stack stays hidden, though it is listed last.
        hidden = covered + '14 12 0:34 / /var/cache/pacman/pkg/sub rw - xfs /dev/propagated rw\n'
        self.assertEqual(details('/var/cache/pacman/pkg/sub', hidden), ('/dev/shallower', 'ext4'))
        # A mount made inside the covering one is visible again.
        inner = covered + '15 13 0:35 / /var/cache/pacman/pkg rw - xfs /dev/inner rw\n'
        for path in self.PACKAGE_PATHS:
            self.assertEqual(details(path, inner), ('/dev/inner', 'xfs'))
        self.assertEqual(details('/var/cache', inner), ('/dev/shallower', 'ext4'))
        self.assertEqual(details('/', inner), ('/dev/root', 'btrfs'))
        # A table that cannot be walked from its root proves nothing.
        self.assertEqual(details('/var', '10 10 0:30 / / rw - btrfs /dev/root rw\n'), (None, None))

    def test_visible_mount_follows_the_mount_tree(self):
        self.assert_visible_mounts(space.mount_details)

    def test_mutant_without_parent_check_is_rejected(self):
        path = ROOT / 'update-manager/emaki_update_space.py'
        source = path.read_text()
        old = 'mount[2] == prefix and mount[1] == visible[0]'
        self.assertEqual(source.count(old), 1)
        namespace = {'__file__': str(path), '__name__': 'space_mutant'}
        exec(compile(source.replace(old, 'mount[2] == prefix and mount[0] != visible[0]'), str(path), 'exec'), namespace)
        with self.assertRaises(AssertionError):
            self.assert_visible_mounts(namespace['mount_details'])

    def findmnt_details(self, path, table=None):
        findmnt = Path('/usr/bin/findmnt')
        if not findmnt.exists():
            self.skipTest('findmnt is not installed')
        # Search from the visible end of a stack, returning exactly that mount.
        command = [str(findmnt), '-n', '-o', 'SOURCE,FSTYPE',
                   '--first-only', '--direction', 'backward', '--target', str(path)]
        if table is not None:
            command += ['--tab-file', str(table)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        source, kind = result.stdout.split()
        source = source.split('[', 1)[0]
        expected = os.path.realpath(source) if source.startswith('/dev/') else None
        return expected, kind

    def test_findmnt_selects_visible_stacked_mount(self):
        with tempfile.TemporaryDirectory() as work:
            cache = Path(work) / 'cache'
            cache.mkdir()
            package = cache / 'package.pkg.tar.zst'
            package.touch()
            mounts = self.STACKED.replace('/var/cache/pacman/pkg', str(cache))
            table = Path(work) / 'mountinfo'
            table.write_text(mounts)
            for path in (cache, package):
                with self.subTest(path=path):
                    self.assertEqual(self.findmnt_details(path, table), ('/dev/visible', 'ext4'))
                    self.assertEqual(space.mount_details(path, mounts), self.findmnt_details(path, table))

    def test_real_mount_table_agrees_with_findmnt(self):
        for path in ('/', '/var/cache/pacman/pkg', tempfile.gettempdir()):
            if not Path(path).exists():
                continue
            with self.subTest(path=path):
                self.assertEqual(space.mount_details(path), self.findmnt_details(path))

    def test_snapshot_root_charges_installed_not_only_net(self):
        with patch.object(Path, 'is_dir', return_value=True):
            self.assertTrue(space.root_snapshots('btrfs'))
            self.assertFalse(space.root_snapshots('ext4'))
        with patch.object(Path, 'is_dir', return_value=False), \
                patch.object(Path, 'glob', return_value=[Path('/etc/snapper/configs/root')]), \
                patch.object(Path, 'read_text', return_value='SUBVOLUME="/"\n'):
            self.assertTrue(space.root_snapshots('btrfs'))
        with self.assertRaisesRegex(RuntimeError, 'Not enough free space'):
            space.check_space(100, 1, filesystem(1, space.MARGIN + 500),
                              filesystem(2, space.MARGIN + 500), installed=1000)
        self.assertEqual(space.requirements(100, 2000, filesystem(1, 10000),
                                           filesystem(2, 10000), installed=1000)[0][0],
                         space.MARGIN + 2100)

    def test_refresh_accepts_recovered_mirror_errors_but_not_failure_status(self):
        source = 'print("error: failed retrieving file core.db from mirror")'
        self.assertIn('error:', space.command([sys.executable, '-c', source]))
        with self.assertRaisesRegex(RuntimeError, 'could not finish'):
            space.command([sys.executable, '-c', source + '; raise SystemExit(1)'])

    def test_probe_refusals_preserve_first_error(self):
        diagnostic = 'error: failed to prepare transaction (could not satisfy dependencies)'
        output = diagnostic + '\nerror: a later failure\n'
        for ending in ('raise SystemExit(1)', 'raise SystemExit(0)',
                       f'print({SUMMARY!r}, flush=True); assert input() == "n"; raise SystemExit(1)',
                       f'print({SUMMARY!r}, flush=True); assert input() == "n"; raise SystemExit(2)',
                       'print(":: Unexpected operation? [Y/n]", flush=True); input()'):
            source = f'print({output!r}, flush=True)\n' + ending
            with self.subTest(ending=ending):
                with self.assertRaises(RuntimeError) as caught:
                    space.command([sys.executable, '-c', source], probe=True)
                self.assertEqual(str(caught.exception), space.FAILURE + '\n' + diagnostic)

    def test_refresh_failure_preserves_first_error(self):
        diagnostic = 'error: failed retrieving file core.db from mirror'
        source = f'print({diagnostic!r}); raise SystemExit(1)'
        with self.assertRaises(RuntimeError) as caught:
            space.command([sys.executable, '-c', source])
        self.assertEqual(str(caught.exception), space.FAILURE + '\n' + diagnostic)

    def test_unknown_estimate_failure_does_not_advise_deleting_files(self):
        with self.assertRaises(RuntimeError) as caught:
            space.transaction_sizes('incomplete summary')
        self.assertEqual(str(caught.exception),
                         'The free-space check could not finish. The update was not started.')
        self.assertNotIn('Settings > Storage', str(caught.exception))

    def test_conflict_and_skip_prompts_are_accepted_only_in_probe(self):
        for prompt in (':: old and new are in conflict. Remove old? [y/N]',
                       ':: Do you want to skip the above package for this upgrade? [y/N]',
                       ':: Do you want to skip the above packages for this upgrade? [y/N]'):
            source = (f'print({prompt!r}, end="", flush=True)\n'
                      'assert input() == "y"\n'
                      f'print({SUMMARY!r}, end="", flush=True)\n'
                      'assert input() == "n"\nraise SystemExit(1)\n')
            with self.subTest(prompt=prompt):
                self.assertEqual(space.transaction_sizes(space.command([sys.executable, '-c', source], probe=True)),
                                 space.transaction_sizes(SUMMARY))

    def test_probe_accepts_replacement_default_but_never_installation(self):
        source = ('import sys\n'
                  'print(":: Replace old with core/new? [Y/n] ", end="", flush=True)\n'
                  'assert input() == ""\n'
                  f'print({SUMMARY!r}, end="", flush=True)\n'
                  'assert input() == "n"\n'
                  'raise SystemExit(1)\n')
        self.assertEqual(space.transaction_sizes(space.command([sys.executable, '-c', source], probe=True)),
                         space.transaction_sizes(SUMMARY))

    def test_unknown_confirmation_is_never_accepted(self):
        source = ('print(":: Start an unexpected operation? [Y/n] ", end="", flush=True); '
                  'input(); raise SystemExit(73)')
        with self.assertRaisesRegex(RuntimeError, 'could not finish'):
            space.command([sys.executable, '-c', source], probe=True)

    def test_termination_stops_detached_probe_and_restores_handlers(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGHUP, signal.SIGTERM)}
        with patch.object(space, '_preflight'):
            space.preflight()
        self.assertEqual(previous, {sig: signal.getsignal(sig) for sig in previous})
        for sig in previous:
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / 'pid'
                child = (f'import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); '
                         'time.sleep(30)')
                source = (f'import sys; sys.path.insert(0, {str(ROOT / "update-manager")!r}); '
                          'import emaki_update_space as s; '
                          f's._preflight=lambda: s.command([sys.executable,"-c",{child!r}]); s.preflight()')
                with subprocess.Popen([sys.executable, '-c', source], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                    try:
                        deadline = time.monotonic() + 5
                        while not marker.exists() and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(marker.exists())
                        pid = int(marker.read_text())
                        process.send_signal(sig)
                        output, errors = process.communicate(timeout=5)
                        self.assertEqual(process.returncode, 128 + sig, errors)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()

    def test_wrapper_keeps_preflight_before_package_changes(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        for arguments in ([], ['--noninteractive'], ['--install', 'extra/example'],
                          ['--install-only', 'extra/example']):
            with self.subTest(arguments=arguments):
                check = Mock(side_effect=space.InsufficientSpace('measured shortage'))
                run = Mock(return_value=0)
                with patch.dict(module['main'].__globals__, preflight=check, run=run,
                                upgrades=Mock(return_value={})), \
                        patch.object(os, 'geteuid', return_value=0), \
                        patch.object(sys, 'argv', ['emaki-update', *arguments]), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module['main'](), 1)
                check.assert_called_once_with(**({'targets': ['extra/example']}
                                                if 'extra/example' in arguments else {}))
                run.assert_not_called()

    def test_wrapper_successful_preflight_precedes_transaction(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        calls = []
        with patch.dict(module['main'].__globals__,
                        preflight=lambda: calls.append('preflight'),
                        update=lambda *a, **kw: calls.append('transaction') or 0), \
                patch.object(os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', ['emaki-update']):
            self.assertEqual(module['main'](), 0)
        self.assertEqual(calls, ['preflight', 'transaction'])

    def test_probe_answers_group_selection_before_declining_installation(self):
        source = ('print("Enter a selection (default=all): ", end="", flush=True)\n'
                  'assert input() == ""\n'
                  f'print({SUMMARY!r}, end="", flush=True)\n'
                  'assert input() == "n"\nraise SystemExit(1)\n')
        # Bound the regression run instead of waiting for the production deadline.
        monotonic = time.monotonic
        with patch.object(space.time, 'monotonic', side_effect=lambda: monotonic() * 100):
            self.assertEqual(space.transaction_sizes(space.command([sys.executable, '-c', source], probe=True)),
                             space.transaction_sizes(SUMMARY))

    def test_parent_identity_rejects_unrelated_launcher_and_non_pacman(self):
        for supplied in ('', '-1', '1/../2', 'unrecognized'):
            with self.subTest(pid=supplied), self.assertRaises(ValueError):
                space.parent_selection(supplied)
        with patch.object(space.os, 'getppid', return_value=100), \
                patch.object(space, 'process_parent', return_value=200), \
                self.assertRaisesRegex(ValueError, 'unrelated launcher'):
            space.parent_selection('300')
        with patch.object(space.os, 'getppid', return_value=100), \
                patch.object(space, 'process_parent', return_value=os.getpid()), \
                self.assertRaisesRegex(ValueError, 'unknown package manager'):
            space.parent_selection('100')

    def test_private_database_no_download_or_system_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, cache = root / 'db', root / 'cache'
            (database / 'local').mkdir(parents=True)
            (database / 'local/fixture').write_text('installed')
            (database / 'sync').mkdir()
            (database / 'sync/core.db').write_bytes(b'unchanged database')
            cache.mkdir()
            calls = []
            def command(arguments, probe=False):
                calls.append(arguments)
                if arguments == ['pacman-conf', 'DBPath']:
                    return str(database)
                if arguments == ['pacman-conf', 'CacheDir']:
                    return str(cache)
                private = Path(arguments[arguments.index('--dbpath') + 1])
                self.assertNotEqual(private, database)
                self.assertEqual((private / 'local/fixture').read_text(), 'installed')
                self.assertFalse((private / 'local').is_symlink())
                if arguments[1] == '-Sy':
                    self.assertFalse(probe)
                    copied = private / 'sync/core.db'
                    self.assertEqual(copied.read_bytes(), b'unchanged database')
                    self.assertNotEqual(copied.stat().st_ino, (database / 'sync/core.db').stat().st_ino)
                    copied.write_bytes(b'refreshed privately')
                    return ''
                self.assertEqual(arguments[1:3], ['-Su', '--confirm'])
                self.assertTrue(probe)
                return SUMMARY
            with patch.object(space, 'command', side_effect=command), \
                    patch.object(space, 'mount_details', return_value=('/dev/test', 'btrfs')), \
                    patch.object(space, 'root_snapshots', return_value=True), \
                    patch.object(space, 'check_space') as check:
                space.preflight()
                check.assert_called_once()
                self.assertEqual(check.call_args.args[-2:], (('/dev/test', '/dev/test'), 94377083))
            private = Path(calls[-1][calls[-1].index('--dbpath') + 1])
            self.assertFalse(private.exists())
            self.assertEqual(set(database.iterdir()), {database / 'local', database / 'sync'})
            self.assertEqual((database / 'sync/core.db').read_bytes(), b'unchanged database')
            (database / 'db.lck').touch()
            with patch.object(space, 'command', side_effect=command), \
                    patch.object(space, 'lock_message', return_value='The package database lock appears abandoned.') as diagnose, \
                    self.assertRaisesRegex(RuntimeError, 'lock appears abandoned'):
                space.preflight()
            diagnose.assert_called_once_with(database / 'db.lck')
            self.assertEqual(calls[-1], ['pacman-conf', 'CacheDir'])


class TransactionSpace(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pacman'), 'pacman is required for native selection output')
    def test_native_print_selection_uses_only_private_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / 'local/example-1-1'
            local.mkdir(parents=True)
            (root / 'local/ALPM_DB_VERSION').write_text('9\n')
            (local / 'files').write_text('%FILES%\n')
            (local / 'desc').write_text('%NAME%\nexample\n\n%VERSION%\n1-1\n\n%ARCH%\nany\n\n%SIZE%\n100\n')
            (root / 'sync').mkdir()
            record = ('%NAME%\nexample\n\n%VERSION%\n2-1\n\n%ARCH%\nany\n\n'
                      '%FILENAME%\nexample-2-1-any.pkg.tar.zst\n\n%CSIZE%\n100\n\n%ISIZE%\n200\n')
            with tarfile.open(root / 'sync/fixture.db', 'w:gz') as archive:
                entry = tarfile.TarInfo('example-2-1/desc')
                entry.size = len(record)
                archive.addfile(entry, io.BytesIO(record.encode()))
            config = root / 'pacman.conf'
            config.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n'
                              '[fixture]\nServer = file:///missing\n')
            selection = space.sync_selection(['-Syu'])
            result = space.command(['pacman', *selection, '--config', str(config),
                                    '--dbpath', str(root), '--logfile', '/dev/null',
                                    '--print', '--print-format', '%n', '--noconfirm'])
            self.assertEqual(result.splitlines(), ['example'])
            self.assertFalse((root / 'db.lck').exists())
            self.assertIn('1-1', (local / 'desc').read_text())

    def test_sync_selection_preserves_upgrade_and_repository_targets(self):
        for arguments, expected in (
                (['-Syu'], ['-Su']),
                (['--sync', '--refresh', '--sysupgrade'], ['-Su']),
                (['-S', '--noconfirm', '--needed', 'extra/example'], ['-S', '--needed', 'extra/example']),
                (['-Syyuu', 'core/example'], ['-Suu', 'core/example'])):
            self.assertEqual(space.sync_selection(arguments), expected)
        for arguments in (['-U', 'example.pkg.tar.zst'], ['-R', 'example'],
                          ['-Syu', '--root', '/target'], ['-Syu', '--dbpath=/target'],
                          ['-Syu', '--config', '/custom'], ['-S', '-'], ['-Syu', '--ignore=example']):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                space.sync_selection(arguments)

    def test_fits_shortage_and_unknown_with_parent_database_lock(self):
        for summary, free, status, warning in (
                (SUMMARY, space.MARGIN + 110 * 1024 ** 2, 0, False),
                (SUMMARY, space.MARGIN + 50 * 1024 ** 2, 1, False),
                ('odd output', 0, 0, True),
                (OSError('tool missing'), 0, 0, True),
                (RuntimeError('probe failed'), 0, 0, True)):
            with self.subTest(summary=summary, free=free), tempfile.TemporaryDirectory() as directory:
                database = Path(directory)
                (database / 'local').mkdir()
                lock = database / 'db.lck'
                lock.write_text('parent transaction')
                replies = [str(database), str(database), 'new-package\n', summary]
                with patch.object(space, 'command', side_effect=replies) as probe, \
                        patch.object(space, 'parent_selection', return_value=['-Su']), \
                        patch.object(space, 'mount_details', return_value=('/dev/test', 'btrfs')), \
                        patch.object(space, 'root_snapshots', return_value=True), \
                        patch.object(space.os, 'statvfs', return_value=filesystem(1, free)), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(space.transaction_preflight(['new-package']), status)
                self.assertEqual(lock.read_text(), 'parent transaction')
                self.assertEqual(probe.call_count, 4)
                self.assertNotIn('-Sy', [arg for call in probe.call_args_list for arg in call.args[0]])
                private = Path(probe.call_args.args[0][probe.call_args.args[0].index('--dbpath') + 1])
                self.assertFalse(private.exists())
                if warning:
                    self.assertEqual(errors.getvalue(), space.WARNING + '\n')
                elif status:
                    self.assertEqual(len(errors.getvalue().splitlines()), 1)
                    self.assertIn('it needs about ', errors.getvalue())
                    self.assertIn(' is free. ' + space.SPACE_ADVICE, errors.getvalue())
                else:
                    self.assertEqual(errors.getvalue(), '')

    def test_non_btrfs_and_missing_mount_data_warn_without_probing(self):
        for kind in ('ext4', None):
            with patch.object(space, 'mount_details', return_value=(None, kind)), \
                    patch.object(space, 'preflight') as check, \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(space.transaction_preflight(['example']), 0)
            check.assert_not_called()
            self.assertEqual(errors.getvalue(), space.WARNING + '\n')

    def test_unrecognized_parent_and_changed_target_selection_warn(self):
        with patch.object(space, 'mount_details', return_value=('/dev/test', 'btrfs')), \
                patch.object(space, 'parent_selection', side_effect=ValueError('local archive')), \
                patch.object(space, 'preflight') as check, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(space.transaction_preflight(['example']), 0)
        check.assert_not_called()
        self.assertEqual(errors.getvalue(), space.WARNING + '\n')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'local').mkdir()
            with patch.object(space, 'command', side_effect=[directory, directory, 'other\n']), \
                    patch.object(space, 'parent_selection', return_value=['-Su']), \
                    patch.object(space, 'mount_details', return_value=('/dev/test', 'btrfs')), \
                    patch.object(space, 'check_space') as check, \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(space.transaction_preflight(['example']), 0)
            check.assert_not_called()
            self.assertEqual(errors.getvalue(), space.WARNING + '\n')


if __name__ == '__main__':
    unittest.main()
