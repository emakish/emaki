#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Space estimates, private probes and refusals without a real package manager."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
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
        with self.assertRaisesRegex(RuntimeError, '^Not enough free space for this update: it needs about 1.1 GB, 500.0 MB is free. Free some space, then try again.$'):
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

    def test_real_mount_table_agrees_with_findmnt(self):
        findmnt = Path('/usr/bin/findmnt')
        if not findmnt.exists():
            self.skipTest('findmnt is not installed')
        for path in ('/', '/var/cache/pacman/pkg', tempfile.gettempdir()):
            if not Path(path).exists():
                continue
            result = subprocess.run([str(findmnt), '-n', '-o', 'SOURCE,FSTYPE', '--target', path],
                                    capture_output=True, text=True, check=True)
            source, kind = result.stdout.split()
            source = source.split('[', 1)[0]
            expected = os.path.realpath(source) if source.startswith('/dev/') else None
            with self.subTest(path=path):
                self.assertEqual(space.mount_details(path), (expected, kind))

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

    def test_unpreparable_probe_hands_off_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)
            (database / 'local').mkdir()
            replies = [str(database), str(database), '', RuntimeError(space.FAILURE)]
            with patch.object(space, 'command', side_effect=replies), \
                    patch.object(space, 'check_space') as check, \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                space.preflight()
            check.assert_not_called()
            self.assertIn('continuing with pacman', errors.getvalue())

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

    def assert_wrapper_refuses(self, module):
        with patch.dict(module['main'].__globals__, preflight=Mock(side_effect=RuntimeError('space refusal')),
                        run=Mock(return_value=0)), patch.object(os, 'geteuid', return_value=0):
            for arguments in (['emaki-update'], ['emaki-update', '--noninteractive']):
                with patch.object(sys, 'argv', arguments), contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(module['main'](), 1)
                    self.assertEqual(errors.getvalue().strip(), 'space refusal')
            module['main'].__globals__['run'].assert_not_called()

    def test_wrapper_refuses_before_real_pacman(self):
        self.assert_wrapper_refuses(runpy.run_path(str(ROOT / 'scripts/emaki-update')))

    def test_mutant_skipped_check_is_rejected(self):
        path = ROOT / 'scripts/emaki-update'
        source = path.read_text()
        self.assertEqual(source.count('        preflight()'), 1)
        namespace = {'__file__': str(path), '__name__': 'space_mutant'}
        exec(compile(source.replace('        preflight()', '        pass'), str(path), 'exec'), namespace)
        with self.assertRaises(AssertionError):
            self.assert_wrapper_refuses(namespace)

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
                if arguments == ['/usr/bin/pacman-conf', 'DBPath']:
                    return str(database)
                if arguments == ['/usr/bin/pacman-conf', 'CacheDir']:
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
            with patch.object(space, 'command', side_effect=command), self.assertRaisesRegex(RuntimeError, 'Another package operation'):
                space.preflight()
            self.assertEqual(calls[-1], ['/usr/bin/pacman-conf', 'CacheDir'])


if __name__ == '__main__':
    unittest.main()
