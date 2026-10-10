#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise upgrade delivery without touching login sessions or a message bus."""
import importlib.machinery
import importlib.util
import json
import threading
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('session_update', str(ROOT / 'scripts/emaki-session-update'))
spec = importlib.util.spec_from_loader(loader.name, loader)
update = importlib.util.module_from_spec(spec)
loader.exec_module(update)


class SessionUpdate(unittest.TestCase):
    def test_marker_changes_and_refuses_symlink(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=evidence) as directory:
            marker = Path(directory) / 'marker'
            update.mark_update(marker)
            update.os.utime(marker, ns=(1, 1))
            update.mark_update(marker)
            self.assertGreater(marker.stat().st_mtime_ns, 1)
            link = Path(directory) / 'link'
            link.symlink_to(marker)
            with self.assertRaises(OSError):
                update.mark_update(link)

    def test_only_local_graphical_user_sessions(self):
        base = {'User': '1001', 'Remote': 'no', 'Type': 'wayland', 'Class': 'user', 'State': 'active'}
        cases = [base, dict(base, State='online'), dict(base, Remote='yes'),
                 dict(base, Class='greeter'), dict(base, Type='tty'),
                 dict(base, User='0'), dict(base, State='closing'), dict(base, User='bad')]
        def run(command, **kwargs):
            if command[1] == 'list-sessions':
                return types.SimpleNamespace(stdout='\n'.join(str(i + 1) for i in range(len(cases))))
            props = cases[int(command[2]) - 1]
            return types.SimpleNamespace(stdout='\n'.join(k + '=' + v for k, v in props.items()))
        with patch.object(update.subprocess, 'run', side_effect=run):
            self.assertEqual(update.graphical_users(), [1001])

    def test_child_identity_environment_and_timeout(self):
        with patch.object(update.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_gid=1002)), \
                patch.object(update.subprocess, 'run') as run:
            update.notify_user(1001)
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(command[:3], ['python3', '-I', '-B'])
        self.assertEqual(command[-1], '--notify')
        self.assertEqual((options['user'], options['group'], options['extra_groups']), (1001, 1002, []))
        self.assertEqual(options['env'], {'PATH': update.emaki_paths.TRUSTED_PATH, 'LANG': 'C.UTF-8'})
        self.assertEqual(options['timeout'], 5)
        self.assertEqual(options['cwd'], '/')

    def test_detached_worker_has_sanitized_environment(self):
        with patch.object(update.sys, 'argv', ['hook']), patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update.state, 'foreign_root', return_value=False), \
                patch.object(update.state, 'LIVE', Path('/nonexistent-emaki-live-fixture')), \
                patch.object(update, 'targets', return_value=['niri']), \
                patch.object(update, 'mark_update', return_value='transaction') as mark, \
                patch.object(update.subprocess, 'Popen') as spawn, \
                patch.object(update, 'notify_user') as notify:
            self.assertEqual(update.main(), 0)
        mark.assert_called_once_with(components=['niri'])
        notify.assert_not_called()
        self.assertEqual(spawn.call_args.args[0], ['python3', '-I', '-B',
                         str(ROOT / 'scripts/emaki-session-update'), '--after-transaction', 'transaction'])
        self.assertEqual(spawn.call_args.kwargs, dict(start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd='/', env={'PATH': update.emaki_paths.TRUSTED_PATH, 'LANG': 'C.UTF-8'}))

    def test_delivery_failure_keeps_worker_successful(self):
        with patch.object(update.arch, 'transaction_running', return_value=False), \
                patch.object(update.state, 'foreign_root', return_value=False), \
                patch.object(update.state, 'LIVE', Path('/nonexistent-emaki-live-fixture')), \
                patch.object(update.state, 'marker_value', return_value='transaction'), \
                patch.object(update, 'graphical_users', return_value=[1001, 1002]), \
                patch.object(update, 'notify_user', side_effect=subprocess.TimeoutExpired('notify', 5)) as notify:
            self.assertEqual(update.deliver('transaction'), 0)
            self.assertEqual(notify.call_count, 2)

    def test_missing_session_service_keeps_worker_successful(self):
        with patch.object(update.arch, 'transaction_running', return_value=False), \
                patch.object(update.state, 'foreign_root', return_value=False), \
                patch.object(update.state, 'LIVE', Path('/nonexistent-emaki-live-fixture')), \
                patch.object(update.state, 'marker_value', return_value='transaction'), \
                patch.object(update, 'graphical_users', side_effect=OSError('unavailable')):
            self.assertEqual(update.deliver('transaction'), 0)

    def test_live_and_foreign_roots_do_not_mark_spawn_or_contact_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / 'live'
            proc = base / 'proc'
            (proc / '1').mkdir(parents=True)
            (proc / '1/root').symlink_to(base)
            for scenario in ('foreign-root', 'arch-chroot', 'live'):
                with self.subTest(scenario=scenario):
                    if scenario == 'live':
                        live.touch()
                    # arch-chroot can expose matching PID 1 root identity; the
                    # independent virtualization check must still reject it.
                    root = base if scenario != 'foreign-root' else base / 'proc'
                    with patch.object(update.sys, 'argv', ['hook']), \
                            patch.object(update.os, 'geteuid', return_value=0), \
                            patch.object(update.state, 'ROOT', root), \
                            patch.object(update.state, 'PROC', proc), \
                            patch.object(update.state, 'LIVE', live), \
                            patch.object(update.subprocess, 'run', return_value=types.SimpleNamespace(
                                returncode=0 if scenario == 'arch-chroot' else 1)), \
                            patch.object(update, 'mark_update') as mark, \
                            patch.object(update.subprocess, 'Popen') as spawn, \
                            patch.object(update, 'graphical_users') as users, \
                            patch.object(update, 'send_notification') as send:
                        self.assertEqual(update.main(), 0)
                        mark.assert_not_called()
                        spawn.assert_not_called()
                        users.assert_not_called()
                        send.assert_not_called()

    def test_marker_is_scoped_to_root_and_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker'
            with patch.object(update.state, 'root_id', return_value=[12, 34]), \
                    patch.object(update.state, 'boot_id', return_value='boot-a'):
                token = update.mark_update(marker, ['niri', 'qt6-base'])
                self.assertEqual(update.state.marker_value(marker), token)
                record = json.loads(marker.read_text())
                self.assertRegex(record.pop('at'), r'\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00\Z')
                self.assertEqual(record, dict(root=[12, 34], boot='boot-a', transaction=token,
                                              components=['niri', 'qt6-base'], action='sign-out'))
                with patch.object(update.state, 'root_id', return_value=[12, 35]):
                    self.assertIsNone(update.state.marker_value(marker))
                with patch.object(update.state, 'boot_id', return_value='boot-b'):
                    self.assertIsNone(update.state.marker_value(marker))
                marker.write_text('old unscoped marker')
                self.assertIsNone(update.state.marker_value(marker))

    def test_worker_waits_for_live_lock_holder_before_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'db.lck'
            child = subprocess.Popen(['python3', '-I', '-B', '-c',
                'import sys; f=open(sys.argv[1], "w"); print("locked", flush=True); sys.stdin.read()',
                str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.poll() is None and child.kill())
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            waiting = threading.Event()
            notified = threading.Event()
            original = update.arch.transaction_running
            def running():
                busy = original(lock)
                if busy:
                    waiting.set()
                return busy
            with patch.object(update.arch, 'transaction_running', side_effect=running), \
                    patch.object(update.arch.session, 'readonly_root', return_value=False), \
                    patch.object(update.state, 'foreign_root', return_value=False), \
                    patch.object(update.state, 'LIVE', Path(directory) / 'live'), \
                    patch.object(update.state, 'marker_value', return_value='transaction'), \
                    patch.object(update, 'graphical_users', return_value=[1001]), \
                    patch.object(update, 'notify_user', side_effect=lambda uid: notified.set()):
                worker = threading.Thread(target=update.deliver, args=('transaction',), daemon=True)
                worker.start()
                try:
                    self.assertTrue(waiting.wait(3), 'live lock holder was not observed')
                    self.assertFalse(notified.wait(0.15), 'notice escaped during transaction')
                finally:
                    child.communicate(timeout=3)
                    worker.join(timeout=4)
                self.assertFalse(worker.is_alive())
                self.assertTrue(notified.is_set(), 'completed transaction did not notify')
                self.assertTrue(lock.exists(), 'leftover lock fixture unexpectedly disappeared')

    def test_superseded_transaction_and_new_transaction_suppress_delivery(self):
        for marker, running in [('new-token', [False]), ('transaction', [False, True])]:
            with self.subTest(marker=marker, running=running), \
                    patch.object(update.arch, 'transaction_running', side_effect=running), \
                    patch.object(update.state, 'foreign_root', return_value=False), \
                    patch.object(update.state, 'LIVE', Path('/nonexistent-emaki-live-fixture')), \
                    patch.object(update.state, 'marker_value', return_value=marker), \
                    patch.object(update, 'graphical_users', return_value=[1001]), \
                    patch.object(update, 'notify_user') as notify:
                self.assertEqual(update.deliver('transaction'), 0)
                notify.assert_not_called()

    def test_running_package_query_does_not_hold_the_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.Popen(['python3', '-I', '-B', '-c',
                'import ctypes,sys; ctypes.CDLL(None).prctl(15,b"pacman",0,0,0); '
                'print("ready",flush=True); sys.stdin.read()', '-Ss', 'niri'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            notified = threading.Event()
            with patch.object(update.arch, 'LOCK', Path(directory) / 'db.lck'), \
                    patch.object(update.arch.session, 'readonly_root', return_value=False), \
                    patch.object(update.state, 'foreign_root', return_value=False), \
                    patch.object(update.state, 'LIVE', Path(directory) / 'live'), \
                    patch.object(update.state, 'marker_value', return_value='transaction'), \
                    patch.object(update, 'graphical_users', return_value=[1001]), \
                    patch.object(update, 'notify_user', side_effect=lambda uid: notified.set()):
                worker = threading.Thread(target=update.deliver, args=('transaction',), daemon=True)
                worker.start()
                try:
                    delivered_while_query_running = notified.wait(3)
                    self.assertIsNone(child.poll())
                finally:
                    child.communicate(timeout=3)
                    worker.join(timeout=4)
                self.assertFalse(worker.is_alive())
                self.assertTrue(delivered_while_query_running)

    def test_hook_sorts_after_snapshot_and_other_repository_hooks(self):
        hook = ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook'
        self.assertTrue(hook.exists())
        hooks = [path.name for path in (ROOT / 'packaging').rglob('*.hook')]
        self.assertEqual(sorted(hooks + ['zz-snap-pac-post.hook'])[-1], hook.name)
        self.assertIn('When = PostTransaction', hook.read_text())

    def test_root_never_connects_to_user_bus(self):
        with patch.object(update.sys, 'argv', ['hook', '--notify']), \
                patch.object(update.os, 'getuid', return_value=0), \
                patch.object(update, 'send_notification') as send:
            self.assertEqual(update.main(), 1)
            send.assert_not_called()

    def test_notification_content_and_critical_hint(self):
        calls = []
        connection = types.SimpleNamespace(call_sync=lambda *args: calls.append(args))
        gio = types.SimpleNamespace(
            DBusConnection=types.SimpleNamespace(new_for_address_sync=lambda *args: connection),
            DBusConnectionFlags=types.SimpleNamespace(AUTHENTICATION_CLIENT=1, MESSAGE_BUS_CONNECTION=2),
            DBusCallFlags=types.SimpleNamespace(NO_AUTO_START=1))
        glib = types.SimpleNamespace(Variant=lambda signature, value: (signature, value))
        gi = types.SimpleNamespace(require_version=lambda *args: None)
        with patch.dict('sys.modules', {'gi': gi, 'gi.repository': types.SimpleNamespace(Gio=gio, GLib=glib)}), \
                patch.object(update.os, 'getuid', return_value=1001):
            update.send_notification()
        values = calls[0][4][1]
        self.assertEqual(values[0], 'Desktop update')
        self.assertEqual(values[3:5], (update.SUMMARY, update.BODY))
        self.assertEqual(values[6]['urgency'], ('y', 2))
        self.assertEqual(values[7], 0)
        qml = (ROOT / 'shell/SessionUpdateNotice.qml').read_text()
        self.assertIn(update.SUMMARY, qml)
        self.assertIn(update.BODY, qml)


if __name__ == '__main__':
    unittest.main()
