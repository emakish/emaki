#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Protected publication and compositor-lifetime generation retention."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import stat
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('shell_tree', ROOT / 'scripts/emaki_shell_tree.py')
TREE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TREE)


class Ownership(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shell-ownership-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def check_fixture(self, target, uid=None):
        # The real fixture retains its actual type and permissions. Only its
        # ancestors represent the protected installation directories unavailable
        # to an unprivileged test; uid=0 represents a root-owned leaf for mode tests.
        original = Path.lstat

        def metadata(path):
            actual = original(path)
            fields = list(actual)
            if path == target:
                if uid is not None:
                    fields[4] = uid
            else:
                fields[4] = 0
                fields[0] &= ~0o022
            return os.stat_result(fields)

        with patch.object(Path, 'lstat', metadata):
            return TREE.trusted_path(target)

    @unittest.skipIf(os.getuid() == 0, 'requires an ordinary user-owned fixture')
    def test_real_user_directory_and_file_are_rejected(self):
        directory = self.base / 'shell'
        directory.mkdir(mode=0o755)
        file = directory / 'shell.qml'
        file.write_text('prompt')
        for target in (directory, file):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    TREE.trusted_path(target)
                with self.assertRaisesRegex(ValueError, str(target)):
                    self.check_fixture(target)

    def test_group_or_other_writable_directory_and_file_are_rejected(self):
        directory = self.base / 'shell'
        directory.mkdir()
        file = self.base / 'shell.qml'
        file.write_text('prompt')
        for target in (directory, file):
            for bits in (0o020, 0o002):
                with self.subTest(target=target, bits=bits):
                    target.chmod((0o755 if target.is_dir() else 0o644) | bits)
                    with self.assertRaisesRegex(ValueError, str(target)):
                        self.check_fixture(target, uid=0)

    def test_root_link_cannot_cross_a_writable_intermediate_directory(self):
        writable = self.base / 'writable'
        writable.mkdir()
        writable.chmod(0o777)
        safe = self.base / 'safe'
        safe.mkdir()
        file = safe / 'shell.qml'
        file.write_text('prompt')
        (writable / 'redirect').symlink_to(safe, target_is_directory=True)
        link = self.base / 'root-link'
        link.symlink_to(writable / 'redirect', target_is_directory=True)
        original = Path.lstat

        def metadata(path):
            fields = list(original(path))
            fields[4] = 0
            if path != writable:
                fields[0] &= ~0o022
            return os.stat_result(fields)

        with patch.object(Path, 'lstat', metadata):
            with self.assertRaisesRegex(ValueError, str(writable)):
                TREE.trusted_path(link / 'shell.qml')

    @unittest.skipIf(os.geteuid() == 0, 'requires an unprivileged caller')
    def test_package_cli_rejects_an_unprivileged_install(self):
        script = self.base / 'emaki-shell-generations'
        script.write_text((ROOT / 'scripts/emaki-shell-generations').read_text().replace(
            '@EMAKI_LIBEXECDIR@', str(ROOT / 'scripts')))
        result = subprocess.run([sys.executable, '-I', str(script), 'install'],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('root package installation is required', result.stderr)


class Generations(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shell-generations-')
        self.base = Path(self.temporary.name)
        # Published directories deliberately lack owner write. Restore it only
        # for fixture cleanup; production pruning performs the same bounded step.
        self.addCleanup(self.cleanup)
        self.source = self.base / 'installed-shell'
        (self.source / 'Emaki/Sudo').mkdir(parents=True)
        (self.source / 'shell.qml').write_text('version one')
        (self.source / 'Emaki/Sudo/libemakisudoplugin.so').write_bytes(b'plugin one')
        self.binary = self.base / 'emaki'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o755)
        self.watcher = self.base / 'watcher.py'
        self.watcher.write_text('print("watch")\n')
        self.module = self.base / 'emaki_session_state.py'
        self.module.write_text('VALUE = 1\n')
        self.paths_module = self.base / 'emaki_paths.py'
        self.paths_module.write_text('DATADIR = ' + repr(str(self.base)) + '\n')
        self.store = self.base / 'store'
        self.runtimes = self.base / 'runtimes'
        self.runtime = self.runtimes / str(os.getuid())
        self.runtime.mkdir(parents=True, mode=0o700)
        self.controller = os.getpid()
        self.start = TREE.process_start(self.controller)
        # This fixture represents root-owned package files. Ownership itself is
        # exercised above with the production validator and real stat metadata.
        root_files = patch.object(TREE, 'trusted_path', side_effect=lambda path, **kwargs: Path(path).resolve(strict=True))
        runtimes = patch.object(TREE, 'RUNTIMES', self.runtimes)
        root_files.start()
        runtimes.start()
        self.addCleanup(root_files.stop)
        self.addCleanup(runtimes.stop)

    def cleanup(self):
        for directory, _, _ in os.walk(self.base):
            Path(directory).chmod(0o755)
        self.temporary.cleanup()

    def publish(self, content=None):
        if content is not None:
            (self.source / 'shell.qml').write_text(content)
        return TREE.publish(self.source, self.binary, self.watcher,
                            [self.module, self.paths_module], self.store)

    def record(self):
        return self.runtime / 'emaki-shell-generations' / f'{self.controller}-{self.start}.json'

    def test_publication_is_complete_readonly_and_idempotent(self):
        generation = self.publish()
        before = {str(path.relative_to(generation)): (path.stat().st_ino, path.stat().st_mtime_ns)
                  for path in generation.rglob('*')}
        self.assertEqual(TREE.select(self.controller, self.store), generation)
        self.assertEqual(self.publish(), generation)
        after = {str(path.relative_to(generation)): (path.stat().st_ino, path.stat().st_mtime_ns)
                 for path in generation.rglob('*')}
        self.assertEqual(before, after)
        self.assertEqual((self.store / 'current').read_text().strip(), generation.name)
        for path in (generation, *generation.rglob('*')):
            self.assertFalse(path.stat().st_mode & 0o222, str(path))
        self.assertTrue((generation / 'emaki').stat().st_mode & stat.S_IXUSR)
        self.assertTrue(json.loads((generation / 'generation.json').read_text())['trusted'])

    def test_republishing_older_digest_is_the_newest_publication(self):
        original = self.publish('original')
        before = {str(path.relative_to(original)): (path.stat().st_ino, path.stat().st_mtime_ns)
                  for path in original.rglob('*')}
        newer = self.publish('newer')
        # Represent earlier publications without depending on filesystem clock
        # resolution or a sleep between closely spaced package operations.
        os.utime(original, ns=(1, 1))
        os.utime(newer, ns=(2, 2))
        self.assertEqual(TREE.newest_generation(self.store), newer)
        self.assertEqual(self.publish('original'), original)
        self.assertGreater(original.stat().st_mtime_ns, newer.stat().st_mtime_ns)
        self.assertEqual(TREE.select(self.controller, self.store), original)
        after = {str(path.relative_to(original)): (path.stat().st_ino, path.stat().st_mtime_ns)
                 for path in original.rglob('*')}
        self.assertEqual(before, after)

    def test_newest_selection_skips_an_unsafe_candidate(self):
        valid = self.publish()
        unsafe = self.store / ('f' * 64)
        unsafe.mkdir()
        os.utime(unsafe, ns=(valid.stat().st_mtime_ns + 1, valid.stat().st_mtime_ns + 1))
        original = TREE.trusted_tree

        def validate(path, **kwargs):
            if Path(path) == unsafe:
                raise ValueError('candidate is user-owned')
            return original(path, **kwargs)

        with patch.object(TREE, 'trusted_tree', side_effect=validate):
            self.assertEqual(TREE.newest_generation(self.store), valid)

    def test_mid_session_update_and_shell_restart_retain_original_generation(self):
        original = self.publish()
        self.assertEqual(TREE.select(self.controller, self.store), original)
        current = self.publish('version two')
        self.assertNotEqual(original, current)
        # A new shell process passes the same live compositor identity; its own
        # PID does not participate in the record name or generation selection.
        self.assertEqual(TREE.select(self.controller, self.store), original)
        self.assertEqual((original / 'shell/shell.qml').read_text(), 'version one')
        self.assertEqual((current / 'shell/shell.qml').read_text(), 'version two')
        record = json.loads(self.record().read_text())
        self.assertEqual(record, dict(controller=self.controller, start=self.start, generation=original.name))

    def test_pruning_preserves_live_records_current_previous_and_removes_dead(self):
        live_generation = self.publish('live')
        TREE.select(self.controller, self.store)
        dead_generation = self.publish('dead')
        dead_record = self.record().with_name('dead.json')
        dead_record.write_text(json.dumps(dict(controller=self.controller,
                                              start='different-process-start', generation=dead_generation.name)))
        previous = self.publish('previous')
        current = self.publish('current')
        TREE.prune(self.store)
        self.assertTrue(live_generation.is_dir())
        self.assertTrue(previous.is_dir())
        self.assertTrue(current.is_dir())
        self.assertFalse(dead_generation.exists())
        self.record().unlink()
        TREE.prune(self.store)
        self.assertFalse(live_generation.exists())
        self.assertTrue(previous.is_dir())
        self.assertTrue(current.is_dir())

    def test_malicious_generation_path_cannot_escape_store(self):
        generation = self.publish()
        TREE.select(self.controller, self.store)
        for value in ('../../outside', str(self.source), 'a' * 63, 'a' * 64 + '/shell'):
            with self.subTest(value=value):
                self.record().write_text(json.dumps(dict(controller=self.controller,
                                                         start=self.start, generation=value)))
                self.assertEqual(TREE.select(self.controller, self.store), generation)
        outside = self.base / 'outside.json'
        outside.write_text('not a generation record')
        self.record().unlink()
        self.record().symlink_to(outside)
        self.assertEqual(TREE.select(self.controller, self.store), generation)
        self.assertFalse(self.record().is_symlink())
        self.assertEqual(outside.read_text(), 'not a generation record')

    def test_lock_contention_has_a_bounded_failure(self):
        generation = self.publish()
        with (self.store / '.publish-lock').open('rb') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            with patch.object(TREE, 'LOCK_WAIT', 0), patch.object(TREE.time, 'sleep') as sleep:
                with self.assertRaisesRegex(TimeoutError, 'lock is busy'):
                    self.publish('blocked')
                self.assertEqual(TREE.select(self.controller, self.store), generation)
                sleep.assert_not_called()
        with TREE.locked(self.store):
            pass

    def test_legacy_public_lock_cannot_block_publication_or_selection(self):
        self.store.mkdir()
        legacy = self.store / '.lock'
        legacy.write_text('')
        legacy.chmod(0o644)
        with legacy.open('rb') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_SH)
            with patch.object(TREE, 'LOCK_WAIT', 0):
                generation = self.publish()
                self.assertEqual(TREE.select(self.controller, self.store), generation)
        self.assertEqual((self.store / '.publish-lock').stat().st_mode & 0o777, 0o600)

    def test_nonprivate_or_nonregular_publication_lock_is_rejected(self):
        self.store.mkdir()
        lock = self.store / '.publish-lock'
        lock.write_text('')
        lock.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'not private'):
            self.publish()
        lock.unlink()
        os.mkfifo(lock, mode=0o600)
        with self.assertRaisesRegex(ValueError, 'not private'):
            self.publish()

    def test_predictable_legacy_temporaries_are_not_used(self):
        self.publish()
        outside = self.base / 'untouched'
        outside.write_text('unchanged')
        for name in ('.current-new', '.previous-new'):
            (self.store / name).symlink_to(outside)
        latest = self.publish('latest')
        self.assertEqual(outside.read_text(), 'unchanged')
        self.assertEqual(TREE.pointer(self.store, 'current'), latest.name)
        self.assertFalse(list(self.store.glob('.pointer-*')))

    def test_failed_pointer_publication_selects_newest_complete_generation(self):
        old = self.publish()
        real_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.store / 'current':
                raise OSError('publication fixture failure')
            return real_replace(source, destination)

        with patch.object(TREE.os, 'replace', side_effect=replace):
            with self.assertRaisesRegex(OSError, 'fixture failure'):
                self.publish('newest')
        self.assertEqual(TREE.pointer(self.store, 'current'), old.name)
        selected = TREE.select(self.controller, self.store)
        self.assertNotEqual(selected, old)
        self.assertEqual((selected / 'shell/shell.qml').read_text(), 'newest')
        self.assertFalse(list(self.store.glob('.pointer-*')))

    def test_invalid_current_record_and_newest_candidate_fall_back(self):
        self.publish()
        latest = self.publish('latest')
        TREE.select(self.controller, self.store)
        bad = self.store / ('f' * 64)
        bad.mkdir()
        (self.store / 'current').write_text(bad.name)
        self.record().write_text(json.dumps(dict(controller=self.controller, start=self.start,
                                                 generation=bad.name)))
        self.assertEqual(TREE.select(self.controller, self.store), latest)
        self.record().unlink()
        (self.store / 'current').unlink()
        self.assertEqual(TREE.select(self.controller, self.store), latest)

    def test_selection_retries_if_pruning_wins_record_race(self):
        old = self.publish()
        TREE.select(self.controller, self.store)
        newest = self.publish('newest')
        real_write = TREE.write_json
        removed = False

        def write(path, value, mode=0o600):
            nonlocal removed
            real_write(path, value, mode)
            if not removed:
                removed = True
                for directory, _, _ in os.walk(old):
                    Path(directory).chmod(0o755)
                shutil.rmtree(old)

        with patch.object(TREE, 'write_json', side_effect=write):
            self.assertEqual(TREE.select(self.controller, self.store), newest)
        self.assertEqual(json.loads(self.record().read_text())['generation'], newest.name)

    def test_real_package_entrypoint_publishes_readable_files_under_restrictive_umask(self):
        render = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        values = render['paths'](prefix=str(self.base / 'prefix'),
                                 localstatedir=str(self.base / 'state'))
        datadir, bindir, libexec = (Path(values[key]) for key in ('DATADIR', 'BINDIR', 'LIBEXECDIR'))
        for directory in (datadir, bindir, libexec):
            directory.mkdir(parents=True)
        shutil.copytree(self.source, datadir / 'shell')
        shutil.copy2(self.binary, bindir / 'emaki')
        shutil.copy2(self.watcher, bindir / 'emaki-session-files')
        shutil.copy2(self.module, libexec / self.module.name)
        (libexec / 'emaki_paths.py').write_text(render['modules'](values)[0])
        manager = bindir / 'emaki-shell-generations'
        manager.write_text(render['substitute'](
            (ROOT / 'scripts/emaki-shell-generations').read_text(), values))
        store = Path(values['LOCALSTATEDIR']) / 'shell-generations'
        previous_umask = os.umask(0o077)
        try:
            # Execute the package manager's real main and publication backend.
            # Only root identity/ownership and the filesystem installation root
            # are represented by this ordinary-user fixture.
            with patch.object(os, 'geteuid', return_value=0), \
                    patch.object(TREE, 'STORE', store), \
                    patch.dict(sys.modules, emaki_shell_tree=TREE), \
                    patch.object(sys, 'argv', [str(manager), 'install']), \
                    patch.object(sys, 'path', sys.path.copy()), patch.dict(os.environ):
                runpy.run_path(str(manager), run_name='__main__')
                resulting_umask = os.umask(0o077)
                self.assertEqual(resulting_umask, 0o022)
        finally:
            os.umask(previous_umask)
        generation = store / TREE.pointer(store, 'current')
        self.assertEqual((generation / 'shell/shell.qml').read_text(), 'version one')
        self.assertEqual((generation / 'emaki_paths.py').read_text(), render['modules'](values)[0])
        self.assertEqual((generation / 'emaki_session_state.py').read_text(), self.module.read_text())
        self.assertEqual((store / '.publish-lock').stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.stat().st_mode & 0o777, 0o755)
        self.assertEqual(generation.stat().st_mode & 0o777, 0o555)
        self.assertEqual((generation / 'shell/shell.qml').stat().st_mode & 0o777, 0o444)
        self.assertEqual(TREE.select(self.controller, store), generation)

    def test_malformed_record_disables_pruning(self):
        first = self.publish('first')
        TREE.select(self.controller, self.store)
        self.publish('second')
        self.record().write_text('{broken')
        self.publish('third')
        self.assertTrue(first.is_dir())


if __name__ == '__main__':
    unittest.main()
