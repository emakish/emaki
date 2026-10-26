#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Replace installed files while a shell generation keeps its original helpers."""
from contextlib import contextmanager
import importlib.machinery
import importlib.util
import json
import os
import runpy
from pathlib import Path
import subprocess
import selectors
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(ROOT / 'scripts' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SessionFiles(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='session-files-', dir=evidence)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'installed'
        self.source.mkdir()
        (self.source / 'shell.qml').write_text('old QML')
        (self.source / 'helper.py').write_text('print("old helper")')
        self.binary = self.base / 'emaki'
        self.binary.write_text('#!/bin/sh\necho old-core\n')
        self.binary.chmod(0o700)
        self.helper = load('emaki-session-files')
        # The session reads only the state command; answer it with the real backend over fixtures.
        self.update = load('emaki-session-update')
        # Synthetic process inventory: unrelated host transactions cannot affect capture.
        self.proc = proc = self.base / 'owned-proc'
        (proc / 'sys/kernel/random').mkdir(parents=True)
        (proc / 'sys/kernel/random/boot_id').write_text('11111111-1111-1111-1111-111111111111')
        self.holder = proc / '123'
        (self.holder / 'fd').mkdir(parents=True)
        (self.holder / 'stat').write_text('123 (fixture) S ' + '0 ' * 18 + '1')
        (self.holder / 'comm').write_text('fixture-locker')
        (self.holder / 'cmdline').write_bytes(b'fixture-locker\0')
        transaction_running = self.update.arch.transaction_running
        def fixture_transaction(*args, **kwargs):
            with patch.object(self.update.arch.session, 'PROC', self.proc):
                return transaction_running(*args, **kwargs)
        transactions = patch.object(self.update.arch, 'transaction_running', side_effect=fixture_transaction)
        transactions.start()
        self.addCleanup(transactions.stop)
        self.packages = self.base / 'packages'
        self.lock = self.base / 'db.lck'
        self.marker = self.base / 'update-marker'
        for name, value in (('PACKAGES', self.packages), ('LOCK', self.lock)):
            fixture = patch.object(self.update.arch, name, value)
            fixture.start()
            self.addCleanup(fixture.stop)
        backend = patch.object(self.helper, 'query_state',
                               side_effect=lambda: json.dumps(self.update.desktop_state(self.marker)))
        backend.start()
        self.addCleanup(backend.stop)
        writable = patch.object(self.helper.state, 'readonly_root', return_value=False)
        writable.start()
        self.addCleanup(writable.stop)
        self.environment = patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.base / 'runtime'), NIRI_SOCKET='session-one')
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @contextmanager
    def lock_holder(self):
        self.lock.touch()
        descriptor = self.holder / 'fd/3'
        descriptor.symlink_to(self.lock)
        try:
            yield
        finally:
            descriptor.unlink()

    def prepare(self):
        return self.helper.prepare(self.source, self.binary)

    def test_replacement_keeps_lazy_qml_helpers_and_core_together(self):
        shell = self.prepare()
        self.assertFalse(self.helper.changed(shell.parent))
        (self.source / 'shell.qml').write_text('new QML')
        (self.source / 'helper.py').write_text('print("new helper")')
        self.binary.write_text('#!/bin/sh\necho new-core\n')
        self.assertTrue(self.helper.changed(shell.parent))
        self.assertEqual((shell / 'shell.qml').read_text(), 'old QML')
        self.assertEqual(subprocess.check_output(['python3', '-I', '-B', str(shell / 'helper.py')], text=True), 'old helper\n')
        self.assertEqual(subprocess.check_output([str(shell.parent / 'emaki')], text=True), 'old-core\n')
        self.assertEqual(self.helper.active(self.source), shell)
        restarted = self.prepare()
        self.assertNotEqual(restarted, shell)
        self.assertEqual((restarted / 'shell.qml').read_text(), 'new QML')
        self.assertEqual(self.helper.active(self.source), restarted)

    def test_same_version_reinstall_and_compositor_update_survive_shell_restart(self):
        shell = self.prepare()
        self.update.mark_update(self.marker)
        self.assertTrue(self.helper.changed(shell.parent))
        self.assertTrue(self.helper.changed(self.prepare().parent))
        with patch.dict(os.environ, NIRI_SOCKET='session-two'):
            self.assertFalse(self.helper.changed(self.prepare().parent))

    def test_package_versions_detect_update_without_hook(self):
        package = self.packages / 'qt6-base-6.10-1/desc'
        package.parent.mkdir(parents=True)
        package.write_text('old package')
        shell = self.prepare()
        package.parent.rename(package.parent.with_name('qt6-base-6.10-2'))
        self.assertTrue(self.helper.changed(shell.parent))

    def test_transaction_and_copy_race_do_not_publish_mixed_generation(self):
        copy = self.helper.shutil.copytree

        def replace_during_copy(source, target, **kwargs):
            copy(source, target, **kwargs)
            (source / 'helper.py').write_text('changed during copy')

        with patch.object(self.helper.shutil, 'copytree', side_effect=replace_during_copy):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                self.prepare()
        self.assertFalse((self.helper.runtime(self.source) / 'active.json').exists())

    def test_stale_lock_does_not_delay_panel(self):
        self.lock.touch()
        with patch.object(self.helper.time, 'sleep') as sleep:
            shell = self.helper.prepare(self.source, self.binary, wait=True)
        sleep.assert_not_called()
        self.assertNotEqual(shell, self.source)
        self.assertFalse(self.helper.changed(shell.parent))

    def test_snapshot_root_with_lock_does_not_delay_panel(self):
        self.lock.touch()
        with patch.object(self.helper.state, 'readonly_root', return_value=True), \
                patch.object(self.helper.time, 'sleep') as sleep:
            shell = self.helper.prepare(self.source, self.binary, wait=True)
        sleep.assert_not_called()
        self.assertNotEqual(shell, self.source)

    def test_live_holder_starts_installed_panel_after_short_bound(self):
        with self.lock_holder(), patch.object(self.helper, 'START_WAIT', 0.1):
            start = time.monotonic()
            shell = self.helper.prepare(self.source, self.binary, wait=True)
            elapsed = time.monotonic() - start
        self.assertEqual(shell, self.source)
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, 1)

    def test_running_pacman_search_neither_delays_panel_nor_holds_notice(self):
        code = ('import ctypes, sys; '
                'ctypes.CDLL(None).prctl(15, b"pacman", 0, 0, 0); '
                'print("ready", flush=True); sys.stdin.read()')
        query = subprocess.Popen([sys.executable, '-IB', '-c', code, '-Ss', 'emaki'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(query.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5))
            self.assertEqual(query.stdout.readline().strip(), 'ready')
            proc = self.base / 'proc'
            proc.mkdir()
            (proc / str(query.pid)).symlink_to(Path('/proc') / str(query.pid))
            with patch.object(self, 'proc', proc), \
                    patch.object(self.helper.time, 'sleep') as sleep:
                captured = self.helper.prepare(self.source, self.binary, wait=True)
                self.assertNotEqual(captured, self.source)
                sleep.assert_not_called()
                package = self.packages / 'niri-99-1/desc'
                package.parent.mkdir(parents=True)
                package.touch()
                self.assertTrue(self.helper.changed(captured.parent))
                self.assertTrue(self.helper.changed_installed(self.source))
                self.assertIsNone(query.poll())
        finally:
            query.terminate()
            query.wait(timeout=5)
            query.stdin.close()
            query.stdout.close()

    def test_installed_fallback_keeps_baseline_through_update_and_recapture(self):
        with self.lock_holder():
            self.assertEqual(self.prepare(), self.source)
            baseline = self.helper.runtime(self.source) / 'session.json'
            original = baseline.read_text()
            self.update.mark_update(self.marker)
            self.assertFalse(self.helper.changed_installed(self.source))
        self.assertTrue(self.helper.changed_installed(self.source))
        captured = self.prepare()
        self.assertEqual(baseline.read_text(), original)
        self.assertTrue(self.helper.changed(captured.parent))
        with patch.dict(os.environ, NIRI_SOCKET='session-two'):
            self.prepare()
            self.assertFalse(self.helper.changed_installed(self.source))

    def test_first_fallback_after_hook_marker_still_remembers_old_compositor(self):
        self.update.mark_update(self.marker)
        with self.lock_holder():
            self.assertEqual(self.prepare(), self.source)
            self.assertFalse(self.helper.changed_installed(self.source))
        self.assertTrue(self.helper.changed_installed(self.source))
        self.assertTrue(self.helper.changed(self.prepare().parent))

    def test_failed_capture_keeps_original_baseline(self):
        with patch.object(self.helper.shutil, 'copytree', side_effect=OSError('copy refused')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.helper.changed_installed(self.source))
        self.update.mark_update(self.marker)
        self.assertTrue(self.helper.changed_installed(self.source))
        self.assertTrue(self.helper.changed(self.prepare().parent))

    def test_baseline_survives_failure_before_reading_source_tree(self):
        with patch.object(self.helper, 'tree', side_effect=OSError('source unreadable')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.helper.changed_installed(self.source))
        self.update.mark_update(self.marker)
        self.assertTrue(self.helper.changed_installed(self.source))
        self.assertTrue(self.helper.changed(self.prepare().parent))

    def test_installed_observer_records_a_missing_baseline_instead_of_a_notice(self):
        self.helper.runtime(self.source)
        with patch.object(self.update.arch, 'transaction_running', return_value=False), \
                patch.object(self.helper.state, 'LIVE', self.base / 'absent-live'), \
                patch('builtins.print') as output:
            with patch.object(self.helper.time, 'sleep', side_effect=StopIteration), \
                    self.assertRaises(StopIteration):
                self.helper.watch(self.source, installed_source=True)
            output.assert_not_called()
            self.assertTrue((self.helper.runtime(self.source) / 'session.json').exists())
            self.update.mark_update(self.marker)
            self.helper.watch(self.source, installed_source=True)
        self.assertEqual(json.loads(output.call_args.args[0]), dict(schema=1, restart=True))

    @contextmanager
    def observer(self, sources=True, installed_source=False, target=None, first=None):
        """The generation's observer in a thread, counting state questions; yields (thread, notices, asked).
        first: 'busy' or 'error' makes the first state answer a running transaction or a failure."""
        asked, notices = [], []
        def query(request='state'):
            asked.append(request)
            if request == 'sources':
                if not sources:
                    raise subprocess.CalledProcessError(1, ['emaki-session-update', 'sources'])
                return json.dumps(self.update.state_sources(self.marker))
            if first is not None and asked.count('state') == 1:
                if first == 'error':
                    raise subprocess.CalledProcessError(1, ['emaki-session-update', 'state'])
                return json.dumps(dict(self.update.desktop_state(self.marker), busy=True))
            return json.dumps(self.update.desktop_state(self.marker))
        if target is None:
            target = self.source if installed_source else self.prepare().parent
        with patch.object(self.helper, 'query_state', side_effect=query), \
                patch.object(self.helper.state, 'LIVE', self.base / 'absent-live'), \
                patch.object(self.helper, 'POLL', 0.3), \
                patch.object(self.helper, 'print', create=True, side_effect=lambda line, **_: notices.append(line)):
            thread = threading.Thread(target=self.helper.watch, args=(target, installed_source), daemon=True)
            thread.start()
            try:
                yield thread, notices, asked
            finally:
                if thread.is_alive():
                    # Release a waiting observer: any answer it reads now is an update.
                    self.update.mark_update(self.marker)
                    (self.source / 'release').write_text('end of test')
                thread.join(10)

    def assert_notice(self, thread, notices, within=8):
        thread.join(within)
        self.assertFalse(thread.is_alive(), f'no notice {within} s after the update')
        self.assertEqual([json.loads(line) for line in notices], [dict(schema=1, restart=True)])

    def test_observer_waits_on_the_update_files_instead_of_polling(self):
        for change in ('marker', 'shell file', 'core', 'package'):
            with self.subTest(change), self.observer() as (thread, notices, asked):
                thread.join(1.0)
                settled_questions = len(asked)
                # At idle: no question, no notice; the first case waits past the old 5 s poll.
                thread.join(6.0 if change == 'marker' else 1.5)
                self.assertEqual(len(asked), settled_questions)
                self.assertTrue(thread.is_alive())
                self.assertEqual(notices, [])
                if change == 'marker':
                    self.update.mark_update(self.marker)
                elif change == 'shell file':
                    (self.source / 'helper.py').write_text('new helper')
                elif change == 'core':
                    self.binary.write_text('#!/bin/sh\necho new-core\n')
                else:
                    package = self.packages / 'quickshell-emaki-0.3.1-14/desc'
                    package.parent.mkdir(parents=True, exist_ok=True)
                    package.write_text('%NAME%\nquickshell-emaki\n')
                self.assert_notice(thread, notices, within=3)
            self.marker.unlink(missing_ok=True)
            (self.source / 'helper.py').write_text('print("old helper")')
            self.binary.write_text('#!/bin/sh\necho old-core\n')
            shutil.rmtree(self.packages, ignore_errors=True)
            shutil.rmtree(self.base / 'runtime', ignore_errors=True)
            (self.source / 'release').unlink(missing_ok=True)

    def test_observer_polls_only_while_a_transaction_runs(self):
        # The end of a transaction seen only through its holder writes no watched file.
        generation = self.prepare().parent
        with self.lock_holder(), self.observer(target=generation) as (thread, notices, asked):
            thread.join(0.5)
            self.update.mark_update(self.marker)
            thread.join(1.5)
            self.assertEqual(notices, [])
            polled = len(asked)
            thread.join(1.0)
            self.assertGreater(len(asked), polled, 'no poll while the transaction runs')
            self.holder.joinpath('fd/3').unlink()
            self.assert_notice(thread, notices)
            self.holder.joinpath('fd/3').symlink_to(self.lock)

    def test_observer_without_a_named_source_polls(self):
        # An installed state command that cannot name its files (or no inotify): the old poll.
        with self.observer(sources=False) as (thread, notices, asked):
            thread.join(1.0)
            polled = len(asked)
            thread.join(1.0)
            self.assertGreater(len(asked), polled)
            self.update.mark_update(self.marker)
            self.assert_notice(thread, notices)

    def test_observer_checks_again_after_a_transient_answer(self):
        # The update is on disk before the observer starts, so no watched file changes later; the
        # first answer is a transaction or a failure and every later one is settled. The check that
        # saw no settled desktop must be repeated, not followed by an unbounded wait.
        for installed_source in (False, True):
            for first in ('busy', 'error'):
                with self.subTest(installed_source=installed_source, first=first):
                    if installed_source:
                        target = self.source
                        self.helper.remember(self.helper.runtime(self.source) / 'session.json', self.helper.answer())
                    else:
                        target = self.prepare().parent
                    self.update.mark_update(self.marker)
                    with self.observer(installed_source=installed_source, target=target, first=first) \
                            as (thread, notices, _asked):
                        self.assert_notice(thread, notices, within=3)
                    self.marker.unlink(missing_ok=True)
                    shutil.rmtree(self.base / 'runtime', ignore_errors=True)
                    (self.source / 'release').unlink(missing_ok=True)

    def test_observer_follows_a_moved_parent_directory(self):
        # The core's directory is replaced by one with an identical core (no notice), then that core
        # changes: only watches re-armed on the new directory can see it before the safety check.
        folder = self.base / 'bin'
        folder.mkdir()
        self.binary = self.binary.rename(folder / 'emaki')
        replacement = self.base / 'new-bin'
        replacement.mkdir()
        shutil.copy2(self.binary, replacement / 'emaki')
        with patch.object(self.helper, 'SAFETY', 600), self.observer() as (thread, notices, asked):
            thread.join(1.0)
            settled_questions = len(asked)
            folder.rename(self.base / 'old-bin')
            replacement.rename(folder)
            thread.join(2.0)
            self.assertGreater(len(asked), settled_questions, 'the moved directory was not checked')
            self.assertTrue(thread.is_alive())
            self.assertEqual(notices, [])
            checked = len(asked)
            thread.join(1.0)
            self.assertEqual(len(asked), checked, 'the observer polls instead of waiting')
            self.binary.write_text('#!/bin/sh\necho new-core\n')
            self.assert_notice(thread, notices, within=3)

    def test_observer_follows_a_symlinked_core_to_its_target(self):
        target = self.base / 'build/emaki'
        target.parent.mkdir()
        self.binary = self.binary.rename(target)
        link = self.base / 'emaki-link'
        link.symlink_to(target)
        self.binary = link
        with patch.object(self.helper, 'SAFETY', 600), self.observer() as (thread, notices, asked):
            thread.join(1.0)
            target.write_text('#!/bin/sh\necho new-core\n')
            self.assert_notice(thread, notices, within=3)

    def test_safety_check_sees_what_inotify_cannot(self):
        # A moved ancestor of the core's directory, and an intermediate symlink pointed at another
        # directory: no watched directory gets an event, the stat comparison finds the new core.
        for case in ('ancestor', 'symlink'):
            with self.subTest(case):
                root = self.base / case
                old = root / 'old/bin/emaki'
                new = root / 'new/bin/emaki'
                for path, text in ((old, 'old-core'), (new, 'new-core')):
                    path.parent.mkdir(parents=True)
                    path.write_text(f'#!/bin/sh\necho {text}\n')
                if case == 'ancestor':
                    (root / 'old').rename(root / 'current')
                else:
                    (root / 'current').symlink_to(root / 'old')
                self.binary = root / 'current/bin/emaki'
                try:
                    with patch.object(self.helper, 'SAFETY', 1.0), self.observer() as (thread, notices, asked):
                        thread.join(1.5)
                        settled_questions = len(asked)
                        self.assertTrue(thread.is_alive())
                        self.assertEqual(notices, [])
                        if case == 'ancestor':
                            (root / 'current').rename(root / 'moved-away')
                            (root / 'new').rename(root / 'current')
                        else:
                            (root / 'next').symlink_to(root / 'new')
                            (root / 'next').replace(root / 'current')
                        self.assert_notice(thread, notices, within=3)
                        self.assertGreater(len(asked), settled_questions)
                finally:
                    self.marker.unlink(missing_ok=True)
                    shutil.rmtree(self.base / 'runtime', ignore_errors=True)
                    (self.source / 'release').unlink(missing_ok=True)

    def test_safety_check_at_idle_asks_nothing_and_hashes_nothing(self):
        with patch.object(self.helper, 'SAFETY', 0.2), self.observer() as (thread, notices, asked):
            thread.join(1.0)
            settled_questions = len(asked)
            with patch.object(self.helper, 'digest', wraps=self.helper.digest) as digest, \
                    patch.object(self.helper, 'mark', wraps=self.helper.mark) as mark:
                thread.join(2.0)
                self.assertGreater(mark.call_count, 0, 'no safety check ran')
                self.assertEqual(digest.call_count, 0)
            self.assertEqual(len(asked), settled_questions)
            self.assertTrue(thread.is_alive())
            (self.source / 'shell.qml').write_text('new QML')
            self.assert_notice(thread, notices, within=3)

    def test_installed_observer_waits_on_the_state_files(self):
        self.helper.remember(self.helper.runtime(self.source) / 'session.json', self.helper.answer())
        with self.observer(installed_source=True) as (thread, notices, asked):
            thread.join(2.0)
            settled_questions = len(asked)
            thread.join(1.5)
            self.assertEqual(len(asked), settled_questions)
            self.assertEqual(notices, [])
            self.update.mark_update(self.marker)
            self.assert_notice(thread, notices)

    def test_no_notice_until_transaction_and_later_hooks_exit(self):
        shell = self.prepare()
        with self.lock_holder():
            (self.source / 'helper.py').write_text('new files before later hooks')
            self.assertFalse(self.helper.changed(shell.parent))
            self.update.mark_update(self.marker)
            self.assertFalse(self.helper.changed(shell.parent))
        self.assertTrue(self.helper.changed(shell.parent))

    def test_reason_only_database_change_does_not_request_signout(self):
        package = self.packages / 'qt6-base-6.10-1/desc'
        package.parent.mkdir(parents=True)
        package.write_text('%NAME%\nqt6-base\n\n%VERSION%\n6.10-1\n\n%REASON%\n0\n')
        shell = self.prepare()
        package.write_text(package.read_text().replace('%REASON%\n0', '%REASON%\n1'))
        self.assertFalse(self.helper.changed(shell.parent))

    def test_ssh_ipc_uses_panel_record_and_rejects_dead_or_reused_pid(self):
        shell = self.prepare()
        with patch.dict(os.environ):
            os.environ.pop('NIRI_SOCKET', None)
            os.environ.pop('WAYLAND_DISPLAY', None)
            self.assertEqual(self.helper.active(self.source), shell)
            with patch.object(self.helper.state, 'process_start', return_value=None):
                self.assertEqual(self.helper.active(self.source), self.source)
            with patch.object(self.helper.state, 'process_start', return_value='different-start'):
                self.assertEqual(self.helper.active(self.source), self.source)

    def test_shell_call_from_ssh_reaches_selected_configuration(self):
        shell = self.prepare()
        tools = self.base / 'bin'
        tools.mkdir()
        qs = tools / 'qs'
        qs.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        qs.chmod(0o700)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   EMAKI_SHELL_DIR=str(self.source))
        env.pop('NIRI_SOCKET', None)
        env.pop('WAYLAND_DISPLAY', None)
        wrapper = tools / 'emaki-shell'
        render = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        values = dict(render['paths'](), TRUSTED_PATH=str(tools) + ':/usr/bin')
        wrapper.write_text(render['substitute']((ROOT / 'scripts/emaki-shell').read_text(), values))
        for name in ('emaki_paths.py', 'emaki-session-files', 'emaki_session_state.py'):
            (tools / name).symlink_to(ROOT / 'scripts' / name)
        result = subprocess.run([sys.executable, '-I', str(wrapper), 'call', 'dock', 'status'],
                                env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ['-p', str(shell), 'ipc', 'call', 'dock', 'status'])

    def test_switcher_without_an_answering_panel_focuses_the_previous_window(self):
        shell = self.prepare()
        tools = self.base / 'bin'
        tools.mkdir()
        calls = self.base / 'niri-calls'
        niri = tools / 'niri'
        niri.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n')
        niri.chmod(0o700)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   EMAKI_SHELL_DIR=str(self.source))
        env.pop('NIRI_SOCKET', None)
        env.pop('WAYLAND_DISPLAY', None)
        wrapper = tools / 'emaki-shell'
        render = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        values = dict(render['paths'](), TRUSTED_PATH=str(tools) + ':/usr/bin')
        wrapper.write_text(render['substitute']((ROOT / 'scripts/emaki-shell').read_text(), values))
        for name in ('emaki_paths.py', 'emaki-session-files', 'emaki_session_state.py'):
            (tools / name).symlink_to(ROOT / 'scripts' / name)
        qs = tools / 'qs'
        # The panel's answer to "switcher step <direction> <press time>": it handled the press, it
        # could not, or no panel answered. The press time is this process's start, in ms.
        for answer, status, function, fallback, code in (
                ('true', 0, 'next', False, 0), ('false', 0, 'next', True, 0), ('', 1, 'next', True, 0),
                ('', 1, 'previous', False, 75), ('true', 0, 'appPrevious', False, 0)):
            with self.subTest(answer=answer, status=status, function=function):
                calls.unlink(missing_ok=True)
                qs.write_text(f'#!/bin/sh\n[ "$1 $2 $3 $4 $5 $6 $7" = "-p {shell} ipc call switcher step {function}" ] || exit 9\n'
                              f'printf "%s\\n" "$8" > "{calls}.sent"\n'
                              f'[ -z "{answer}" ] || echo {answer}\nexit {status}\n')
                qs.chmod(0o700)
                before = time.time() * 1000
                result = subprocess.run([sys.executable, '-I', str(wrapper), 'call', 'switcher', function],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(calls.read_text() if calls.exists() else '',
                                 'msg action focus-window-previous\n' if fallback else '', result.stderr)
                self.assertEqual(result.returncode, code, result.stderr)
                sent = int(Path(f'{calls}.sent').read_text())
                self.assertTrue(before - 50 <= sent <= time.time() * 1000, (before, sent))

    def test_text_console_without_session_environment_uses_user_runtime(self):
        runtimes = self.base / 'users'
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(runtimes / str(os.getuid()))):
            shell = self.prepare()
        with patch.dict(os.environ), patch.object(self.helper, 'RUNTIMES', runtimes):
            for key in ('XDG_RUNTIME_DIR', 'NIRI_SOCKET', 'WAYLAND_DISPLAY'):
                os.environ.pop(key, None)
            self.assertEqual(self.helper.active(Path('/usr/share/emaki/shell')), shell)

    def test_wrapper_starts_installed_files_after_capture_failure_or_timeout(self):
        tools = self.base / 'launch-tools'
        tools.mkdir()
        scripts = {
            'emaki-shell': (ROOT / 'scripts/emaki-shell').read_text().replace(
                '@EMAKI_TRUSTED_PATH@', str(tools) + ':/usr/bin'),
            'emaki_paths.py': (ROOT / 'scripts/emaki_paths.py').read_text(),
            'paths': (ROOT / 'scripts/paths').read_text(),
            'emaki-shell-health': '#!/bin/sh\nprintf "%s\\n" "$2"\n',
            'emaki-qt-check': '#!/bin/sh\nexit 0\n',
            'qs': '''#!/usr/bin/python3
import json, os, sys
print(json.dumps([sys.argv[1:], os.environ['EMAKI_BIN'], os.environ.get('EMAKI_SESSION_GENERATION'), os.environ.get('EMAKI_SESSION_SOURCE'), os.environ.get('EMAKI_SESSION_WATCHER')]))
''',
        }
        for name, content in scripts.items():
            (tools / name).write_text(content)
            (tools / name).chmod(0o700)
        helper = tools / 'emaki-session-files'
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   EMAKI_SHELL_DIR=str(self.source), EMAKI_BIN=str(self.binary),
                   EMAKI_SESSION_GENERATION='previous-panel')
        env.pop('EMAKI_LOGIN_HANDOFF', None)
        for script in ('exit 75', 'printf "%s\\n" "$2"'):
            with self.subTest(script=script):
                helper.write_text('#!/bin/sh\n' + script + '\n')
                helper.chmod(0o700)
                result = subprocess.run([str(tools / 'emaki-shell')], env=env, text=True,
                                        capture_output=True, check=True, timeout=3)
                self.assertEqual(json.loads(result.stdout),
                                 [['-n', '-p', str(self.source)], str(self.binary), None, str(self.source), str(helper)])

    def test_removed_installed_files_keep_existing_generation_available(self):
        shell = self.prepare()
        (self.source / 'shell.qml').unlink()
        self.assertTrue(self.helper.changed(shell.parent))
        self.assertEqual((shell / 'shell.qml').read_text(), 'old QML')
        self.assertEqual(self.helper.active(self.source), shell)

    def test_recovery_maps_only_unchanged_personal_files(self):
        health = load('emaki-shell-health')
        home = self.base / 'home'
        original = home / '.config/emaki/shell'
        original.mkdir(parents=True)
        (original / 'Panel.qml').write_text('bad personal file')
        packaged = self.base / 'packaged'
        packaged.mkdir()
        (packaged / 'Panel.qml').write_text('good packaged file')
        with patch.dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config')), patch.object(health, 'PACKAGED', packaged):
            frozen = self.helper.prepare(original, self.binary)
            log = f'Failed to load configuration\ncaused by file://{frozen}/Panel.qml:1: parse failure'
            self.assertEqual(health.failed_file(log, original, str(frozen)), original / 'Panel.qml')
            (original / 'Panel.qml').write_text('fixed while running')
            self.assertIsNone(health.failed_file(log, original, str(frozen)))


if __name__ == '__main__':
    unittest.main()
