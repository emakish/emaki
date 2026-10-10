#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Replace installed files while a shell generation keeps its original helpers."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import selectors
import sys
import tempfile
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
        with self.lock.open('w'), patch.object(self.helper, 'START_WAIT', 0.1):
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
            with patch.object(self.helper.state, 'PROC', proc), \
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
        with self.lock.open('w'):
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
        with self.lock.open('w'):
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

    def test_no_notice_until_transaction_and_later_hooks_exit(self):
        shell = self.prepare()
        with self.lock.open('w'):
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
        result = subprocess.run([str(ROOT / 'scripts/emaki-shell'), 'call', 'dock', 'status'],
                                env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ['-p', str(shell), 'ipc', 'call', 'dock', 'status'])

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
            'emaki-shell': (ROOT / 'scripts/emaki-shell').read_text(),
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
